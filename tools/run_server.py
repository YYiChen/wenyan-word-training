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
from server_config import (
    ADMIN_SETTINGS_BACKUP_DIR,
    ADMIN_SETTINGS_PATH,
    ADMIN_SESSION_TTL_SECONDS,
    ANSWER_RECORDS_BACKUP_DIR,
    ANSWER_RECORDS_PATH,
    ANSWER_RECORD_MAX_COUNT,
    ANSWER_RECORD_RETENTION_DAYS,
    APP_NAME,
    APP_VERSION,
    BACKUP_DIR,
    BACKUP_MAX_COUNT,
    BACKUP_RETENTION_DAYS,
    DATA_DIR,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_SCORING_CONFIG,
    LEGACY_LEADERBOARD_PATH,
    LEADERBOARD_BACKUP_DIR,
    LEADERBOARD_PATH,
    MAX_DURATION_SECONDS,
    MAX_STREAK_THRESHOLD,
    MIN_DURATION_SECONDS,
    PID_PATH,
    PUBLIC_QUESTION_BANK_PATH,
    QUESTION_BANK_HISTORY_PATH,
    QUESTION_REVIEWS_PATH,
    QUESTIONS_PATH,
    ROOT,
    SUPER_ADMIN_PASSWORD_HASH,
    USER_DATA_DIR,
    VALID_DUPLICATE_REVIEW_STATUSES,
    VALID_OPTION_KEYS,
    VALID_QUESTION_BANK_HISTORY_KINDS,
    VALID_REVIEW_STATUSES,
    VALID_TYPES,
    get_user_data_dir,
)

from server_validators import (
    _validate_history_count,
    _validate_history_string_list,
    _validate_pk_player,
    _to_base36,
    empty_question_bank,
    empty_question_bank_history,
    empty_question_review,
    empty_question_reviews,
    find_word_occurrences,
    infer_review_status_after_underlining_fix,
    make_duplicate_group_id,
    make_json_etag,
    normalize_duplicate_reviews,
    normalize_identity_text,
    prune_answer_records,
    question_bank_history_view,
    question_core_signature,
    question_detail_signature,
    question_target_occurrence,
    strip_system_review_notes,
    validate_answer_record,
    validate_answer_record_question,
    validate_answer_records,
    validate_answer_records_import,
    validate_duration_seconds,
    validate_leaderboard,
    validate_leaderboard_context,
    validate_pk_record,
    validate_question_bank_history,
    validate_question_review,
    validate_question_reviews,
    validate_questions,
    validate_scoring_config,
)
from server_storage import read_json, prune_backups as _prune_backups, backup_and_write as _backup_and_write
from server_auth import (
    ADMIN_SESSIONS,
    ADMIN_SESSION_LOCK,
    authenticate_admin_password as _authenticate_admin_password,
    create_admin_session,
    hash_admin_password,
    is_valid_admin_session,
    revoke_admin_session,
    revoke_all_admin_sessions,
    validate_admin_password,
    read_admin_password_hash as _read_admin_password_hash,
)
from server_questions import (
    apply_question_review_publication_status,
    configure_paths as _configure_question_services,
    ensure_question_bank as _ensure_question_bank,
    ensure_question_bank_history as _ensure_question_bank_history,
    ensure_question_reviews as _ensure_question_reviews,
    append_question_bank_history_event as _append_question_bank_history_event,
    revoke_question_bank_import as _revoke_question_bank_import,
    sync_question_reviews_after_bank_write as _sync_question_reviews_after_bank_write,
)
from server_records import (
    configure_paths as _configure_record_services,
    ensure_answer_records as _ensure_answer_records,
    ensure_leaderboard as _ensure_leaderboard,
    filter_student_answer_records,
    load_answer_records as _load_answer_records,
)

WRITE_LOCK = Lock()
UPDATE_MANAGER: UpdateManager | None = None
HTTP_SERVER: ThreadingHTTPServer | None = None


def read_admin_password_hash() -> str:
    return _read_admin_password_hash(ADMIN_SETTINGS_PATH)


def authenticate_admin_password(password: Any) -> bool:
    return _authenticate_admin_password(password, ADMIN_SETTINGS_PATH)


def backup_and_write(path: Path, payload: Any, backup_dir: Path | None = None) -> None:
    """Compatibility wrapper with the historic dynamic default path."""
    _backup_and_write(path, payload, BACKUP_DIR if backup_dir is None else backup_dir)


def prune_backups(path: Path, backup_dir: Path) -> None:
    _prune_backups(path, backup_dir)


def _prepare_question_services() -> None:
    _configure_question_services(
        questions_path=QUESTIONS_PATH,
        public_question_bank_path=PUBLIC_QUESTION_BANK_PATH,
        question_reviews_path=QUESTION_REVIEWS_PATH,
        question_bank_history_path=QUESTION_BANK_HISTORY_PATH,
        backup_writer=backup_and_write,
    )


def sync_question_reviews_after_bank_write(
    previous_bank: dict[str, Any],
    next_bank: dict[str, Any],
) -> None:
    _prepare_question_services()
    _sync_question_reviews_after_bank_write(previous_bank, next_bank)


def ensure_question_reviews() -> None:
    _prepare_question_services()
    _ensure_question_reviews()


def ensure_question_bank_history() -> None:
    _prepare_question_services()
    _ensure_question_bank_history()


def append_question_bank_history_event(event: dict[str, Any]) -> dict[str, Any]:
    _prepare_question_services()
    return _append_question_bank_history_event(event)


def revoke_question_bank_import(event_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _prepare_question_services()
    return _revoke_question_bank_import(event_id)


def ensure_question_bank() -> None:
    _prepare_question_services()
    _ensure_question_bank()


def _prepare_record_services() -> None:
    _configure_record_services(
        answer_records_path=ANSWER_RECORDS_PATH,
        answer_records_backup_dir=ANSWER_RECORDS_BACKUP_DIR,
        leaderboard_path=LEADERBOARD_PATH,
        leaderboard_backup_dir=LEADERBOARD_BACKUP_DIR,
        legacy_leaderboard_path=LEGACY_LEADERBOARD_PATH,
        backup_writer=backup_and_write,
    )


def load_answer_records(persist_pruned: bool = False) -> list[dict[str, Any]]:
    _prepare_record_services()
    return _load_answer_records(persist_pruned)


def ensure_answer_records() -> None:
    _prepare_record_services()
    _ensure_answer_records()


def ensure_leaderboard() -> None:
    _prepare_record_services()
    _ensure_leaderboard()




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
