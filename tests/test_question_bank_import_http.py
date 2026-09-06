from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402
import server_auth as auth  # noqa: E402


class QuestionBankImportHttpTests(unittest.TestCase):
    def request_json(self, base_url: str, method: str, route: str, payload: object | None = None, token: str = "") -> tuple[int, dict, dict[str, str]]:
        request = Request(
            f"{base_url}{route}",
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"X-Wenyan-Admin-Token": token} if token else {}),
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers)
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8")), dict(error.headers)

    def test_preview_apply_export_and_etag_conflict(self) -> None:
        bank = server.empty_question_bank()
        bank.update({
            "questionTypes": [{"id": "context_meaning", "label": "语境释义题", "description": "语境释义"}],
            "books": [{"id": "book-1", "label": "必修上册", "order": 1}],
            "catalog": [{"id": "article-1", "bookId": "book-1", "unit": "一", "title": "劝学", "author": "荀子"}],
        })
        bank = server.validate_question_bank_v4(bank)
        package = {
            "format": "wenyan-question-import",
            "schemaVersion": "1.0",
            "questionTypes": bank["questionTypes"],
            "books": bank["books"],
            "catalog": bank["catalog"],
            "questions": [{
                "number": 1,
                "type": "context_meaning",
                "articleId": "article-1",
                "word": "利",
                "sentence": "金就砺则利。",
                "targetOccurrence": 1,
                "options": [
                    {"key": "A", "text": "锋利"}, {"key": "B", "text": "利益"},
                    {"key": "C", "text": "有利"}, {"key": "D", "text": "顺利"},
                ],
                "answer": "A",
                "explanation": "利：锋利。",
            }],
        }

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            question_path = temp_root / "questions.json"
            history_path = temp_root / "question-bank-history.json"
            backup_dir = temp_root / "backups"
            question_path.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
            history_path.write_text(json.dumps(server.empty_question_bank_history(), ensure_ascii=False), encoding="utf-8")

            with patch.object(server, "QUESTIONS_PATH", question_path), \
                patch.object(server, "QUESTION_BANK_HISTORY_PATH", history_path), \
                patch.object(server, "BACKUP_DIR", backup_dir):
                http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.QuizRequestHandler)
                thread = threading.Thread(target=http_server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://127.0.0.1:{http_server.server_address[1]}"
                token = auth.create_admin_session()
                original_question_text = question_path.read_text(encoding="utf-8")
                try:
                    status, preview_payload, _headers = self.request_json(
                        base_url,
                        "POST",
                        "/api/question-bank-import/preview",
                        {"mode": "merge", "sourceName": "新增题.json", "package": package},
                        token,
                    )
                    self.assertEqual(status, 200)
                    preview = preview_payload["data"]
                    self.assertEqual(preview["summary"]["newQuestions"], 1)
                    self.assertEqual(question_path.read_text(encoding="utf-8"), original_question_text)
                    self.assertEqual(json.loads(history_path.read_text(encoding="utf-8"))["events"], [])

                    status, applied_payload, _headers = self.request_json(
                        base_url,
                        "POST",
                        "/api/question-bank-import/apply",
                        {
                            "mode": "merge",
                            "strategy": "preserve_local",
                            "sourceName": "新增题.json",
                            "package": package,
                            "baseEtag": preview["baseEtag"],
                        },
                        token,
                    )
                    self.assertEqual(status, 200)
                    saved = applied_payload["data"]["bank"]
                    self.assertEqual(len(saved["questions"]), 1)
                    self.assertNotIn("workflow", server.student_question_bank_view(saved))
                    self.assertEqual(len(json.loads(history_path.read_text(encoding="utf-8")).get("events", [])), 1)

                    status, _stale_payload, _headers = self.request_json(
                        base_url,
                        "POST",
                        "/api/question-bank-import/apply",
                        {
                            "mode": "merge",
                            "strategy": "preserve_local",
                            "sourceName": "重复应用.json",
                            "package": package,
                            "baseEtag": preview["baseEtag"],
                        },
                        token,
                    )
                    self.assertEqual(status, 409)

                    status, export_payload, _headers = self.request_json(
                        base_url,
                        "POST",
                        "/api/question-bank-export",
                        {"sourceName": "完整题库.json"},
                        token,
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(export_payload["data"]["bank"]["format"], "wenyan-question-bank")
                    self.assertIn("workflow", export_payload["data"]["bank"])
                finally:
                    auth.revoke_admin_session(token)
                    http_server.shutdown()
                    http_server.server_close()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
