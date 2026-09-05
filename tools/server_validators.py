"""Pure validation and question identity rules for the local server."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any

from server_config import (
    ANSWER_RECORD_MAX_COUNT,
    ANSWER_RECORD_RETENTION_DAYS,
    DEFAULT_SCORING_CONFIG,
    MAX_DURATION_SECONDS,
    MAX_STREAK_THRESHOLD,
    MIN_DURATION_SECONDS,
    VALID_DUPLICATE_REVIEW_STATUSES,
    VALID_OPTION_KEYS,
    VALID_QUESTION_BANK_HISTORY_KINDS,
    VALID_REVIEW_STATUSES,
    VALID_TYPES,
)

def make_json_etag(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f'"{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}"'

def find_word_occurrences(sentence: str, word: str) -> list[int]:
    starts: list[int] = []
    start = 0
    while start < len(sentence):
        index = sentence.find(word, start)
        if index < 0:
            break
        starts.append(index)
        start = index + len(word)
    return starts

def normalize_identity_text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").strip().split())

def question_target_occurrence(question: dict[str, Any]) -> int:
    raw_occurrence = question.get("targetOccurrence")
    if isinstance(raw_occurrence, int) and not isinstance(raw_occurrence, bool) and raw_occurrence >= 1:
        return raw_occurrence
    target_start = question.get("targetStart")
    if isinstance(target_start, int) and not isinstance(target_start, bool) and target_start >= 0:
        starts = find_word_occurrences(str(question.get("sentence", "")), str(question.get("word", "")))
        if target_start in starts:
            return starts.index(target_start) + 1
    return 1

def question_core_signature(question: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        normalize_identity_text(question.get("articleId")),
        normalize_identity_text(question.get("word")),
        normalize_identity_text(question.get("sentence")),
        question_target_occurrence(question),
    )

def question_detail_signature(question: dict[str, Any]) -> tuple[Any, ...]:
    options = sorted(
        normalize_identity_text(option.get("text"))
        for option in question.get("options", [])
        if isinstance(option, dict)
    )
    answer = str(question.get("answer", "")).strip()
    correct_text = ""
    for option in question.get("options", []):
        if isinstance(option, dict) and option.get("key") == answer:
            correct_text = normalize_identity_text(option.get("text"))
            break
    return (
        normalize_identity_text(question.get("type") or "context_meaning"),
        normalize_identity_text(question.get("stem")),
        tuple(options),
        correct_text,
        normalize_identity_text(question.get("explanation")),
    )

def _to_base36(value: int) -> str:
    if value == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result

def make_duplicate_group_id(core_signature: tuple[Any, ...]) -> str:
    """Keep duplicate group IDs compatible with question_identity.js."""
    serialized = json.dumps(list(core_signature), ensure_ascii=False, separators=(",", ":"))
    hash_value = 2166136261
    encoded = serialized.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        code_unit = int.from_bytes(encoded[index:index + 2], "little")
        hash_value ^= code_unit
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return f"duplicate-{_to_base36(hash_value)}"

def strip_system_review_notes(value: Any) -> str:
    parts = [part.strip() for part in str(value or "").split("；")]
    return "；".join(part for part in parts if part and not part.startswith("系统检测："))

def infer_review_status_after_underlining_fix(question: dict[str, Any]) -> str:
    saved_status = question.get("reviewStatusBeforeAbnormal")
    if isinstance(saved_status, str) and saved_status and saved_status != "abnormal":
        return saved_status
    source_kind = question.get("source", {}).get("kind") if isinstance(question.get("source"), dict) else ""
    if isinstance(source_kind, str) and source_kind.startswith("candidate"):
        return "candidate"
    if source_kind == "textbook_word_bank_reviewed":
        return "verified"
    return "admin_edited"

def normalize_duplicate_reviews(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild duplicate candidates so full-bank imports cannot bypass review."""
    source = [copy.deepcopy(question) for question in questions]
    previous_reviews: dict[str, dict[str, Any]] = {}
    for question in source:
        duplicate_review = question.get("duplicateReview")
        if isinstance(duplicate_review, dict):
            previous_reviews[question["id"]] = copy.deepcopy(duplicate_review)
        question.pop("duplicateReview", None)

    grouped: dict[tuple[str, str, str, int], dict[tuple[Any, ...], list[dict[str, Any]]]] = {}
    for question in source:
        core = question_core_signature(question)
        detail = question_detail_signature(question)
        grouped.setdefault(core, {}).setdefault(detail, []).append(question)

    for core, detail_groups in grouped.items():
        if len(detail_groups) < 2:
            continue
        group_questions = [question for group in detail_groups.values() for question in group]
        related_ids = [question["id"] for question in group_questions]
        unchanged = all(
            isinstance(previous_reviews.get(question["id"]), dict)
            and set(previous_reviews[question["id"]].get("relatedQuestionIds", [])) == set(related_ids)
            for question in group_questions
        )
        previous_group_ids = {
            previous_reviews[question["id"]].get("groupId")
            for question in group_questions
            if isinstance(previous_reviews.get(question["id"]), dict)
        }
        group_id = (
            next(iter(previous_group_ids))
            if unchanged and len(previous_group_ids) == 1
            else make_duplicate_group_id(core)
        )
        for question in group_questions:
            previous = previous_reviews.get(question["id"])
            status = previous.get("status") if unchanged and isinstance(previous, dict) else "pending"
            if status not in VALID_DUPLICATE_REVIEW_STATUSES:
                status = "pending"
            question["duplicateReview"] = {
                "status": status,
                "groupId": group_id,
                "relatedQuestionIds": related_ids,
            }
    return source

