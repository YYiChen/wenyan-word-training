"""Shared sync protocol helpers for the classroom bank synchronizer.

This module is pure logic with no I/O: it is imported by both the local
client (``tools/server_sync.py``) and the remote sync server
(``sync_server/``), so there is exactly one definition of operation shape,
deterministic operation ids, entity diffing and application.  Anything that
touches the disk or the network lives in the caller.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
from typing import Any


SYNC_PROTOCOL_VERSION = 1

#: Entities covered by realtime sync (§43).  Leaderboard, answer records, PK
#: records, passwords, sessions and logs are never synchronized.
ENTITY_KINDS = frozenset({
    "bank_meta",
    "question_type",
    "book",
    "catalog",
    "question",
    "review",
    "duplicate_resolution",
    "quiz_defaults",
})

OPERATION_TYPES = frozenset({
    "bank_meta_set",
    "question_type_put",
    "question_type_delete",
    "book_put",
    "book_delete",
    "catalog_put",
    "catalog_delete",
    "question_put",
    "question_delete",
    "review_set",
    "duplicate_resolution_set",
    "duplicate_resolution_delete",
    "quiz_defaults_set",
})

RESOLUTION_CHOICES = ("server", "incoming")

MAX_OPERATION_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024

KDF_ITERATIONS = 200_000
SESSION_TTL_SECONDS = 24 * 60 * 60
CHALLENGE_TTL_SECONDS = 60


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def deterministic_operation_id(
    client_id: str, entity_kind: str, entity_id: str, base_hash: str, new_hash: str
) -> str:
    """Stable id so a crashed-then-retried push is idempotent (§24, §63)."""
    raw = "|".join([client_id, entity_kind, entity_id, base_hash, new_hash])
    return "op_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def derive_auth_key(password: str, salt_hex: str, iterations: int = KDF_ITERATIONS) -> bytes:
    salt = bytes.fromhex(salt_hex)
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def new_salt_hex() -> str:
    return secrets.token_hex(16)


def hmac_hex(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def new_nonce_hex(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def _reviews_equal(left: Any, right: Any) -> bool:
    from server_validators import _v4_review

    try:
        return _v4_review(left) == _v4_review(right)
    except ValueError:
        return False


def _question_body(question: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in question.items()}


def build_sync_operations(
    base_bank: dict[str, Any], local_bank: dict[str, Any], client_id: str
) -> list[dict[str, Any]]:
    """Diff shadow (base) vs local bank into entity operations (§44).

    Question edits travel as one atomic bundle: ``question_put`` carries
    ``baseQuestion``/``newQuestion`` together with ``baseReview``/``newReview``
    so a changed question never keeps a stale ``passed`` review (§45).
    Pure review edits become ``review_set`` (§46).
    """
    operations: list[dict[str, Any]] = []

    def emit(operation_type: str, entity_kind: str, entity_id: str, base: Any, new: Any) -> None:
        base_hash = canonical_hash(base)
        new_hash = canonical_hash(new)
        if base_hash == new_hash:
            return
        operations.append({
            "operation_id": deterministic_operation_id(
                client_id, entity_kind, entity_id, base_hash, new_hash
            ),
            "client_id": client_id,
            "operation_type": operation_type,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
            "base": copy.deepcopy(base),
            "new": copy.deepcopy(new),
        })

    base_meta = {"title": base_bank.get("title", ""), "description": base_bank.get("description", "")}
    local_meta = {"title": local_bank.get("title", ""), "description": local_bank.get("description", "")}
    emit("bank_meta_set", "bank_meta", "bank_meta", base_meta, local_meta)

    emit("quiz_defaults_set", "quiz_defaults", "quiz_defaults",
         base_bank.get("quizDefaults", {}), local_bank.get("quizDefaults", {}))

    for key, put_type, delete_type, entity_kind in (
        ("questionTypes", "question_type_put", "question_type_delete", "question_type"),
        ("books", "book_put", "book_delete", "book"),
        ("catalog", "catalog_put", "catalog_delete", "catalog"),
    ):
        base_items = {item["id"]: item for item in base_bank.get(key, [])}
        local_items = {item["id"]: item for item in local_bank.get(key, [])}
        for item_id in sorted(set(base_items) | set(local_items)):
            if item_id in local_items and item_id not in base_items:
                emit(put_type, entity_kind, item_id, None, local_items[item_id])
            elif item_id in base_items and item_id not in local_items:
                emit(delete_type, entity_kind, item_id, base_items[item_id], None)
            else:
                emit(put_type, entity_kind, item_id, base_items[item_id], local_items[item_id])

    base_questions = {item["id"]: item for item in base_bank.get("questions", [])}
    local_questions = {item["id"]: item for item in local_bank.get("questions", [])}
    base_reviews = base_bank.get("workflow", {}).get("reviews", {})
    local_reviews = local_bank.get("workflow", {}).get("reviews", {})
    for question_id in sorted(set(base_questions) | set(local_questions)):
        if question_id in local_questions and question_id not in base_questions:
            emit("question_put", "question", question_id, None, {
                "question": _question_body(local_questions[question_id]),
                "review": copy.deepcopy(local_reviews.get(question_id)),
            })
        elif question_id in base_questions and question_id not in local_questions:
            emit("question_delete", "question", question_id, {
                "question": _question_body(base_questions[question_id]),
            }, None)
        else:
            if _question_body(base_questions[question_id]) != _question_body(local_questions[question_id]):
                emit("question_put", "question", question_id, {
                    "question": _question_body(base_questions[question_id]),
                    "review": copy.deepcopy(base_reviews.get(question_id)),
                }, {
                    "question": _question_body(local_questions[question_id]),
                    "review": copy.deepcopy(local_reviews.get(question_id)),
                })
            elif not _reviews_equal(base_reviews.get(question_id), local_reviews.get(question_id)):
                emit("review_set", "review", question_id,
                     copy.deepcopy(base_reviews.get(question_id)),
                     copy.deepcopy(local_reviews.get(question_id)))

    base_dups = base_bank.get("workflow", {}).get("duplicateResolutions", {})
    local_dups = local_bank.get("workflow", {}).get("duplicateResolutions", {})
    for group_id in sorted(set(base_dups) | set(local_dups)):
        if group_id in local_dups and group_id not in base_dups:
            emit("duplicate_resolution_set", "duplicate_resolution", group_id, None, local_dups[group_id])
        elif group_id in base_dups and group_id not in local_dups:
            emit("duplicate_resolution_delete", "duplicate_resolution", group_id, base_dups[group_id], None)
        else:
            emit("duplicate_resolution_set", "duplicate_resolution", group_id,
                 base_dups[group_id], local_dups[group_id])
    return operations


def validate_operation_shape(operation: Any) -> dict[str, Any]:
    """Reject malformed operations before any state is touched."""
    if not isinstance(operation, dict):
        raise ValueError("operation 必须是对象。")
    operation_type = operation.get("operation_type")
    entity_kind = operation.get("entity_kind")
    entity_id = operation.get("entity_id")
    if operation_type not in OPERATION_TYPES:
        raise ValueError(f"不支持的 operation 类型：{operation_type}")
    if entity_kind not in ENTITY_KINDS:
        raise ValueError(f"不支持的同步实体：{entity_kind}")
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("operation 缺少 entity_id。")
    for key in ("base", "new"):
        if key not in operation:
            raise ValueError(f"operation 缺少 {key}。")
    return operation


def get_entity_value(bank: dict[str, Any], entity_kind: str, entity_id: str) -> Any:
    """Read one sync entity from a v4 bank (shared client/server logic)."""
    if entity_kind == "bank_meta":
        return {"title": bank.get("title", ""), "description": bank.get("description", "")}
    if entity_kind == "quiz_defaults":
        return copy.deepcopy(bank.get("quizDefaults", {}))
    if entity_kind == "question_type":
        items = {item["id"]: item for item in bank.get("questionTypes", [])}
    elif entity_kind == "book":
        items = {item["id"]: item for item in bank.get("books", [])}
    elif entity_kind == "catalog":
        items = {item["id"]: item for item in bank.get("catalog", [])}
    elif entity_kind == "question":
        questions = {item["id"]: item for item in bank.get("questions", [])}
        reviews = bank.get("workflow", {}).get("reviews", {})
        if entity_id not in questions:
            return None
        return {
            "question": copy.deepcopy(questions[entity_id]),
            "review": copy.deepcopy(reviews.get(entity_id)),
        }
    elif entity_kind == "review":
        return copy.deepcopy(bank.get("workflow", {}).get("reviews", {}).get(entity_id))
    elif entity_kind == "duplicate_resolution":
        return copy.deepcopy(
            bank.get("workflow", {}).get("duplicateResolutions", {}).get(entity_id)
        )
    else:
        raise ValueError(f"不支持的同步实体：{entity_kind}")
    return copy.deepcopy(items.get(entity_id))


def set_entity_value(bank: dict[str, Any], entity_kind: str, entity_id: str, value: Any) -> None:
    """Write one sync entity into a v4 bank (shared client/server logic)."""
    if entity_kind == "bank_meta":
        bank["title"] = (value or {}).get("title", "")
        bank["description"] = (value or {}).get("description", "")
    elif entity_kind == "quiz_defaults":
        bank["quizDefaults"] = copy.deepcopy(value or {})
    elif entity_kind in ("question_type", "book", "catalog"):
        key = {"question_type": "questionTypes", "book": "books", "catalog": "catalog"}[entity_kind]
        items = [item for item in bank.get(key, []) if item.get("id") != entity_id]
        if value is not None:
            items.append(copy.deepcopy(value))
        bank[key] = items
    elif entity_kind == "question":
        questions = [item for item in bank.get("questions", []) if item.get("id") != entity_id]
        reviews = bank.get("workflow", {}).get("reviews", {})
        if value is not None:
            questions.append(copy.deepcopy(value["question"]))
            reviews[entity_id] = copy.deepcopy(value.get("review"))
        else:
            reviews.pop(entity_id, None)
        bank["questions"] = questions
    elif entity_kind == "review":
        reviews = bank.setdefault("workflow", {}).setdefault("reviews", {})
        if value is not None:
            reviews[entity_id] = copy.deepcopy(value)
        # Reviews are never deleted by sync; a missing value is a no-op.
    elif entity_kind == "duplicate_resolution":
        resolutions = bank.setdefault("workflow", {}).setdefault("duplicateResolutions", {})
        if value is not None:
            resolutions[entity_id] = copy.deepcopy(value)
        else:
            resolutions.pop(entity_id, None)
    else:
        raise ValueError(f"不支持的同步实体：{entity_kind}")


def summarize_bank(bank: dict[str, Any]) -> dict[str, Any]:
    """Review/size summary for backup metadata (§82)."""
    reviews = bank.get("workflow", {}).get("reviews", {})
    counts = {"passed": 0, "pending": 0, "needs_revision": 0, "skipped": 0}
    for review in reviews.values():
        status = review.get("status") if isinstance(review, dict) else "pending"
        if status in counts:
            counts[status] += 1
    return {
        "bank_id": bank.get("bankId", ""),
        "question_count": len(bank.get("questions", [])),
        "review_summary": counts,
    }
