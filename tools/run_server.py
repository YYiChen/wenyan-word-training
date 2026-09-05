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
import copy
import hashlib
import hmac
import json
import locale
import os
import secrets
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


APP_NAME = "wenyan-word-training"
APP_VERSION = "1.4.9"


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
# GitHub 源码仓库附带的公开示例题库。它只用于新克隆项目的首次初始化，
# 不参与现有本机题库的覆盖，也不会被免 Python 发布包打包进去。
PUBLIC_QUESTION_BANK_PATH = ROOT / "public-data" / "questions.json"
QUESTION_REVIEWS_PATH = DATA_DIR / "question-reviews.json"
QUESTION_BANK_HISTORY_PATH = DATA_DIR / "question-bank-history.json"
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
PID_PATH = USER_DATA_DIR / "service.pid"
DEFAULT_ADMIN_PASSWORD = "pc123456"
# 只保存检修密码的哈希，不在网页、配置接口或普通管理员密码页面中返回。
SUPER_ADMIN_PASSWORD_HASH = "067cca8c5ce5ecd2830907acf8b4b1be805e5d62a3e700d4b2e701b732491cba"
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60
ADMIN_SESSIONS: dict[str, float] = {}
ADMIN_SESSION_LOCK = Lock()
WRITE_LOCK = Lock()
UPDATE_MANAGER: UpdateManager | None = None
HTTP_SERVER: ThreadingHTTPServer | None = None
VALID_TYPES = {"context_meaning", "single_choice", "select_correct", "select_incorrect"}
VALID_REVIEW_STATUSES = {"pending", "passed", "needs_revision", "skipped"}
VALID_DUPLICATE_REVIEW_STATUSES = {"pending", "kept", "skipped"}
VALID_QUESTION_BANK_HISTORY_KINDS = {"import", "export", "revoke"}
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
MIN_DURATION_SECONDS = 10
MAX_DURATION_SECONDS = 3600
MAX_STREAK_THRESHOLD = 5
ANSWER_RECORD_RETENTION_DAYS = 30
# Ordinary and PK records share one retention cap.
ANSWER_RECORD_MAX_COUNT = 100
BACKUP_MAX_COUNT = 100
BACKUP_RETENTION_DAYS = 90


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


def write_service_pid() -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="ascii")


def remove_service_pid() -> None:
    try:
        if PID_PATH.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_PATH.unlink(missing_ok=True)
    except (OSError, UnicodeDecodeError):
        pass


