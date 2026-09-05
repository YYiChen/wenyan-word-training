from __future__ import annotations

import sys
import time
import unittest
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402


class QuizResultContractTests(unittest.TestCase):
    def _make_http_record(self, record_id: str, archived: bool) -> dict:
        now = int(time.time() * 1000)
        return server.validate_answer_record({
            "id": record_id,
            "name": record_id,
            "score": 1,
            "startedAt": now - 1000,
            "finishedAt": now,
            "usedSeconds": 1,
            "archived": archived,
            "questions": [{
                "id": f"question-{record_id}",
                "number": 1,
                "article": "测试篇目",
                "volume": "测试册",
                "word": "利",
                "sentence": "金就砺则利。",
                "options": [
                    {"key": "A", "text": "锋利"},
                    {"key": "B", "text": "利益"},
                    {"key": "C", "text": "有利"},
                    {"key": "D", "text": "顺利"},
                ],
                "answer": "A",
                "selectedKey": "A",
                "explanation": "锋利。",
            }],
        })

    def test_empty_answer_record_is_valid_for_early_submission(self) -> None:
        now = int(time.time() * 1000)
        record = server.validate_answer_record({
            "id": "record-empty",
            "name": "未命名",
            "score": 0,
            "startedAt": now - 1000,
            "finishedAt": now,
            "usedSeconds": 1,
            "questions": [],
        })
        self.assertEqual(record["questions"], [])
        self.assertEqual(record["answeredCount"], 0)
        self.assertEqual(record["correctCount"], 0)

    def test_answered_question_keeps_quiz_order(self) -> None:
        question = {
            "id": "question-1",
            "number": 3,
            "article": "测试篇目",
            "volume": "测试册",
            "word": "利",
            "sentence": "金就砺则利。",
            "options": [
                {"key": "A", "text": "锋利"},
                {"key": "B", "text": "利益"},
                {"key": "C", "text": "有利"},
                {"key": "D", "text": "顺利"},
            ],
            "answer": "A",
            "selectedKey": "A",
            "isCorrect": True,
            "quizIndex": 7,
            "explanation": "锋利。",
        }
        record = server.validate_answer_record({
            "id": "record-ordered",
            "score": 1,
            "startedAt": 1000,
            "finishedAt": 2000,
            "usedSeconds": 1,
            "questions": [question],
        })
        self.assertEqual(record["questions"][0]["quizIndex"], 7)
        self.assertEqual(record["answeredCount"], 1)

    def test_leaderboard_ties_use_earlier_submission_first(self) -> None:
        entries = server.validate_leaderboard([
            {"id": "later", "name": "后提交", "score": 5, "createdAt": 200},
            {"id": "earlier", "name": "先提交", "score": 5, "createdAt": 100},
        ])
        self.assertEqual([item["id"] for item in entries], ["earlier", "later"])

    def test_leaderboard_context_is_normalized(self) -> None:
        entries = server.validate_leaderboard([{
            "id": "score-1",
            "name": "学生",
            "score": 8,
            "createdAt": 100,
            "context": {
                "volumes": [{"id": "b1", "label": "必修上册"}],
                "articles": [{"id": "a1", "label": "劝学"}],
                "durationSeconds": 120,
                "scoring": server.DEFAULT_SCORING_CONFIG,
            },
        }])
        self.assertEqual(entries[0]["context"]["volumes"][0]["label"], "必修上册")
        self.assertEqual(entries[0]["context"]["durationSeconds"], 120)

    def test_student_records_hide_archived_records_and_restore_them(self) -> None:
        records = [
            {"id": "A", "archived": False},
            {"id": "B", "archived": True},
            {"id": "C", "archived": False},
        ]
        self.assertEqual(
            [record["id"] for record in server.filter_student_answer_records(records)],
            ["A", "C"],
        )
        records[1]["archived"] = False
        self.assertEqual(
            [record["id"] for record in server.filter_student_answer_records(records)],
            ["A", "B", "C"],
        )

    def test_student_records_http_api_is_read_only_and_tracks_fold_state(self) -> None:
        original_path = server.ANSWER_RECORDS_PATH
        original_backup_path = server.ANSWER_RECORDS_BACKUP_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            answer_path = Path(temp_dir) / "answer-records.json"
            server.ANSWER_RECORDS_PATH = answer_path
            server.ANSWER_RECORDS_BACKUP_DIR = Path(temp_dir) / "backups"
            records = [
                self._make_http_record("visible-a", False),
                self._make_http_record("folded-b", True),
            ]
            answer_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.QuizRequestHandler)
            thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{http_server.server_address[1]}"
            try:
                with urlopen(f"{base_url}/api/student-answer-records") as response:
                    self.assertEqual(response.status, 200)
                    visible = json.loads(response.read().decode("utf-8"))
                self.assertEqual([record["id"] for record in visible], ["visible-a"])

                with self.assertRaises(HTTPError) as admin_error:
                    urlopen(f"{base_url}/api/answer-records")
                self.assertEqual(admin_error.exception.code, 401)

                request = Request(
                    f"{base_url}/api/student-answer-records",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as write_error:
                    urlopen(request)
                self.assertEqual(write_error.exception.code, 404)

                records[1]["archived"] = False
                answer_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
                with urlopen(f"{base_url}/api/student-answer-records") as response:
                    restored = json.loads(response.read().decode("utf-8"))
                self.assertEqual([record["id"] for record in restored], ["visible-a", "folded-b"])
            finally:
                http_server.shutdown()
                http_server.server_close()
                thread.join(timeout=2)
                server.ANSWER_RECORDS_PATH = original_path
                server.ANSWER_RECORDS_BACKUP_DIR = original_backup_path


if __name__ == "__main__":
    unittest.main()
