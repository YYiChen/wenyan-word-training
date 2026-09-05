"""Question-bank history, review, and initialization services."""

from __future__ import annotations

import copy
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from server_config import (
    PUBLIC_QUESTION_BANK_PATH,
    QUESTION_BANK_HISTORY_PATH,
    QUESTION_REVIEWS_PATH,
    QUESTIONS_PATH,
)
from server_storage import read_json
from server_validators import (
    empty_question_bank,
    empty_question_bank_history,
    empty_question_review,
    empty_question_reviews,
    make_json_etag,
    question_core_signature,
    question_detail_signature,
    validate_question_bank_history,
    validate_question_reviews,
    validate_questions,
)


_BACKUP_WRITER: Callable[..., None] | None = None


def configure_paths(
    *,
    questions_path: Path,
    public_question_bank_path: Path,
    question_reviews_path: Path,
    question_bank_history_path: Path,
    backup_writer: Callable[..., None],
) -> None:
    global QUESTIONS_PATH, PUBLIC_QUESTION_BANK_PATH, QUESTION_REVIEWS_PATH
    global QUESTION_BANK_HISTORY_PATH, _BACKUP_WRITER
    QUESTIONS_PATH = questions_path
    PUBLIC_QUESTION_BANK_PATH = public_question_bank_path
    QUESTION_REVIEWS_PATH = question_reviews_path
    QUESTION_BANK_HISTORY_PATH = question_bank_history_path
    _BACKUP_WRITER = backup_writer


def backup_and_write(path: Path, payload: Any) -> None:
    if _BACKUP_WRITER is None:
        raise RuntimeError("题库存储服务尚未配置。")
    _BACKUP_WRITER(path, payload)

def apply_question_review_publication_status(
    question_bank: dict[str, Any],
    question_id: str,
    review: dict[str, Any],
) -> bool:
    """Keep the student-facing publish state in sync with quick review status.

    Quick review records are stored separately from the question bank, but a
    question marked for revision must also be excluded by the student API.
    Candidate is the existing, student-blocked state used for unpublished
    questions; a later passed review promotes it back to verified.
    """
    question = next(
        (item for item in question_bank["questions"] if item["id"] == question_id),
        None,
    )
    if question is None:
        return False

    review_status = review["status"]
    current_status = question.get("reviewStatus")
    if review_status == "needs_revision":
        if current_status == "abnormal" or current_status == "candidate":
            return False
        question["reviewStatus"] = "candidate"
        return True

    if review_status == "passed" and current_status in {"candidate", "admin_created", "admin_edited"}:
        question["reviewStatus"] = "verified"
        question.pop("reviewNote", None)
        return True

    return False

def sync_question_reviews_after_bank_write(
    previous_bank: dict[str, Any],
    next_bank: dict[str, Any],
) -> None:
    """Reset reviews for changed questions and remove reviews for deleted IDs."""
    current = validate_question_reviews(read_json(QUESTION_REVIEWS_PATH, empty_question_reviews()))
    reviews = dict(current["reviews"])
    previous_by_id = {question["id"]: question for question in previous_bank["questions"]}
    next_by_id = {question["id"]: question for question in next_bank["questions"]}
    changed = False

    for question_id in list(reviews):
        if question_id not in next_by_id:
            reviews.pop(question_id, None)
            changed = True

    for question_id in previous_by_id.keys() & next_by_id.keys():
        previous_question = previous_by_id[question_id]
        next_question = next_by_id[question_id]
        if (
            question_core_signature(previous_question) != question_core_signature(next_question)
            or question_detail_signature(previous_question) != question_detail_signature(next_question)
        ):
            next_review = empty_question_review()
            if reviews.get(question_id) != next_review:
                reviews[question_id] = next_review
                changed = True

    if changed:
        backup_and_write(
            QUESTION_REVIEWS_PATH,
            {"schemaVersion": 1, "reviews": reviews},
        )

def ensure_question_reviews() -> None:
    try:
        question_bank = validate_questions(read_json(QUESTIONS_PATH))
        current = validate_question_reviews(read_json(QUESTION_REVIEWS_PATH, empty_question_reviews()))
        question_ids = {question["id"] for question in question_bank["questions"]}
        clean_reviews = {
            question_id: review
            for question_id, review in current["reviews"].items()
            if question_id in question_ids
        }
        normalized = {"schemaVersion": 1, "reviews": clean_reviews}
        if normalized != current:
            backup_and_write(QUESTION_REVIEWS_PATH, normalized)

        # Repair the publication state of existing records as well. This is
        # needed when an older server saved needs_revision only in the review
        # file, leaving the question incorrectly visible to students.
        synced_bank = copy.deepcopy(question_bank)
        bank_changed = any(
            apply_question_review_publication_status(synced_bank, question_id, review)
            for question_id, review in clean_reviews.items()
        )
        if bank_changed:
            backup_and_write(QUESTIONS_PATH, synced_bank)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"题目审查记录检查失败，请检查 {QUESTION_REVIEWS_PATH}：{error}") from error