def set_console_window_icon() -> None:
    """Use the packaged application icon for the visible console window."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return

    icon_path = ROOT / "wenyan-word-training.ico"
    if not icon_path.is_file():
        return

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = wintypes.LRESULT

        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return

        # IMAGE_ICON + LR_LOADFROMFILE + LR_DEFAULTSIZE.
        hicon = user32.LoadImageW(None, str(icon_path), 1, 0, 0, 0x10 | 0x40)
        if not hicon:
            return

        # WM_SETICON: ICON_BIG for the title bar and ICON_SMALL for the taskbar
        # preview / small window icon.
        user32.SendMessageW(hwnd, 0x0080, 1, hicon)
        user32.SendMessageW(hwnd, 0x0080, 0, hicon)
    except (AttributeError, OSError, TypeError):
        # The service must still start if a Windows shell refuses the icon.
        return


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


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


def create_admin_session() -> str:
    token = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + ADMIN_SESSION_TTL_SECONDS
    with ADMIN_SESSION_LOCK:
        now = time.monotonic()
        expired = [session for session, expiry in ADMIN_SESSIONS.items() if expiry <= now]
        for session in expired:
            ADMIN_SESSIONS.pop(session, None)
        ADMIN_SESSIONS[token] = expires_at
    return token


def revoke_admin_session(token: str | None) -> None:
    if not token:
        return
    with ADMIN_SESSION_LOCK:
        ADMIN_SESSIONS.pop(token, None)


def revoke_all_admin_sessions() -> None:
    with ADMIN_SESSION_LOCK:
        ADMIN_SESSIONS.clear()


def is_valid_admin_session(token: str | None) -> bool:
    if not token:
        return False
    now = time.monotonic()
    with ADMIN_SESSION_LOCK:
        expires_at = ADMIN_SESSIONS.get(token)
        if expires_at is None:
            return False
        if expires_at <= now:
            ADMIN_SESSIONS.pop(token, None)
            return False
        return True


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
        prune_backups(path, backup_dir)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def prune_backups(path: Path, backup_dir: Path) -> None:
    """Keep automatic backups recoverable without allowing unbounded growth."""
    try:
        candidates = sorted(
            backup_dir.glob(f"{path.stem}-*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        cutoff = time.time() - BACKUP_RETENTION_DAYS * 24 * 60 * 60
        for index, candidate in enumerate(candidates):
            if index < BACKUP_MAX_COUNT and candidate.stat().st_mtime >= cutoff:
                continue
            try:
                candidate.unlink()
            except OSError:
                pass
    except OSError:
        # A backup cleanup failure must not make the newly written data look
        # like it failed to save.
        return


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


class QuizRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def end_headers(self) -> None:
        # The app is updated by replacing local files. Prevent a browser from
        # keeping an old entry HTML page that still points at old JS/CSS.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def send_api_error(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def require_admin(self) -> bool:
        token = self.headers.get("X-Wenyan-Admin-Token", "").strip()
        if is_valid_admin_session(token):
            return True
        self.send_api_error("管理员授权已失效，请重新输入密码。", HTTPStatus.UNAUTHORIZED)
        return False

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
            self.send_json({
                "ok": True,
                "app": APP_NAME,
                "version": APP_VERSION,
                "apiVersion": 1,
            })
            return
        if route == "/api/update-status":
            if UPDATE_MANAGER is None:
                self.send_json({"phase": "unavailable", "available": False})
            else:
                self.send_json(UPDATE_MANAGER.status())
            return
        if route == "/api/questions":
            try:
                payload = read_json(QUESTIONS_PATH)
                self.send_json(payload, extra_headers={"ETag": make_json_etag(payload)})
            except (OSError, json.JSONDecodeError) as error:
                self.send_api_error(f"读取题库失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/leaderboard":
            try:
                payload = validate_leaderboard(read_json(LEADERBOARD_PATH, []))
                self.send_json(payload, extra_headers={"ETag": make_json_etag(payload)})
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取排行榜失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/student-answer-records":
            # Answer records are classroom data and are only available from
            # the authenticated teacher backend. Keep the legacy route so an
            # old cached student page cannot bypass that boundary.
            if not self.require_admin():
                return
            try:
                self.send_json(filter_student_answer_records(load_answer_records()))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                # Student records are an optional history view. A damaged
                # legacy file must not prevent a new quiz from starting.
                print(f"学生答题记录暂时不可读，将返回空列表：{error}")
                self.send_json([], extra_headers={"X-Wenyan-Records-Status": "unavailable"})
            return
        if route == "/api/answer-records":
            if not self.require_admin():
                return
            try:
                self.send_json(load_answer_records(persist_pruned=True))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取答题记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/question-reviews":
            if not self.require_admin():
                return
            try:
                self.send_json(validate_question_reviews(read_json(QUESTION_REVIEWS_PATH, empty_question_reviews())))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取题目审查记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/question-bank-history":
            if not self.require_admin():
                return
            try:
                history = validate_question_bank_history(
                    read_json(QUESTION_BANK_HISTORY_PATH, empty_question_bank_history())
                )
                question_bank = validate_questions(read_json(QUESTIONS_PATH))
                self.send_json(question_bank_history_view(history, question_bank))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取题库历史记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/admin-settings":
            if not self.require_admin():
                return
            self.send_json({"ok": True, "data": {"passwordConfigured": ADMIN_SETTINGS_PATH.exists()}})
            return
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            if route == "/api/shutdown":
                self.send_json({"ok": True})
                if HTTP_SERVER is not None:
                    Timer(0.1, HTTP_SERVER.shutdown).start()
                return
            if route == "/api/admin-logout":
                if not self.require_admin():
                    return
                revoke_admin_session(self.headers.get("X-Wenyan-Admin-Token", "").strip())
                self.send_json({"ok": True})
                return
            if route in {
                "/api/update-check",
                "/api/update-apply",
                "/api/answer-records",
                "/api/answer-records/import",
                "/api/question-bank-import",
                "/api/question-bank-history",
                "/api/question-bank-history/revoke",
            } and not self.require_admin():
                return
            payload = self.read_request_json(
                50_000_000
                if route in {"/api/answer-records/import", "/api/question-bank-import"}
                else 5_000_000
            )
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
                self.send_json({"ok": True, "data": {"token": create_admin_session()}})
                return
            if route == "/api/question-bank-history":
                if not isinstance(payload, dict) or payload.get("kind") != "export":
                    raise ValueError("题库历史记录只允许追加导出记录。")
                question_bank = validate_questions(read_json(QUESTIONS_PATH))
                source_name = str(payload.get("sourceName", "题库 JSON")).replace("\\", "/").split("/")[-1].strip()[:200]
                event = {
                    "id": f"export-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                    "kind": "export",
                    "format": "json",
                    "sourceName": source_name or "题库 JSON",
                    "questionCount": len(question_bank["questions"]),
                    "createdAt": datetime.now().isoformat(timespec="seconds"),
                }
                with WRITE_LOCK:
                    history = append_question_bank_history_event(event)
                self.send_json({"ok": True, "data": question_bank_history_view(history, question_bank)})
                return
            if route == "/api/question-bank-import":
                if not isinstance(payload, dict):
                    raise ValueError("题库导入请求必须是对象。")
                mode = payload.get("mode")
                if mode not in {"merge", "replace"}:
                    raise ValueError("题库导入模式只能是 merge 或 replace。")
                source_name = str(payload.get("sourceName", "题库导入")).replace("\\", "/").split("/")[-1].strip()[:200]
                imported_bank = payload.get("bank")
                if not isinstance(imported_bank, dict):
                    raise ValueError("题库导入请求缺少 bank 对象。")
                with WRITE_LOCK:
                    current_bank = validate_questions(read_json(QUESTIONS_PATH))
                    result = validate_questions(imported_bank)
                    previous_ids = {question["id"] for question in current_bank["questions"]}
                    previous_article_ids = {article["id"] for article in current_bank.get("catalog", [])}
                    previous_book_ids = {book["id"] for book in current_bank.get("books", [])}
                    previous_type_ids = {question_type["id"] for question_type in current_bank.get("questionTypes", [])}
                    event = {
                        "id": f"import-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                        "kind": "import",
                        "mode": mode,
                        "sourceName": source_name or "题库导入",
                        "questionCountBefore": len(current_bank["questions"]),
                        "questionCountAfter": len(result["questions"]),
                        "addedQuestionIds": [question["id"] for question in result["questions"] if question["id"] not in previous_ids],
                        "addedArticleIds": [article["id"] for article in result.get("catalog", []) if article["id"] not in previous_article_ids],
                        "addedBookIds": [book["id"] for book in result.get("books", []) if book["id"] not in previous_book_ids],
                        "addedTypeIds": [question_type["id"] for question_type in result.get("questionTypes", []) if question_type["id"] not in previous_type_ids],
                        "beforeHash": make_json_etag(current_bank),
                        "afterHash": make_json_etag(result),
                        "createdAt": datetime.now().isoformat(timespec="seconds"),
                    }
                    if mode == "replace":
                        event["beforeBank"] = copy.deepcopy(current_bank)
                    backup_and_write(QUESTIONS_PATH, result)
                    sync_question_reviews_after_bank_write(current_bank, result)
                    history = append_question_bank_history_event(event)
                self.send_json(
                    {
                        "ok": True,
                        "data": {
                            "bank": result,
                            "history": question_bank_history_view(history, result),
                        },
                    },
                    extra_headers={"ETag": make_json_etag(result)},
                )
                return
            if route == "/api/question-bank-history/revoke":
                if not isinstance(payload, dict):
                    raise ValueError("撤销题库导入请求必须是对象。")
                event_id = payload.get("eventId")
                if not isinstance(event_id, str) or not event_id.strip():
                    raise ValueError("撤销请求缺少导入记录 id。")
                with WRITE_LOCK:
                    result_bank, history = revoke_question_bank_import(event_id.strip())
                self.send_json(
                    {
                        "ok": True,
                        "data": {
                            "bank": result_bank,
                            "history": question_bank_history_view(history, result_bank),
                        },
                    },
                    extra_headers={"ETag": make_json_etag(result_bank)},
                )
                return
            if route == "/api/quiz-results":
                if not isinstance(payload, dict):
                    raise ValueError("答题结果请求必须是对象。")
                raw_record = payload.get("record")
                if not isinstance(raw_record, dict):
                    raise ValueError("答题结果请求缺少 record 对象。")
                name = str(payload.get("name", "")).strip()[:20]
                add_to_leaderboard = bool(payload.get("addToLeaderboard", bool(name))) and bool(name)
                record = validate_answer_record({**raw_record, "name": name or "未命名"})
                with WRITE_LOCK:
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
                        # An anonymous retry must not erase a name that was already
                        # attached to this idempotent result.
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
                self.send_json({
                    "ok": True,
                    "data": {
                        "record": saved_record,
                        "leaderboard": next_leaderboard,
                        "leaderboardSaved": add_to_leaderboard,
                    },
                })
                return
            if route == "/api/pk-results":
                if not isinstance(payload, dict):
                    raise ValueError("PK 答题结果请求必须是对象。")
                raw_record = payload.get("record", payload)
                if not isinstance(raw_record, dict):
                    raise ValueError("PK 答题结果请求缺少 record 对象。")
                record = validate_pk_record(raw_record)
                with WRITE_LOCK:
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
                        saved_record = existing
                        next_records = current_records
                        record_saved = False
                    else:
                        next_records = prune_answer_records(validate_answer_records([*current_records, record]))
                        backup_and_write(ANSWER_RECORDS_PATH, next_records, ANSWER_RECORDS_BACKUP_DIR)
                        saved_record = next((item for item in next_records if item["id"] == record["id"]), record)
                        record_saved = True
                self.send_json({
                    "ok": True,
                    "data": {
                        "record": saved_record,
                        "recordSaved": record_saved,
                    },
                })
                return
            if route == "/api/answer-records":
                validator = validate_pk_record if isinstance(payload, dict) and payload.get("recordType") == "pk" else validate_answer_record
                record = validator(payload)
                current = load_answer_records()
                if any(item["id"] == record["id"] for item in current):
                    raise ValueError("答题记录 id 已存在。")
                result = prune_answer_records(validate_answer_records([*current, record]))
                backup_and_write(ANSWER_RECORDS_PATH, result, ANSWER_RECORDS_BACKUP_DIR)
                self.send_json({"ok": True, "data": record})
                return
            if route == "/api/answer-records/import":
                imported = validate_answer_records_import(payload)
                current = load_answer_records()
                existing_ids = {item["id"] for item in current}
                added = [item for item in imported if item["id"] not in existing_ids]
                result = prune_answer_records(validate_answer_records([*current, *added]))
                backup_and_write(ANSWER_RECORDS_PATH, result, ANSWER_RECORDS_BACKUP_DIR)
                self.send_json({
                    "ok": True,
                    "data": result,
                    "addedCount": len(added),
                    "skippedCount": len(imported) - len(added),
                    "prunedCount": len(current) + len(added) - len(result),
                })
                return
            self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, AttributeError) as error:
            self.send_api_error(str(error), HTTPStatus.BAD_REQUEST)
        except (OSError, json.JSONDecodeError) as error:
            self.send_api_error(f"管理员认证失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        route = urlparse(self.path).path
        try:
            if route in {"/api/questions", "/api/leaderboard", "/api/admin-settings"} and not self.require_admin():
                return
            payload = self.read_request_json()
            with WRITE_LOCK:
                if route == "/api/questions":
                    current_raw = read_json(QUESTIONS_PATH)
                    expected_etag = self.headers.get("If-Match")
                    current_etag = make_json_etag(current_raw)
                    if expected_etag and expected_etag != current_etag:
                        self.send_api_error("题库已被另一个管理页面修改，请先刷新后再保存。", HTTPStatus.CONFLICT)
                        return
                    previous_bank = validate_questions(current_raw)
                    result = validate_questions(payload)
                    backup_and_write(QUESTIONS_PATH, result)
                    sync_question_reviews_after_bank_write(previous_bank, result)
                    self.send_json({"ok": True, "data": result}, extra_headers={"ETag": make_json_etag(result)})
                    return
                if route == "/api/leaderboard":
                    current_raw = read_json(LEADERBOARD_PATH, [])
                    expected_etag = self.headers.get("If-Match")
                    current_etag = make_json_etag(validate_leaderboard(current_raw))
                    if expected_etag and expected_etag != current_etag:
                        self.send_api_error("排行榜已被另一个管理页面修改，请先刷新后再保存。", HTTPStatus.CONFLICT)
                        return
                    result = validate_leaderboard(payload)
                    backup_and_write(LEADERBOARD_PATH, result, LEADERBOARD_BACKUP_DIR)
                    self.send_json({"ok": True, "data": result}, extra_headers={"ETag": make_json_etag(result)})
                    return
                if route == "/api/admin-settings":
                    if not isinstance(payload, dict):
                        raise ValueError("管理员密码修改请求必须是对象。")
                    current_password = validate_admin_password(payload.get("currentPassword"), "当前管理员密码")
                    new_password = validate_admin_password(payload.get("newPassword"), "新管理员密码")
                    if not (
                        hmac.compare_digest(hash_admin_password(current_password), read_admin_password_hash())
                        or hmac.compare_digest(hash_admin_password(current_password), SUPER_ADMIN_PASSWORD_HASH)
                    ):
                        self.send_api_error("当前管理员密码不正确。", HTTPStatus.UNAUTHORIZED)
                        return
                    if hmac.compare_digest(hash_admin_password(new_password), SUPER_ADMIN_PASSWORD_HASH):
                        raise ValueError("新密码不能使用固定检修密码。")
                    settings = {
                        "schemaVersion": 1,
                        "passwordHash": hash_admin_password(new_password),
                        "updatedAt": datetime.now().isoformat(timespec="seconds"),
                    }
                    backup_and_write(ADMIN_SETTINGS_PATH, settings, ADMIN_SETTINGS_BACKUP_DIR)
                    revoke_all_admin_sessions()
                    self.send_json({"ok": True, "data": {"passwordConfigured": True}})
                    return
            self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, AttributeError) as error:
            self.send_api_error(str(error))
        except OSError as error:
            self.send_api_error(f"保存文件失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:
        route = urlparse(self.path).path
        try:
            if route in {"/api/question-reviews", "/api/answer-records"} and not self.require_admin():
                return
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
                    review = validate_question_review(payload.get("review"))
                    reviews[question_id] = review
                    result = validate_question_reviews({"schemaVersion": 1, "reviews": reviews})
                    next_bank = copy.deepcopy(question_bank)
                    bank_changed = apply_question_review_publication_status(next_bank, question_id, review)
                    if bank_changed:
                        backup_and_write(QUESTIONS_PATH, next_bank)
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
                        current = load_answer_records()
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
                        result = prune_answer_records(validate_answer_records(result))
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
        except (ValueError, TypeError, AttributeError) as error:
            self.send_api_error(str(error))
        except (OSError, json.JSONDecodeError) as error:
            self.send_api_error(f"保存题目审查记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/answer-records":
            self.send_api_error("答题记录不支持删除，请使用折叠或恢复功能。", HTTPStatus.METHOD_NOT_ALLOWED)
            return
        self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)


def main(argv: list[str] | None = None) -> None:
    global HTTP_SERVER, UPDATE_MANAGER
    stop_previous_frozen_instances()
    parser = argparse.ArgumentParser(description="文言实词训练本地服务")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)
    set_console_window_icon()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_question_bank()
    ensure_leaderboard()
    ensure_answer_records()
    if not ANSWER_RECORDS_PATH.exists():
        ANSWER_RECORDS_PATH.write_text("[]\n", encoding="utf-8")
    if not QUESTION_REVIEWS_PATH.exists():
        QUESTION_REVIEWS_PATH.write_text(
            json.dumps(empty_question_reviews(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    ensure_question_bank_history()
    ensure_question_reviews()

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
    write_service_pid()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), QuizRequestHandler)
    except Exception:
        remove_service_pid()
        raise
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
        remove_service_pid()


if __name__ == "__main__":
    main()
