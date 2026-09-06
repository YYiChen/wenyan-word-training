"""Sync server tests: auth, operations, conflicts, backups (§107)."""

from __future__ import annotations

import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sync_server"))

import sync_protocol as protocol  # noqa: E402
from storage import StaleConflict, SyncStorage  # noqa: E402
from auth import SyncAuth  # noqa: E402


def make_bank():
    from server_validators import validate_question_bank_v4
    return validate_question_bank_v4({
        "format": "wenyan-question-bank", "schemaVersion": "4.0", "bankId": "bank_sync",
        "title": "同步题库", "description": "",
        "questionTypes": [{"id": "context_meaning", "label": "X", "description": ""}],
        "books": [{"id": "book-1", "label": "B", "order": 1}],
        "catalog": [{"id": "article-1", "bookId": "book-1", "unit": "U", "title": "T", "author": "A"}],
        "questions": [
            {"id": "q1", "number": 1, "type": "context_meaning", "articleId": "article-1",
             "word": "利", "sentence": "金就砺则利。", "targetOccurrence": 1, "stem": "",
             "options": [{"key": k, "text": f"o{k}1"} for k in "ABCD"],
             "answer": "A", "explanation": "e1"},
            {"id": "q2", "number": 2, "type": "context_meaning", "articleId": "article-1",
             "word": "学", "sentence": "学而时习之。", "targetOccurrence": 1, "stem": "",
             "options": [{"key": k, "text": f"o{k}2"} for k in "ABCD"],
             "answer": "A", "explanation": "e2"},
        ],
        "workflow": {"reviews": {"q1": {"status": "pending"}, "q2": {"status": "passed"}},
                     "duplicateResolutions": {}},
    })


def make_stack():
    directory = tempfile.mkdtemp(prefix="wenyan-sync-test-")
    storage = SyncStorage(Path(directory) / "sync.db")
    auth = SyncAuth(storage)
    return storage, auth


def create_account(storage, username="teacher01", password="secret123"):
    salt = protocol.new_salt_hex()
    key = protocol.derive_auth_key(password, salt, protocol.KDF_ITERATIONS)
    storage.create_user(username, salt, key.hex(), protocol.KDF_ITERATIONS)
    return salt


def login(auth, username="teacher01", password="secret123"):
    challenge = auth.issue_challenge(username)
    key = protocol.derive_auth_key(password, challenge["salt"], challenge["iterations"])
    proof = protocol.hmac_hex(
        key, f"{challenge['challenge_id']}|{challenge['nonce']}|{username}")
    return auth.verify_login(username, challenge["challenge_id"], proof)


def review_op(client_id, qid, base_status, new_status, seq=0):
    base = {"status": base_status} if base_status else None
    new = {"status": new_status}
    return {
        "operation_id": protocol.deterministic_operation_id(
            client_id, "review", qid,
            protocol.canonical_hash(base), protocol.canonical_hash(new)),
        "client_id": client_id,
        "operation_type": "review_set",
        "entity_kind": "review",
        "entity_id": qid,
        "base": base,
        "new": new,
    }


