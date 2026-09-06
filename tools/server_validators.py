"""Pure validation and question identity rules for the local server."""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
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
        "format": "wenyan-question-bank",
        "schemaVersion": "4.0",
        "bankId": f"bank_{uuid.uuid4()}",
        "title": "文言实词限时训练（待导入题库）",
        "description": "这是一个空白题库。请管理员在后台导入或新增题库后开始训练。",
        "questionTypes": [],
        "books": [],
        "quizDefaults": {
            "durationSeconds": 120,
            "scoring": dict(DEFAULT_SCORING_CONFIG),
        },
        "catalog": [],
        "questions": [],
        "workflow": {"reviews": {}, "duplicateResolutions": {}},
    }

def validate_questions(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and (
        payload.get("format") == "wenyan-question-bank"
        or str(payload.get("schemaVersion", "")) == "4.0"
    ):
        return validate_question_bank_v4(payload)
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


def _validate_history_delta(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError(f"题库历史记录的 {label} 必须是对象。")
    clean: dict[str, dict[str, Any]] = {}
    for item_id, delta in value.items():
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(delta, dict):
            raise ValueError(f"题库历史记录的 {label} 包含无效变更项。")
        if not isinstance(delta.get("before"), dict) or not isinstance(delta.get("after"), dict):
            raise ValueError(f"题库历史记录的 {label} 必须包含 before 和 after 快照。")
        clean[item_id.strip()[:120]] = {
            "before": copy.deepcopy(delta["before"]),
            "after": copy.deepcopy(delta["after"]),
        }
        if "afterFingerprint" in delta:
            fingerprint = str(delta.get("afterFingerprint", "")).strip()
            if not fingerprint:
                raise ValueError(f"题库历史记录的 {label} 缺少 afterFingerprint。")
            clean[item_id.strip()[:120]]["afterFingerprint"] = fingerprint[:200]
    return clean


def _validate_history_duplicate_delta(value: Any, label: str) -> dict[str, dict[str, Any]]:
    # Duplicate-resolution deltas are optional internal history fields: old
    # events predate them and read as empty.  ``before`` may be None when the
    # import created a decision for a group that had none.
    if not isinstance(value, dict):
        raise ValueError(f"题库历史记录的 {label} 必须是对象。")
    clean: dict[str, dict[str, Any]] = {}
    for item_id, delta in value.items():
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(delta, dict):
            raise ValueError(f"题库历史记录的 {label} 包含无效变更项。")
        before = delta.get("before")
        after = delta.get("after")
        if before is not None and not isinstance(before, dict):
            raise ValueError(f"题库历史记录的 {label} 必须包含 before 快照。")
        if not isinstance(after, dict):
            raise ValueError(f"题库历史记录的 {label} 必须包含 after 快照。")
        clean[item_id.strip()[:120]] = {
            "before": copy.deepcopy(before) if before is not None else None,
            "after": copy.deepcopy(after),
        }
    return clean


def _validate_added_directory_snapshots(value: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(value, dict):
        raise ValueError("题库历史记录的新增目录快照必须是对象。")
    clean: dict[str, dict[str, dict[str, Any]]] = {}
    for collection in ("books", "catalog", "questionTypes"):
        raw_collection = value.get(collection, {})
        if not isinstance(raw_collection, dict):
            raise ValueError("题库历史记录的新增目录快照格式无效。")
        clean[collection] = {}
        for item_id, item in raw_collection.items():
            if not isinstance(item_id, str) or not item_id.strip() or not isinstance(item, dict):
                raise ValueError("题库历史记录的新增目录快照包含无效项。")
            clean[collection][item_id.strip()[:120]] = copy.deepcopy(item)
    return clean

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
                "updatedQuestions": _validate_history_delta(raw_event.get("updatedQuestions", {}), "更新题目"),
                "updatedReviews": _validate_history_delta(raw_event.get("updatedReviews", {}), "更新审查"),
                "updatedDuplicateResolutions": _validate_history_duplicate_delta(raw_event.get("updatedDuplicateResolutions", {}), "更新重复处理"),
                "updatedBooks": _validate_history_delta(raw_event.get("updatedBooks", {}), "更新教材册"),
                "updatedCatalog": _validate_history_delta(raw_event.get("updatedCatalog", {}), "更新篇目"),
                "updatedTypes": _validate_history_delta(raw_event.get("updatedTypes", {}), "更新题型"),
                "addedDirectorySnapshots": _validate_added_directory_snapshots(
                    raw_event.get("addedDirectorySnapshots", {})
                ),
                "addedQuestionFingerprints": {
                    item_id.strip()[:120]: str(fingerprint).strip()[:200]
                    for item_id, fingerprint in (
                        raw_event.get("addedQuestionFingerprints", {})
                        if isinstance(raw_event.get("addedQuestionFingerprints", {}), dict)
                        else {}
                    ).items()
                    if isinstance(item_id, str) and item_id.strip() and str(fingerprint).strip()
                },
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
                can_revoke = True
                revoke_reason = ""
                current_questions = {q["id"]: q for q in question_bank.get("questions", [])}
                current_reviews = question_bank.get("workflow", {}).get("reviews", {})
                if event.get("addedQuestionFingerprints"):
                    can_revoke = all(
                        qid not in current_questions
                        or make_question_semantic_fingerprint(current_questions[qid]) == fingerprint
                        for qid, fingerprint in event["addedQuestionFingerprints"].items()
                    )
                    if not can_revoke:
                        revoke_reason = "本次导入新增的题目后来又被修改，无法安全撤销。"
                if can_revoke:
                    for qid, delta in event.get("updatedQuestions", {}).items():
                        current_item = current_questions.get(qid)
                        if current_item is None or make_question_semantic_fingerprint(current_item) != delta.get("afterFingerprint"):
                            can_revoke = False
                            revoke_reason = "本次导入影响的题目后来又被修改，无法安全撤销。"
                            break
                if can_revoke:
                    for qid, delta in event.get("updatedReviews", {}).items():
                        if current_reviews.get(qid) != delta.get("after"):
                            can_revoke = False
                            revoke_reason = "本次导入影响的审查结果后来又被修改，无法安全撤销。"
                            break
                if can_revoke:
                    current_duplicates = question_bank.get("workflow", {}).get("duplicateResolutions", {})
                    for gid, delta in event.get("updatedDuplicateResolutions", {}).items():
                        if current_duplicates.get(gid) != delta.get("after"):
                            can_revoke = False
                            revoke_reason = "本次导入影响的重复处理结果后来又被修改，无法安全撤销。"
                            break
                if can_revoke:
                    directory_maps = {
                        "books": {item["id"]: item for item in question_bank.get("books", [])},
                        "catalog": {item["id"]: item for item in question_bank.get("catalog", [])},
                        "questionTypes": {item["id"]: item for item in question_bank.get("questionTypes", [])},
                    }
                    for collection in ("books", "catalog", "questionTypes"):
                        delta_key = {
                            "books": "updatedBooks",
                            "catalog": "updatedCatalog",
                            "questionTypes": "updatedTypes",
                        }[collection]
                        for item_id, delta in event.get(delta_key, {}).items():
                            if directory_maps[collection].get(item_id) != delta.get("after"):
                                can_revoke = False
                                revoke_reason = "本次导入影响的目录信息后来又被修改，无法安全撤销。"
                                break
                        if not can_revoke:
                            break
                if can_revoke and event.get("addedDirectorySnapshots"):
                    added_question_ids = set(event.get("addedQuestionIds", []))
                    catalog_by_id = {item["id"]: item for item in question_bank.get("catalog", [])}
                    for collection in ("books", "catalog", "questionTypes"):
                        snapshots = event["addedDirectorySnapshots"].get(collection, {})
                        for item_id, snapshot in snapshots.items():
                            current_item = next(
                                (item for item in question_bank.get(collection, []) if item["id"] == item_id),
                                None,
                            )
                            if current_item is not None and current_item != snapshot:
                                can_revoke = False
                                revoke_reason = "本次导入新增的目录信息后来又被修改，无法安全撤销。"
                                break
                            if collection == "catalog" and any(
                                q["articleId"] == item_id and q["id"] not in added_question_ids
                                for q in question_bank.get("questions", [])
                            ):
                                can_revoke = False
                                revoke_reason = "本次导入新增的篇目仍被其他题目使用，无法安全撤销。"
                                break
                            if collection == "books" and any(
                                catalog_by_id.get(q["articleId"], {}).get("bookId") == item_id
                                and q["id"] not in added_question_ids
                                for q in question_bank.get("questions", [])
                            ):
                                can_revoke = False
                                revoke_reason = "本次导入新增的教材册仍被其他题目使用，无法安全撤销。"
                                break
                        if not can_revoke:
                            break
                public.update({
                    "revoked": False,
                    "canRevoke": can_revoke,
                    "revokeReason": "" if can_revoke else revoke_reason or "本次导入影响的数据后来又被修改，无法安全撤销。",
                })
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

# v4 canonical bank helpers. Python is the only authority for these rules.
def _new_question_id(used: set[str]) -> str:
    while True:
        value = f"q_{uuid.uuid4()}"
        if value not in used:
            return value

def make_question_core_signature(question: dict[str, Any]) -> tuple[Any, ...]:
    return (normalize_identity_text(question.get("articleId")), normalize_identity_text(question.get("word")), normalize_identity_text(question.get("sentence")), int(question.get("targetOccurrence", 1)))

def make_question_semantic_fingerprint(question: dict[str, Any]) -> str:
    value = {"core": list(make_question_core_signature(question)), "detail": list(question_detail_signature(question))}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _v4_review(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    status = raw.get("status", "pending")
    if not isinstance(status, str) or status not in VALID_REVIEW_STATUSES:
        raise ValueError("题目审查状态无效。")
    suggested = raw.get("suggestedAnswer")
    if suggested is not None and (not isinstance(suggested, str) or suggested not in VALID_OPTION_KEYS):
        raise ValueError("suggestedAnswer 必须是 A-D。")
    issues = raw.get("optionIssues", [])
    if not isinstance(issues, list) or any(not isinstance(key, str) or key not in VALID_OPTION_KEYS for key in issues):
        raise ValueError("optionIssues 必须是 A-D 数组。")
    option_issues = sorted(set(issues))
    note = str(raw.get("note", "")).strip()[:2000]
    reviewed_at = str(raw.get("reviewedAt", "")).strip()[:60]
    if status == "passed":
        suggested = None
        option_issues = []
        note = ""
    return {
        "status": status,
        "suggestedAnswer": suggested,
        "optionIssues": option_issues,
        "note": note,
        "reviewedAt": reviewed_at,
    }

def question_issues(question: dict[str, Any]) -> list[dict[str, str]]:
    starts = find_word_occurrences(str(question.get("sentence", "")), str(question.get("word", "")))
    occurrence = question.get("targetOccurrence", 1)
    if not starts:
        return [{"code": "WORD_NOT_FOUND", "message": "考察词不在原句中。"}]
    if not isinstance(occurrence, int) or isinstance(occurrence, bool) or not 1 <= occurrence <= len(starts):
        return [{"code": "TARGET_OCCURRENCE_OUT_OF_RANGE", "message": "考察词出现次数无效。"}]
    return []

def _v4_question(raw: Any, position: int, used: set[str], catalog_ids: set[str], type_ids: set[str], generate_id: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ValueError(f"第 {position} 题不是对象。")
    source = copy.deepcopy(raw)
    qid = source.get("id")
    if generate_id:
        qid = _new_question_id(used)
    elif not isinstance(qid, str) or not qid.strip():
        raise ValueError(f"第 {position} 题的 id 缺失或重复。")
    if not isinstance(qid, str) or not qid.strip() or qid != qid.strip() or len(qid) > 160 or qid in used: raise ValueError(f"第 {position} 题的 id 缺失或重复。")
    used.add(qid)
    question_type = source.get("type", "context_meaning")
    article_id = source.get("articleId")
    if not isinstance(question_type, str) or question_type.strip() not in type_ids: raise ValueError(f"第 {position} 题的题型不存在。")
    if not isinstance(article_id, str) or article_id.strip() not in catalog_ids: raise ValueError(f"第 {position} 题的篇目不存在。")
    word, sentence = str(source.get("word", "")).strip(), str(source.get("sentence", "")).strip()
    if not word or not sentence: raise ValueError(f"第 {position} 题的 word 和 sentence 不能为空。")
    occurrence = source.get("targetOccurrence", 1)
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 1: raise ValueError(f"第 {position} 题的 targetOccurrence 无效。")
    options = source.get("options")
    if not isinstance(options, list) or len(options) != 4 or {x.get("key") for x in options if isinstance(x, dict)} != VALID_OPTION_KEYS: raise ValueError(f"第 {position} 题必须有 A-D 四个选项。")
    clean_options = []
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("text"), str) or not option["text"].strip(): raise ValueError(f"第 {position} 题的选项不完整。")
        clean_options.append({"key": option["key"], "text": option["text"].strip()})
    if len({x["text"] for x in clean_options}) != 4: raise ValueError(f"第 {position} 题的选项不能重复。")
    answer = source.get("answer")
    if not isinstance(answer, str) or answer not in VALID_OPTION_KEYS: raise ValueError(f"第 {position} 题的答案必须为 A-D。")
    number = source.get("number", position)
    if isinstance(number, bool) or not isinstance(number, int) or number < 1: raise ValueError(f"第 {position} 题的 number 无效。")
    result = {"id": qid, "number": number, "type": question_type.strip() or "context_meaning", "articleId": article_id.strip(), "word": word, "sentence": sentence, "targetOccurrence": occurrence, "stem": str(source.get("stem", "")).strip(), "options": clean_options, "answer": answer, "explanation": str(source.get("explanation", "")).strip()}
    for key in ("source", "rule", "context", "supportingItems", "rawText"):
        if key in source: result[key] = source[key]
    return result

def _v4_duplicates(questions: list[dict[str, Any]], stored: Any) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for q in questions: groups.setdefault(make_question_core_signature(q), []).append(q)
    output = {}
    stored = stored if isinstance(stored, dict) else {}
    for core, members in groups.items():
        if len(members) < 2 or len({tuple(question_detail_signature(q)) for q in members}) < 2: continue
        gid = make_duplicate_group_id(core)
        fingerprint = hashlib.sha256("|".join(f"{q['id']}:{make_question_semantic_fingerprint(q)}" for q in sorted(members, key=lambda x: x["id"])).encode()).hexdigest()
        old = stored.get(gid) if isinstance(stored.get(gid), dict) else {}
        decisions = old.get("decisions", {}) if old.get("fingerprint") == fingerprint else {}
        output[gid] = {"fingerprint": fingerprint, "questionIds": [q["id"] for q in members], "decisions": {q["id"]: decisions[q["id"]] for q in members if decisions.get(q["id"]) in {"kept", "skipped"}}, "updatedAt": old.get("updatedAt", "")}
    return output

def validate_question_bank_v4(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != "wenyan-question-bank" or str(payload.get("schemaVersion")) != "4.0": raise ValueError("题库必须是 wenyan-question-bank 4.0 格式。")
    bank_id = payload.get("bankId")
    if not isinstance(bank_id, str) or not bank_id.strip() or len(bank_id.strip()) > 160: raise ValueError("题库缺少有效 bankId。")
    books, catalog, types, raw_questions = (payload.get(k, []) for k in ("books", "catalog", "questionTypes", "questions"))
    if not all(isinstance(x, list) for x in (books, catalog, types, raw_questions)): raise ValueError("题库目录和 questions 必须是数组。")
    book_ids, clean_books = set(), []
    for position, item in enumerate(books, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("label"), str): raise ValueError(f"教材册目录第 {position} 项无效。")
        book_id, label = item["id"].strip(), item["label"].strip()
        if not book_id or not label or len(book_id) > 160 or book_id in book_ids: raise ValueError("教材册目录无效或重复。")
        order = item.get("order", position)
        if isinstance(order, bool) or not isinstance(order, int) or order < 0: raise ValueError("教材册目录排序必须是非负整数。")
        book_ids.add(book_id)
        clean_books.append({"id": book_id, "label": label, "order": order})
    type_ids, clean_types = set(), []
    for position, item in enumerate(types, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("label"), str): raise ValueError(f"题型目录第 {position} 项无效。")
        type_id, label = item["id"].strip(), item["label"].strip()
        if not type_id or not label or len(type_id) > 160 or type_id in type_ids: raise ValueError("题型目录无效或重复。")
        type_ids.add(type_id)
        clean_types.append({"id": type_id, "label": label, "description": str(item.get("description", "")).strip()})
    # Built-in question types remain available even in a blank bank whose
    # directory has not been materialized yet; custom types are additive.
    type_ids |= set(VALID_TYPES)
    article_ids, clean_catalog = set(), []
    for position, item in enumerate(catalog, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("bookId"), str) or not isinstance(item.get("title"), str): raise ValueError(f"篇目目录第 {position} 项无效。")
        article_id, book_id, title = item["id"].strip(), item["bookId"].strip(), item["title"].strip()
        if not article_id or not book_id or not title or len(article_id) > 160 or article_id in article_ids or book_id not in book_ids: raise ValueError("篇目目录无效，必须引用有效 bookId。")
        article_ids.add(article_id)
        # volume is a v3 compatibility field; it is intentionally discarded
        # from the canonical v4 directory and always derived from bookId.
        clean_catalog.append({
            "id": article_id,
            "bookId": book_id,
            "unit": str(item.get("unit", "")).strip(),
            "title": title,
            "author": str(item.get("author", "")).strip(),
        })
    raw_defaults_payload = payload.get("quizDefaults")
    if raw_defaults_payload is not None and not isinstance(raw_defaults_payload, dict):
        raise ValueError("题库的 quizDefaults 必须是对象。")
    raw_defaults = dict(raw_defaults_payload or {})
    # v4 stores the scoring object as the canonical contract.  The legacy
    # correctScore/wrongScore fields are still accepted as input by
    # validate_scoring_config, but must not be carried into a v4 write.
    defaults = {
        "durationSeconds": validate_duration_seconds(raw_defaults),
        "scoring": validate_scoring_config(raw_defaults),
    }
    used, questions = set(), []
    for pos, raw in enumerate(raw_questions, 1): questions.append(_v4_question(raw, pos, used, article_ids, type_ids))
    numbers = [q["number"] for q in questions]
    if len(numbers) != len(set(numbers)): raise ValueError("题库存在重复题号。")
    workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
    raw_reviews = workflow.get("reviews") if isinstance(workflow.get("reviews"), dict) else {}
    reviews = {q["id"]: _v4_review(raw_reviews.get(q["id"])) for q in questions}
    title = str(payload.get("title", "")).strip() or "文言实词题库"
    description = str(payload.get("description", "")).strip()
    return {"format": "wenyan-question-bank", "schemaVersion": "4.0", "bankId": bank_id.strip(), "title": title, "description": description, "questionTypes": clean_types, "books": clean_books, "catalog": clean_catalog, "quizDefaults": defaults, "questions": questions, "workflow": {"reviews": reviews, "duplicateResolutions": _v4_duplicates(questions, workflow.get("duplicateResolutions"))}}

def question_bank_diagnostics(bank: dict[str, Any]) -> dict[str, Any]:
    decisions = {}; group_members = {}
    for gid, group in bank.get("workflow", {}).get("duplicateResolutions", {}).items():
        decisions.update(group.get("decisions", {}))
        for qid in group.get("questionIds", list(group.get("decisions", {}))): group_members[qid] = (gid, group.get("decisions", {}).get(qid))
    issues = {q["id"]: question_issues(q) for q in bank["questions"]}; availability = {}
    for q in bank["questions"]:
        review = bank["workflow"]["reviews"][q["id"]]; decision = decisions.get(q["id"])
        question_issues_for_id = issues[q["id"]]
        duplicate_blocked = q["id"] in group_members and decision != "kept"
        playable = not question_issues_for_id and review["status"] == "passed" and not duplicate_blocked
        if question_issues_for_id:
            reason = "invalid"
        elif review["status"] == "pending":
            reason = "review_pending"
        elif review["status"] == "needs_revision":
            reason = "review_needs_revision"
        elif review["status"] == "skipped":
            reason = "review_skipped"
        elif duplicate_blocked:
            reason = "duplicate_skipped" if decision == "skipped" else "duplicate_pending"
        else:
            reason = "playable" if playable else "blocked"
        availability[q["id"]] = {"playable": playable, "issues": question_issues_for_id, "reason": reason}
    return {"issues": issues, "availability": availability, "pendingReviewCount": sum(r["status"] == "pending" for r in bank["workflow"]["reviews"].values())}

def _enrich_question_views(bank: dict[str, Any], include_workflow: bool) -> dict[str, Any]:
    view = copy.deepcopy(bank) if include_workflow else {k: copy.deepcopy(bank[k]) for k in ("format", "schemaVersion", "title", "description", "questionTypes", "books", "catalog", "quizDefaults", "questions")}
    diagnostics = question_bank_diagnostics(bank)
    for article in view.get("catalog", []):
        book = next((b for b in bank["books"] if b["id"] == article.get("bookId")), {})
        article["volume"] = book.get("label", "")
    duplicate_groups = {}
    for core, members in _group_v4_questions(bank["questions"]).items():
        if len(members) >= 2 and len({tuple(question_detail_signature(q)) for q in members}) >= 2:
            gid = make_duplicate_group_id(core); stored = bank["workflow"]["duplicateResolutions"].get(gid, {})
            duplicate_groups.update({q["id"]: {"status": stored.get("decisions", {}).get(q["id"], "pending"), "groupId": gid, "relatedQuestionIds": [x["id"] for x in members]} for q in members})
    for q in view["questions"]:
        article = next((a for a in bank["catalog"] if a["id"] == q["articleId"]), {}); book = next((b for b in bank["books"] if b["id"] == article.get("bookId")), {})
        q.update({"article": article.get("title", ""), "volume": book.get("label", ""), "unit": article.get("unit", "")})
        q["availability"] = diagnostics["availability"][q["id"]]
        if include_workflow:
            review = bank["workflow"]["reviews"][q["id"]]
            q["reviewStatus"] = "abnormal" if diagnostics["issues"][q["id"]] else "verified" if review["status"] == "passed" else "candidate"
            if diagnostics["issues"][q["id"]]:
                q["reviewNote"] = "；".join(issue["message"] for issue in diagnostics["issues"][q["id"]])
        if include_workflow and q["id"] in duplicate_groups: q["duplicateReview"] = duplicate_groups[q["id"]]
    if include_workflow: view["diagnostics"] = diagnostics
    return view

def student_question_bank_view(bank: dict[str, Any]) -> dict[str, Any]: return _enrich_question_views(bank, False)
def admin_question_bank_view(bank: dict[str, Any]) -> dict[str, Any]: return _enrich_question_views(bank, True)

def _group_v4_questions(questions: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for question in questions: groups.setdefault(make_question_core_signature(question), []).append(question)
    return groups

def validate_question_import(payload: Any, current: dict[str, Any], *, generate_ids: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != "wenyan-question-import" or str(payload.get("schemaVersion")) != "1.0":
        raise ValueError("新增题目导入必须是 wenyan-question-import 1.0 格式。")
    books = payload.get("books", current.get("books", [])); catalog = payload.get("catalog", current.get("catalog", [])); types = payload.get("questionTypes", current.get("questionTypes", []))
    draft = {"format": "wenyan-question-bank", "schemaVersion": "4.0", "bankId": current["bankId"], "title": payload.get("title", current.get("title", "")), "description": payload.get("description", current.get("description", "")), "books": books, "catalog": catalog, "questionTypes": types, "quizDefaults": current.get("quizDefaults", {}), "questions": payload.get("questions", []), "workflow": {"reviews": {}, "duplicateResolutions": {}}}
    used = {q["id"] for q in current["questions"]}; article_ids = {x.get("id") for x in catalog if isinstance(x, dict) and isinstance(x.get("id"), str)}; type_ids = set(VALID_TYPES) | {x.get("id") for x in types if isinstance(x, dict) and isinstance(x.get("id"), str)}; normalized = []
    used_numbers = {int(q.get("number", 0)) for q in current.get("questions", [])}; next_number = max(used_numbers or {0}) + 1
    for pos, raw in enumerate(draft["questions"], 1):
        item = copy.deepcopy(raw)
        if generate_ids or item.get("number") in used_numbers or not isinstance(item.get("number"), int):
            item["number"] = next_number; next_number += 1
        used_numbers.add(item["number"])
        normalized.append(_v4_question(item, pos, used, article_ids, type_ids, generate_id=generate_ids))
    draft["questions"] = normalized
    result = validate_question_bank_v4(draft)
    result["importKind"] = "external"
    result["workflow"] = {"reviews": {q["id"]: _v4_review({}) for q in result["questions"]}, "duplicateResolutions": {}}
    return result

def question_import_preview(current: dict[str, Any], incoming: dict[str, Any], mode: str) -> dict[str, Any]:
    """Legacy pure preview helper kept for old callers and tests.

    HTTP imports use ``server_question_import.build_import_preview`` so that
    preview and apply share one authoritative merge implementation.
    """
    imported = incoming["questions"]; current_by_id = {q["id"]: q for q in current["questions"]}; same_bank = incoming.get("importKind") != "external" and incoming.get("format") == "wenyan-question-bank" and incoming.get("bankId") == current.get("bankId")
    summary = {"importedTotal": len(imported), "unchanged": 0, "newQuestions": 0, "modified": 0, "majorModified": 0, "exactDuplicates": 0, "duplicateCandidates": 0, "reviewConflicts": 0}
    conflicts = []
    for q in imported:
        local = current_by_id.get(q["id"]) if same_bank else None
        if local is None:
            if any(make_question_semantic_fingerprint(q) == make_question_semantic_fingerprint(old) for old in current["questions"]): summary["exactDuplicates"] += 1
            else: summary["newQuestions"] += 1
        elif make_question_semantic_fingerprint(local) == make_question_semantic_fingerprint(q):
            summary["unchanged"] += 1
            local_review = current.get("workflow", {}).get("reviews", {}).get(q["id"], {})
            incoming_review = incoming.get("workflow", {}).get("reviews", {}).get(q["id"], {})
            if local_review.get("status") not in {None, "pending"} and incoming_review.get("status") not in {None, "pending", local_review.get("status")}: summary["reviewConflicts"] += 1; conflicts.append({"questionId": q["id"], "kind": "review", "message": "审查结论不同"})
        elif make_question_core_signature(local) == make_question_core_signature(q): summary["modified"] += 1
        else: summary["majorModified"] += 1
    return {"mode": mode, "format": incoming.get("format"), "sameBank": same_bank, "baseEtag": make_json_etag(current), "summary": summary, "conflicts": conflicts}

def remap_foreign_bank_questions(bank: dict[str, Any], used: set[str]) -> dict[str, Any]:
    result = copy.deepcopy(bank); mapping = {}
    for q in result["questions"]:
        old = q["id"]; new = _new_question_id(used); mapping[old] = new; q["id"] = new
    old_reviews = result.get("workflow", {}).get("reviews", {})
    result["workflow"] = {"reviews": {q["id"]: _v4_review({}) for q in result["questions"]}, "duplicateResolutions": {}}
    return result

def drop_exact_duplicates(bank: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    existing = {make_question_semantic_fingerprint(q) for q in current.get("questions", [])}
    result = copy.deepcopy(bank)
    result["questions"] = [q for q in result.get("questions", []) if make_question_semantic_fingerprint(q) not in existing]
    result["workflow"] = {"reviews": {q["id"]: _v4_review({}) for q in result["questions"]}, "duplicateResolutions": {}}
    return result
