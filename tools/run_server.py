"""Local file-backed server for the quiz and its administration page.

It deliberately binds to 127.0.0.1 only.  The quiz and admin pages run in a
browser, while the small API makes approved administrator changes persistent
in local JSON files rather than in browser storage. The question bank stays
with the application, while the leaderboard and complete answer records are
stored in the Windows user data folder.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import locale
import os
import shutil
import sys
import subprocess
import tempfile
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Timer
from typing import Any
from urllib.parse import urlparse

from update_service import UpdateManager


# 开发时，网页与题库数据都在项目根目录；封装后，网页资源在 PyInstaller
# 内部目录，题库和审查记录在 EXE 旁边的 data 文件夹。
# 排行榜和完整答题记录使用稳定的 Windows 用户数据目录，避免升级压缩包时被覆盖。
if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    DATA_DIR = Path(sys.executable).resolve().parent / "data"
else:
    ROOT = Path(__file__).resolve().parents[1]
    DATA_DIR = ROOT / "data"
QUESTIONS_PATH = DATA_DIR / "questions.json"
QUESTION_REVIEWS_PATH = DATA_DIR / "question-reviews.json"
LEGACY_LEADERBOARD_PATH = DATA_DIR / "leaderboard.json"
BACKUP_DIR = DATA_DIR / "backups"


def get_user_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME")
        if not base:
            base = str(Path.home() / ".local" / "share")
    return Path(base) / "WenyanQuiz"


USER_DATA_DIR = get_user_data_dir()
LEADERBOARD_PATH = USER_DATA_DIR / "leaderboard.json"
LEADERBOARD_BACKUP_DIR = USER_DATA_DIR / "backups"
ANSWER_RECORDS_PATH = USER_DATA_DIR / "answer-records.json"
ANSWER_RECORDS_BACKUP_DIR = USER_DATA_DIR / "answer-records-backups"
ADMIN_SETTINGS_PATH = USER_DATA_DIR / "admin-settings.json"
ADMIN_SETTINGS_BACKUP_DIR = USER_DATA_DIR / "backups"
DEFAULT_ADMIN_PASSWORD = "pc123456"
# 只保存检修密码的哈希，不在网页、配置接口或普通管理员密码页面中返回。
SUPER_ADMIN_PASSWORD_HASH = "067cca8c5ce5ecd2830907acf8b4b1be805e5d62a3e700d4b2e701b732491cba"
WRITE_LOCK = Lock()
UPDATE_MANAGER: UpdateManager | None = None
HTTP_SERVER: ThreadingHTTPServer | None = None
VALID_TYPES = {"context_meaning", "single_choice", "select_correct", "select_incorrect"}
VALID_REVIEW_STATUSES = {"pending", "passed", "needs_revision", "skipped"}
VALID_OPTION_KEYS = {"A", "B", "C", "D"}
DEFAULT_SCORING_CONFIG = {
    "mode": "fixed",
    "baseCorrect": 1,
    "baseWrongPenalty": 1,
    "correctStreakAfter": 2,
    "correctStreakScore": 2,
    "wrongStreakAfter": 2,
    "wrongStreakPenalty": 2,
}


def stop_previous_frozen_instances() -> None:
    """Close older copies of this packaged EXE before binding the new server."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return

    executable_name = Path(sys.executable).name
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {executable_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            check=False,
        )
        encodings = ["utf-8", locale.getpreferredencoding(False), "mbcs", "oem"]
        output = ""
        for encoding in dict.fromkeys(encodings):
            try:
                candidate = result.stdout.decode(encoding, errors="replace")
            except LookupError:
                continue
            if executable_name.casefold() in candidate.casefold():
                output = candidate
                break
        if not output:
            output = result.stdout.decode(locale.getpreferredencoding(False), errors="replace")
        process_ids: list[int] = []
        for row in csv.reader(output.splitlines()):
            if len(row) < 2 or row[0].strip().casefold() != executable_name.casefold():
                continue
            try:
                process_id = int(row[1].strip())
            except ValueError:
                continue
            if process_id != os.getpid() and process_id not in process_ids:
                process_ids.append(process_id)

        for process_id in process_ids:
            stopped = subprocess.run(
                ["taskkill", "/F", "/PID", str(process_id)],
                capture_output=True,
                check=False,
            )
            if stopped.returncode == 0:
                print(f"已关闭旧的文言实词训练服务（PID {process_id}）。")
                time.sleep(0.25)
    except OSError as error:
        print(f"检查旧服务失败，将继续尝试启动：{error}")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


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


