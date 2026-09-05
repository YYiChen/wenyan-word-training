from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from run_server import apply_question_review_publication_status, validate_questions  # noqa: E402


def make_question(question_id: str, word: str = "实") -> dict:
    return {
        "id": question_id,
        "type": "context_meaning",
        "articleId": "article-1",
        "article": "测试篇目",
        "volume": "测试册",
        "word": word,
        "sentence": f"{word}有其名。",
        "targetOccurrence": 1,
        "options": [
            {"key": "A", "text": "真实"},
            {"key": "B", "text": "果实"},
            {"key": "C", "text": "实际"},
            {"key": "D", "text": "充实"},
        ],
        "answer": "A",
        "explanation": "测试解析",
    }


def make_bank(questions: list[dict]) -> dict:
    return {
        "schemaVersion": "3.0",
        "questionTypes": [{"id": "context_meaning", "label": "语境释义题"}],
        "books": [{"id": "book-1", "label": "测试册", "order": 1}],
        "catalog": [{"id": "article-1", "title": "测试篇目", "volume": "测试册"}],
        "questions": questions,
    }


class DuplicateReviewValidationTests(unittest.TestCase):
    def test_needs_revision_hides_question_from_students_until_passed(self) -> None:
        question = make_question("question-1")
        question["reviewStatus"] = "verified"
        bank = validate_questions(make_bank([question]))

        changed = apply_question_review_publication_status(
            bank,
            "question-1",
            {"status": "needs_revision"},
        )

        self.assertTrue(changed)
        self.assertEqual(bank["questions"][0]["reviewStatus"], "candidate")

        changed = apply_question_review_publication_status(
            bank,
            "question-1",
            {"status": "passed"},
        )

        self.assertTrue(changed)
        self.assertEqual(bank["questions"][0]["reviewStatus"], "verified")

    def test_underlining_abnormal_question_stays_blocked(self) -> None:
        question = make_question("question-1")
        question["targetOccurrence"] = 2
        bank = validate_questions(make_bank([question]))

        changed = apply_question_review_publication_status(
            bank,
            "question-1",
            {"status": "passed"},
        )

        self.assertFalse(changed)
        self.assertEqual(bank["questions"][0]["reviewStatus"], "abnormal")

    def test_valid_duplicate_review_is_normalized(self) -> None:
        first = make_question("question-1")
        second = make_question("question-2")
        second["explanation"] = "不同解析"
        first["duplicateReview"] = {
            "status": "pending",
            "groupId": " duplicate-group ",
            "relatedQuestionIds": ["question-1", "question-2", "question-1"],
        }
        second["duplicateReview"] = {
            "status": "kept",
            "groupId": "duplicate-group",
            "relatedQuestionIds": ["question-1", "question-2"],
        }

        normalized = validate_questions(make_bank([first, second]))

        normalized_first = normalized["questions"][0]
        self.assertEqual(normalized_first["duplicateReview"]["groupId"], "duplicate-group")
        self.assertEqual(normalized_first["duplicateReview"]["relatedQuestionIds"], ["question-1", "question-2"])
        self.assertEqual(first["duplicateReview"]["groupId"], " duplicate-group ", "校验不应修改调用方原始对象")

    def test_unknown_duplicate_reference_is_rejected(self) -> None:
        question = make_question("question-1")
        question["duplicateReview"] = {
            "status": "pending",
            "groupId": "duplicate-group",
            "relatedQuestionIds": ["question-1", "missing-question"],
        }

        with self.assertRaises(ValueError):
            validate_questions(make_bank([question]))

    def test_invalid_duplicate_status_is_rejected(self) -> None:
        question = make_question("question-1")
        question["duplicateReview"] = {
            "status": "unknown",
            "groupId": "duplicate-group",
            "relatedQuestionIds": ["question-1"],
        }

        with self.assertRaises(ValueError):
            validate_questions(make_bank([question]))

    def test_one_way_relation_is_rejected(self) -> None:
        first = make_question("question-1")
        second = make_question("question-2")
        second["explanation"] = "不同解析"
        first["duplicateReview"] = {
            "status": "pending",
            "groupId": "duplicate-group",
            "relatedQuestionIds": ["question-1", "question-2"],
        }

        with self.assertRaises(ValueError):
            validate_questions(make_bank([first, second]))

    def test_inconsistent_group_is_rejected(self) -> None:
        first = make_question("question-1")
        second = make_question("question-2")
        second["explanation"] = "不同解析"
        first["duplicateReview"] = {
            "status": "pending",
            "groupId": "duplicate-group",
            "relatedQuestionIds": ["question-1", "question-2"],
        }
        second["duplicateReview"] = {
            "status": "kept",
            "groupId": "another-group",
            "relatedQuestionIds": ["question-1", "question-2"],
        }

        with self.assertRaises(ValueError):
            validate_questions(make_bank([first, second]))

    def test_core_mismatch_is_rejected(self) -> None:
        first = make_question("question-1")
        second = make_question("question-2", "名")
        for question in (first, second):
            question["duplicateReview"] = {
                "status": "pending",
                "groupId": "duplicate-group",
                "relatedQuestionIds": ["question-1", "question-2"],
            }

        with self.assertRaises(ValueError):
            validate_questions(make_bank([first, second]))

    def test_identical_detail_group_is_rejected(self) -> None:
        first = make_question("question-1")
        second = make_question("question-2")
        for question in (first, second):
            question["duplicateReview"] = {
                "status": "pending",
                "groupId": "duplicate-group",
                "relatedQuestionIds": ["question-1", "question-2"],
            }

        with self.assertRaises(ValueError):
            validate_questions(make_bank([first, second]))

    def test_question_id_with_spaces_is_rejected(self) -> None:
        question = make_question(" question-1")

        with self.assertRaises(ValueError):
            validate_questions(make_bank([question]))

    def test_missing_number_is_filled_from_position(self) -> None:
        normalized = validate_questions(make_bank([make_question("question-1")]))

        self.assertEqual(normalized["questions"][0]["number"], 1)

    def test_malformed_option_values_are_rejected_as_value_errors(self) -> None:
        question = make_question("question-1")
        question["options"][0]["text"] = 123

        with self.assertRaises(ValueError):
            validate_questions(make_bank([question]))

    def test_empty_catalog_is_rejected(self) -> None:
        bank = make_bank([make_question("question-1")])
        bank["catalog"] = []

        with self.assertRaises(ValueError):
            validate_questions(bank)

    def test_article_display_name_must_match_catalog(self) -> None:
        question = make_question("question-1")
        question["article"] = "另一篇"

        with self.assertRaises(ValueError):
            validate_questions(make_bank([question]))

    def test_unmarked_same_core_variants_are_rebuilt_as_pending_duplicates(self) -> None:
        first = make_question("question-1")
        second = make_question("question-2")
        second["explanation"] = "不同解析"

        normalized = validate_questions(make_bank([first, second]))

        self.assertEqual(
            [question["duplicateReview"]["status"] for question in normalized["questions"]],
            ["pending", "pending"],
        )

    def test_abnormal_underlining_fix_restores_previous_publish_status(self) -> None:
        question = make_question("question-1")
        question["reviewStatus"] = "candidate"
        question["word"] = "不存在"
        question["sentence"] = "原句里没有考点"
        broken = validate_questions(make_bank([question]))["questions"][0]

        self.assertEqual(broken["reviewStatus"], "abnormal")
        self.assertEqual(broken["reviewStatusBeforeAbnormal"], "candidate")

        fixed = dict(broken)
        fixed["word"] = "实"
        fixed["sentence"] = "实有其名。"
        fixed["targetStart"] = 0
        fixed["targetOccurrence"] = 1
        repaired = validate_questions(make_bank([fixed]))["questions"][0]

        self.assertEqual(repaired["reviewStatus"], "candidate")
        self.assertNotIn("reviewStatusBeforeAbnormal", repaired)
        self.assertNotIn("reviewNote", repaired)


if __name__ == "__main__":
    unittest.main()