def ensure_question_bank_history() -> None:
    try:
        raw = read_json(QUESTION_BANK_HISTORY_PATH, empty_question_bank_history())
        normalized = validate_question_bank_history(raw)
        if normalized != raw or not QUESTION_BANK_HISTORY_PATH.exists():
            backup_and_write(QUESTION_BANK_HISTORY_PATH, normalized)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"题库历史记录检查失败，请检查 {QUESTION_BANK_HISTORY_PATH}：{error}") from error

def append_question_bank_history_event(event: dict[str, Any]) -> dict[str, Any]:
    current = validate_question_bank_history(
        read_json(QUESTION_BANK_HISTORY_PATH, empty_question_bank_history())
    )
    if any(item["id"] == event.get("id") for item in current["events"]):
        raise ValueError("题库历史记录 id 已存在。")
    next_history = validate_question_bank_history({
        "schemaVersion": 1,
        "events": [*current["events"], event],
    })
    backup_and_write(QUESTION_BANK_HISTORY_PATH, next_history)
    return next_history

def revoke_question_bank_import(event_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    history = validate_question_bank_history(
        read_json(QUESTION_BANK_HISTORY_PATH, empty_question_bank_history())
    )
    target = next(
        (event for event in history["events"] if event["kind"] == "import" and event["id"] == event_id),
        None,
    )
    if target is None:
        raise ValueError("找不到要撤销的题库导入记录。")
    if any(
        event["kind"] == "revoke" and event["targetEventId"] == event_id
        for event in history["events"]
    ):
        raise ValueError("这次题库导入已经撤销，不能重复操作。")

    current_bank = validate_questions(read_json(QUESTIONS_PATH))
    current_hash = make_json_etag(current_bank)
    if target["mode"] == "replace":
        if current_hash != target["afterHash"]:
            raise ValueError("本次替换导入后题库已有变化，不能安全撤销；请先处理最近一次题库变更。")
        next_bank = validate_questions(copy.deepcopy(target["beforeBank"]))
    else:
        removed_question_ids = set(target["addedQuestionIds"])
        next_bank = copy.deepcopy(current_bank)
        next_bank["questions"] = [
            question
            for question in next_bank["questions"]
            if question["id"] not in removed_question_ids
        ]
        remaining_questions = next_bank["questions"]
        used_article_ids = {question["articleId"] for question in remaining_questions}
        added_article_ids = set(target["addedArticleIds"])
        next_bank["catalog"] = [
            article
            for article in next_bank.get("catalog", [])
            if article["id"] not in added_article_ids or article["id"] in used_article_ids
        ]
        if "books" in next_bank:
            used_volumes = {question["volume"] for question in remaining_questions}
            used_volumes.update(article["volume"] for article in next_bank.get("catalog", []))
            added_book_ids = set(target["addedBookIds"])
            next_bank["books"] = [
                book
                for book in next_bank.get("books", [])
                if book["id"] not in added_book_ids or book["label"] in used_volumes
            ]
        if "questionTypes" in next_bank:
            used_types = {question["type"] for question in remaining_questions}
            added_type_ids = set(target["addedTypeIds"])
            next_bank["questionTypes"] = [
                question_type
                for question_type in next_bank.get("questionTypes", [])
                if question_type["id"] not in added_type_ids or question_type["id"] in used_types
            ]
        next_bank = validate_questions(next_bank)

    if next_bank != current_bank:
        backup_and_write(QUESTIONS_PATH, next_bank)
        sync_question_reviews_after_bank_write(current_bank, next_bank)

    revoke_event = {
        "id": f"revoke-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
        "kind": "revoke",
        "targetEventId": event_id,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
    }
    next_history = append_question_bank_history_event(revoke_event)
    return next_bank, next_history

def ensure_question_bank() -> None:
    try:
        if not QUESTIONS_PATH.exists():
            initial_bank = empty_question_bank()
            if PUBLIC_QUESTION_BANK_PATH.exists():
                initial_bank = validate_questions(read_json(PUBLIC_QUESTION_BANK_PATH))
            backup_and_write(QUESTIONS_PATH, initial_bank)
            return
        raw = read_json(QUESTIONS_PATH)
        normalized = validate_questions(raw)
        if normalized != raw:
            backup_and_write(QUESTIONS_PATH, normalized)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"题库检查失败，请检查 {QUESTIONS_PATH}：{error}") from error