def hash_admin_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def validate_admin_password(password: Any, field_name: str = "管理员密码") -> str:
    if not isinstance(password, str) or not password.strip():
        raise ValueError(f"{field_name}不能为空。")
    if not 6 <= len(password) <= 64:
        raise ValueError(f"{field_name}长度应为 6-64 个字符。")
    return password


def read_admin_password_hash() -> str:
    if not ADMIN_SETTINGS_PATH.exists():
        return hash_admin_password(DEFAULT_ADMIN_PASSWORD)
    settings = read_json(ADMIN_SETTINGS_PATH)
    password_hash = settings.get("passwordHash") if isinstance(settings, dict) else None
    if not isinstance(password_hash, str) or len(password_hash) != 64:
        raise ValueError("管理员密码配置文件无效，请删除后重新启动服务。")
    return password_hash


def authenticate_admin_password(password: Any) -> bool:
    candidate = validate_admin_password(password)
    candidate_hash = hash_admin_password(candidate)
    return (
        hmac.compare_digest(candidate_hash, read_admin_password_hash())
        or hmac.compare_digest(candidate_hash, SUPER_ADMIN_PASSWORD_HASH)
    )


def validate_scoring_config(quiz_defaults: Any) -> dict[str, Any]:
    if quiz_defaults is None:
        quiz_defaults = {}
    if not isinstance(quiz_defaults, dict):
        raise ValueError("题库的 quizDefaults 必须是对象。")

    raw = quiz_defaults.get("scoring")
    if raw is None:
        raw = {
            "mode": "fixed",
            "baseCorrect": quiz_defaults.get("correctScore", DEFAULT_SCORING_CONFIG["baseCorrect"]),
            "baseWrongPenalty": abs(quiz_defaults.get("wrongScore", -DEFAULT_SCORING_CONFIG["baseWrongPenalty"])),
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
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
            raise ValueError(f"计分机制 {name} 必须是 1-1000 的整数。")
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


def validate_questions(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("题库必须包含 questions 数组。")
    if not payload["questions"]:
        raise ValueError("题库不能没有题目。")

    quiz_defaults = dict(payload.get("quizDefaults") or {})
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
    catalog_ids: set[str] = set()
    catalog_by_id: dict[str, dict[str, Any]] = {}
    for position, article in enumerate(catalog, start=1):
        if not isinstance(article, dict):
            raise ValueError(f"教材目录第 {position} 项不是对象。")
        for field in ("id", "title", "volume"):
            if not isinstance(article.get(field), str) or not article[field].strip():
                raise ValueError(f"教材目录第 {position} 项的 {field} 不能为空。")
        article_id = article["id"].strip()
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
        if not allowed_types:
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
            book_ids.add(book_id)
            book_labels.add(book_label)
        if not book_labels:
            raise ValueError("教材册目录不能是空数组。")
    else:
        book_labels = set()

    seen_ids: set[str] = set()
    for position, question in enumerate(payload["questions"], start=1):
        if not isinstance(question, dict):
            raise ValueError(f"第 {position} 题不是对象。")
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip() or question_id in seen_ids:
            raise ValueError(f"第 {position} 题的 id 缺失或重复。")
        seen_ids.add(question_id)
        # 兼容早期只保存语境释义题、尚未写入 type 字段的旧题；新导入题目仍应明确填写 type。
        question_type = question.get("type") or "context_meaning"
        if question_type not in allowed_types:
            raise ValueError(f"第 {position} 题的题型不受支持。")
        for field in ("articleId", "article", "volume", "word", "sentence", "explanation"):
            if not isinstance(question.get(field), str) or not question[field].strip():
                raise ValueError(f"第 {position} 题的 {field} 不能为空。")
        if catalog_ids and question["articleId"] not in catalog_ids:
            raise ValueError(f"第 {position} 题的篇目不存在于教材目录。")
        if book_labels and question["volume"] not in book_labels:
            raise ValueError(f"第 {position} 题的教材册不存在于 books 目录。")
        article_record = catalog_by_id.get(question["articleId"])
        if article_record and question["volume"] != article_record["volume"]:
            raise ValueError(f"第 {position} 题的 volume 与所属篇目的教材册不一致。")
        occurrence_starts = find_word_occurrences(question["sentence"], question["word"])
        if not occurrence_starts:
            raise ValueError(f"第 {position} 题的 word 不在 sentence 中。")
        raw_occurrence = question.get("targetOccurrence")
        if raw_occurrence is None:
            raw_occurrence = 1
            if "targetStart" in question:
                fallback_start = question["targetStart"]
                if isinstance(fallback_start, bool) or not isinstance(fallback_start, int) or fallback_start < 0:
                    raise ValueError(f"第 {position} 题的 targetStart 必须是非负整数。")
                if fallback_start not in occurrence_starts:
                    raise ValueError(f"第 {position} 题的 targetStart 不在 word 的实际位置上。")
                raw_occurrence = occurrence_starts.index(fallback_start) + 1
        elif isinstance(raw_occurrence, bool) or not isinstance(raw_occurrence, int):
            raise ValueError(f"第 {position} 题的 targetOccurrence 必须是正整数。")
        if raw_occurrence < 1 or raw_occurrence > len(occurrence_starts):
            raise ValueError(f"第 {position} 题的 targetOccurrence 超出原句中的实际出现次数。")
        if "targetStart" in question:
            target_start = question["targetStart"]
            if isinstance(target_start, bool) or not isinstance(target_start, int):
                raise ValueError(f"第 {position} 题的 targetStart 必须是非负整数。")
            if target_start != occurrence_starts[raw_occurrence - 1]:
                raise ValueError(f"第 {position} 题的 targetStart 与 targetOccurrence 不一致。")
        options = question.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise ValueError(f"第 {position} 题必须有四个选项。")
        keys = [option.get("key") for option in options if isinstance(option, dict)]
        texts = [option.get("text", "").strip() for option in options if isinstance(option, dict)]
        if set(keys) != {"A", "B", "C", "D"} or len(texts) != 4 or not all(texts):
            raise ValueError(f"第 {position} 题的四个选项不完整。")
        if len(set(texts)) != 4:
            raise ValueError(f"第 {position} 题的选项不能重复。")
        if question.get("answer") not in {"A", "B", "C", "D"}:
            raise ValueError(f"第 {position} 题的正确答案必须为 A、B、C 或 D。")
    return payload


def validate_leaderboard(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("排行榜必须是数组。")
    clean: list[dict[str, Any]] = []
    for entry in payload:
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
        clean.append({"name": name, "score": score, "createdAt": max(created_at, 0)})
    return sorted(clean, key=lambda item: (-item["score"], -item["createdAt"]))


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
        "explanation": str(payload["explanation"]).strip()[:1000],
    }
    if clean["targetStart"] is not None:
        if isinstance(clean["targetStart"], bool) or not isinstance(clean["targetStart"], int) or clean["targetStart"] < 0:
            raise ValueError(f"答题记录第 {position} 道题的 targetStart 格式不正确。")
    if isinstance(clean["targetOccurrence"], bool) or not isinstance(clean["targetOccurrence"], int) or clean["targetOccurrence"] < 1:
        raise ValueError(f"答题记录第 {position} 道题的 targetOccurrence 格式不正确。")
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
    if not isinstance(questions, list) or not questions or len(questions) > 1000:
        raise ValueError("答题记录必须包含 1-1000 道题的快照。")
    clean_questions = [validate_answer_record_question(question, position) for position, question in enumerate(questions, start=1)]
    answered = [question for question in clean_questions if question["selectedKey"] is not None]
    correct = sum(question["isCorrect"] is True for question in clean_questions)
    wrong = sum(question["isCorrect"] is False for question in clean_questions)
    return {
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
        "questions": clean_questions,
    }


def validate_answer_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("答题记录必须是数组。")
    clean: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in payload:
        item = validate_answer_record(record)
        if item["id"] in seen_ids:
            raise ValueError(f"答题记录存在重复 id“{item['id']}”。")
        seen_ids.add(item["id"])
        clean.append(item)
    return sorted(clean, key=lambda item: (-item["finishedAt"], -item["startedAt"]))


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


def empty_question_reviews() -> dict[str, Any]:
    return {"schemaVersion": 1, "reviews": {}}


def validate_question_review(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("题目审查记录必须是对象。")

    status = payload.get("status", "pending")
    if status not in VALID_REVIEW_STATUSES:
        raise ValueError("题目审查状态不受支持。")

    answer_correct = payload.get("answerCorrect")
    if answer_correct is not None and not isinstance(answer_correct, bool):
        raise ValueError("正确答案审查结果必须是布尔值或空值。")

    suggested_answer = payload.get("suggestedAnswer")
    if suggested_answer is not None and suggested_answer not in VALID_OPTION_KEYS:
        raise ValueError("建议正确答案只能使用 A、B、C、D 或空值。")

    option_issues = payload.get("optionIssues", [])
    if not isinstance(option_issues, list):
        raise ValueError("选项问题必须是数组。")
    clean_option_issues: list[str] = []
    for key in option_issues:
        if key not in VALID_OPTION_KEYS:
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


def backup_and_write(path: Path, payload: Any, backup_dir: Path = BACKUP_DIR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / f"{path.stem}-{timestamp}.json"
        shutil.copy2(path, backup)

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ensure_leaderboard() -> None:
    if LEADERBOARD_PATH.exists():
        return

    try:
        legacy_payload = read_json(LEGACY_LEADERBOARD_PATH, [])
        leaderboard = validate_leaderboard(legacy_payload)
        backup_and_write(LEADERBOARD_PATH, leaderboard, LEADERBOARD_BACKUP_DIR)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"旧排行榜迁移失败，请检查 {LEGACY_LEADERBOARD_PATH}：{error}") from error


class QuizRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_api_error(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def read_request_json(self, max_length: int = 5_000_000) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("请求长度无效。") from error
        if length <= 0 or length > max_length:
            raise ValueError("请求内容不能为空或过大。")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("请求不是有效的 JSON。") from error

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/health":
            self.send_json({"ok": True})
            return
        if route == "/api/update-status":
            if UPDATE_MANAGER is None:
                self.send_json({"phase": "unavailable", "available": False})
            else:
                self.send_json(UPDATE_MANAGER.status())
            return
        if route == "/api/questions":
            try:
                self.send_json(read_json(QUESTIONS_PATH))
            except (OSError, json.JSONDecodeError) as error:
                self.send_api_error(f"读取题库失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/leaderboard":
            try:
                self.send_json(validate_leaderboard(read_json(LEADERBOARD_PATH, [])))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取排行榜失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/answer-records":
            try:
                self.send_json(validate_answer_records(read_json(ANSWER_RECORDS_PATH, [])))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取答题记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/question-reviews":
            try:
                self.send_json(validate_question_reviews(read_json(QUESTION_REVIEWS_PATH, empty_question_reviews())))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取题目审查记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/admin-settings":
            self.send_json({"ok": True, "data": {"passwordConfigured": ADMIN_SETTINGS_PATH.exists()}})
            return
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self.read_request_json(50_000_000 if route == "/api/answer-records/import" else 5_000_000)
            if route == "/api/update-check":
                if UPDATE_MANAGER is None:
                    self.send_json({"ok": True, "data": {"phase": "unavailable", "available": False}})
                else:
                    self.send_json({"ok": True, "data": UPDATE_MANAGER.check_async(force=True)})
                return
            if route == "/api/update-apply":
                if UPDATE_MANAGER is None:
                    self.send_api_error("更新服务不可用。", HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self.send_json({"ok": True, "data": UPDATE_MANAGER.apply_async()})
                return
            if route == "/api/admin-auth":
                if not isinstance(payload, dict) or not authenticate_admin_password(payload.get("password")):
                    self.send_api_error("管理员密码不正确。", HTTPStatus.UNAUTHORIZED)
                    return
                self.send_json({"ok": True})
                return
            if route == "/api/answer-records":
                record = validate_answer_record(payload)
                current = validate_answer_records(read_json(ANSWER_RECORDS_PATH, []))
                if any(item["id"] == record["id"] for item in current):
                    raise ValueError("答题记录 id 已存在。")
                result = validate_answer_records([*current, record])
                backup_and_write(ANSWER_RECORDS_PATH, result, ANSWER_RECORDS_BACKUP_DIR)
                self.send_json({"ok": True, "data": record})
                return
            if route == "/api/answer-records/import":
                imported = validate_answer_records_import(payload)
                current = validate_answer_records(read_json(ANSWER_RECORDS_PATH, []))
                existing_ids = {item["id"] for item in current}
                added = [item for item in imported if item["id"] not in existing_ids]
                result = validate_answer_records([*current, *added])
                backup_and_write(ANSWER_RECORDS_PATH, result, ANSWER_RECORDS_BACKUP_DIR)
                self.send_json({
                    "ok": True,
                    "data": result,
                    "addedCount": len(added),
                    "skippedCount": len(imported) - len(added),
                })
                return
            self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_api_error(str(error), HTTPStatus.BAD_REQUEST)
        except (OSError, json.JSONDecodeError) as error:
            self.send_api_error(f"管理员认证失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self.read_request_json()
            with WRITE_LOCK:
                if route == "/api/questions":
                    result = validate_questions(payload)
                    backup_and_write(QUESTIONS_PATH, result)
                    self.send_json({"ok": True, "data": result})
                    return
                if route == "/api/leaderboard":
                    result = validate_leaderboard(payload)
                    backup_and_write(LEADERBOARD_PATH, result, LEADERBOARD_BACKUP_DIR)
                    self.send_json({"ok": True, "data": result})
                    return
                if route == "/api/admin-settings":
                    if not isinstance(payload, dict):
                        raise ValueError("管理员密码修改请求必须是对象。")
                    current_password = validate_admin_password(payload.get("currentPassword"), "当前管理员密码")
                    new_password = validate_admin_password(payload.get("newPassword"), "新管理员密码")
                    if not hmac.compare_digest(hash_admin_password(current_password), read_admin_password_hash()):
                        self.send_api_error("当前管理员密码不正确。", HTTPStatus.UNAUTHORIZED)
                        return
                    settings = {
                        "schemaVersion": 1,
                        "passwordHash": hash_admin_password(new_password),
                        "updatedAt": datetime.now().isoformat(timespec="seconds"),
                    }
                    backup_and_write(ADMIN_SETTINGS_PATH, settings, ADMIN_SETTINGS_BACKUP_DIR)
                    self.send_json({"ok": True, "data": {"passwordConfigured": True}})
                    return
            self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_api_error(str(error))
        except OSError as error:
            self.send_api_error(f"保存文件失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self.read_request_json()
            with WRITE_LOCK:
                if route == "/api/question-reviews":
                    if not isinstance(payload, dict):
                        raise ValueError("题目审查请求必须是对象。")
                    question_id = payload.get("questionId")
                    if not isinstance(question_id, str) or not question_id.strip():
                        raise ValueError("题目审查请求缺少题目 id。")

                    question_bank = validate_questions(read_json(QUESTIONS_PATH))
                    question_ids = {question["id"] for question in question_bank["questions"]}
                    if question_id not in question_ids:
                        raise ValueError("找不到要审查的题目。")

                    current = validate_question_reviews(
                        read_json(QUESTION_REVIEWS_PATH, empty_question_reviews())
                    )
                    reviews = dict(current["reviews"])
                    reviews[question_id] = validate_question_review(payload.get("review"))
                    result = validate_question_reviews({"schemaVersion": 1, "reviews": reviews})
                    backup_and_write(QUESTION_REVIEWS_PATH, result)
                    self.send_json({"ok": True, "data": result})
                    return
                if route == "/api/answer-records":
                    if not isinstance(payload, dict):
                        raise ValueError("答题记录修改请求必须是对象。")
                    if "ids" in payload:
                        raw_ids = payload.get("ids")
                        if not isinstance(raw_ids, list) or not raw_ids:
                            raise ValueError("批量处理必须提供非空 ids 数组。")
                        if len(raw_ids) > 2000:
                            raise ValueError("一次最多批量处理 2000 条答题记录。")
                        record_ids: list[str] = []
                        for raw_id in raw_ids:
                            record_id = str(raw_id).strip()[:120]
                            if not record_id:
                                raise ValueError("批量处理包含空的答题记录 id。")
                            if record_id not in record_ids:
                                record_ids.append(record_id)
                        archived = payload.get("archived")
                        if not isinstance(archived, bool):
                            raise ValueError("批量处理必须提供 archived 布尔值。")
                        current = validate_answer_records(read_json(ANSWER_RECORDS_PATH, []))
                        record_id_set = set(record_ids)
                        matched_count = sum(item["id"] in record_id_set for item in current)
                        if matched_count == 0:
                            self.send_api_error("找不到要处理的答题记录。", HTTPStatus.NOT_FOUND)
                            return
                        archived_at = int(time.time() * 1000) if archived else 0
                        result = [
                            {
                                **item,
                                "archived": archived,
                                "archivedAt": archived_at if item["id"] in record_id_set else item["archivedAt"],
                            }
                            if item["id"] in record_id_set else item
                            for item in current
                        ]
                        result = validate_answer_records(result)
                        backup_and_write(ANSWER_RECORDS_PATH, result, ANSWER_RECORDS_BACKUP_DIR)
                        self.send_json({"ok": True, "data": result, "changedCount": matched_count})
                        return
                    record_id = str(payload.get("id", "")).strip()
                    if not record_id:
                        raise ValueError("答题记录修改请求缺少 id。")
                    current = validate_answer_records(read_json(ANSWER_RECORDS_PATH, []))
                    target = next((item for item in current if item["id"] == record_id), None)
                    if target is None:
                        self.send_api_error("找不到要修改的答题记录。", HTTPStatus.NOT_FOUND)
                        return
                    name = str(payload.get("name", "")).strip()[:20] or "未命名"
                    result = [{**item, "name": name} if item["id"] == record_id else item for item in current]
                    result = validate_answer_records(result)
                    backup_and_write(ANSWER_RECORDS_PATH, result, ANSWER_RECORDS_BACKUP_DIR)
                    self.send_json({"ok": True, "data": next(item for item in result if item["id"] == record_id)})
                    return
            self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_api_error(str(error))
        except (OSError, json.JSONDecodeError) as error:
            self.send_api_error(f"保存题目审查记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/answer-records":
            self.send_api_error("答题记录不支持删除，请使用折叠或恢复功能。", HTTPStatus.METHOD_NOT_ALLOWED)
            return
        self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)


def main() -> None:
    global HTTP_SERVER, UPDATE_MANAGER
    stop_previous_frozen_instances()
    parser = argparse.ArgumentParser(description="文言实词训练本地服务")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_leaderboard()
    if not ANSWER_RECORDS_PATH.exists():
        ANSWER_RECORDS_PATH.write_text("[]\n", encoding="utf-8")
    if not QUESTION_REVIEWS_PATH.exists():
        QUESTION_REVIEWS_PATH.write_text(
            json.dumps(empty_question_reviews(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def request_shutdown() -> None:
        if HTTP_SERVER is not None:
            HTTP_SERVER.shutdown()

    UPDATE_MANAGER = UpdateManager(
        root=ROOT,
        user_data_dir=USER_DATA_DIR,
        port=args.port,
        frozen=bool(getattr(sys, "frozen", False)),
        shutdown_callback=request_shutdown,
    )
    UPDATE_MANAGER.start_background()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), QuizRequestHandler)
    HTTP_SERVER = server
    student_url = f"http://127.0.0.1:{args.port}/"
    print(f"文言实词训练已启动：{student_url}")
    print(f"管理后台地址：http://127.0.0.1:{args.port}/admin.html")
    print("请保持此窗口打开；关闭此窗口即可停止服务。")
    if not args.no_browser:
        browser_timer = Timer(0.35, webbrowser.open, args=(student_url,))
        browser_timer.daemon = True
        browser_timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
