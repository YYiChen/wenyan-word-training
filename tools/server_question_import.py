"""Authoritative Schema v4 question-bank import planning and merging.

The browser only submits the source package and a user-selected strategy.  All
identity, directory remapping, duplicate handling, review inheritance and
review reset decisions live here so the legacy request handler cannot create a
second merge implementation.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from server_validators import (
    _v4_review,
    make_json_etag,
    make_question_core_signature,
    make_question_semantic_fingerprint,
    normalize_identity_text,
    question_detail_signature,
    validate_question_bank_v4,
    validate_question_import,
)


EMPTY_REVIEW = {
    "status": "pending",
    "suggestedAnswer": None,
    "optionIssues": [],
    "note": "",
    "reviewedAt": "",
}


def _new_id(prefix: str, used: set[str]) -> str:
    while True:
        value = f"{prefix}_{uuid.uuid4()}"
        if value not in used:
            used.add(value)
            return value


def _book_key(book: dict[str, Any]) -> tuple[str]:
    return (normalize_identity_text(book.get("label")),)


def _catalog_key(article: dict[str, Any], book_id: str) -> tuple[str, ...]:
    return (
        book_id,
        normalize_identity_text(article.get("unit")),
        normalize_identity_text(article.get("title")),
        normalize_identity_text(article.get("author")),
    )


def _type_key(question_type: dict[str, Any]) -> tuple[str, str]:
    return (
        normalize_identity_text(question_type.get("label")),
        normalize_identity_text(question_type.get("description")),
    )


def _same_item(left: dict[str, Any], right: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return all(
        normalize_identity_text(left.get(key)) == normalize_identity_text(right.get(key))
        for key in keys
    )


def _merge_books(
    current: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    same_bank: bool,
    strategy: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    result = copy.deepcopy(current)
    by_id = {item["id"]: item for item in result}
    by_key = {_book_key(item): item["id"] for item in result}
    used = set(by_id)
    mapping: dict[str, str] = {}
    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for raw in incoming:
        item = copy.deepcopy(raw)
        incoming_id = item["id"]
        same_id = by_id.get(incoming_id)
        same_key_id = by_key.get(_book_key(item))
        if same_id is not None:
            if _same_item(same_id, item, ("label", "order")):
                mapping[incoming_id] = incoming_id
                continue
            conflicts.append({
                "kind": "book",
                "id": incoming_id,
                "message": f"教材册 ID“{incoming_id}”的名称或排序发生变化。",
            })
            if same_bank and strategy == "use_imported":
                mapping[incoming_id] = incoming_id
                before = copy.deepcopy(same_id)
                same_id.update(item)
                updates.append({"id": incoming_id, "before": before, "after": copy.deepcopy(same_id)})
                continue
            if same_bank:
                mapping[incoming_id] = incoming_id
                continue
            # A foreign directory ID collision must not silently attach the
            # imported questions to the local directory with another meaning.
            local_id = _new_id("book", used)
            item["id"] = local_id
            result.append(item)
            by_id[local_id] = item
            by_key[_book_key(item)] = local_id
            mapping[incoming_id] = local_id
            continue
        if same_key_id:
            mapping[incoming_id] = same_key_id
            continue
        local_id = incoming_id
        if local_id in used:
            local_id = _new_id("book", used)
        else:
            used.add(local_id)
        item["id"] = local_id
        result.append(item)
        by_id[local_id] = item
        by_key[_book_key(item)] = local_id
        mapping[incoming_id] = local_id

    return result, mapping, updates, conflicts


def _merge_catalog(
    current: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    book_mapping: dict[str, str],
    *,
    same_bank: bool,
    strategy: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    result = copy.deepcopy(current)
    by_id = {item["id"]: item for item in result}
    by_key = {
        _catalog_key(item, str(item.get("bookId", ""))): item["id"]
        for item in result
    }
    used = set(by_id)
    mapping: dict[str, str] = {}
    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for raw in incoming:
        item = copy.deepcopy(raw)
        incoming_id = item["id"]
        item["bookId"] = book_mapping.get(item.get("bookId"), item.get("bookId"))
        same_id = by_id.get(incoming_id)
        same_key_id = by_key.get(_catalog_key(item, item["bookId"]))
        if same_id is not None:
            if _same_item(same_id, item, ("bookId", "unit", "title", "author")):
                mapping[incoming_id] = incoming_id
                continue
            conflicts.append({
                "kind": "catalog",
                "id": incoming_id,
                "message": f"篇目 ID“{incoming_id}”的教材册、名称或作者信息发生变化。",
            })
            if same_bank and strategy == "use_imported":
                mapping[incoming_id] = incoming_id
                before = copy.deepcopy(same_id)
                same_id.update(item)
                updates.append({"id": incoming_id, "before": before, "after": copy.deepcopy(same_id)})
                continue
            if same_bank:
                mapping[incoming_id] = incoming_id
                continue
            local_id = _new_id("article", used)
            item["id"] = local_id
            result.append(item)
            by_id[local_id] = item
            by_key[_catalog_key(item, item["bookId"])] = local_id
            mapping[incoming_id] = local_id
            continue
        if same_key_id:
            mapping[incoming_id] = same_key_id
            continue
        local_id = incoming_id
        if local_id in used:
            local_id = _new_id("article", used)
        else:
            used.add(local_id)
        item["id"] = local_id
        result.append(item)
        by_id[local_id] = item
        by_key[_catalog_key(item, item["bookId"])] = local_id
        mapping[incoming_id] = local_id

    return result, mapping, updates, conflicts


def _merge_types(
    current: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    same_bank: bool,
    strategy: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    result = copy.deepcopy(current)
    by_id = {item["id"]: item for item in result}
    by_key = {_type_key(item): item["id"] for item in result}
    used = set(by_id)
    mapping: dict[str, str] = {}
    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for raw in incoming:
        item = copy.deepcopy(raw)
        incoming_id = item["id"]
        same_id = by_id.get(incoming_id)
        same_key_id = by_key.get(_type_key(item))
        if same_id is not None:
            if _same_item(same_id, item, ("label", "description")):
                mapping[incoming_id] = incoming_id
                continue
            conflicts.append({
                "kind": "questionType",
                "id": incoming_id,
                "message": f"题型 ID“{incoming_id}”的名称或说明发生变化。",
            })
            if same_bank and strategy == "use_imported":
                mapping[incoming_id] = incoming_id
                before = copy.deepcopy(same_id)
                same_id.update(item)
                updates.append({"id": incoming_id, "before": before, "after": copy.deepcopy(same_id)})
                continue
            if same_bank:
                mapping[incoming_id] = incoming_id
                continue
            local_id = _new_id("type", used)
            item["id"] = local_id
            result.append(item)
            by_id[local_id] = item
            by_key[_type_key(item)] = local_id
            mapping[incoming_id] = local_id
            continue
        if same_key_id:
            mapping[incoming_id] = same_key_id
            continue
        local_id = incoming_id
        if local_id in used:
            local_id = _new_id("type", used)
        else:
            used.add(local_id)
        item["id"] = local_id
        result.append(item)
        by_id[local_id] = item
        by_key[_type_key(item)] = local_id
        mapping[incoming_id] = local_id

    return result, mapping, updates, conflicts


def _review_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("status") not in {"pending", None}
        and right.get("status") not in {"pending", None}
        and left != right
    )


def merge_question_review(
    local_review: Any,
    incoming_review: Any,
    *,
    trusted_same_bank: bool,
    content_state: str,
    strategy: str,
) -> tuple[dict[str, Any], bool]:
    """Merge one review decision and report a real non-pending conflict.

    ``content_state`` is one of ``untouched``, ``new``, ``unchanged`` or
    ``changed``.  Only an unchanged question from the same bank may merge the
    two review states.  External packages never import a teacher decision.
    """
    if content_state not in {"untouched", "new", "unchanged", "changed"}:
        raise ValueError("题目内容状态无效。")
    if strategy not in {"preserve_local", "use_imported"}:
        raise ValueError("题库导入策略无效。")

    local = _v4_review(local_review)
    incoming = _v4_review(incoming_review)
    if content_state == "untouched":
        return copy.deepcopy(local), False
    if not trusted_same_bank:
        return copy.deepcopy(EMPTY_REVIEW), False
    if content_state == "new":
        return copy.deepcopy(incoming), False
    if content_state == "changed":
        return copy.deepcopy(incoming if strategy == "use_imported" else local), False

    # Same-bank, unchanged content: a non-pending decision wins over pending;
    # if both are reviewed and disagree, the selected strategy decides.
    if local["status"] == "pending" and incoming["status"] != "pending":
        return copy.deepcopy(incoming), False
    if incoming["status"] == "pending" and local["status"] != "pending":
        return copy.deepcopy(local), False
    if _review_conflict(local, incoming):
        return copy.deepcopy(incoming if strategy == "use_imported" else local), True
    return copy.deepcopy(local), False


def _duplicate_candidate_groups(questions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for question in questions:
        groups.setdefault(make_question_core_signature(question), []).append(question)
    return [
        members
        for members in groups.values()
        if len(members) >= 2
        and len({tuple(question_detail_signature(item)) for item in members}) >= 2
    ]


def _review_summary(bank: dict[str, Any]) -> dict[str, int]:
    summary = {"pending": 0, "passed": 0, "needs_revision": 0, "skipped": 0}
    for review in bank.get("workflow", {}).get("reviews", {}).values():
        status = review.get("status") if isinstance(review, dict) else "pending"
        if status in summary:
            summary[status] += 1
    return summary


def _prepare_incoming_questions(
    current: dict[str, Any],
    incoming: dict[str, Any],
    article_mapping: dict[str, str],
    type_mapping: dict[str, str],
    *,
    same_bank: bool,
    strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    current_questions = {item["id"]: item for item in current["questions"]}
    current_fingerprints = {
        make_question_semantic_fingerprint(item): item["id"]
        for item in current["questions"]
    }
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    used_ids = set(current_questions)
    used_numbers = {item["number"] for item in current["questions"]}
    next_number = max(used_numbers or {0}) + 1
    accepted_fingerprints = set(current_fingerprints)
    imported_ids: set[str] = set()
    id_map: dict[str, str] = {}
    content_status: dict[str, str] = {}
    foreign = not same_bank

    for raw in incoming["questions"]:
        question = copy.deepcopy(raw)
        old_id = question["id"]
        question["articleId"] = article_mapping.get(question["articleId"], question["articleId"])
        question["type"] = type_mapping.get(question["type"], question["type"])
        local = current_questions.get(old_id) if same_bank else None

        # The fingerprint is calculated after directory IDs have been mapped;
        # a directory collision that resolves to the same local record is not
        # treated as a different question.
        fingerprint = make_question_semantic_fingerprint(question)
        if foreign:
            if fingerprint in accepted_fingerprints:
                skipped.append({"id": old_id, "reason": "exact_duplicate"})
                continue
            question["id"] = _new_id("q", used_ids)
            id_map[old_id] = question["id"]
            question["number"] = question["number"] if question["number"] not in used_numbers else next_number
            while question["number"] in used_numbers:
                next_number += 1
                question["number"] = next_number
            used_numbers.add(question["number"])
            next_number = max(next_number, question["number"] + 1)
            accepted.append(question)
            accepted_fingerprints.add(make_question_semantic_fingerprint(question))
            imported_ids.add(question["id"])
            content_status[question["id"]] = "new"
            continue

        if local is None:
            if fingerprint in accepted_fingerprints:
                skipped.append({"id": old_id, "reason": "exact_duplicate"})
                continue
            if question["number"] in used_numbers:
                question["number"] = next_number
                while question["number"] in used_numbers:
                    next_number += 1
                    question["number"] = next_number
            used_numbers.add(question["number"])
            next_number = max(next_number, question["number"] + 1)
            accepted.append(question)
            accepted_fingerprints.add(fingerprint)
            imported_ids.add(question["id"])
            content_status[question["id"]] = "new"
            continue

        local_fingerprint = make_question_semantic_fingerprint(local)
        if local_fingerprint == fingerprint:
            skipped.append({"id": old_id, "reason": "unchanged"})
            content_status[old_id] = "unchanged"
            continue
        if make_question_core_signature(local) == make_question_core_signature(question):
            conflicts.append({
                "kind": "question",
                "questionId": old_id,
                "classification": "modified",
                "message": f"题目“{old_id}”的选项、答案或解析发生修改。",
            })
        else:
            conflicts.append({
                "kind": "question",
                "questionId": old_id,
                "classification": "majorModified",
                "message": f"题目“{old_id}”的文章、考点、原句或考点位置发生重大修改。",
            })
        if strategy == "use_imported":
            accepted.append(question)
            imported_ids.add(old_id)
            accepted_fingerprints.add(fingerprint)
            content_status[old_id] = "changed"
        else:
            skipped.append({"id": old_id, "reason": "preserve_local"})

    return accepted, skipped, {
        "conflicts": conflicts,
        "importedIds": imported_ids,
        "idMap": id_map,
        "contentStatus": content_status,
    }


def merge_question_bank_v4(
    current: dict[str, Any],
    incoming: dict[str, Any],
    *,
    mode: str = "merge",
    strategy: str = "preserve_local",
) -> dict[str, Any]:
    """Return a validated bank and a server-authoritative merge report."""
    current = validate_question_bank_v4(current)
    incoming_is_external = incoming.get("importKind") == "external"
    incoming = validate_question_bank_v4(incoming)
    if mode not in {"merge", "replace"}:
        raise ValueError("题库导入模式只能是 merge 或 replace。")
    if strategy not in {"preserve_local", "use_imported"}:
        raise ValueError("题库导入策略无效。")

    same_bank = not incoming_is_external and incoming["bankId"] == current["bankId"]
    if mode == "replace":
        replacement = copy.deepcopy(incoming)
        replacement.pop("importKind", None)
        if not same_bank:
            replacement["workflow"] = {
                "reviews": {
                    item["id"]: copy.deepcopy(EMPTY_REVIEW)
                    for item in replacement["questions"]
                },
                "duplicateResolutions": {},
            }
        return {
            "bank": validate_question_bank_v4(replacement),
            "report": {
                "sameBank": same_bank,
                "acceptedIds": [item["id"] for item in replacement["questions"]],
                "skipped": [],
                "directoryConflicts": [],
                "questionConflicts": [],
                "reviewConflicts": [],
                "foreignIdCollisions": [],
                "candidateGroupCount": 0,
                "candidateQuestionCount": 0,
            },
        }

    books, book_mapping, book_updates, book_conflicts = _merge_books(
        current["books"], incoming["books"], same_bank=same_bank, strategy=strategy
    )
    catalog, article_mapping, catalog_updates, catalog_conflicts = _merge_catalog(
        current["catalog"], incoming["catalog"], book_mapping, same_bank=same_bank, strategy=strategy
    )
    types, type_mapping, type_updates, type_conflicts = _merge_types(
        current["questionTypes"], incoming["questionTypes"], same_bank=same_bank, strategy=strategy
    )
    accepted, skipped, question_report = _prepare_incoming_questions(
        current,
        incoming,
        article_mapping,
        type_mapping,
        same_bank=same_bank,
        strategy=strategy,
    )
    accepted_by_id = {item["id"]: item for item in accepted}
    result = copy.deepcopy(current)
    result["books"] = books
    result["catalog"] = catalog
    result["questionTypes"] = types
    result["questions"] = [
        accepted_by_id.pop(item["id"], item) if item["id"] in accepted_by_id else item
        for item in current["questions"]
    ]
    result["questions"].extend(accepted_by_id.values())
    if same_bank and strategy == "use_imported":
        result["title"] = incoming.get("title", result["title"])
        result["description"] = incoming.get("description", result["description"])
        result["quizDefaults"] = copy.deepcopy(incoming.get("quizDefaults", result["quizDefaults"]))

    current_reviews = current["workflow"]["reviews"]
    incoming_reviews = incoming["workflow"]["reviews"]
    merged_reviews: dict[str, Any] = {}
    content_status = question_report.get("contentStatus", {})
    review_conflicts: list[dict[str, Any]] = []
    for question in result["questions"]:
        qid = question["id"]
        state = content_status.get(qid, "untouched")
        merged_review, conflict = merge_question_review(
            current_reviews.get(qid, EMPTY_REVIEW),
            incoming_reviews.get(qid, EMPTY_REVIEW),
            trusted_same_bank=same_bank,
            content_state=state,
            strategy=strategy,
        )
        merged_reviews[qid] = merged_review
        if conflict:
            review_conflicts.append({
                "kind": "review",
                "questionId": qid,
                "message": "同一道题的审查结论不同，已按所选导入策略处理。",
            })

    result["workflow"] = {
        "reviews": merged_reviews,
        "duplicateResolutions": copy.deepcopy(current["workflow"].get("duplicateResolutions", {})),
    }
    if same_bank:
        for gid, group in incoming["workflow"].get("duplicateResolutions", {}).items():
            if strategy == "use_imported" or gid not in result["workflow"]["duplicateResolutions"]:
                result["workflow"]["duplicateResolutions"][gid] = copy.deepcopy(group)
    result = validate_question_bank_v4(result)

    candidate_groups = _duplicate_candidate_groups(result["questions"])
    imported_result_ids = set(question_report["importedIds"])
    candidate_groups_in_import = [
        group for group in candidate_groups if any(item["id"] in imported_result_ids for item in group)
    ]
    report = {
        "sameBank": same_bank,
        "acceptedIds": [item["id"] for item in accepted],
        "skipped": skipped,
        "questionConflicts": question_report["conflicts"],
        "reviewConflicts": review_conflicts,
        "directoryConflicts": book_conflicts + catalog_conflicts + type_conflicts,
        "directoryUpdates": {
            "books": book_updates,
            "catalog": catalog_updates,
            "questionTypes": type_updates,
        },
        "candidateGroupCount": len(candidate_groups_in_import),
        "candidateQuestionCount": sum(
            1 for group in candidate_groups_in_import for item in group if item["id"] in imported_result_ids
        ),
        "foreignIdCollisions": [
            item["id"] for item in incoming["questions"]
            if not same_bank and item["id"] in {q["id"] for q in current["questions"]}
        ],
    }
    return {"bank": result, "report": report}


def materialize_question_import(
    payload: dict[str, Any],
    current: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    """Validate an external package; replace receives a fresh bank lineage."""
    bank = validate_question_import(payload, current)
    if mode == "replace":
        bank["bankId"] = f"bank_{uuid.uuid4()}"
        bank["questions"] = [
            {**question, "number": index}
            for index, question in enumerate(bank["questions"], start=1)
        ]
        bank.pop("importKind", None)
        bank = validate_question_bank_v4(bank)
        bank["workflow"] = {
            "reviews": {question["id"]: copy.deepcopy(EMPTY_REVIEW) for question in bank["questions"]},
            "duplicateResolutions": {},
        }
        bank = validate_question_bank_v4(bank)
    return bank


def build_import_preview(
    current: dict[str, Any],
    incoming: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    current = validate_question_bank_v4(current)
    incoming_is_external = incoming.get("importKind") == "external"
    incoming = validate_question_bank_v4(incoming)
    if incoming_is_external:
        incoming["importKind"] = "external"
    same_bank = not incoming_is_external and incoming["bankId"] == current["bankId"]
    # Preview and apply deliberately call the same merger.  The browser must
    # not maintain a second approximation of directory remapping, duplicate
    # detection or review inheritance.
    dry_run = merge_question_bank_v4(current, incoming, mode=mode, strategy="preserve_local")
    report = dry_run["report"]
    current_ids = {item["id"] for item in current["questions"]}
    if mode == "replace":
        unchanged = 0
        exact_duplicates = 0
        new_questions = len(incoming["questions"])
    else:
        unchanged = sum(item.get("reason") == "unchanged" for item in report["skipped"])
        exact_duplicates = sum(item.get("reason") == "exact_duplicate" for item in report["skipped"])
        new_questions = sum(item_id not in current_ids for item_id in report["acceptedIds"])
    modified = sum(
        item.get("classification") == "modified"
        for item in report["questionConflicts"]
    )
    major_modified = sum(
        item.get("classification") == "majorModified"
        for item in report["questionConflicts"]
    )
    if mode == "replace":
        replacement_groups = _duplicate_candidate_groups(dry_run["bank"]["questions"])
        duplicate_group_count = len(replacement_groups)
        duplicate_question_count = sum(len(group) for group in replacement_groups)
    else:
        duplicate_group_count = report["candidateGroupCount"]
        duplicate_question_count = report["candidateQuestionCount"]
    conflicts = [
        *report["questionConflicts"],
        *report["reviewConflicts"],
        *report["directoryConflicts"],
    ]
    summary = {
        "importedTotal": len(incoming["questions"]),
        "unchanged": unchanged,
        "newQuestions": new_questions,
        "modified": modified,
        "majorModified": major_modified,
        "exactDuplicates": exact_duplicates,
        "duplicateCandidates": duplicate_group_count,
        "duplicateCandidateQuestions": duplicate_question_count,
        "foreignIdCollisions": len(report["foreignIdCollisions"]),
        "reviewConflicts": len(report["reviewConflicts"]),
        "directoryConflicts": len(report["directoryConflicts"]),
    }
    return {
        "mode": mode,
        "format": incoming.get("format"),
        "sameBank": same_bank,
        "baseEtag": make_json_etag(current),
        "summary": summary,
        "reviewSummary": {
            "current": _review_summary(current),
            "imported": _review_summary(incoming),
            "afterPreserveLocal": _review_summary(dry_run["bank"]),
        },
        "conflicts": conflicts,
    }
