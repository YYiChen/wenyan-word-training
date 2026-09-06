import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from server_validators import (
    empty_question_bank,
    make_question_semantic_fingerprint,
    question_bank_diagnostics,
    student_question_bank_view,
    validate_question_bank_v4,
    validate_question_import,
    question_import_preview,
    remap_foreign_bank_questions,
    drop_exact_duplicates,
)


def sample_bank():
    bank = empty_question_bank()
    bank.update({
        "title": "测试题库",
        "questionTypes": [{"id": "context_meaning", "label": "语境释义题"}],
        "books": [{"id": "book-1", "label": "必修上册", "order": 1}],
        "catalog": [{"id": "article-1", "bookId": "book-1", "unit": "一", "title": "劝学", "author": "荀子"}],
        "questions": [{
            "id": "legacy-1", "number": 1, "type": "context_meaning", "articleId": "article-1",
            "word": "利", "sentence": "金就砺则利。", "targetOccurrence": 1, "stem": "",
            "options": [{"key": "A", "text": "锋利"}, {"key": "B", "text": "利益"}, {"key": "C", "text": "有利"}, {"key": "D", "text": "顺利"}],
            "answer": "A", "explanation": "利：锋利。",
        }],
    })
    bank["workflow"]["reviews"] = {"legacy-1": {"status": "passed"}}
    return validate_question_bank_v4(bank)


class QuestionBankV4Tests(unittest.TestCase):
    def test_canonical_and_student_projection(self):
        bank = sample_bank()
        self.assertEqual(bank["schemaVersion"], "4.0")
        self.assertEqual(bank["workflow"]["reviews"]["legacy-1"]["status"], "passed")
        view = student_question_bank_view(bank)
        self.assertNotIn("workflow", view)
        self.assertNotIn("reviewStatus", view["questions"][0])
        self.assertNotIn("duplicateReview", view["questions"][0])
        self.assertTrue(view["questions"][0]["availability"]["playable"])
        self.assertEqual(view["questions"][0]["volume"], "必修上册")

    def test_invalid_target_is_stored_but_blocked(self):
        bank = sample_bank()
        bank["questions"][0]["word"] = "不存在"
        normalized = validate_question_bank_v4(bank)
        diagnostic = question_bank_diagnostics(normalized)
        self.assertFalse(diagnostic["availability"]["legacy-1"]["playable"])
        self.assertEqual(diagnostic["issues"]["legacy-1"][0]["code"], "WORD_NOT_FOUND")

    def test_external_import_generates_new_pending_id(self):
        bank = sample_bank()
        package = {
            "format": "wenyan-question-import", "schemaVersion": "1.0",
            "books": bank["books"], "catalog": bank["catalog"], "questionTypes": bank["questionTypes"],
            "questions": [{**copy.deepcopy(bank["questions"][0]), "id": "fake", "number": 2}],
        }
        imported = validate_question_import(package, bank)
        self.assertFalse(question_import_preview(bank, imported, "merge")["sameBank"])
        self.assertNotEqual(imported["questions"][0]["id"], "fake")
        self.assertTrue(imported["questions"][0]["id"].startswith("q_"))
        self.assertEqual(imported["workflow"]["reviews"][imported["questions"][0]["id"]]["status"], "pending")

    def test_legacy_fields_are_not_canonical(self):
        bank = sample_bank()
        bank["questions"][0].update({"article": "劝学", "volume": "必修上册", "targetStart": 1, "reviewStatus": "verified"})
        normalized = validate_question_bank_v4(bank)
        self.assertNotIn("article", normalized["questions"][0])
        self.assertNotIn("targetStart", normalized["questions"][0])
        self.assertNotIn("reviewStatus", normalized["questions"][0])
        self.assertTrue(make_question_semantic_fingerprint(normalized["questions"][0]))

    def test_preview_classifies_same_bank_change(self):
        bank = sample_bank()
        incoming = copy.deepcopy(bank)
        incoming["questions"][0]["explanation"] = "利，锋利，形容词。"
        incoming = validate_question_bank_v4(incoming)
        preview = question_import_preview(bank, incoming, "merge")
        self.assertEqual(preview["summary"]["modified"], 1)
        self.assertEqual(preview["summary"]["majorModified"], 0)

    def test_foreign_ids_are_remapped(self):
        bank = sample_bank()
        foreign = copy.deepcopy(bank)
        foreign["bankId"] = "bank_foreign"
        remapped = remap_foreign_bank_questions(foreign, {"legacy-1"})
        self.assertNotEqual(remapped["questions"][0]["id"], "legacy-1")
        self.assertEqual(remapped["workflow"]["reviews"][remapped["questions"][0]["id"]]["status"], "pending")

    def test_foreign_exact_duplicate_is_dropped_before_id_remap(self):
        bank = sample_bank(); foreign = copy.deepcopy(bank); foreign["bankId"] = "bank_foreign"
        self.assertEqual(drop_exact_duplicates(foreign, bank)["questions"], [])


if __name__ == "__main__":
    unittest.main()
