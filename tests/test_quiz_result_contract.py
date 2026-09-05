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

    def _make_pk_record(
        self,
        record_id: str = "pk-test-record",
        *,
        mode: str = "questions",
        archived: bool = False,
        player_one_ms: int = 1200,
        player_two_ms: int = 1800,
    ) -> dict:
        now = int(time.time() * 1000)
        question = self._make_http_record(f"{record_id}-source", False)["questions"][0]
        return server.validate_pk_record({
            "id": record_id,
            "matchId": f"{record_id}-match",
            "recordType": "pk",
            "startedAt": now - max(player_one_ms, player_two_ms),
            "finishedAt": now,
            "pkMode": mode,
            "timeLimitSeconds": 30 if mode == "time" else None,
            "questionLimit": 1 if mode == "questions" else None,
            "archived": archived,
            "players": [
                {
                    "playerId": "player1",
                    "score": 1,
                    "answeredCount": 1,
                    "correctCount": 1,
                    "wrongCount": 0,
                    "usedMilliseconds": player_one_ms,
                    "usedSeconds": player_one_ms // 1000,
                    "completed": mode == "questions",
                    "finishedAt": now - max(player_two_ms - player_one_ms, 0),
                    "questions": [{**question, "quizIndex": 0}],
                },
                {
                    "playerId": "player2",
                    "score": 0,
                    "answeredCount": 0,
                    "correctCount": 0,
                    "wrongCount": 0,
                    "usedMilliseconds": player_two_ms,
                    "usedSeconds": player_two_ms // 1000,
                    "completed": mode == "questions",
                    "finishedAt": now,
                    "questions": [],
                },
            ],
            "sharedQuestionIds": [question["id"]],
            "scoring": server.DEFAULT_SCORING_CONFIG,
            "context": {},
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

    def test_legacy_student_records_route_requires_admin(self) -> None:
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
                with self.assertRaises(HTTPError) as student_error:
                    urlopen(f"{base_url}/api/student-answer-records")
                self.assertEqual(student_error.exception.code, 401)

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

            finally:
                http_server.shutdown()
                http_server.server_close()
                thread.join(timeout=2)
                server.ANSWER_RECORDS_PATH = original_path
                server.ANSWER_RECORDS_BACKUP_DIR = original_backup_path

    def test_student_records_http_api_does_not_block_on_broken_history(self) -> None:
        original_path = server.ANSWER_RECORDS_PATH
        original_backup_path = server.ANSWER_RECORDS_BACKUP_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            answer_path = Path(temp_dir) / "answer-records.json"
            server.ANSWER_RECORDS_PATH = answer_path
            server.ANSWER_RECORDS_BACKUP_DIR = Path(temp_dir) / "backups"
            answer_path.write_text("not valid json", encoding="utf-8")
            http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.QuizRequestHandler)
            thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{http_server.server_address[1]}"
            try:
                with self.assertRaises(HTTPError) as student_error:
                    urlopen(f"{base_url}/api/student-answer-records")
                self.assertEqual(student_error.exception.code, 401)
            finally:
                http_server.shutdown()
                http_server.server_close()
                thread.join(timeout=2)
                server.ANSWER_RECORDS_PATH = original_path
                server.ANSWER_RECORDS_BACKUP_DIR = original_backup_path

    def test_pk_record_is_valid_and_old_records_remain_solo(self) -> None:
        now = int(time.time() * 1000)
        question = self._make_http_record("solo-compat", False)["questions"][0]
        pk_record = server.validate_pk_record({
            "id": "pk-match-1",
            "matchId": "match-1",
            "recordType": "pk",
            "startedAt": now - 3000,
            "finishedAt": now,
            "pkMode": "questions",
            "questionLimit": 1,
            "players": [
                {
                    "playerId": "player1",
                    "score": 1,
                    "answeredCount": 1,
                    "correctCount": 1,
                    "wrongCount": 0,
                    "usedMilliseconds": 1200,
                    "usedSeconds": 1,
                    "completed": True,
                    "finishedAt": now - 400,
                    "questions": [{**question, "quizIndex": 0}],
                },
                {
                    "playerId": "player2",
                    "score": -1,
                    "answeredCount": 1,
                    "correctCount": 0,
                    "wrongCount": 1,
                    "usedMilliseconds": 1800,
                    "usedSeconds": 1,
                    "completed": True,
                    "finishedAt": now,
                    "questions": [{**question, "selectedKey": "B", "quizIndex": 0}],
                },
            ],
            "sharedQuestionIds": [question["id"]],
            "scoring": server.DEFAULT_SCORING_CONFIG,
            "context": {},
        })
        self.assertEqual(pk_record["recordType"], "pk")
        self.assertEqual(pk_record["answeredCount"], 2)
        old_record = server.validate_answer_record({
            "id": "legacy-solo",
            "score": 0,
            "startedAt": now - 1000,
            "finishedAt": now,
            "usedSeconds": 1,
            "questions": [],
        })
        self.assertEqual(old_record["recordType"], "solo")
        self.assertEqual(server.validate_answer_records([old_record, pk_record])[1]["recordType"], "pk")

    def test_pk_record_keeps_millisecond_completion_time_without_winner_field(self) -> None:
        record = self._make_pk_record(player_one_ms=30200, player_two_ms=30700)

        self.assertEqual(record["recordType"], "pk")
        self.assertEqual(record["players"][0]["usedMilliseconds"], 30200)
        self.assertEqual(record["players"][1]["usedMilliseconds"], 30700)
        self.assertNotIn("winner", record)
        self.assertEqual(record["answeredCount"], 1)

    def test_pk_record_requires_distinct_player_ids(self) -> None:
        record = self._make_pk_record()
        record["players"][1]["playerId"] = "player1"

        with self.assertRaises(ValueError):
            server.validate_pk_record(record)

    def test_archived_pk_record_is_hidden_from_student_records(self) -> None:
        visible = self._make_pk_record("pk-visible", archived=False)
        archived = self._make_pk_record("pk-archived", archived=True)

        self.assertEqual(
            [record["id"] for record in server.filter_student_answer_records([visible, archived])],
            ["pk-visible"],
        )

    def test_pk_result_api_is_idempotent_and_does_not_touch_leaderboard(self) -> None:
        original_records_path = server.ANSWER_RECORDS_PATH
        original_records_backup_path = server.ANSWER_RECORDS_BACKUP_DIR
        original_leaderboard_path = server.LEADERBOARD_PATH
        original_leaderboard_backup_path = server.LEADERBOARD_BACKUP_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server.ANSWER_RECORDS_PATH = root / "answer-records.json"
            server.ANSWER_RECORDS_BACKUP_DIR = root / "record-backups"
            server.LEADERBOARD_PATH = root / "leaderboard.json"
            server.LEADERBOARD_BACKUP_DIR = root / "leaderboard-backups"
            server.ANSWER_RECORDS_PATH.write_text("[]", encoding="utf-8")
            server.LEADERBOARD_PATH.write_text("[]", encoding="utf-8")
            now = int(time.time() * 1000)
            question = self._make_http_record("pk-api", False)["questions"][0]
            record = {
                "id": "pk-api-record",
                "matchId": "pk-api-match",
                "recordType": "pk",
                "startedAt": now - 3000,
                "finishedAt": now,
                "pkMode": "time",
                "timeLimitSeconds": 30,
                "players": [
                    {"playerId": "player1", "score": 1, "answeredCount": 1, "correctCount": 1, "wrongCount": 0,
                     "usedMilliseconds": 3000, "usedSeconds": 3, "completed": False, "finishedAt": now,
                     "questions": [{**question, "quizIndex": 0}]},
                    {"playerId": "player2", "score": 0, "answeredCount": 0, "correctCount": 0, "wrongCount": 0,
                     "usedMilliseconds": 3000, "usedSeconds": 3, "completed": False, "finishedAt": now,
                     "questions": []},
                ],
                "sharedQuestionIds": [question["id"]],
                "scoring": server.DEFAULT_SCORING_CONFIG,
                "context": {},
            }
            http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.QuizRequestHandler)
            thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{http_server.server_address[1]}"
            try:
                body = json.dumps({"record": record}).encode("utf-8")
                for _ in range(2):
                    request = Request(
                        f"{base_url}/api/pk-results",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(payload["ok"])
                saved = json.loads(server.ANSWER_RECORDS_PATH.read_text(encoding="utf-8"))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["matchId"], "pk-api-match")
                self.assertEqual(json.loads(server.LEADERBOARD_PATH.read_text(encoding="utf-8")), [])
            finally:
                http_server.shutdown()
                http_server.server_close()
                thread.join(timeout=2)
                server.ANSWER_RECORDS_PATH = original_records_path
                server.ANSWER_RECORDS_BACKUP_DIR = original_records_backup_path
                server.LEADERBOARD_PATH = original_leaderboard_path
                server.LEADERBOARD_BACKUP_DIR = original_leaderboard_backup_path


if __name__ == "__main__":
    unittest.main()
