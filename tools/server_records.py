"""Leaderboard and answer-record persistence helpers."""

from __future__ import annotations

import json
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

