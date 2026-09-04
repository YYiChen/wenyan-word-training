"""Local file-backed server for the quiz and its administration page.

It deliberately binds to 127.0.0.1 only.  The quiz and admin pages run in a
browser, while the small API makes approved administrator changes persistent
in local JSON files rather than in browser storage. The question bank stays
with the application, while the leaderboard is stored in the Windows user
data folder.
"""

from __future__ import annotations

import argparse
import csv
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


# 开发时，网页与题库数据都在项目根目录；封装后，网页资源在 PyInstaller
# 内部目录，题库和审查记录在 EXE 旁边的 data 文件夹。
# 排行榜使用稳定的 Windows 用户数据目录，避免升级压缩包时被覆盖。
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
WRITE_LOCK = Lock()
VALID_TYPES = {"context_meaning", "single_choice", "select_correct", "select_incorrect"}
VALID_REVIEW_STATUSES = {"pending", "passed", "needs_revision", "skipped"}
VALID_OPTION_KEYS = {"A", "B", "C", "D"}


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


def validate_questions(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("题库必须包含 questions 数组。")
    if not payload["questions"]:
        raise ValueError("题库不能没有题目。")

    catalog_ids = {
        item.get("id")
        for item in payload.get("catalog", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    seen_ids: set[str] = set()
    for position, question in enumerate(payload["questions"], start=1):
        if not isinstance(question, dict):
            raise ValueError(f"第 {position} 题不是对象。")
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip() or question_id in seen_ids:
            raise ValueError(f"第 {position} 题的 id 缺失或重复。")
        seen_ids.add(question_id)
        if question.get("type") not in VALID_TYPES:
            raise ValueError(f"第 {position} 题的题型不受支持。")
        for field in ("articleId", "article", "volume", "word", "sentence", "explanation"):
            if not isinstance(question.get(field), str) or not question[field].strip():
                raise ValueError(f"第 {position} 题的 {field} 不能为空。")
        if catalog_ids and question["articleId"] not in catalog_ids:
            raise ValueError(f"第 {position} 题的篇目不存在于教材目录。")
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

    def read_request_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("请求长度无效。") from error
        if length <= 0 or length > 5_000_000:
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
        if route == "/api/question-reviews":
            try:
                self.send_json(validate_question_reviews(read_json(QUESTION_REVIEWS_PATH, empty_question_reviews())))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self.send_api_error(f"读取题目审查记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        super().do_GET()

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
            self.send_api_error("未找到这个管理接口。", HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_api_error(str(error))
        except (OSError, json.JSONDecodeError) as error:
            self.send_api_error(f"保存题目审查记录失败：{error}", HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    stop_previous_frozen_instances()
    parser = argparse.ArgumentParser(description="文言实词训练本地服务")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_leaderboard()
    if not QUESTION_REVIEWS_PATH.exists():
        QUESTION_REVIEWS_PATH.write_text(
            json.dumps(empty_question_reviews(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    server = ThreadingHTTPServer(("127.0.0.1", args.port), QuizRequestHandler)
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
