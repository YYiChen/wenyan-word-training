from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from run_server import (  # noqa: E402
    ANSWER_RECORD_MAX_COUNT,
    ANSWER_RECORD_RETENTION_DAYS,
    prune_answer_records,
    validate_answer_records,
    validate_questions,
    validate_scoring_config,
    validate_duration_seconds,
)


def make_record(record_id: str, finished_at: int) -> dict:
    return {
        "id": record_id,
        "name": "测试",
        "score": 1,
        "startedAt": finished_at - 1000,
        "finishedAt": finished_at,
        "usedSeconds": 1,
        "completedAll": True,
        "questions": [
            {
                "id": "question-1",
                "number": 1,
                "article": "测试篇目",
                "volume": "测试册",
                "word": "实",
                "sentence": "实有其名",
                "options": [
                    {"key": "A", "text": "真实"},
                    {"key": "B", "text": "果实"},
                    {"key": "C", "text": "实际"},
                    {"key": "D", "text": "充实"},
                ],
                "answer": "A",
                "selectedKey": "A",
                "explanation": "测试解析",
            }
        ],
    }


class AnswerRecordRetentionTests(unittest.TestCase):
    def test_keeps_recent_records_and_caps_count(self) -> None:
        now_ms = 1_800_000_000_000
        records = [make_record(f"recent-{index}", now_ms - index * 1000) for index in range(105)]
        records.append(
            make_record(
                "old-record",
                now_ms - (ANSWER_RECORD_RETENTION_DAYS * 24 * 60 * 60 * 1000) - 1,
            )
        )

        retained = prune_answer_records(validate_answer_records(records), now_ms=now_ms)

        self.assertEqual(len(retained), ANSWER_RECORD_MAX_COUNT)
        self.assertEqual(retained[0]["id"], "recent-0")
        self.assertEqual(retained[-1]["id"], "recent-99")
        self.assertNotIn("old-record", {record["id"] for record in retained})

    def test_record_at_cutoff_is_still_recent(self) -> None:
        now_ms = 1_800_000_000_000
        cutoff_record = make_record(
            "cutoff-record",
            now_ms - ANSWER_RECORD_RETENTION_DAYS * 24 * 60 * 60 * 1000,
        )

        retained = prune_answer_records(validate_answer_records([cutoff_record]), now_ms=now_ms)

        self.assertEqual([record["id"] for record in retained], ["cutoff-record"])

    def test_folded_and_unfolded_records_have_independent_caps(self) -> None:
        now_ms = 1_800_000_000_000
        records = []
        for index in range(105):
            record = make_record(f"unfolded-{index}", now_ms - index * 1000)
            records.append(record)
        for index in range(105):
            record = make_record(f"folded-{index}", now_ms - (index + 200) * 1000)
            record["archived"] = True
            record["archivedAt"] = record["finishedAt"]
            records.append(record)

        retained = prune_answer_records(validate_answer_records(records), now_ms=now_ms)

        self.assertEqual(sum(not record["archived"] for record in retained), ANSWER_RECORD_MAX_COUNT)
        self.assertEqual(sum(record["archived"] for record in retained), ANSWER_RECORD_MAX_COUNT)
        self.assertNotIn("unfolded-104", {record["id"] for record in retained})
        self.assertNotIn("folded-104", {record["id"] for record in retained})

    def test_scoring_limits_and_duration_are_validated(self) -> None:
        self.assertEqual(validate_duration_seconds({"durationSeconds": 3600}), 3600)
        with self.assertRaises(ValueError):
            validate_duration_seconds({"durationSeconds": 5})
        with self.assertRaises(ValueError):
            validate_scoring_config({"scoring": {"correctStreakAfter": 6}})

    def test_broken_underlining_is_marked_abnormal_instead_of_deleted(self) -> None:
        bank = {
            "quizDefaults": {"durationSeconds": 120},
            "questionTypes": [{"id": "context_meaning", "label": "语境释义题"}],
            "books": [{"id": "book-1", "label": "测试册"}],
            "catalog": [{"id": "article-1", "title": "测试篇目", "volume": "测试册"}],
            "questions": [{
                "id": "broken-1",
                "type": "context_meaning",
                "articleId": "article-1",
                "article": "测试篇目",
                "volume": "测试册",
                "word": "不存在",
                "sentence": "原句里没有考点",
                "targetStart": 0,
                "targetOccurrence": 1,
                "options": [
                    {"key": "A", "text": "甲"},
                    {"key": "B", "text": "乙"},
                    {"key": "C", "text": "丙"},
                    {"key": "D", "text": "丁"},
                ],
                "answer": "A",
                "explanation": "待复核",
            }],
        }

        normalized = validate_questions(bank)

        self.assertEqual(len(normalized["questions"]), 1)
        self.assertEqual(normalized["questions"][0]["reviewStatus"], "abnormal")
        self.assertIn("请人工复核", normalized["questions"][0]["reviewNote"])


if __name__ == "__main__":
    unittest.main()