def validate_scoring_config(quiz_defaults: Any) -> dict[str, Any]:
    if quiz_defaults is None:
        quiz_defaults = {}
    if not isinstance(quiz_defaults, dict):
        raise ValueError("题库的 quizDefaults 必须是对象。")

    raw = quiz_defaults.get("scoring")
    if raw is None:
        legacy_wrong = quiz_defaults.get("wrongScore", -DEFAULT_SCORING_CONFIG["baseWrongPenalty"])
        if isinstance(legacy_wrong, bool) or not isinstance(legacy_wrong, int):
            raise ValueError("旧版 wrongScore 必须是整数。")
        raw = {
            "mode": "fixed",
            "baseCorrect": quiz_defaults.get("correctScore", DEFAULT_SCORING_CONFIG["baseCorrect"]),
            "baseWrongPenalty": abs(legacy_wrong),
            "correctStreakAfter": DEFAULT_SCORING_CONFIG["correctStreakAfter"],
            "correctStreakScore": DEFAULT_SCORING_CONFIG["correctStreakScore"],
            "wrongStreakAfter": DEFAULT_SCORING_CONFIG["wrongStreakAfter"],
            "wrongStreakPenalty": DEFAULT_SCORING_CONFIG["wrongStreakPenalty"],
        }
    if not isinstance(raw, dict):
        raise ValueError("quizDefaults.scoring 必须是对象。")

    mode = raw.get("mode", DEFAULT_SCORING_CONFIG["mode"])
    if mode not in {"fixed", "streak"}:
        raise ValueError("计分机制 mode 必须是 fixed 或 streak。")

    def read_score(name: str) -> int:
        value = raw.get(name, DEFAULT_SCORING_CONFIG[name])
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
            raise ValueError(f"计分机制 {name} 必须是 0-1000 的整数。")
        return value

    def read_threshold(name: str) -> int:
        value = raw.get(name, DEFAULT_SCORING_CONFIG[name])
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_STREAK_THRESHOLD:
            raise ValueError(f"计分机制 {name} 必须是 1-{MAX_STREAK_THRESHOLD} 的整数。")
        return value

    return {
        "mode": mode,
        "baseCorrect": read_score("baseCorrect"),
        "baseWrongPenalty": read_score("baseWrongPenalty"),
        "correctStreakAfter": read_threshold("correctStreakAfter"),
        "correctStreakScore": read_score("correctStreakScore"),
        "wrongStreakAfter": read_threshold("wrongStreakAfter"),
        "wrongStreakPenalty": read_score("wrongStreakPenalty"),
    }

def validate_duration_seconds(quiz_defaults: dict[str, Any]) -> int:
    value = quiz_defaults.get("durationSeconds", 120)
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_DURATION_SECONDS <= value <= MAX_DURATION_SECONDS:
        raise ValueError(f"答题时长必须是 {MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS} 秒的整数。")
    return value

def empty_question_bank() -> dict[str, Any]:
    """Return the blank bank used by public source and release packages."""
    return {
        "schemaVersion": "3.0",
        "title": "文言实词限时训练（待导入题库）",
        "description": "这是一个空白题库。请管理员在后台导入或新增题库后开始训练。",
        "questionTypes": [],
        "books": [],
        "quizDefaults": {
            "durationSeconds": 120,
            "scoring": dict(DEFAULT_SCORING_CONFIG),
            "correctScore": DEFAULT_SCORING_CONFIG["baseCorrect"],
            "wrongScore": -DEFAULT_SCORING_CONFIG["baseWrongPenalty"],
        },
        "catalog": [],
        "lexicon": [],
        "source": {"kind": "blank_template", "questionCount": 0},
        "questions": [],
    }

