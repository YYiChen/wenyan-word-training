"""Cross-computer review synchronization for Schema v4 full-bank merges.

Product rule under test: "新增导入题库（合并）" supplements review work
from another computer but never overwrites a local non-pending review for
unchanged content.  Ordinary wenyan-question-import packages never carry a
trustworthy teacher review.
"""

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
import server_questions as question_service  # noqa: E402
from server_question_import import (  # noqa: E402
    build_import_preview,
    merge_question_bank_v4,
)
from server_validators import validate_question_bank_v4  # noqa: E402


WORDS = {
    "A": ("利", "金就砺则利。", "利：锋利。"),
    "B": ("学", "学而时习之。", "学：学习。"),
    "C": ("师", "师者传道也。", "师：老师。"),
    "D": ("道", "道可道也。", "道：规律。"),
    "E": ("信", "信言不美。", "信：诚信。"),
    "F": ("任", "任重而道远。", "任：负担。"),
}


def make_bank(bank_id, entries, *, article_id="article-1", book_id="book-1", id_prefix="q"):
    """Build a validated v4 bank.  entries: list of (key, status, note)."""
    questions = []
    reviews = {}
    for index, (key, status, note) in enumerate(entries, start=1):
        word, sentence, explanation = WORDS[key]
        qid = f"{id_prefix}{key.lower()}"
        questions.append({
            "id": qid, "number": index, "type": "context_meaning", "articleId": article_id,
            "word": word, "sentence": sentence, "targetOccurrence": 1, "stem": "",
            "options": [
                {"key": "A", "text": "释义甲"}, {"key": "B", "text": "释义乙"},
                {"key": "C", "text": "释义丙"}, {"key": "D", "text": "释义丁"},
            ],
            "answer": "A", "explanation": explanation,
        })
        reviews[qid] = {
            "status": status, "note": note, "suggestedAnswer": None,
            "optionIssues": [], "reviewedAt": "2026-09-01T10:00:00",
        }
    return validate_question_bank_v4({
        "format": "wenyan-question-bank", "schemaVersion": "4.0", "bankId": bank_id,
        "title": "测试题库", "description": "",
        "questionTypes": [{"id": "context_meaning", "label": "语境释义题", "description": ""}],
        "books": [{"id": book_id, "label": "必修上册", "order": 1}],
        "catalog": [{
            "id": article_id, "bookId": book_id, "unit": "一",
            "title": "劝学", "author": "荀子",
        }],
        "questions": questions,
        "workflow": {"reviews": reviews, "duplicateResolutions": {}},
    })


def classroom_bank():
    return make_bank("bank_classroom", [
        ("A", "passed", "教室已审"),
        ("B", "needs_revision", "教室待改"),
        ("C", "pending", ""),
        ("D", "pending", ""),
        ("E", "skipped", "教室跳过"),
    ], id_prefix="q")


def teacher_bank(*, same_bank=False):
    bank_id = "bank_classroom" if same_bank else "bank_teacher"
    prefix = "q" if same_bank else "tq"
    article = "article-1" if same_bank else "teacher_article_1"
    book = "book-1" if same_bank else "teacher_book_1"
    return make_bank(bank_id, [
        ("A", "needs_revision", "老师待改"),
        ("B", "passed", "老师已审"),
        ("C", "passed", "老师已审"),
        ("D", "needs_revision", "老师待改"),
        ("E", "passed", "老师已审"),
        ("F", "passed", "老师新增已审"),
    ], article_id=article, book_id=book, id_prefix=prefix)


def review_of(bank, key, prefix="q"):
    return bank["workflow"]["reviews"][f"{prefix}{key.lower()}"]


