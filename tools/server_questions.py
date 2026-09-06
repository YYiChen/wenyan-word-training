"""Question-bank history, review, and initialization services."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import shutil
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
    make_duplicate_group_id,
    question_target_occurrence,
    strip_system_review_notes,
)


_BACKUP_WRITER: Callable[..., None] | None = None


def build_import_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old_q = {q["id"]: q for q in previous["questions"]}
    new_q = {q["id"]: q for q in current["questions"]}
    added = [qid for qid in new_q if qid not in old_q]
    updated = {}
    for qid in old_q.keys() & new_q.keys():
        if old_q[qid] != new_q[qid]:
            updated[qid] = {
                "before": copy.deepcopy(old_q[qid]),
                "after": copy.deepcopy(new_q[qid]),
                "afterFingerprint": make_question_semantic_fingerprint(new_q[qid]),
            }
    old_reviews = previous.get("workflow", {}).get("reviews", {})
    new_reviews = current.get("workflow", {}).get("reviews", {})
    updated_reviews = {
        qid: {
            "before": copy.deepcopy(old_reviews.get(qid)),
            "after": copy.deepcopy(new_reviews.get(qid)),
        }
        for qid in old_reviews.keys() & new_reviews.keys()
        if old_reviews.get(qid) != new_reviews.get(qid)
    }

    def directory_delta(
        key: str,
        identity: str,
    ) -> dict[str, dict[str, Any]]:
        old_items = {item[identity]: item for item in previous.get(key, [])}
        new_items = {item[identity]: item for item in current.get(key, [])}
        return {
            item_id: {
                "before": copy.deepcopy(old_items[item_id]),
                "after": copy.deepcopy(new_items[item_id]),
            }
            for item_id in old_items.keys() & new_items.keys()
            if old_items[item_id] != new_items[item_id]
        }

    return {
        "addedQuestionFingerprints": {
            qid: make_question_semantic_fingerprint(new_q[qid]) for qid in added
        },
        "updatedQuestions": updated,
        "updatedReviews": updated_reviews,
        "updatedBooks": directory_delta("books", "id"),
        "updatedCatalog": directory_delta("catalog", "id"),
        "updatedTypes": directory_delta("questionTypes", "id"),
    }


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
    is_v4 = current_bank.get("schemaVersion") == "4.0"
    if target["mode"] == "replace":
        if current_hash != target["afterHash"]:
            raise ValueError("本次替换导入后题库已有变化，不能安全撤销；请先处理最近一次题库变更。")
        next_bank = validate_questions(copy.deepcopy(target["beforeBank"]))
    else:
        if is_v4:
            next_bank = copy.deepcopy(current_bank)
            by_id = {q["id"]: q for q in next_bank["questions"]}
            added_question_ids = set(target.get("addedQuestionIds", []))
            for qid, fingerprint in target.get("addedQuestionFingerprints", {}).items():
                if qid in by_id and make_question_semantic_fingerprint(by_id[qid]) != fingerprint:
                    raise ValueError("本次导入新增的题目后来又被修改，无法安全撤销。")
            for qid, delta in target.get("updatedQuestions", {}).items():
                current_item = by_id.get(qid)
                if current_item is None or make_question_semantic_fingerprint(current_item) != delta.get("afterFingerprint"):
                    raise ValueError("本次导入影响的题目后来又被修改，无法安全撤销。")
                before = delta.get("before")
                if not isinstance(before, dict) or before.get("id") != qid:
                    raise ValueError("本次导入历史缺少可恢复的题目快照，无法安全撤销。")
                by_id[qid] = copy.deepcopy(before)
            next_bank["questions"] = [q for q in by_id.values() if q["id"] not in added_question_ids]
            reviews = next_bank["workflow"]["reviews"]
            for qid, delta in target.get("updatedReviews", {}).items():
                if reviews.get(qid) != delta.get("after"):
                    raise ValueError("本次导入影响的审查结果后来又被修改，无法安全撤销。")
                if delta.get("before") is None: reviews.pop(qid, None)
                else: reviews[qid] = copy.deepcopy(delta["before"])

            directory_specs = (
                ("books", "updatedBooks", "addedBookIds"),
                ("catalog", "updatedCatalog", "addedArticleIds"),
                ("questionTypes", "updatedTypes", "addedTypeIds"),
            )
            for collection, delta_key, added_key in directory_specs:
                current_items = {item["id"]: item for item in next_bank.get(collection, [])}
                for item_id, delta in target.get(delta_key, {}).items():
                    if current_items.get(item_id) != delta.get("after"):
                        raise ValueError("本次导入影响的目录信息后来又被修改，无法安全撤销。")
                    before = delta.get("before")
                    if not isinstance(before, dict) or before.get("id") != item_id:
                        raise ValueError("本次导入历史缺少可恢复的目录快照，无法安全撤销。")
                    current_items[item_id] = copy.deepcopy(before)
                added_ids = set(target.get(added_key, []))
                snapshots = target.get("addedDirectorySnapshots", {}).get(collection, {})
                for item_id, snapshot in snapshots.items():
                    if item_id in current_items and current_items[item_id] != snapshot:
                        raise ValueError("本次导入新增的目录信息后来又被修改，无法安全撤销。")
                if collection == "catalog":
                    used = {q["articleId"] for q in next_bank["questions"]}
                elif collection == "books":
                    used_article_ids = {q["articleId"] for q in next_bank["questions"]}
                    used = {
                        article.get("bookId")
                        for article in next_bank.get("catalog", [])
                        if article.get("id") in used_article_ids
                    }
                else:
                    used = {q["type"] for q in next_bank["questions"]}
                if added_ids & used:
                    raise ValueError("本次导入新增的目录仍被其他题目使用，无法安全撤销。")
                next_bank[collection] = [
                    item for item in current_items.values() if item["id"] not in added_ids
                ]
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
        if not is_v4:
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
                if str(public_raw.get("schemaVersion")) == "4.0":
                    initial_bank = validate_question_bank_v4(public_raw)
                else:
                    initial_bank = _migrate_v3_to_v4(public_raw)
                    backup_legacy_question_reviews()
            backup_and_write(QUESTIONS_PATH, initial_bank)
            return
        raw = read_json(QUESTIONS_PATH)
        if isinstance(raw, dict) and str(raw.get("schemaVersion")) == "4.0":
            normalized = validate_question_bank_v4(raw)
        else:
            normalized = _migrate_v3_to_v4(raw)
            backup_legacy_question_reviews()
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
    legacy_reviews = validate_question_reviews(
        read_json(QUESTION_REVIEWS_PATH, empty_question_reviews())
    )
    review_map = legacy_reviews["reviews"]
    reviews = {}
    questions = []
    legacy_duplicate_groups: dict[str, list[dict[str, Any]]] = {}
    for question in legacy["questions"]:
        item = copy.deepcopy(question)
        old_duplicate_review = item.get("duplicateReview")
        item.pop("article", None); item.pop("volume", None); item.pop("unit", None)
        item.pop("targetStart", None); item.pop("reviewStatus", None); item.pop("reviewStatusBeforeAbnormal", None); item.pop("reviewNote", None); item.pop("duplicateReview", None)
        item["targetOccurrence"] = question_target_occurrence(question)
        old_status = question.get("reviewStatus")
        old_review = review_map.get(question["id"], {})
        status = old_review.get("status") if isinstance(old_review, dict) else None
        if status not in {"pending", "passed", "needs_revision", "skipped"}:
            status = {
                "verified": "passed",
                "candidate": "pending",
                "admin_created": "pending",
                "admin_edited": "pending",
                "abnormal": "pending",
            }.get(question.get("reviewStatusBeforeAbnormal") or old_status, "pending")
        review = {
            "status": status,
            "suggestedAnswer": old_review.get("suggestedAnswer"),
            "optionIssues": old_review.get("optionIssues", []),
            "note": old_review.get("note") or strip_system_review_notes(question.get("reviewNote")),
            "reviewedAt": old_review.get("reviewedAt", ""),
        }
        reviews[question["id"]] = review
        questions.append(item)
        if isinstance(old_duplicate_review, dict) and old_duplicate_review.get("groupId"):
            legacy_duplicate_groups.setdefault(old_duplicate_review["groupId"], []).append({
                "question": item,
                "review": old_duplicate_review,
            })
    duplicate_resolutions: dict[str, Any] = {}
    questions_by_id = {question["id"]: question for question in questions}
    for members in legacy_duplicate_groups.values():
        member_questions = [entry["question"] for entry in members if entry["question"]["id"] in questions_by_id]
        if len(member_questions) < 2:
            continue
        cores = {question_core_signature(question) for question in member_questions}
        details = {question_detail_signature(question) for question in member_questions}
        if len(cores) != 1 or len(details) < 2:
            continue
        core = next(iter(cores))
        group_id = make_duplicate_group_id(core)
        ordered = sorted(member_questions, key=lambda question: question["id"])
        fingerprint = hashlib.sha256(
            "|".join(
                f"{question['id']}:{make_question_semantic_fingerprint(question)}"
                for question in ordered
            ).encode()
        ).hexdigest()
        decisions = {
            entry["question"]["id"]: entry["review"].get("status")
            for entry in members
            if entry["question"]["id"] in questions_by_id
            and entry["review"].get("status") in {"kept", "skipped"}
        }
        duplicate_resolutions[group_id] = {
            "fingerprint": fingerprint,
            "questionIds": [question["id"] for question in member_questions],
            "decisions": decisions,
            "updatedAt": "",
        }
    result = {"format": "wenyan-question-bank", "schemaVersion": "4.0", "bankId": f"bank_{uuid.uuid4()}", "title": legacy.get("title", ""), "description": legacy.get("description", ""), "questionTypes": legacy.get("questionTypes", []), "books": books, "catalog": catalog, "quizDefaults": legacy.get("quizDefaults", {}), "questions": questions, "workflow": {"reviews": reviews, "duplicateResolutions": duplicate_resolutions}}
    return validate_question_bank_v4(result)


def backup_legacy_question_reviews() -> None:
    """Keep a recoverable copy before the one-way v3 review migration."""
    if not QUESTION_REVIEWS_PATH.exists():
        return
    backup_dir = QUESTION_REVIEWS_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"question-reviews-v3-migration-{timestamp}.json"
    shutil.copy2(QUESTION_REVIEWS_PATH, destination)