def validate_questions(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("题库必须包含 questions 数组。")
    is_blank_bank = len(payload["questions"]) == 0
    payload = copy.deepcopy(payload)

    raw_quiz_defaults = payload.get("quizDefaults")
    if raw_quiz_defaults is not None and not isinstance(raw_quiz_defaults, dict):
        raise ValueError("题库的 quizDefaults 必须是对象。")
    quiz_defaults = dict(raw_quiz_defaults or {})
    quiz_defaults["durationSeconds"] = validate_duration_seconds(quiz_defaults)
    scoring = validate_scoring_config(quiz_defaults)
    quiz_defaults["scoring"] = scoring
    # Keep the two legacy fields synchronized so older packaged frontends fall
    # back to the base scores instead of reading stale values.
    quiz_defaults["correctScore"] = scoring["baseCorrect"]
    quiz_defaults["wrongScore"] = -scoring["baseWrongPenalty"]
    payload = {**payload, "quizDefaults": quiz_defaults}

    catalog = payload.get("catalog", [])
    if not isinstance(catalog, list):
        raise ValueError("教材目录 catalog 必须是数组。")
    if not catalog and not is_blank_bank:
        raise ValueError("教材目录 catalog 不能是空数组。")
    catalog_ids: set[str] = set()
    catalog_by_id: dict[str, dict[str, Any]] = {}
    for position, article in enumerate(catalog, start=1):
        if not isinstance(article, dict):
            raise ValueError(f"教材目录第 {position} 项不是对象。")
        for field in ("id", "title", "volume"):
            if not isinstance(article.get(field), str) or not article[field].strip():
                raise ValueError(f"教材目录第 {position} 项的 {field} 不能为空。")
        article_id = article["id"].strip()
        article = {
            **article,
            "id": article_id,
            "title": article["title"].strip(),
            "volume": article["volume"].strip(),
        }
        payload["catalog"][position - 1] = article
        if article_id in catalog_ids:
            raise ValueError(f"教材目录存在重复 id“{article_id}”。")
        catalog_ids.add(article_id)
        catalog_by_id[article_id] = article

    question_types = payload.get("questionTypes")
    allowed_types = set(VALID_TYPES)
    if question_types is not None:
        if not isinstance(question_types, list):
            raise ValueError("题型目录 questionTypes 必须是数组。")
        allowed_types = set()
        type_ids: set[str] = set()
        for position, question_type in enumerate(question_types, start=1):
            if not isinstance(question_type, dict):
                raise ValueError(f"题型目录第 {position} 项不是对象。")
            for field in ("id", "label"):
                if not isinstance(question_type.get(field), str) or not question_type[field].strip():
                    raise ValueError(f"题型目录第 {position} 项的 {field} 不能为空。")
            type_id = question_type["id"].strip()
            if type_id in type_ids:
                raise ValueError(f"题型目录存在重复 id“{type_id}”。")
            type_ids.add(type_id)
            allowed_types.add(type_id)
        if not allowed_types and not is_blank_bank:
            raise ValueError("题型目录不能是空数组。")

    books = payload.get("books")
    if books is not None:
        if not isinstance(books, list):
            raise ValueError("教材册目录 books 必须是数组。")
        book_ids: set[str] = set()
        book_labels: set[str] = set()
        for position, book in enumerate(books, start=1):
            if not isinstance(book, dict):
                raise ValueError(f"教材册目录第 {position} 项不是对象。")
            for field in ("id", "label"):
                if not isinstance(book.get(field), str) or not book[field].strip():
                    raise ValueError(f"教材册目录第 {position} 项的 {field} 不能为空。")
            book_id = book["id"].strip()
            book_label = book["label"].strip()
            if book_id in book_ids:
                raise ValueError(f"教材册目录存在重复 id“{book_id}”。")
            if book_label in book_labels:
                raise ValueError(f"教材册目录存在重复名称“{book_label}”。")
            books[position - 1] = {**book, "id": book_id, "label": book_label}
            book_ids.add(book_id)
            book_labels.add(book_label)
        if not book_labels and not is_blank_bank:
            raise ValueError("教材册目录不能是空数组。")
    else:
        book_labels = set()

    if book_labels:
        for article in catalog:
            if article["volume"] not in book_labels:
                raise ValueError(f"教材目录篇目“{article['title']}”的教材册不存在于 books 目录。")

    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    duplicate_review_refs: list[tuple[str, dict[str, Any]]] = []
    for position, question in enumerate(payload["questions"], start=1):
        if not isinstance(question, dict):
            raise ValueError(f"第 {position} 题不是对象。")
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError(f"第 {position} 题的 id 缺失或重复。")
        if question_id != question_id.strip():
            raise ValueError(f"第 {position} 题的 id 前后不能有空格。")
        if question_id in seen_ids:
            raise ValueError(f"第 {position} 题的 id 缺失或重复。")
        seen_ids.add(question_id)
        raw_number = question.get("number", position)
        if isinstance(raw_number, bool) or not isinstance(raw_number, int) or raw_number < 1:
            raise ValueError(f"第 {position} 题的 number 必须是正整数。")
        if raw_number in seen_numbers:
            raise ValueError(f"题库存在重复题号“{raw_number}”。")
        seen_numbers.add(raw_number)
        question["number"] = raw_number
        duplicate_review = question.get("duplicateReview")
        if duplicate_review is not None:
            if not isinstance(duplicate_review, dict):
                raise ValueError(f"第 {position} 题的 duplicateReview 必须是对象。")
            duplicate_status = duplicate_review.get("status")
            if not isinstance(duplicate_status, str) or duplicate_status not in VALID_DUPLICATE_REVIEW_STATUSES:
                raise ValueError(f"第 {position} 题的重复审查状态不受支持。")
            group_id = duplicate_review.get("groupId")
            if not isinstance(group_id, str) or not group_id.strip() or len(group_id.strip()) > 100:
                raise ValueError(f"第 {position} 题的重复审查组 ID 无效。")
            related_ids = duplicate_review.get("relatedQuestionIds")
            if not isinstance(related_ids, list) or not related_ids:
                raise ValueError(f"第 {position} 题的重复关联题目不能为空。")
            clean_related_ids: list[str] = []
            for related_id in related_ids:
                if not isinstance(related_id, str) or not related_id.strip():
                    raise ValueError(f"第 {position} 题的重复关联题目 ID 无效。")
                clean_related_id = related_id.strip()
                if clean_related_id not in clean_related_ids:
                    clean_related_ids.append(clean_related_id)
            question["duplicateReview"] = {
                "status": duplicate_status,
                "groupId": group_id.strip(),
                "relatedQuestionIds": clean_related_ids,
            }
            duplicate_review_refs.append((question_id, question["duplicateReview"]))
        # 兼容早期只保存语境释义题、尚未写入 type 字段的旧题；新导入题目仍应明确填写 type。
        question_type = question.get("type") or "context_meaning"
        if not isinstance(question_type, str) or question_type not in allowed_types:
            raise ValueError(f"第 {position} 题的题型不受支持。")
        question["type"] = question_type
        for field in ("articleId", "article", "volume", "word", "sentence", "explanation"):
            if not isinstance(question.get(field), str) or not question[field].strip():
                raise ValueError(f"第 {position} 题的 {field} 不能为空。")
            question[field] = question[field].strip()
        if catalog_ids and question["articleId"] not in catalog_ids:
            raise ValueError(f"第 {position} 题的篇目不存在于教材目录。")
        if book_labels and question["volume"] not in book_labels:
            raise ValueError(f"第 {position} 题的教材册不存在于 books 目录。")
        article_record = catalog_by_id.get(question["articleId"])
        if article_record and question["volume"] != article_record["volume"]:
            raise ValueError(f"第 {position} 题的 volume 与所属篇目的教材册不一致。")
        if article_record and normalize_identity_text(question["article"]) != normalize_identity_text(article_record["title"]):
            raise ValueError(f"第 {position} 题的 article 与所属篇目的 title 不一致。")
        occurrence_starts = find_word_occurrences(question["sentence"], question["word"])
        underline_issue = ""
        raw_occurrence = question.get("targetOccurrence")
        if raw_occurrence is None:
            raw_occurrence = 1
            if "targetStart" in question:
                fallback_start = question["targetStart"]
                if isinstance(fallback_start, bool) or not isinstance(fallback_start, int) or fallback_start < 0:
                    underline_issue = "targetStart 不是非负整数"
                elif fallback_start not in occurrence_starts:
                    underline_issue = "targetStart 不在 word 的实际位置上"
                else:
                    raw_occurrence = occurrence_starts.index(fallback_start) + 1
        elif isinstance(raw_occurrence, bool) or not isinstance(raw_occurrence, int):
            underline_issue = "targetOccurrence 不是正整数"
        if not underline_issue and not occurrence_starts:
            underline_issue = "word 不在 sentence 中"
        if not underline_issue and (raw_occurrence < 1 or raw_occurrence > len(occurrence_starts)):
            underline_issue = "targetOccurrence 超出原句中的实际出现次数"
        if not underline_issue and "targetStart" in question:
            target_start = question["targetStart"]
            if isinstance(target_start, bool) or not isinstance(target_start, int):
                underline_issue = "targetStart 不是非负整数"
            elif target_start < 0:
                underline_issue = "targetStart 不是非负整数"
            elif target_start != occurrence_starts[raw_occurrence - 1]:
                underline_issue = "targetStart 与 targetOccurrence 不一致"
        if underline_issue:
            previous_status = question.get("reviewStatus")
            if previous_status and previous_status != "abnormal":
                question["reviewStatusBeforeAbnormal"] = previous_status
            previous_note = strip_system_review_notes(question.get("reviewNote"))
            question["reviewStatus"] = "abnormal"
            question["reviewNote"] = f"{previous_note}{'；' if previous_note else ''}系统检测：{underline_issue}，请人工复核。"
        else:
            if question.get("reviewStatus") == "abnormal":
                question["reviewStatus"] = infer_review_status_after_underlining_fix(question)
            question.pop("reviewStatusBeforeAbnormal", None)
            clean_review_note = strip_system_review_notes(question.get("reviewNote"))
            if clean_review_note:
                question["reviewNote"] = clean_review_note
            else:
                question.pop("reviewNote", None)
        options = question.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise ValueError(f"第 {position} 题必须有四个选项。")
        if not all(isinstance(option, dict) for option in options):
            raise ValueError(f"第 {position} 题的四个选项格式不正确。")
        keys = [option.get("key") for option in options]
        texts: list[str] = []
        for option in options:
            key = option.get("key")
            text = option.get("text")
            if not isinstance(key, str) or not isinstance(text, str) or not text.strip():
                raise ValueError(f"第 {position} 题的四个选项不完整。")
            texts.append(text.strip())
            option["key"] = key.strip()
            option["text"] = text.strip()
        if set(keys) != {"A", "B", "C", "D"} or len(texts) != 4 or not all(texts):
            raise ValueError(f"第 {position} 题的四个选项不完整。")
        if len(set(texts)) != 4:
            raise ValueError(f"第 {position} 题的选项不能重复。")
        if not isinstance(question.get("answer"), str) or question["answer"] not in {"A", "B", "C", "D"}:
            raise ValueError(f"第 {position} 题的正确答案必须为 A、B、C 或 D。")
    question_by_id = {question["id"]: question for question in payload["questions"]}
    declared_groups: dict[str, frozenset[str]] = {}
    for question_id, duplicate_review in duplicate_review_refs:
        related_ids = frozenset(duplicate_review["relatedQuestionIds"])
        if len(related_ids) < 2 or question_id not in related_ids or not related_ids.issubset(seen_ids):
            raise ValueError(f"题目“{question_id}”的重复关联题目不存在于当前题库。")
        previous_related_ids = declared_groups.get(duplicate_review["groupId"])
        if previous_related_ids is not None and previous_related_ids != related_ids:
            raise ValueError(f"重复审查组“{duplicate_review['groupId']}”的关联题目集合不一致。")
        declared_groups[duplicate_review["groupId"]] = related_ids
        core_signatures = set()
        detail_signatures = set()
        for related_id in related_ids:
            related_question = question_by_id[related_id]
            related_review = related_question.get("duplicateReview")
            if not isinstance(related_review, dict):
                raise ValueError(f"题目“{related_id}”缺少重复审查关系。")
            if related_review.get("groupId") != duplicate_review["groupId"]:
                raise ValueError(f"重复审查组“{duplicate_review['groupId']}”的 groupId 不一致。")
            related_review_ids = frozenset(related_review.get("relatedQuestionIds", []))
            if related_review_ids != related_ids:
                raise ValueError(f"重复审查组“{duplicate_review['groupId']}”的关联关系不是双向一致的。")
            core_signatures.add(question_core_signature(related_question))
            detail_signatures.add(question_detail_signature(related_question))
        if len(core_signatures) != 1:
            raise ValueError(f"重复审查组“{duplicate_review['groupId']}”的核心内容不一致。")
        if len(detail_signatures) < 2:
            raise ValueError(f"重复审查组“{duplicate_review['groupId']}”没有不同的题目细节版本。")
    payload["questions"] = normalize_duplicate_reviews(payload["questions"])
    return payload

def validate_leaderboard_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"volumes": [], "articles": [], "durationSeconds": 0, "scoring": None}

    def clean_refs(value: Any, limit: int, label: str) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, str]] = []
        for item in value[:limit]:
            if isinstance(item, dict):
                item_id = str(item.get("id", "")).strip()[:120]
                item_label = str(item.get("label", item.get("title", ""))).strip()[:120]
            else:
                item_id = ""
                item_label = str(item).strip()[:120]
            if item_label:
                result.append({"id": item_id, "label": item_label})
        return result

    duration = payload.get("durationSeconds", 0)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 3600:
        duration = 0
    scoring = None
    if isinstance(payload.get("scoring"), dict):
        try:
            scoring = validate_scoring_config({"scoring": payload["scoring"]})
        except ValueError:
            scoring = None
    return {
        "volumes": clean_refs(payload.get("volumes"), 50, "教材册"),
        "articles": clean_refs(payload.get("articles"), 200, "篇目"),
        "durationSeconds": duration,
        "scoring": scoring,
    }

