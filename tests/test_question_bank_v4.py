import copy
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from server_validators import (
    empty_question_bank,
    make_duplicate_group_id,
    make_question_core_signature,
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
    merge_question_review,
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

    def test_foreign_directory_remap_makes_semantic_duplicate_exact(self):
        bank = sample_bank()
        foreign = copy.deepcopy(bank)
        foreign["bankId"] = "bank-foreign"
        foreign["books"][0]["id"] = "foreign-book"
        foreign["catalog"][0]["id"] = "foreign-article"
        foreign["catalog"][0]["bookId"] = "foreign-book"
        foreign["questions"][0]["id"] = "foreign-question"
        foreign["questions"][0]["articleId"] = "foreign-article"
        foreign["workflow"]["reviews"] = {"foreign-question": {"status": "passed"}}

        preview = build_import_preview(bank, foreign, mode="merge")
        merged = merge_question_bank_v4(bank, foreign, mode="merge")["bank"]

        self.assertEqual(preview["summary"]["exactDuplicates"], 1)
        self.assertEqual(preview["summary"]["newQuestions"], 0)
        self.assertEqual(len(merged["questions"]), len(bank["questions"]))

    def test_external_import_cannot_forge_review_status(self):
        bank = sample_bank()
        package = {
            "format": "wenyan-question-import",
            "schemaVersion": "1.0",
            "questionTypes": bank["questionTypes"],
            "books": bank["books"],
            "catalog": bank["catalog"],
            "workflow": {"reviews": {"forged": {"status": "passed"}}},
            "questions": [{
                **copy.deepcopy(bank["questions"][0]),
                "id": "forged",
                "number": 2,
                "word": "任",
                "sentence": "任重而道远。",
                "options": [
                    {"key": "A", "text": "负担"}, {"key": "B", "text": "任凭"},
                    {"key": "C", "text": "任何"}, {"key": "D", "text": "任职"},
                ],
                "answer": "A",
                "explanation": "任：负担。",
                "reviewStatus": "verified",
                "review": {"status": "passed"},
            }],
        }
        imported = validate_question_import(package, bank)
        merged = merge_question_bank_v4(bank, imported, mode="merge")["bank"]
        imported_id = next(qid for qid in merged["workflow"]["reviews"] if qid != "legacy-1")
        self.assertEqual(merged["workflow"]["reviews"][imported_id]["status"], "pending")

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

        incoming["workflow"]["reviews"]["legacy-1"] = {"status": "pending"}
        adopted_pending = merge_question_bank_v4(bank, incoming, mode="merge", strategy="use_imported")["bank"]
        self.assertEqual(adopted_pending["workflow"]["reviews"]["legacy-1"]["status"], "pending")

    def test_review_merge_matrix_is_authoritative(self):
        pending = {"status": "pending"}
        passed = {"status": "passed"}
        needs_revision = {"status": "needs_revision"}
        skipped = {"status": "skipped"}

        cases = [
            (pending, pending, "pending", "preserve_local", False),
            (pending, passed, "passed", "preserve_local", False),
            (pending, needs_revision, "needs_revision", "preserve_local", False),
            (pending, skipped, "skipped", "preserve_local", False),
            (passed, pending, "passed", "preserve_local", False),
            (needs_revision, pending, "needs_revision", "preserve_local", False),
            (skipped, pending, "skipped", "preserve_local", False),
            (passed, passed, "passed", "preserve_local", False),
            (passed, needs_revision, "passed", "preserve_local", True),
            (passed, needs_revision, "needs_revision", "use_imported", True),
            (passed, skipped, "passed", "preserve_local", True),
            (passed, skipped, "skipped", "use_imported", True),
            (needs_revision, skipped, "needs_revision", "preserve_local", True),
            (needs_revision, skipped, "skipped", "use_imported", True),
        ]
        for local, incoming, expected, strategy, conflict_expected in cases:
            with self.subTest(local=local["status"], incoming=incoming["status"], strategy=strategy):
                merged, conflict = merge_question_review(
                    local,
                    incoming,
                    trusted_same_bank=True,
                    content_state="unchanged",
                    strategy=strategy,
                )
                self.assertEqual(merged["status"], expected)
                self.assertEqual(conflict, conflict_expected)

        merged, conflict = merge_question_review(
            passed, pending, trusted_same_bank=True, content_state="changed", strategy="use_imported"
        )
        self.assertEqual(merged["status"], "pending")
        self.assertFalse(conflict)

        merged, conflict = merge_question_review(
            passed, needs_revision, trusted_same_bank=True, content_state="changed", strategy="use_imported"
        )
        self.assertEqual(merged["status"], "needs_revision")
        self.assertFalse(conflict)

        merged, conflict = merge_question_review(
            passed, passed, trusted_same_bank=False, content_state="new", strategy="use_imported"
        )
        self.assertEqual(merged["status"], "pending")
        self.assertFalse(conflict)

        merged, conflict = merge_question_review(
            pending, passed, trusted_same_bank=True, content_state="new", strategy="preserve_local"
        )
        self.assertEqual(merged["status"], "passed")
        self.assertFalse(conflict)

    def test_v4_drops_legacy_scoring_fields(self):
        bank = sample_bank()
        bank["quizDefaults"]["correctScore"] = 99
        bank["quizDefaults"]["wrongScore"] = -99
        normalized = validate_question_bank_v4(bank)
        self.assertNotIn("correctScore", normalized["quizDefaults"])
        self.assertNotIn("wrongScore", normalized["quizDefaults"])

    def test_public_sample_questions_have_passed_reviews(self):
        sample_path = Path(__file__).resolve().parents[1] / "public-data" / "questions.json"
        public_bank = validate_question_bank_v4(json.loads(sample_path.read_text(encoding="utf-8")))
        self.assertGreater(len(public_bank["questions"]), 0)
        article_counts = Counter(question["articleId"] for question in public_bank["questions"])
        self.assertEqual(set(article_counts), {article["id"] for article in public_bank["catalog"]})
        self.assertTrue(all(1 <= count <= 3 for count in article_counts.values()))
        self.assertEqual(set(public_bank["workflow"]["reviews"]), {q["id"] for q in public_bank["questions"]})
        self.assertTrue(all(review["status"] == "passed" for review in public_bank["workflow"]["reviews"].values()))

    def test_availability_reason_is_stable(self):
        bank = sample_bank()
        question = bank["questions"][0]
        bank["workflow"]["reviews"][question["id"]] = {"status": "pending"}
        self.assertEqual(question_bank_diagnostics(bank)["availability"][question["id"]]["reason"], "review_pending")
        bank["workflow"]["reviews"][question["id"]] = {"status": "needs_revision"}
        self.assertEqual(question_bank_diagnostics(bank)["availability"][question["id"]]["reason"], "review_needs_revision")
        question["word"] = "不存在"
        self.assertEqual(question_bank_diagnostics(bank)["availability"][question["id"]]["reason"], "invalid")

    def test_availability_reason_distinguishes_duplicate_pending_and_skipped(self):
        bank = sample_bank()
        duplicate = copy.deepcopy(bank["questions"][0])
        duplicate["id"] = "legacy-2"
        duplicate["number"] = 2
        duplicate["explanation"] = "利：锋利，形容词。"
        bank["questions"].append(duplicate)
        bank["workflow"]["reviews"]["legacy-2"] = {"status": "passed"}
        bank = validate_question_bank_v4(bank)
        diagnostics = question_bank_diagnostics(bank)
        self.assertEqual(diagnostics["availability"]["legacy-1"]["reason"], "duplicate_pending")
        self.assertEqual(diagnostics["availability"]["legacy-2"]["reason"], "duplicate_pending")

        group_id = make_duplicate_group_id(make_question_core_signature(duplicate))
        bank["workflow"]["duplicateResolutions"][group_id]["decisions"] = {
            "legacy-1": "skipped",
            "legacy-2": "skipped",
        }
        diagnostics = question_bank_diagnostics(bank)
        self.assertEqual(diagnostics["availability"]["legacy-1"]["reason"], "duplicate_skipped")
        self.assertEqual(diagnostics["availability"]["legacy-2"]["reason"], "duplicate_skipped")

    def test_external_replace_does_not_import_review_decisions(self):
        bank = sample_bank()
        incoming = copy.deepcopy(bank)
        incoming["bankId"] = "bank-external"
        incoming["importKind"] = "external"
        incoming["workflow"]["reviews"]["legacy-1"] = {"status": "passed"}
        replaced = merge_question_bank_v4(bank, incoming, mode="replace")["bank"]
        self.assertEqual(replaced["workflow"]["reviews"]["legacy-1"]["status"], "pending")

    def test_replace_preview_matches_replacement_review_and_counts(self):
        bank = sample_bank()
        incoming = copy.deepcopy(bank)
        incoming["bankId"] = "bank-external"
        incoming["importKind"] = "external"
        incoming["questions"][0]["explanation"] = "利：锋利，用来形容刀剑。"
        incoming["workflow"]["reviews"]["legacy-1"] = {"status": "passed"}

        preview = build_import_preview(bank, incoming, mode="replace")
        replaced = merge_question_bank_v4(bank, incoming, mode="replace")["bank"]

        self.assertEqual(preview["summary"]["importedTotal"], len(replaced["questions"]))
        self.assertEqual(preview["summary"]["newQuestions"], len(replaced["questions"]))
        self.assertEqual(preview["reviewSummary"]["afterPreserveLocal"]["pending"], len(replaced["questions"]))
        self.assertEqual(replaced["workflow"]["reviews"]["legacy-1"]["status"], "pending")

    def test_canonical_bank_requires_stable_question_ids(self):
        bank = sample_bank()
        del bank["questions"][0]["id"]
        with self.assertRaises(ValueError):
            validate_question_bank_v4(bank)


if __name__ == "__main__":
    unittest.main()
