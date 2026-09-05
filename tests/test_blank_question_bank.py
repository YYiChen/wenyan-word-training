from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402


class BlankQuestionBankTests(unittest.TestCase):
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
