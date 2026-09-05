from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402


PUBLIC_SAMPLE_PATH = ROOT / "public-data" / "questions.json"


class BlankQuestionBankTests(unittest.TestCase):
    def test_missing_bank_is_initialized_from_public_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            question_path = Path(temporary_directory) / "data" / "questions.json"
            with patch.object(server, "QUESTIONS_PATH", question_path), patch.object(
                server, "PUBLIC_QUESTION_BANK_PATH", PUBLIC_SAMPLE_PATH
            ):
                server.ensure_question_bank()

            seeded = server.read_json(question_path)
            self.assertEqual(len(seeded["questions"]), 87)

    def test_public_sample_has_three_verified_questions_per_article(self) -> None:
        bank = server.validate_questions(server.read_json(PUBLIC_SAMPLE_PATH))
        counts = {}
        for question in bank["questions"]:
            counts[question["articleId"]] = counts.get(question["articleId"], 0) + 1

        article_ids = {article["id"] for article in bank["catalog"]}
        self.assertEqual(len(bank["questions"]), 87)
        self.assertEqual(article_ids, set(counts))
        self.assertTrue(all(count == 3 for count in counts.values()))

    def test_blank_bank_is_valid_for_public_packages(self) -> None:
        bank = server.validate_questions(server.empty_question_bank())
        self.assertEqual(bank["questions"], [])
        self.assertEqual(bank["catalog"], [])
        self.assertEqual(bank["books"], [])
        self.assertEqual(bank["questionTypes"], [])

    def test_non_empty_bank_still_requires_catalog(self) -> None:
        bank = server.empty_question_bank()
        bank["questions"] = [{"id": "q-1"}]
        with self.assertRaises(ValueError):
            server.validate_questions(bank)


if __name__ == "__main__":
    unittest.main()