def validate_leaderboard(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("排行榜必须是数组。")
    clean: list[dict[str, Any]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError("排行榜中存在无效记录。")
        name = str(entry.get("name", "")).strip()[:20]
        if not name:
            raise ValueError("排行榜姓名不能为空。")
        try:
            score = int(entry.get("score"))
            created_at = int(entry.get("createdAt", 0))
        except (TypeError, ValueError) as error:
            raise ValueError("排行榜分数或时间格式不正确。") from error
        entry_id = str(entry.get("id", "")).strip()[:120]
        if not entry_id:
            entry_id = f"legacy-score-{max(created_at, 0)}-{index}"
        record_id = str(entry.get("recordId", "")).strip()[:120]
        clean.append({
            "id": entry_id,
            "recordId": record_id or None,
            "name": name,
            "score": score,
            "createdAt": max(created_at, 0),
            "context": validate_leaderboard_context(entry.get("context")),
        })
    return sorted(clean, key=lambda item: (-item["score"], item["createdAt"], item["id"]))

def validate_answer_record_question(payload: Any, position: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"答题记录第 {position} 道题不是对象。")
    required_fields = ("id", "number", "article", "volume", "word", "sentence", "explanation")
    for field in required_fields:
        if field == "number":
            continue
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"答题记录第 {position} 道题的 {field} 不能为空。")
    try:
        number = int(payload.get("number", position))
    except (TypeError, ValueError) as error:
        raise ValueError(f"答题记录第 {position} 道题的题号格式不正确。") from error

    options = payload.get("options")
    if not isinstance(options, list) or len(options) != 4:
        raise ValueError(f"答题记录第 {position} 道题必须有四个选项。")
    clean_options: list[dict[str, str]] = []
    for option in options:
        if not isinstance(option, dict) or option.get("key") not in VALID_OPTION_KEYS:
            raise ValueError(f"答题记录第 {position} 道题的选项格式不正确。")
        text = str(option.get("text", "")).strip()[:200]
        if not text:
            raise ValueError(f"答题记录第 {position} 道题的选项文字不能为空。")
        clean_options.append({"key": option["key"], "text": text})
    if {option["key"] for option in clean_options} != VALID_OPTION_KEYS:
        raise ValueError(f"答题记录第 {position} 道题的选项键必须是 A、B、C、D。")

    answer = payload.get("answer")
    if answer not in VALID_OPTION_KEYS:
        raise ValueError(f"答题记录第 {position} 道题的正确答案格式不正确。")
    selected_key = payload.get("selectedKey")
    if selected_key is not None and selected_key not in VALID_OPTION_KEYS:
        raise ValueError(f"答题记录第 {position} 道题的作答选项格式不正确。")
    is_correct = payload.get("isCorrect")
    if is_correct is not None and not isinstance(is_correct, bool):
        raise ValueError(f"答题记录第 {position} 道题的判定结果格式不正确。")
    if selected_key is None:
        is_correct = None
    else:
        is_correct = selected_key == answer

    score_delta = payload.get("scoreDelta")
    if score_delta is not None and (isinstance(score_delta, bool) or not isinstance(score_delta, int) or not -1000 <= score_delta <= 1000):
        raise ValueError(f"答题记录第 {position} 道题的 scoreDelta 格式不正确。")
    score_tier = payload.get("scoreTier")
    if score_tier is not None and score_tier not in {"base", "streak"}:
        raise ValueError(f"答题记录第 {position} 道题的 scoreTier 格式不正确。")
    score_label = payload.get("scoreLabel")
    if score_label is not None and (not isinstance(score_label, str) or len(score_label.strip()) > 40):
        raise ValueError(f"答题记录第 {position} 道题的 scoreLabel 格式不正确。")
    correct_streak = payload.get("correctStreak", 0)
    wrong_streak = payload.get("wrongStreak", 0)
    for field, value in (("correctStreak", correct_streak), ("wrongStreak", wrong_streak)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
            raise ValueError(f"答题记录第 {position} 道题的 {field} 格式不正确。")

    clean: dict[str, Any] = {
        "id": str(payload["id"]).strip()[:120],
        "number": number,
        "articleId": str(payload.get("articleId", "")).strip()[:120],
        "article": str(payload["article"]).strip()[:120],
        "volume": str(payload["volume"]).strip()[:80],
        "unit": str(payload.get("unit", "")).strip()[:80],
        "word": str(payload["word"]).strip()[:80],
        "sentence": str(payload["sentence"]).strip()[:500],
        "targetStart": payload.get("targetStart"),
        "targetOccurrence": payload.get("targetOccurrence", 1),
        "stem": str(payload.get("stem", "")).strip()[:500],
        "options": clean_options,
        "answer": answer,
        "selectedKey": selected_key,
        "isCorrect": is_correct,
        "scoreDelta": score_delta,
        "scoreTier": score_tier,
        "scoreLabel": str(score_label).strip()[:40] if score_label is not None else None,
        "correctStreak": correct_streak,
        "wrongStreak": wrong_streak,
        "explanation": str(payload["explanation"]).strip()[:1000],
        "quizIndex": payload.get("quizIndex"),
    }
    if clean["targetStart"] is not None:
        if isinstance(clean["targetStart"], bool) or not isinstance(clean["targetStart"], int) or clean["targetStart"] < 0:
            raise ValueError(f"答题记录第 {position} 道题的 targetStart 格式不正确。")
    if isinstance(clean["targetOccurrence"], bool) or not isinstance(clean["targetOccurrence"], int) or clean["targetOccurrence"] < 1:
        raise ValueError(f"答题记录第 {position} 道题的 targetOccurrence 格式不正确。")
    if clean["quizIndex"] is not None:
        if isinstance(clean["quizIndex"], bool) or not isinstance(clean["quizIndex"], int) or clean["quizIndex"] < 0:
            raise ValueError(f"答题记录第 {position} 道题的 quizIndex 格式不正确。")
    return clean

def validate_answer_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("答题记录必须是对象。")
    record_id = str(payload.get("id", "")).strip()[:120]
    if not record_id:
        raise ValueError("答题记录缺少 id。")
    try:
        score = int(payload.get("score", 0))
        started_at = int(payload.get("startedAt", 0))
        finished_at = int(payload.get("finishedAt", 0))
        used_seconds = int(payload.get("usedSeconds", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("答题记录的分数或时间格式不正确。") from error
    if min(started_at, finished_at, used_seconds) < 0 or used_seconds > 86400:
        raise ValueError("答题记录的时间范围不正确。")
    archived = payload.get("archived", False)
    if not isinstance(archived, bool):
        raise ValueError("答题记录的折叠状态格式不正确。")
    try:
        archived_at = int(payload.get("archivedAt", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("答题记录的折叠时间格式不正确。") from error
    if archived_at < 0:
        raise ValueError("答题记录的折叠时间格式不正确。")
    if archived and archived_at == 0:
        archived_at = int(time.time() * 1000)
    if not archived:
        archived_at = 0
    questions = payload.get("questions")
    if not isinstance(questions, list) or len(questions) > 1000:
        raise ValueError("答题记录必须包含 0-1000 道题的快照。")
    scoring = None
    if payload.get("scoring") is not None:
        scoring = validate_scoring_config({"scoring": payload.get("scoring")})
    clean_questions = [validate_answer_record_question(question, position) for position, question in enumerate(questions, start=1)]
    answered = [question for question in clean_questions if question["selectedKey"] is not None]
    correct = sum(question["isCorrect"] is True for question in clean_questions)
    wrong = sum(question["isCorrect"] is False for question in clean_questions)
    return {
        "recordType": "solo",
        "id": record_id,
        "name": str(payload.get("name", "")).strip()[:20] or "未命名",
        "score": score,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "usedSeconds": used_seconds,
        "completedAll": bool(payload.get("completedAll", False)),
        "answeredCount": len(answered),
        "correctCount": correct,
        "wrongCount": wrong,
        "archived": archived,
        "archivedAt": archived_at,
        "scoring": scoring,
        "context": validate_leaderboard_context(payload.get("context")),
        "questions": clean_questions,
    }

def _validate_pk_player(payload: Any, position: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"PK 玩家 {position} 数据必须是对象。")
    player_id = str(payload.get("playerId", f"player{position}")).strip()[:20]
    if player_id not in {"player1", "player2"}:
        raise ValueError("PK 玩家 id 必须是 player1 或 player2。")
    integer_fields = ("score", "answeredCount", "correctCount", "wrongCount", "usedMilliseconds", "usedSeconds")
    clean_numbers: dict[str, int] = {}
    for field in integer_fields:
        value = payload.get(field, 0)
        if isinstance(value, bool):
            raise ValueError(f"PK 玩家 {position} 的 {field} 格式不正确。")
        try:
            value = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"PK 玩家 {position} 的 {field} 格式不正确。") from error
        if field != "score" and value < 0:
            raise ValueError(f"PK 玩家 {position} 的 {field} 不能为负数。")
        if field == "usedSeconds" and value > 86400:
            raise ValueError(f"PK 玩家 {position} 的用时过长。")
        if field == "usedMilliseconds" and value > 86400000:
            raise ValueError(f"PK 玩家 {position} 的用时过长。")
        clean_numbers[field] = value
    questions = payload.get("questions", [])
    if not isinstance(questions, list) or len(questions) > 1000:
        raise ValueError(f"PK 玩家 {position} 的题目快照数量不正确。")
    clean_questions = [
        validate_answer_record_question(question, question_position)
        for question_position, question in enumerate(questions, start=1)
    ]
    completed = payload.get("completed", False)
    if not isinstance(completed, bool):
        raise ValueError(f"PK 玩家 {position} 的完成状态格式不正确。")
    finished_at = payload.get("finishedAt", 0)
    if isinstance(finished_at, bool):
        raise ValueError(f"PK 玩家 {position} 的完成时间格式不正确。")
    try:
        finished_at = int(finished_at)
    except (TypeError, ValueError) as error:
        raise ValueError(f"PK 玩家 {position} 的完成时间格式不正确。") from error
    if finished_at < 0:
        raise ValueError(f"PK 玩家 {position} 的完成时间格式不正确。")
    return {
        "playerId": player_id,
        **clean_numbers,
        "completed": completed,
        "finishedAt": finished_at,
        "questions": clean_questions,
    }

def validate_pk_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("PK 答题记录必须是对象。")
    record_id = str(payload.get("id", "")).strip()[:120]
    if not record_id:
        raise ValueError("PK 答题记录缺少 id。")
    match_id = str(payload.get("matchId", record_id)).strip()[:120]
    if not match_id:
        raise ValueError("PK 答题记录缺少 matchId。")
    pk_mode = payload.get("pkMode")
    if pk_mode not in {"time", "questions"}:
        raise ValueError("PK 模式必须是 time 或 questions。")
    def optional_limit(field: str, maximum: int) -> int | None:
        value = payload.get(field)
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"PK 记录的 {field} 格式不正确。")
        try:
            value = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"PK 记录的 {field} 格式不正确。") from error
        if value < 1 or value > maximum:
            raise ValueError(f"PK 记录的 {field} 超出范围。")
        return value
    time_limit = optional_limit("timeLimitSeconds", 86400)
    question_limit = optional_limit("questionLimit", 1000)
    if pk_mode == "time" and time_limit is None:
        raise ValueError("比时间 PK 必须记录 timeLimitSeconds。")
    if pk_mode == "questions" and question_limit is None:
        raise ValueError("比题数 PK 必须记录 questionLimit。")
    try:
        started_at = int(payload.get("startedAt", 0))
        finished_at = int(payload.get("finishedAt", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("PK 记录的开始或结束时间格式不正确。") from error
    if min(started_at, finished_at) < 0 or finished_at < started_at:
        raise ValueError("PK 记录的时间范围不正确。")
    archived = payload.get("archived", False)
    if not isinstance(archived, bool):
        raise ValueError("PK 记录的折叠状态格式不正确。")
    try:
        archived_at = int(payload.get("archivedAt", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("PK 记录的折叠时间格式不正确。") from error
    if archived_at < 0:
        raise ValueError("PK 记录的折叠时间格式不正确。")
    if archived and archived_at == 0:
        archived_at = int(time.time() * 1000)
    if not archived:
        archived_at = 0
    players = payload.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("PK 记录必须包含两名玩家。")
    clean_players = [_validate_pk_player(player, index) for index, player in enumerate(players, start=1)]
    if {player["playerId"] for player in clean_players} != {"player1", "player2"}:
        raise ValueError("PK 记录的玩家必须分别是 player1 和 player2。")
    shared_ids = payload.get("sharedQuestionIds", [])
    if not isinstance(shared_ids, list) or len(shared_ids) > 1000:
        raise ValueError("PK 记录的 sharedQuestionIds 格式不正确。")
    clean_shared_ids: list[str] = []
    for question_id in shared_ids:
        question_id = str(question_id).strip()[:120]
        if question_id and question_id not in clean_shared_ids:
            clean_shared_ids.append(question_id)
    scoring = None
    if payload.get("scoring") is not None:
        scoring = validate_scoring_config({"scoring": payload.get("scoring")})
    answer_count = sum(player["answeredCount"] for player in clean_players)
    correct_count = sum(player["correctCount"] for player in clean_players)
    wrong_count = sum(player["wrongCount"] for player in clean_players)
    return {
        "recordType": "pk",
        "id": record_id,
        "matchId": match_id,
        "name": "双人 PK",
        "score": 0,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "usedSeconds": max(player["usedSeconds"] for player in clean_players),
        "completedAll": True,
        "answeredCount": answer_count,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "archived": archived,
        "archivedAt": archived_at,
        "scoring": scoring,
        "context": validate_leaderboard_context(payload.get("context")),
        "pkMode": pk_mode,
        "timeLimitSeconds": time_limit,
        "questionLimit": question_limit,
        "sharedQuestionIds": clean_shared_ids,
        "players": clean_players,
        "questions": [],
    }

def validate_answer_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("答题记录必须是数组。")
    clean: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in payload:
        validator = validate_pk_record if isinstance(record, dict) and record.get("recordType") == "pk" else validate_answer_record
        item = validator(record)
        if item["id"] in seen_ids:
            raise ValueError(f"答题记录存在重复 id“{item['id']}”。")
        seen_ids.add(item["id"])
        clean.append(item)
    return sorted(clean, key=lambda item: (-item["finishedAt"], -item["startedAt"]))

def prune_answer_records(records: list[dict[str, Any]], now_ms: int | None = None) -> list[dict[str, Any]]:
    """Keep one month of records with one shared cap for solo and PK records."""
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    cutoff_ms = current_ms - ANSWER_RECORD_RETENTION_DAYS * 24 * 60 * 60 * 1000
    ordered = sorted(records, key=lambda item: (-item["finishedAt"], -item["startedAt"]))
    recent = [record for record in ordered if record["finishedAt"] >= cutoff_ms]
    return recent[:ANSWER_RECORD_MAX_COUNT]

def validate_answer_records_import(payload: Any) -> list[dict[str, Any]]:
    """Validate either an exported envelope or a plain records array."""
    if isinstance(payload, dict):
        records = payload.get("records")
    else:
        records = payload
    if not isinstance(records, list) or not records:
        raise ValueError("导入文件必须包含非空 records 答题记录数组。")
    if len(records) > 2000:
        raise ValueError("一次最多导入 2000 条答题记录。")
    return validate_answer_records(records)

def empty_question_bank_history() -> dict[str, Any]:
    return {"schemaVersion": 1, "events": []}

def _validate_history_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"题库历史记录的 {label} 必须是数组。")
    clean: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"题库历史记录的 {label} 包含无效 ID。")
        item = item.strip()[:120]
        if item not in clean:
            clean.append(item)
    return clean

def _validate_history_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"题库历史记录的 {label} 必须是非负整数。")
    return value

def validate_question_bank_history(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("题库历史记录必须是对象。")
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise ValueError("题库历史记录必须包含 events 数组。")

    clean_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, raw_event in enumerate(events, start=1):
        if not isinstance(raw_event, dict):
            raise ValueError(f"题库历史记录第 {position} 项不是对象。")
        event_id = raw_event.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError(f"题库历史记录第 {position} 项缺少 id。")
        event_id = event_id.strip()[:120]
        if event_id in seen_ids:
            raise ValueError(f"题库历史记录存在重复 id“{event_id}”。")
        seen_ids.add(event_id)
        kind = raw_event.get("kind")
        if kind not in VALID_QUESTION_BANK_HISTORY_KINDS:
            raise ValueError(f"题库历史记录第 {position} 项的类型不受支持。")
        created_at = str(raw_event.get("createdAt", "")).strip()[:80]
        if not created_at:
            raise ValueError(f"题库历史记录第 {position} 项缺少时间。")

        clean: dict[str, Any] = {
            "id": event_id,
            "kind": kind,
            "createdAt": created_at,
        }
        if kind == "import":
            mode = raw_event.get("mode")
            if mode not in {"merge", "replace"}:
                raise ValueError(f"题库导入历史第 {position} 项的模式不受支持。")
            source_name = str(raw_event.get("sourceName", "")).strip()[:200]
            if not source_name:
                raise ValueError(f"题库导入历史第 {position} 项缺少文件名。")
            clean.update({
                "mode": mode,
                "sourceName": source_name,
                "questionCountBefore": _validate_history_count(raw_event.get("questionCountBefore"), "导入前题目数"),
                "questionCountAfter": _validate_history_count(raw_event.get("questionCountAfter"), "导入后题目数"),
                "addedQuestionIds": _validate_history_string_list(raw_event.get("addedQuestionIds", []), "新增题目 ID"),
                "addedArticleIds": _validate_history_string_list(raw_event.get("addedArticleIds", []), "新增篇目 ID"),
                "addedBookIds": _validate_history_string_list(raw_event.get("addedBookIds", []), "新增教材册 ID"),
                "addedTypeIds": _validate_history_string_list(raw_event.get("addedTypeIds", []), "新增题型 ID"),
                "beforeHash": str(raw_event.get("beforeHash", "")).strip()[:200],
                "afterHash": str(raw_event.get("afterHash", "")).strip()[:200],
            })
            if not clean["beforeHash"] or not clean["afterHash"]:
                raise ValueError(f"题库导入历史第 {position} 项缺少版本校验值。")
            if mode == "replace":
                before_bank = raw_event.get("beforeBank")
                if not isinstance(before_bank, dict) or not isinstance(before_bank.get("questions"), list):
                    raise ValueError(f"题库替换历史第 {position} 项缺少可恢复的原题库快照。")
                clean["beforeBank"] = copy.deepcopy(before_bank)
        elif kind == "export":
            format_name = str(raw_event.get("format", "json")).strip().lower()
            if format_name != "json":
                raise ValueError(f"题库导出历史第 {position} 项的格式不受支持。")
            clean.update({
                "format": "json",
                "sourceName": str(raw_event.get("sourceName", "")).strip()[:200] or "题库 JSON",
                "questionCount": _validate_history_count(raw_event.get("questionCount"), "导出题目数"),
            })
        else:
            target_id = raw_event.get("targetEventId")
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError(f"题库撤销历史第 {position} 项缺少目标导入记录。")
            clean["targetEventId"] = target_id.strip()[:120]
        clean_events.append(clean)

    import_ids = {event["id"] for event in clean_events if event["kind"] == "import"}
    for event in clean_events:
        if event["kind"] == "revoke" and event["targetEventId"] not in import_ids:
            raise ValueError(f"题库撤销历史引用了不存在的导入记录“{event['targetEventId']}”。")
    return {"schemaVersion": 1, "events": clean_events}

def question_bank_history_view(
    history: dict[str, Any],
    question_bank: dict[str, Any],
) -> dict[str, Any]:
    """Return read-only history metadata without exposing replacement snapshots."""
    events = history["events"]
    revoked_ids = {
        event["targetEventId"]
        for event in events
        if event["kind"] == "revoke"
    }
    current_hash = make_json_etag(question_bank)
    imports = {event["id"]: event for event in events if event["kind"] == "import"}
    result: list[dict[str, Any]] = []
    for event in events:
        public = {key: copy.deepcopy(value) for key, value in event.items() if key != "beforeBank"}
        if event["kind"] == "import":
            revoked = event["id"] in revoked_ids
            if revoked:
                public.update({"revoked": True, "canRevoke": False, "revokeReason": "本次导入已经撤销。"})
            elif event["mode"] == "merge":
                public.update({"revoked": False, "canRevoke": True, "revokeReason": ""})
            elif current_hash == event["afterHash"]:
                public.update({"revoked": False, "canRevoke": True, "revokeReason": ""})
            else:
                public.update({
                    "revoked": False,
                    "canRevoke": False,
                    "revokeReason": "本次导入后题库已有后续变化，暂不能安全撤销。",
                })
        elif event["kind"] == "revoke":
            target = imports.get(event["targetEventId"])
            public["targetSourceName"] = target["sourceName"] if target else event["targetEventId"]
        result.append(public)
    return {"schemaVersion": 1, "events": result}

def empty_question_reviews() -> dict[str, Any]:
    return {"schemaVersion": 1, "reviews": {}}

def validate_question_review(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("题目审查记录必须是对象。")

    status = payload.get("status", "pending")
    if not isinstance(status, str) or status not in VALID_REVIEW_STATUSES:
        raise ValueError("题目审查状态不受支持。")

    answer_correct = payload.get("answerCorrect")
    if answer_correct is not None and not isinstance(answer_correct, bool):
        raise ValueError("正确答案审查结果必须是布尔值或空值。")

    suggested_answer = payload.get("suggestedAnswer")
    if suggested_answer is not None and (
        not isinstance(suggested_answer, str) or suggested_answer not in VALID_OPTION_KEYS
    ):
        raise ValueError("建议正确答案只能使用 A、B、C、D 或空值。")

    option_issues = payload.get("optionIssues", [])
    if not isinstance(option_issues, list):
        raise ValueError("选项问题必须是数组。")
    clean_option_issues: list[str] = []
    for key in option_issues:
        if not isinstance(key, str) or key not in VALID_OPTION_KEYS:
            raise ValueError("选项问题只能使用 A、B、C 或 D。")
        if key not in clean_option_issues:
            clean_option_issues.append(key)

    reviewed_at = str(payload.get("reviewedAt", "")).strip()[:40]
    note = str(payload.get("note", "")).strip()[:1000]
    return {
        "status": status,
        "answerCorrect": answer_correct,
        "suggestedAnswer": suggested_answer,
        "optionIssues": clean_option_issues,
        "note": note,
        "reviewedAt": reviewed_at,
    }

def validate_question_reviews(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("题目审查记录必须是对象。")
    reviews = payload.get("reviews", {})
    if not isinstance(reviews, dict):
        raise ValueError("题目审查记录必须包含 reviews 对象。")

    clean_reviews: dict[str, Any] = {}
    for question_id, review in reviews.items():
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError("题目审查记录存在无效题目 id。")
        clean_reviews[question_id] = validate_question_review(review)
    return {"schemaVersion": 1, "reviews": clean_reviews}

def empty_question_review() -> dict[str, Any]:
    return validate_question_review({})
