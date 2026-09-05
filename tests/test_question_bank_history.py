from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402
from run_server import (  # noqa: E402
    empty_question_bank_history,
    make_json_etag,
    question_bank_history_view,
    validate_question_bank_history,
    validate_questions,
)


def make_bank(question_id: str) -> dict:
    return {
        "schemaVersion": "3.0",
        "questionTypes": [{"id": "context_meaning", "label": "语境释义题"}],
        "books": [{"id": "book-1", "label": "测试册"}],
        "catalog": [{"id": "article-1", "title": "测试篇目", "volume": "测试册"}],
        "questions": [{
            "id": question_id,
            "type": "context_meaning",
            "articleId": "article-1",
            "article": "测试篇目",
            "volume": "测试册",
            "word": "实",
            "sentence": "实有其名",
            "targetOccurrence": 1,
            "options": [
                {"key": "A", "text": "真实"},
                {"key": "B", "text": "果实"},
                {"key": "C", "text": "实际"},
                {"key": "D", "text": "充实"},
            ],
            "answer": "A",
            "explanation": "测试解析",
        }],
    }


class QuestionBankHistoryTests(unittest.TestCase):
    def test_empty_history_is_valid(self) -> None:
        self.assertEqual(validate_question_bank_history(empty_question_bank_history()), empty_question_bank_history())

    def test_view_is_read_only_and_hides_replace_snapshot(self) -> None:
        bank = validate_questions(make_bank("question-1"))
        history = validate_question_bank_history({
            "schemaVersion": 1,
            "events": [{
                "id": "import-1",
                "kind": "import",
                "mode": "replace",
                "sourceName": "题库.json",
                "createdAt": "2026-09-05T10:00:00",
                "questionCountBefore": 0,
                "questionCountAfter": 1,
                "addedQuestionIds": ["question-1"],
                "addedArticleIds": ["article-1"],
                "addedBookIds": ["book-1"],
                "addedTypeIds": ["context_meaning"],
                "beforeHash": '"before"',
                "afterHash": make_json_etag(bank),
                "beforeBank": copy.deepcopy(bank),
            }],
        })

        view = question_bank_history_view(history, bank)

        self.assertTrue(view["events"][0]["canRevoke"])
        self.assertNotIn("beforeBank", view["events"][0])

    def test_revoke_event_marks_import_as_not_reversible(self) -> None:
        bank = validate_questions(make_bank("question-1"))
        history = validate_question_bank_history({
            "schemaVersion": 1,
            "events": [
                {
                    "id": "import-1",
                    "kind": "import",
                    "mode": "merge",
                    "sourceName": "新增.json",
                    "createdAt": "2026-09-05T10:00:00",
                    "questionCountBefore": 0,
                    "questionCountAfter": 1,
                    "addedQuestionIds": ["question-1"],
                    "addedArticleIds": [],
                    "addedBookIds": [],
                    "addedTypeIds": [],
                    "beforeHash": '"before"',
                    "afterHash": make_json_etag(bank),
                },
                {
                    "id": "revoke-1",
                    "kind": "revoke",
                    "targetEventId": "import-1",
                    "createdAt": "2026-09-05T11:00:00",
                },
            ],
        })

        view = question_bank_history_view(history, bank)

        self.assertTrue(view["events"][0]["revoked"])
        self.assertFalse(view["events"][0]["canRevoke"])
        self.assertEqual(view["events"][1]["targetSourceName"], "新增.json")

    def test_merge_import_can_be_revoked_without_restoring_old_snapshot(self) -> None:
        base_bank = validate_questions(make_bank("question-1"))
        imported_bank = copy.deepcopy(base_bank)
        imported_question = copy.deepcopy(imported_bank["questions"][0])
        imported_question["id"] = "question-2"
        imported_question["number"] = 2
        imported_question["sentence"] = "实在其中"
        imported_question["explanation"] = "新增题解析"
        imported_bank["questions"].append(imported_question)
        imported_bank = validate_questions(imported_bank)
        event = {
            "id": "import-merge-1",
            "kind": "import",
            "mode": "merge",
            "sourceName": "新增.json",
            "createdAt": "2026-09-05T10:00:00",
            "questionCountBefore": 1,
            "questionCountAfter": 2,
            "addedQuestionIds": ["question-2"],
            "addedArticleIds": [],
            "addedBookIds": [],
            "addedTypeIds": [],
            "beforeHash": make_json_etag(base_bank),
            "afterHash": make_json_etag(imported_bank),
        }

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            old_paths = (
                server.QUESTIONS_PATH,
                server.QUESTION_REVIEWS_PATH,
                server.QUESTION_BANK_HISTORY_PATH,
                server.backup_and_write,
            )
            try:
                server.QUESTIONS_PATH = temp_root / "questions.json"
                server.QUESTION_REVIEWS_PATH = temp_root / "question-reviews.json"
                server.QUESTION_BANK_HISTORY_PATH = temp_root / "question-bank-history.json"
                original_backup = old_paths[3]
                server.backup_and_write = lambda path, payload, backup_dir=None: original_backup(
                    path,
                    payload,
                    temp_root / "backups",
                )
                server.QUESTIONS_PATH.write_text(
                    json.dumps(imported_bank, ensure_ascii=False),
                    encoding="utf-8",
                )
                server.QUESTION_REVIEWS_PATH.write_text('{"schemaVersion": 1, "reviews": {}}', encoding="utf-8")
                server.QUESTION_BANK_HISTORY_PATH.write_text(
                    json.dumps({"schemaVersion": 1, "events": [event]}, ensure_ascii=False),
                    encoding="utf-8",
                )

                result_bank, result_history = server.revoke_question_bank_import("import-merge-1")

                self.assertEqual([question["id"] for question in result_bank["questions"]], ["question-1"])
                self.assertEqual(result_history["events"][-1]["targetEventId"], "import-merge-1")
            finally:
                server.QUESTIONS_PATH, server.QUESTION_REVIEWS_PATH, server.QUESTION_BANK_HISTORY_PATH, server.backup_and_write = old_paths


if __name__ == "__main__":
    unittest.main()
