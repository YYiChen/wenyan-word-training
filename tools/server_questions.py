"""Question-bank history, review, and initialization services."""

from __future__ import annotations

import copy
import json
import secrets
import time
import uuid
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
    validate_question_bank_v4,
    make_question_semantic_fingerprint,
)


_BACKUP_WRITER: Callable[..., None] | None = None


def build_import_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old_q = {q["id"]: q for q in previous["questions"]}; new_q = {q["id"]: q for q in current["questions"]}
    added = [qid for qid in new_q if qid not in old_q]
    updated = {}
    for qid in old_q.keys() & new_q.keys():
        if old_q[qid] != new_q[qid]:
            updated[qid] = {"before": copy.deepcopy(old_q[qid]), "afterFingerprint": make_question_semantic_fingerprint(new_q[qid])}
    old_reviews = previous.get("workflow", {}).get("reviews", {}); new_reviews = current.get("workflow", {}).get("reviews", {})
    updated_reviews = {qid: {"before": copy.deepcopy(old_reviews.get(qid)), "after": copy.deepcopy(new_reviews.get(qid))} for qid in old_reviews.keys() & new_reviews.keys() if old_reviews.get(qid) != new_reviews.get(qid)}
    return {"addedQuestionFingerprints": {qid: make_question_semantic_fingerprint(new_q[qid]) for qid in added}, "updatedQuestions": updated, "updatedReviews": updated_reviews}


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
        validate_question_bank_v4(read_json(QUESTIONS_PATH))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"v4 题库审查状态检查失败，请检查 {QUESTIONS_PATH}：{error}") from error

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
        if current_bank.get("schemaVersion") == "4.0":
            next_bank = copy.deepcopy(current_bank)
            by_id = {q["id"]: q for q in next_bank["questions"]}
            for qid, fingerprint in target.get("addedQuestionFingerprints", {}).items():
                if qid in by_id and make_question_semantic_fingerprint(by_id[qid]) != fingerprint:
                    raise ValueError("本次导入新增的题目后来又被修改，无法安全撤销。")
            for qid, delta in target.get("updatedQuestions", {}).items():
                current_item = by_id.get(qid)
                if current_item is None or make_question_semantic_fingerprint(current_item) != delta.get("afterFingerprint"):
                    raise ValueError("本次导入影响的题目后来又被修改，无法安全撤销。")
                by_id[qid] = copy.deepcopy(delta.get("before"))
            next_bank["questions"] = [q for q in by_id.values() if q["id"] not in set(target.get("addedQuestionIds", []))]
            reviews = next_bank["workflow"]["reviews"]
            for qid, delta in target.get("updatedReviews", {}).items():
                if reviews.get(qid) != delta.get("after"):
                    raise ValueError("本次导入影响的审查结果后来又被修改，无法安全撤销。")
                if delta.get("before") is None: reviews.pop(qid, None)
                else: reviews[qid] = copy.deepcopy(delta["before"])
            next_bank = validate_question_bank_v4(next_bank)
        else:
            removed_question_ids = set(target["addedQuestionIds"])
            next_bank = copy.deepcopy(current_bank)
            next_bank["questions"] = [question for question in next_bank["questions"] if question["id"] not in removed_question_ids]
            remaining_questions = next_bank["questions"]
            used_article_ids = {question["articleId"] for question in remaining_questions}
            added_article_ids = set(target["addedArticleIds"])
            next_bank["catalog"] = [article for article in next_bank.get("catalog", []) if article["id"] not in added_article_ids or article["id"] in used_article_ids]
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
                public_raw = read_json(PUBLIC_QUESTION_BANK_PATH)
                initial_bank = validate_question_bank_v4(public_raw) if str(public_raw.get("schemaVersion")) == "4.0" else _migrate_v3_to_v4(public_raw)
            backup_and_write(QUESTIONS_PATH, initial_bank)
            return
        raw = read_json(QUESTIONS_PATH)
        if isinstance(raw, dict) and str(raw.get("schemaVersion")) == "4.0":
            normalized = validate_question_bank_v4(raw)
        else:
            normalized = _migrate_v3_to_v4(raw)
        if normalized != raw:
            backup_and_write(QUESTIONS_PATH, normalized)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"题库检查失败，请检查 {QUESTIONS_PATH}：{error}") from error


def _migrate_v3_to_v4(raw: Any) -> dict[str, Any]:
    """One-way local migration; existing question IDs are retained."""
    legacy = validate_questions(raw)
    books = []
    by_label = {}
    for index, book in enumerate(legacy.get("books", []), 1):
        label = str(book.get("label", "")).strip()
        bid = str(book.get("id", "")).strip() or f"book_{index}"
        by_label[label] = bid
        books.append({"id": bid, "label": label, "order": book.get("order", index)})
    for article in legacy.get("catalog", []):
        label = str(article.get("volume", "")).strip()
        if label and label not in by_label:
            by_label[label] = f"book_{len(books) + 1}"
            books.append({"id": by_label[label], "label": label, "order": len(books) + 1})
    catalog = []
    for article in legacy.get("catalog", []):
        item = {key: value for key, value in article.items() if key not in {"volume"}}
        item["bookId"] = by_label.get(str(article.get("volume", "")).strip(), "")
        catalog.append(item)
    legacy_reviews = read_json(QUESTION_REVIEWS_PATH, empty_question_reviews())
    review_map = legacy_reviews.get("reviews", {}) if isinstance(legacy_reviews, dict) else {}
    reviews = {}
    questions = []
    for question in legacy["questions"]:
        item = copy.deepcopy(question)
        item.pop("article", None); item.pop("volume", None); item.pop("unit", None)
        item.pop("targetStart", None); item.pop("reviewStatus", None); item.pop("reviewStatusBeforeAbnormal", None); item.pop("reviewNote", None); item.pop("duplicateReview", None)
        starts = question_core_signature(question)[3]
        if not isinstance(item.get("targetOccurrence"), int): item["targetOccurrence"] = 1
        old_status = question.get("reviewStatus")
        old_review = review_map.get(question["id"], {})
        status = old_review.get("status") if isinstance(old_review, dict) else None
        if status not in {"pending", "passed", "needs_revision", "skipped"}:
            status = {"verified": "passed", "candidate": "pending", "admin_created": "pending", "admin_edited": "pending"}.get(old_status, "pending")
        reviews[question["id"]] = {
            "status": status, "suggestedAnswer": old_review.get("suggestedAnswer") if isinstance(old_review, dict) else None,
            "optionIssues": old_review.get("optionIssues", []) if isinstance(old_review, dict) else [],
            "note": old_review.get("note", "") if isinstance(old_review, dict) else str(question.get("reviewNote", "")),
            "reviewedAt": old_review.get("reviewedAt", "") if isinstance(old_review, dict) else "",
        }
        questions.append(item)
    result = {"format": "wenyan-question-bank", "schemaVersion": "4.0", "bankId": f"bank_{uuid.uuid4()}", "title": legacy.get("title", ""), "description": legacy.get("description", ""), "questionTypes": legacy.get("questionTypes", []), "books": books, "catalog": catalog, "quizDefaults": legacy.get("quizDefaults", {}), "questions": questions, "workflow": {"reviews": reviews, "duplicateResolutions": {}}}
    return validate_question_bank_v4(result)
