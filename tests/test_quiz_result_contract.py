from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402


class QuizResultContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