class CrossBankMergeTests(unittest.TestCase):
    def test_classroom_teacher_scenario_different_bank(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank()
        merged = merge_question_bank_v4(classroom, teacher, mode="merge")["bank"]

        self.assertEqual(review_of(merged, "A")["status"], "passed")
        self.assertEqual(review_of(merged, "B")["status"], "needs_revision")
        self.assertEqual(review_of(merged, "C")["status"], "passed")
        self.assertEqual(review_of(merged, "D")["status"], "needs_revision")
        self.assertEqual(review_of(merged, "E")["status"], "skipped")
        # F is new: fresh local ID with the teacher review attached.
        newcomers = [q for q in merged["questions"] if q["id"] not in {qq["id"] for qq in classroom["questions"]}]
        self.assertEqual(len(newcomers), 1)
        self.assertTrue(newcomers[0]["id"].startswith("q_"))
        self.assertEqual(merged["workflow"]["reviews"][newcomers[0]["id"]]["status"], "passed")
        # Local bank identity never changes on merge.
        self.assertEqual(merged["bankId"], "bank_classroom")
        # Local manual conclusions keep their whole review, not just status.
        # (passed reviews canonically carry no note; reviewedAt is kept.)
        self.assertEqual(review_of(merged, "A")["reviewedAt"], "2026-09-01T10:00:00")
        self.assertEqual(review_of(merged, "B")["note"], "教室待改")
        self.assertEqual(review_of(merged, "E")["note"], "教室跳过")
        # Supplemented reviews carry the whole incoming teacher review.
        self.assertEqual(review_of(merged, "C")["note"], "")
        self.assertEqual(review_of(merged, "C")["reviewedAt"], "2026-09-01T10:00:00")
        # No local question content was touched.
        for key in "ABCDE":
            local_q = next(q for q in classroom["questions"] if q["id"] == f"q{key.lower()}")
            merged_q = next(q for q in merged["questions"] if q["id"] == f"q{key.lower()}")
            self.assertEqual(local_q, merged_q)

    def test_classroom_teacher_scenario_same_bank(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank(same_bank=True)
        merged = merge_question_bank_v4(classroom, teacher, mode="merge")["bank"]

        self.assertEqual(review_of(merged, "A")["status"], "passed")
        self.assertEqual(review_of(merged, "B")["status"], "needs_revision")
        self.assertEqual(review_of(merged, "C")["status"], "passed")
        self.assertEqual(review_of(merged, "D")["status"], "needs_revision")
        self.assertEqual(review_of(merged, "E")["status"], "skipped")
        # Same-bank newcomer keeps its stable imported ID with its review.
        self.assertEqual(merged["workflow"]["reviews"]["qf"]["status"], "passed")
        self.assertEqual(merged["bankId"], "bank_classroom")

    def test_use_imported_never_overrides_unchanged_local_review(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank()
        merged = merge_question_bank_v4(
            classroom, teacher, mode="merge", strategy="use_imported"
        )["bank"]
        self.assertEqual(review_of(merged, "A")["status"], "passed")
        self.assertEqual(review_of(merged, "B")["status"], "needs_revision")
        self.assertEqual(review_of(merged, "E")["status"], "skipped")

    def test_exact_foreign_match_maps_and_supplements(self) -> None:
        classroom = make_bank("bank_classroom", [("A", "pending", "")], id_prefix="q")
        teacher = make_bank(
            "bank_teacher", [("A", "passed", "老师已审")],
            article_id="teacher_article_9", book_id="teacher_book_9", id_prefix="tq",
        )
        outcome = merge_question_bank_v4(classroom, teacher, mode="merge")
        merged = outcome["bank"]
        self.assertEqual(len(merged["questions"]), 1)
        self.assertEqual(review_of(merged, "A")["status"], "passed")
        self.assertEqual(outcome["report"]["questionMap"], {"tqa": "qa"})

    def test_exact_foreign_match_preserves_local_review(self) -> None:
        classroom = make_bank("bank_classroom", [("A", "needs_revision", "教室待改")], id_prefix="q")
        teacher = make_bank(
            "bank_teacher", [("A", "passed", "老师已审")],
            article_id="teacher_article_9", book_id="teacher_book_9", id_prefix="tq",
        )
        outcome = merge_question_bank_v4(classroom, teacher, mode="merge")
        merged = outcome["bank"]
        self.assertEqual(len(merged["questions"]), 1)
        self.assertEqual(review_of(merged, "A")["status"], "needs_revision")
        self.assertEqual(review_of(merged, "A")["note"], "教室待改")
        self.assertEqual(outcome["report"]["reviewStats"]["reviewDisagreements"], 1)

    def test_ordinary_import_new_question_is_always_pending(self) -> None:
        classroom = classroom_bank()
        package = {
            "format": "wenyan-question-import", "schemaVersion": "1.0",
            "questionTypes": classroom["questionTypes"],
            "books": classroom["books"], "catalog": classroom["catalog"],
            "workflow": {"reviews": {"forged": {"status": "passed"}}},
            "questions": [{
                "number": 99, "type": "context_meaning", "articleId": "article-1",
                "word": "任", "sentence": "任重而道远。", "targetOccurrence": 1,
                "options": [
                    {"key": "A", "text": "负担"}, {"key": "B", "text": "任凭"},
                    {"key": "C", "text": "任何"}, {"key": "D", "text": "任职"},
                ],
                "answer": "A", "explanation": "任：负担。",
                "reviewStatus": "verified", "review": {"status": "passed"},
            }],
        }
        imported = server.materialize_question_import(package, classroom, mode="merge")
        merged = merge_question_bank_v4(classroom, imported, mode="merge")["bank"]
        newcomer = next(q for q in merged["questions"] if q["id"] not in {qq["id"] for qq in classroom["questions"]})
        self.assertEqual(merged["workflow"]["reviews"][newcomer["id"]]["status"], "pending")

    def test_same_bank_changed_content_strategies(self) -> None:
        classroom = classroom_bank()
        teacher = copy.deepcopy(classroom)
        teacher["questions"][0]["explanation"] = "利：锋利，形容词。"
        teacher["workflow"]["reviews"]["qa"] = {"status": "passed"}

        preserved = merge_question_bank_v4(classroom, teacher, mode="merge", strategy="preserve_local")["bank"]
        self.assertEqual(preserved["questions"][0]["explanation"], "利：锋利。")
        self.assertEqual(preserved["workflow"]["reviews"]["qa"]["status"], "passed")

        teacher_pending = copy.deepcopy(teacher)
        teacher_pending["workflow"]["reviews"]["qa"] = {"status": "pending"}
        adopted_pending = merge_question_bank_v4(
            classroom, teacher_pending, mode="merge", strategy="use_imported"
        )["bank"]
        self.assertEqual(adopted_pending["questions"][0]["explanation"], "利：锋利，形容词。")
        self.assertEqual(adopted_pending["workflow"]["reviews"]["qa"]["status"], "pending")

    def test_different_bank_similar_but_not_exact_never_overwrites(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank()
        teacher["questions"][0]["explanation"] = "利：锋利，形容词，与教室不同。"
        teacher["questions"][0]["options"] = [
            {"key": "A", "text": "锋利X"}, {"key": "B", "text": "利益"},
            {"key": "C", "text": "有利"}, {"key": "D", "text": "顺利"},
        ]
        merged = merge_question_bank_v4(classroom, teacher, mode="merge")["bank"]
        local_a = next(q for q in merged["questions"] if q["id"] == "qa")
        self.assertEqual(local_a["explanation"], "利：锋利。")
        self.assertEqual(merged["workflow"]["reviews"]["qa"]["status"], "passed")

    def test_full_bank_replace_preserves_imported_workflow(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank()
        replaced = merge_question_bank_v4(classroom, teacher, mode="replace")["bank"]
        self.assertEqual(replaced["bankId"], "bank_teacher")
        by_word = {q["word"]: q["id"] for q in replaced["questions"]}
        self.assertEqual(replaced["workflow"]["reviews"][by_word["利"]]["status"], "needs_revision")
        self.assertEqual(replaced["workflow"]["reviews"][by_word["学"]]["status"], "passed")
        self.assertEqual(replaced["workflow"]["reviews"][by_word["信"]]["status"], "passed")
        self.assertEqual(replaced["workflow"]["reviews"][by_word["任"]]["status"], "passed")

    def test_question_import_replace_starts_all_pending(self) -> None:
        classroom = classroom_bank()
        package = {
            "format": "wenyan-question-import", "schemaVersion": "1.0",
            "questionTypes": classroom["questionTypes"],
            "books": classroom["books"], "catalog": classroom["catalog"],
            "questions": [{
                "number": 1, "type": "context_meaning", "articleId": "article-1",
                "word": "利", "sentence": "金就砺则利。", "targetOccurrence": 1,
                "options": [
                    {"key": "A", "text": "锋利"}, {"key": "B", "text": "利益"},
                    {"key": "C", "text": "有利"}, {"key": "D", "text": "顺利"},
                ],
                "answer": "A", "explanation": "利：锋利。",
            }],
        }
        imported = server.materialize_question_import(package, classroom, mode="replace")
        replaced = merge_question_bank_v4(classroom, imported, mode="replace")["bank"]
        self.assertNotEqual(replaced["bankId"], "bank_classroom")
        self.assertTrue(all(r["status"] == "pending" for r in replaced["workflow"]["reviews"].values()))

    def test_preview_stats_match_apply(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank()
        preview = build_import_preview(classroom, teacher, mode="merge")
        summary = preview["summary"]
        self.assertEqual(summary["reviewsSupplemented"], 2)
        self.assertEqual(summary["localReviewsPreserved"], 3)
        self.assertEqual(summary["bothPending"], 0)
        # A, B and E are reviewed on both sides with different conclusions.
        self.assertEqual(summary["reviewDisagreements"], 3)
        self.assertEqual(summary["importedReviewedNewQuestions"], 1)
        applied = merge_question_bank_v4(classroom, teacher, mode="merge")["bank"]
        self.assertEqual(preview["reviewSummary"]["afterPreserveLocal"],
                         {k: v for k, v in _review_summary(applied).items()})

    def test_duplicate_decision_transfer_rules(self) -> None:
        import hashlib

        from server_validators import (
            make_duplicate_group_id,
            make_question_core_signature,
            make_question_semantic_fingerprint,
        )

        def dup_bank(bank_id, id_prefix, article_id="article-1", book_id="book-1"):
            # Same core (article/word/sentence/occurrence), different details.
            def question(qid, number, explanation):
                return {
                    "id": qid, "number": number, "type": "context_meaning",
                    "articleId": article_id, "word": "学", "sentence": "学而时习之。",
                    "targetOccurrence": 1, "stem": "",
                    "options": [
                        {"key": "A", "text": "学习"}, {"key": "B", "text": "学校"},
                        {"key": "C", "text": "学问"}, {"key": "D", "text": explanation},
                    ],
                    "answer": "A", "explanation": explanation,
                }

            return validate_question_bank_v4({
                "format": "wenyan-question-bank", "schemaVersion": "4.0",
                "bankId": bank_id, "title": "测试题库", "description": "",
                "questionTypes": [{"id": "context_meaning", "label": "语境释义题", "description": ""}],
                "books": [{"id": book_id, "label": "必修上册", "order": 1}],
                "catalog": [{
                    "id": article_id, "bookId": book_id, "unit": "一",
                    "title": "劝学", "author": "荀子",
                }],
                "questions": [
                    question(f"{id_prefix}qa", 1, "学：学习，第一种讲法。"),
                    question(f"{id_prefix}qb", 2, "学：学习，第二种讲法。"),
                ],
                "workflow": {"reviews": {
                    f"{id_prefix}qa": {"status": "pending"},
                    f"{id_prefix}qb": {"status": "pending"},
                }, "duplicateResolutions": {}},
            })

        def dup_group(questions):
            core = make_question_core_signature(questions[0])
            group_id = make_duplicate_group_id(core)
            ordered = sorted(questions, key=lambda item: item["id"])
            fingerprint = hashlib.sha256(
                "|".join(
                    f"{item['id']}:{make_question_semantic_fingerprint(item)}"
                    for item in ordered
                ).encode()
            ).hexdigest()
            return group_id, fingerprint

        def with_decisions(bank, decisions):
            group_id, fingerprint = dup_group(bank["questions"])
            bank["workflow"]["duplicateResolutions"] = {group_id: {
                "fingerprint": fingerprint,
                "questionIds": [q["id"] for q in bank["questions"]],
                "decisions": decisions, "updatedAt": "",
            }}
            bank = validate_question_bank_v4(bank)
            stored = bank["workflow"]["duplicateResolutions"][group_id]["decisions"]
            self.assertEqual(stored, decisions)
            return bank, group_id

        classroom = dup_bank("bank_classroom", "")
        teacher = dup_bank("bank_teacher", "t", article_id="teacher_article_1", book_id="teacher_book_1")
        teacher, _teacher_gid = with_decisions(teacher, {"tqa": "kept", "tqb": "skipped"})
        # After mapping, the group is identified by the local core signature.
        group_id, _fp = dup_group(classroom["questions"])

        merged = merge_question_bank_v4(classroom, teacher, mode="merge")
        decisions = merged["bank"]["workflow"]["duplicateResolutions"][group_id]["decisions"]
        self.assertEqual(decisions, {"qa": "kept", "qb": "skipped"})
        # Foreign IDs are never written into the local bank.
        self.assertNotIn("tqa", decisions)
        self.assertEqual(len(merged["bank"]["questions"]), 2)
        self.assertEqual(
            len(merged["report"]["duplicateDecisionTransfers"]), 2,
        )

        # Local kept/skipped is never overwritten by the teacher file.
        classroom2, _gid2 = with_decisions(dup_bank("bank_classroom", ""), {"qa": "skipped", "qb": "skipped"})
        merged2 = merge_question_bank_v4(classroom2, teacher, mode="merge")["bank"]
        self.assertEqual(
            merged2["workflow"]["duplicateResolutions"][group_id]["decisions"],
            {"qa": "skipped", "qb": "skipped"},
        )

        # Changed group membership blocks the transfer.
        classroom3 = dup_bank("bank_classroom", "")
        extra = copy.deepcopy(classroom3["questions"][1])
        extra["id"] = "qc"
        extra["number"] = 3
        extra["options"] = [
            {"key": "A", "text": "学习"}, {"key": "B", "text": "学校"},
            {"key": "C", "text": "学堂"}, {"key": "D", "text": "学：第三种讲法。"},
        ]
        extra["explanation"] = "学：学习，第三种讲法。"
        classroom3["questions"].append(extra)
        classroom3["workflow"]["reviews"]["qc"] = {"status": "pending"}
        classroom3 = validate_question_bank_v4(classroom3)
        merged3 = merge_question_bank_v4(classroom3, teacher, mode="merge")["bank"]
        self.assertEqual(
            merged3["workflow"]["duplicateResolutions"][group_id]["decisions"], {}
        )

    def test_review_supplement_history_and_revoke(self) -> None:
        classroom = classroom_bank()
        teacher = teacher_bank()
        merged = merge_question_bank_v4(classroom, teacher, mode="merge")["bank"]
        delta = question_service.build_import_delta(classroom, merged)
        # Only actually changed reviews are recorded.
        self.assertEqual(set(delta["updatedReviews"]), {"qc", "qd"})
        self.assertEqual(delta["updatedReviews"]["qc"]["before"]["status"], "pending")
        self.assertEqual(delta["updatedReviews"]["qc"]["after"]["status"], "passed")

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
                    path, payload, temp_root / "backups",
                )
                server.QUESTIONS_PATH.write_text(
                    json.dumps(merged, ensure_ascii=False), encoding="utf-8",
                )
                server.QUESTION_REVIEWS_PATH.write_text(
                    '{"schemaVersion": 1, "reviews": {}}', encoding="utf-8",
                )
                event = {
                    "id": "import-review-1", "kind": "import", "mode": "merge",
                    "sourceName": "老师题库.json",
                    "createdAt": "2026-09-06T14:00:00",
                    "questionCountBefore": len(classroom["questions"]),
                    "questionCountAfter": len(merged["questions"]),
                    "addedQuestionIds": [
                        q["id"] for q in merged["questions"]
                        if q["id"] not in {qq["id"] for qq in classroom["questions"]}
                    ],
                    "addedArticleIds": [], "addedBookIds": [], "addedTypeIds": [],
                    "beforeHash": server.make_json_etag(classroom),
                    "afterHash": server.make_json_etag(merged),
                    **delta,
                }
                server.QUESTION_BANK_HISTORY_PATH.write_text(
                    json.dumps({"schemaVersion": 1, "events": [event]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                view = server.question_bank_history_view(
                    server.validate_question_bank_history(
                        json.loads(server.QUESTION_BANK_HISTORY_PATH.read_text(encoding="utf-8"))
                    ),
                    merged,
                )
                self.assertTrue(view["events"][0]["canRevoke"])

                revoked_bank, _history = server.revoke_question_bank_import("import-review-1")
                self.assertEqual(revoked_bank["workflow"]["reviews"]["qc"]["status"], "pending")
                self.assertEqual(revoked_bank["workflow"]["reviews"]["qd"]["status"], "pending")
                # Untouched local reviews survive the revoke untouched.
                self.assertEqual(revoked_bank["workflow"]["reviews"]["qa"]["status"], "passed")

                # A later manual edit blocks revoke.
                server.QUESTIONS_PATH.write_text(
                    json.dumps(merged, ensure_ascii=False), encoding="utf-8",
                )
                server.QUESTION_BANK_HISTORY_PATH.write_text(
                    json.dumps({"schemaVersion": 1, "events": [event]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                edited = copy.deepcopy(merged)
                edited["workflow"]["reviews"]["qc"] = {
                    "status": "needs_revision", "note": "教室复改",
                    "suggestedAnswer": None, "optionIssues": [],
                    "reviewedAt": "2026-09-06T15:00:00",
                }
                server.QUESTIONS_PATH.write_text(
                    json.dumps(edited, ensure_ascii=False), encoding="utf-8",
                )
                view2 = server.question_bank_history_view(
                    server.validate_question_bank_history(
                        json.loads(server.QUESTION_BANK_HISTORY_PATH.read_text(encoding="utf-8"))
                    ),
                    edited,
                )
                self.assertFalse(view2["events"][0]["canRevoke"])
                with self.assertRaises(ValueError):
                    server.revoke_question_bank_import("import-review-1")
            finally:
                (server.QUESTIONS_PATH, server.QUESTION_REVIEWS_PATH,
                 server.QUESTION_BANK_HISTORY_PATH, server.backup_and_write) = old_paths


def _review_summary(bank):
    summary = {"pending": 0, "passed": 0, "needs_revision": 0, "skipped": 0}
    for review in bank.get("workflow", {}).get("reviews", {}).values():
        status = review.get("status") if isinstance(review, dict) else "pending"
        if status in summary:
            summary[status] += 1
    return summary


if __name__ == "__main__":
    unittest.main()
