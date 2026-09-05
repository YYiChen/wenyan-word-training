"""Leaderboard and answer-record persistence helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from server_config import (
    ANSWER_RECORDS_BACKUP_DIR,
    ANSWER_RECORDS_PATH,
    LEADERBOARD_BACKUP_DIR,
    LEADERBOARD_PATH,
    LEGACY_LEADERBOARD_PATH,
)
from server_storage import read_json
from server_validators import (
    prune_answer_records,
    validate_answer_records,
    validate_leaderboard,
)


_BACKUP_WRITER: Callable[..., None] | None = None


def configure_paths(
    *,
    answer_records_path: Path,
    answer_records_backup_dir: Path,
    leaderboard_path: Path,
    leaderboard_backup_dir: Path,
    legacy_leaderboard_path: Path,
    backup_writer: Callable[..., None],
) -> None:
    global ANSWER_RECORDS_PATH, ANSWER_RECORDS_BACKUP_DIR
    global LEADERBOARD_PATH, LEADERBOARD_BACKUP_DIR, LEGACY_LEADERBOARD_PATH
    global _BACKUP_WRITER
    ANSWER_RECORDS_PATH = answer_records_path
    ANSWER_RECORDS_BACKUP_DIR = answer_records_backup_dir
    LEADERBOARD_PATH = leaderboard_path
    LEADERBOARD_BACKUP_DIR = leaderboard_backup_dir
    LEGACY_LEADERBOARD_PATH = legacy_leaderboard_path
    _BACKUP_WRITER = backup_writer


def backup_and_write(path: Path, payload: Any, backup_dir: Path) -> None:
    if _BACKUP_WRITER is None:
        raise RuntimeError("答题记录存储服务尚未配置。")
    _BACKUP_WRITER(path, payload, backup_dir)


def load_answer_records(persist_pruned: bool = False) -> list[dict[str, Any]]:
    records = validate_answer_records(read_json(ANSWER_RECORDS_PATH, []))
    retained = prune_answer_records(records)
    if persist_pruned and retained != records:
        backup_and_write(ANSWER_RECORDS_PATH, retained, ANSWER_RECORDS_BACKUP_DIR)
    return retained


def filter_student_answer_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only currently visible records to the student read-only API."""
    return [record for record in records if not record.get("archived", False)]


def ensure_answer_records() -> None:
    if not ANSWER_RECORDS_PATH.exists():
        return
    try:
        load_answer_records(persist_pruned=True)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        # Keep the original file in the automatic backup area, then repair the
        # active file so an old or damaged history cannot block the service.
        print(f"答题记录文件需要修复：{error}")
        try:
            backup_and_write(ANSWER_RECORDS_PATH, [], ANSWER_RECORDS_BACKUP_DIR)
            print("已备份原答题记录并创建空的答题记录文件。")
        except OSError as repair_error:
            # A permission/lock problem should be visible in the launcher log,
            # but the student page can still be used without history access.
            print(f"答题记录文件修复失败，将继续启动服务：{repair_error}")


def ensure_leaderboard() -> None:
    if LEADERBOARD_PATH.exists():
        return

    try:
        legacy_payload = read_json(LEGACY_LEADERBOARD_PATH, [])
        leaderboard = validate_leaderboard(legacy_payload)
        backup_and_write(LEADERBOARD_PATH, leaderboard, LEADERBOARD_BACKUP_DIR)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"旧排行榜迁移失败，请检查 {LEGACY_LEADERBOARD_PATH}：{error}") from error


def save_quiz_result(
    record: dict[str, Any],
    name: str,
    add_to_leaderboard: bool,
) -> dict[str, Any]:
    """Persist one solo result and its optional idempotent leaderboard entry.

    The caller owns the process-wide write lock.  This service deliberately
    keeps the existing two-file write order and response payload unchanged.
    """
    current_records = load_answer_records()
    record_changed = False
    existing_index = next(
        (index for index, item in enumerate(current_records) if item["id"] == record["id"]),
        None,
    )
    if existing_index is None:
        next_records = prune_answer_records(validate_answer_records([*current_records, record]))
        record_changed = True
    else:
        existing = current_records[existing_index]
        # An anonymous retry must not erase a name that was already attached
        # to this idempotent result.
        requested_name = name or existing["name"]
        if existing["name"] != requested_name:
            current_records[existing_index] = {**existing, "name": requested_name}
            record_changed = True
        next_records = prune_answer_records(validate_answer_records(current_records))

    current_leaderboard = validate_leaderboard(read_json(LEADERBOARD_PATH, []))
    next_leaderboard = current_leaderboard
    if add_to_leaderboard:
        leaderboard_entry_id = f"score-{record['id']}"
        entry = {
            "id": leaderboard_entry_id,
            "recordId": record["id"],
            "name": name,
            "score": record["score"],
            "createdAt": record["finishedAt"] or int(time.time() * 1000),
            "context": record["context"],
        }
        matching_index = next(
            (
                index for index, item in enumerate(current_leaderboard)
                if item.get("recordId") == record["id"] or item.get("id") == leaderboard_entry_id
            ),
            None,
        )
        if matching_index is None:
            next_leaderboard = validate_leaderboard([*current_leaderboard, entry])
        else:
            merged = {**current_leaderboard[matching_index], **entry}
            next_leaderboard = validate_leaderboard([
                merged if index == matching_index else item
                for index, item in enumerate(current_leaderboard)
            ])

    if record_changed:
        backup_and_write(ANSWER_RECORDS_PATH, next_records, ANSWER_RECORDS_BACKUP_DIR)
    if next_leaderboard != current_leaderboard:
        backup_and_write(LEADERBOARD_PATH, next_leaderboard, LEADERBOARD_BACKUP_DIR)
    saved_record = next((item for item in next_records if item["id"] == record["id"]), record)
    return {
        "record": saved_record,
        "leaderboard": next_leaderboard,
        "leaderboardSaved": add_to_leaderboard,
    }


def save_pk_result(record: dict[str, Any]) -> dict[str, Any]:
    """Persist one PK result exactly once per match id."""
    current_records = load_answer_records()
    existing = next(
        (
            item for item in current_records
            if item.get("recordType") == "pk"
            and item.get("matchId") == record["matchId"]
        ),
        None,
    )
    if existing is not None:
        return {"record": existing, "recordSaved": False}

    next_records = prune_answer_records(validate_answer_records([*current_records, record]))
    backup_and_write(ANSWER_RECORDS_PATH, next_records, ANSWER_RECORDS_BACKUP_DIR)
    saved_record = next((item for item in next_records if item["id"] == record["id"]), record)
    return {"record": saved_record, "recordSaved": True}