class SyncServerTests(unittest.TestCase):
    def setUp(self):
        self.storage, self.auth = make_stack()
        create_account(self.storage)
        from server import SyncApplication
        self.validate = SyncApplication.validate_bank

    def tearDown(self):
        self.storage.close()

    def bootstrap(self):
        return self.storage.bootstrap(make_bank(), None, self.validate)

    # -- users & auth -------------------------------------------------
    def test_user_list_and_disable(self):
        self.storage.set_user_enabled("teacher01", False)
        with self.assertRaises(ValueError):
            self.auth.issue_challenge("teacher01")
        users = self.storage.list_users()
        self.assertEqual(users[0]["enabled"], 0)
        self.storage.set_user_enabled("teacher01", True)
        self.auth.issue_challenge("teacher01")

    def test_login_rejects_wrong_password(self):
        with self.assertRaises(ValueError):
            login(self.auth, password="wrongpass")

    def test_challenge_is_single_use(self):
        challenge = self.auth.issue_challenge("teacher01")
        key = protocol.derive_auth_key("secret123", challenge["salt"], challenge["iterations"])
        proof = protocol.hmac_hex(key, f"{challenge['challenge_id']}|{challenge['nonce']}|teacher01")
        self.auth.verify_login("teacher01", challenge["challenge_id"], proof)
        with self.assertRaises(ValueError):
            self.auth.verify_login("teacher01", challenge["challenge_id"], proof)

    def test_request_signature_and_replay_rejection(self):
        session = login(self.auth)
        headers = {"x-sync-session": session["session_id"], "x-sync-seq": "1",
                   "x-sync-signature": ""}
        body = b"{}"
        body_hash = __import__("hashlib").sha256(body).hexdigest()
        headers["x-sync-signature"] = self.auth.sign_request(
            session["session_secret"], session["session_id"], 1, "GET", "/x", body_hash)
        self.assertEqual(
            self.auth.verify_request(headers, "GET", "/x", body), "teacher01")
        # Replay the same sequence: rejected.
        with self.assertRaises(ValueError):
            self.auth.verify_request(headers, "GET", "/x", body)
        # Tampered body: rejected.
        headers2 = dict(headers, **{"x-sync-seq": "2"})
        with self.assertRaises(ValueError):
            self.auth.verify_request(headers2, "GET", "/x", b'{"evil":true}')

    def test_session_expiration(self):
        session = login(self.auth)
        with self.auth._lock:
            self.auth._sessions[session["session_id"]]["expires"] = time.time() - 1
        headers = {"x-sync-session": session["session_id"], "x-sync-seq": "9",
                   "x-sync-signature": "x"}
        with self.assertRaises(ValueError):
            self.auth.verify_request(headers, "GET", "/x", b"")

    def test_login_rate_limit(self):
        for _ in range(5):
            self.auth.record_login_failure("1.2.3.4", "teacher01")
        self.assertFalse(self.auth.check_login_allowed("1.2.3.4", "teacher01"))
        self.assertTrue(self.auth.check_login_allowed("1.2.3.5", "teacher01"))

    # -- workspace ------------------------------------------------------
    def test_bootstrap_and_snapshot(self):
        result = self.bootstrap()
        self.assertEqual(result["revision"], 1)
        state = self.storage.get_state()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["bank"]["bankId"], "bank_sync")
        # Second bootstrap with stale base is rejected.
        with self.assertRaises(ValueError):
            self.storage.bootstrap(make_bank(), "stale-etag", self.validate)

    def test_operation_idempotence_and_revision_monotonic(self):
        self.bootstrap()
        op = review_op("client_a", "q1", "pending", "passed")
        first = self.storage.apply_operation(op, "client_a", "teacher01", self.validate)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["revision"], 2)
        again = self.storage.apply_operation(copy.deepcopy(op), "client_a", "teacher01", self.validate)
        self.assertEqual(again["status"], "accepted")
        self.assertEqual(again["revision"], 2)
        self.assertEqual(self.storage.get_state()["revision"], 2)

    def test_review_pending_to_passed(self):
        self.bootstrap()
        outcome = self.storage.apply_operation(
            review_op("client_a", "q1", "pending", "passed"), "client_a", "teacher01", self.validate)
        self.assertEqual(outcome["status"], "accepted")
        state = self.storage.get_state()
        self.assertEqual(state["bank"]["workflow"]["reviews"]["q1"]["status"], "passed")

    def test_review_same_status_metadata_keeps_server(self):
        self.bootstrap()
        op = review_op("client_a", "q2", "passed", "passed")
        op["new"] = {"status": "passed", "note": "client note"}
        op["operation_id"] = protocol.deterministic_operation_id(
            "client_a", "review", "q2",
            protocol.canonical_hash(op["base"]), protocol.canonical_hash(op["new"]))
        outcome = self.storage.apply_operation(op, "client_a", "teacher01", self.validate)
        self.assertEqual(outcome["status"], "noop")
        state = self.storage.get_state()
        self.assertNotEqual(state["bank"]["workflow"]["reviews"]["q2"].get("note"), "client note")

    def test_review_conflict_and_resolve(self):
        self.bootstrap()
        # Lecturer client reviews first.
        self.storage.apply_operation(
            review_op("client_b", "q1", "pending", "needs_revision"),
            "client_b", "teacher01", self.validate)
        # Office client based on the old pending value submits passed.
        outcome = self.storage.apply_operation(
            review_op("client_a", "q1", "pending", "passed"),
            "client_a", "teacher01", self.validate)
        self.assertEqual(outcome["status"], "conflict")
        conflict = outcome["conflict"]
        self.assertEqual(conflict["entity_id"], "q1")
        # Server value untouched.
        state = self.storage.get_state()
        self.assertEqual(state["bank"]["workflow"]["reviews"]["q1"]["status"], "needs_revision")
        # No revision consumed by the conflict itself.
        self.assertEqual(state["revision"], 2)
        # Resolve to incoming.
        resolved = self.storage.resolve_conflict(
            conflict["conflict_id"], "incoming", "teacher01", self.validate)
        self.assertEqual(resolved["status"], "resolved")
        state = self.storage.get_state()
        self.assertEqual(state["revision"], 3)
        self.assertEqual(state["bank"]["workflow"]["reviews"]["q1"]["status"], "passed")
        # Stale resolution is rejected.
        with self.assertRaises(StaleConflict):
            self.storage.resolve_conflict(conflict["conflict_id"], "server", "teacher01", self.validate)

    def test_question_content_conflict(self):
        self.bootstrap()
        state = self.storage.get_state()
        question = next(q for q in state["bank"]["questions"] if q["id"] == "q1")
        review = state["bank"]["workflow"]["reviews"]["q1"]
        base_value = {"question": question, "review": review}
        server_value = copy.deepcopy(base_value)
        server_value["question"] = dict(server_value["question"], explanation="server edit")
        incoming_value = copy.deepcopy(base_value)
        incoming_value["question"] = dict(incoming_value["question"], explanation="client edit")

        def put_op(client_id, value):
            return {
                "operation_id": protocol.deterministic_operation_id(
                    client_id, "question", "q1",
                    protocol.canonical_hash(base_value), protocol.canonical_hash(value)),
                "client_id": client_id,
                "operation_type": "question_put",
                "entity_kind": "question",
                "entity_id": "q1",
                "base": base_value,
                "new": value,
            }

        first = self.storage.apply_operation(
            put_op("client_b", server_value), "client_b", "teacher01", self.validate)
        self.assertEqual(first["status"], "accepted")
        second = self.storage.apply_operation(
            put_op("client_a", incoming_value), "client_a", "teacher01", self.validate)
        self.assertEqual(second["status"], "conflict")
        state = self.storage.get_state()
        kept = next(q for q in state["bank"]["questions"] if q["id"] == "q1")
        self.assertEqual(kept["explanation"], "server edit")

    def test_changes_after_revision(self):
        self.bootstrap()
        self.storage.apply_operation(
            review_op("client_a", "q1", "pending", "passed"), "client_a", "teacher01", self.validate)
        changes = self.storage.list_changes(1, 500)
        self.assertEqual(changes["revision"], 2)
        self.assertEqual(len(changes["operations"]), 1)
        self.assertEqual(changes["operations"][0]["revision"], 2)
        self.assertEqual(self.storage.list_changes(2, 500)["operations"], [])

    def test_invalid_bank_rejected_atomically(self):
        self.bootstrap()
        before = self.storage.get_state()["revision"]
        bad = review_op("client_a", "q1", "pending", "passed")
        bad["operation_type"] = "bogus_type"
        with self.assertRaises(ValueError):
            self.storage.apply_operation(bad, "client_a", "teacher01", self.validate)
        self.assertEqual(self.storage.get_state()["revision"], before)

    # -- backups ----------------------------------------------------------
    def test_backup_does_not_change_revision(self):
        self.bootstrap()
        bank = self.storage.get_state()["bank"]
        raw = __import__("json").dumps(bank, ensure_ascii=False).encode("utf-8")
        summary = protocol.summarize_bank(bank)
        self.storage.add_backup({
            "backup_id": "bk_test", "file_name": "bk_test.json",
            "sha256": "x" * 64, "size": len(raw), "bank_id": summary["bank_id"],
            "question_count": summary["question_count"], "review_summary": summary["review_summary"],
            "username": "teacher01", "client_id": "client_a", "device_name": "office",
            "created_at": "2026-01-01T00:00:00",
        })
        self.assertEqual(self.storage.get_state()["revision"], 1)
        backups = self.storage.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["question_count"], 2)
        self.assertIsNotNone(self.storage.get_backup("bk_test"))
        self.assertIsNone(self.storage.get_backup("bk_missing"))


if __name__ == "__main__":
    unittest.main()
