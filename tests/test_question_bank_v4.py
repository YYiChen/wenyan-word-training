import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from server_validators import (
    empty_question_bank,
    make_question_semantic_fingerprint,
    question_bank_diagnostics,
    admin_question_bank_view,
    student_question_bank_view,
    validate_question_bank_v4,
    validate_question_import,
    question_import_preview,
    remap_foreign_bank_questions,
    drop_exact_duplicates,
)
from server_question_import import (
    build_import_preview,
    merge_question_bank_v4,
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
        self.assertNotIn("suggestedAnswer", view["questions"][0])
        self.assertNotIn("optionIssues", view["questions"][0])
        self.assertNotIn("reviewedAt", view["questions"][0])
        self.assertTrue(view["questions"][0]["availability"]["playable"])
        self.assertEqual(view["questions"][0]["volume"], "必修上册")

    def test_admin_view_marks_invalid_target_as_abnormal_without_persisting_legacy_status(self):
        bank = sample_bank()
        bank["questions"][0]["word"] = "不存在"
        admin_view = admin_question_bank_view(validate_question_bank_v4(bank))
        self.assertEqual(admin_view["questions"][0]["reviewStatus"], "abnormal")
        self.assertEqual(admin_view["questions"][0]["availability"]["playable"], False)
        self.assertNotIn("reviewStatus", validate_question_bank_v4(bank)["questions"][0])

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

    def test_authoritative_foreign_merge_remaps_colliding_directory_ids(self):
        bank = sample_bank()
        foreign = copy.deepcopy(bank)
        foreign["bankId"] = "bank_foreign"
        foreign["books"][0]["label"] = "选择性必修上册"
        foreign["catalog"][0]["title"] = "师说"
        foreign["questions"][0]["sentence"] = "师者，所以传道受业解惑也。"
        foreign["questions"][0]["word"] = "师"
        foreign["questions"][0]["options"] = [
            {"key": "A", "text": "老师"}, {"key": "B", "text": "军队"},
            {"key": "C", "text": "学习"}, {"key": "D", "text": "师父"},
        ]
        foreign["questions"][0]["answer"] = "A"
        foreign["questions"][0]["explanation"] = "师：老师。"
        foreign["workflow"]["reviews"] = {"legacy-1": {"status": "passed"}}

        merged = merge_question_bank_v4(bank, foreign, mode="merge")
        imported = [q for q in merged["bank"]["questions"] if q["id"] != "legacy-1"][0]
        self.assertNotEqual(imported["id"], "legacy-1")
        self.assertNotEqual(imported["articleId"], "article-1")
        imported_article = next(a for a in merged["bank"]["catalog"] if a["id"] == imported["articleId"])
        imported_book = next(b for b in merged["bank"]["books"] if b["id"] == imported_article["bookId"])
        self.assertEqual(imported_article["title"], "师说")
        self.assertEqual(imported_book["label"], "选择性必修上册")
        self.assertEqual(merged["bank"]["workflow"]["reviews"][imported["id"]]["status"], "pending")

    def test_external_import_preview_reports_duplicates_and_candidates(self):
        bank = sample_bank()
        exact = copy.deepcopy(bank)
        exact["bankId"] = "bank-external"
        exact["importKind"] = "external"
        preview = build_import_preview(bank, exact, mode="merge")
        self.assertFalse(preview["sameBank"])
        self.assertEqual(preview["summary"]["exactDuplicates"], 1)

        candidate = copy.deepcopy(bank)
        candidate["bankId"] = "bank-external"
        candidate["importKind"] = "external"
        candidate["questions"][0]["explanation"] = "利：锋利，形容词。"
        candidate_preview = build_import_preview(bank, candidate, mode="merge")
        self.assertEqual(candidate_preview["summary"]["newQuestions"], 1)
        self.assertEqual(candidate_preview["summary"]["duplicateCandidates"], 1)

    def test_same_bank_strategy_controls_question_and_review_inheritance(self):
        bank = sample_bank()
        incoming = copy.deepcopy(bank)
        incoming["questions"][0]["explanation"] = "利：锋利，用来形容刀剑。"
        incoming["workflow"]["reviews"]["legacy-1"] = {"status": "passed", "reviewedAt": "2026-09-06"}

        preserved = merge_question_bank_v4(bank, incoming, mode="merge", strategy="preserve_local")["bank"]
        self.assertEqual(preserved["questions"][0]["explanation"], bank["questions"][0]["explanation"])
        self.assertEqual(preserved["workflow"]["reviews"]["legacy-1"]["status"], "passed")

        adopted = merge_question_bank_v4(bank, incoming, mode="merge", strategy="use_imported")["bank"]
        self.assertEqual(adopted["questions"][0]["explanation"], incoming["questions"][0]["explanation"])
        self.assertEqual(adopted["workflow"]["reviews"]["legacy-1"]["status"], "passed")

    def test_canonical_bank_requires_stable_question_ids(self):
        bank = sample_bank()
        del bank["questions"][0]["id"]
        with self.assertRaises(ValueError):
            validate_question_bank_v4(bank)


if __name__ == "__main__":
    unittest.main()
