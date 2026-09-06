"""Sync client tests: pure diff, worker, and two-client scenarios (§108-113)."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sync_server"))

import sync_protocol as protocol  # noqa: E402
import server_sync  # noqa: E402
from server_sync import SyncWorker  # noqa: E402
from storage import SyncStorage  # noqa: E402
from server import SyncApplication, SyncRequestHandler  # noqa: E402


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


def set_review(bank, qid, status):
    bank = copy.deepcopy(bank)
    bank["workflow"]["reviews"][qid] = {"status": status}
    from server_validators import validate_question_bank_v4
    return validate_question_bank_v4(bank)


class FakeBankAccess(server_sync.LocalBankAccess):
    def __init__(self, bank):
        self.bank = copy.deepcopy(bank)

    def read_bank(self):
        from server_validators import validate_question_bank_v4
        return validate_question_bank_v4(copy.deepcopy(self.bank))

    def write_bank(self, bank):
        from server_validators import validate_question_bank_v4
        self.bank = validate_question_bank_v4(copy.deepcopy(bank))


class SyncProtocolTests(unittest.TestCase):
    def test_identical_banks_produce_no_operations(self):
        bank = make_bank()
        self.assertEqual(protocol.build_sync_operations(bank, bank, "client_a"), [])

    def test_pending_to_passed_produces_one_review_set(self):
        base = make_bank()
        local = set_review(base, "q1", "passed")
        operations = protocol.build_sync_operations(base, local, "client_a")
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["operation_type"], "review_set")
        self.assertEqual(operations[0]["new"]["status"], "passed")

    def test_operation_id_is_deterministic(self):
        base = make_bank()
        local = set_review(base, "q1", "passed")
        first = protocol.build_sync_operations(base, local, "client_a")
        second = protocol.build_sync_operations(base, local, "client_a")
        self.assertEqual(first[0]["operation_id"], second[0]["operation_id"])
        third = protocol.build_sync_operations(base, local, "client_b")
        self.assertNotEqual(first[0]["operation_id"], third[0]["operation_id"])

    def test_question_edit_travels_with_review(self):
        base = make_bank()
        local = copy.deepcopy(base)
        local["questions"][0]["explanation"] = "changed"
        from server_validators import validate_question_bank_v4
        local = validate_question_bank_v4(local)
        operations = protocol.build_sync_operations(base, local, "client_a")
        kinds = {operation["operation_type"] for operation in operations}
        self.assertIn("question_put", kinds)
        self.assertNotIn("review_set", kinds)
        put = next(o for o in operations if o["operation_type"] == "question_put")
        self.assertIn("review", put["base"])
        self.assertIn("review", put["new"])
        self.assertIn("question", put["base"])
        self.assertIn("question", put["new"])

    def test_validate_operation_shape_rejects_garbage(self):
        with self.assertRaises(ValueError):
            protocol.validate_operation_shape({"operation_type": "bogus"})
        with self.assertRaises(ValueError):
            protocol.validate_operation_shape([])


class LiveServerMixin:
    def start_server(self):
        if getattr(self, "application", None) is None:
            self.server_dir = Path(tempfile.mkdtemp(prefix="wenyan-sync-live-"))
            self.application = SyncApplication(self.server_dir)
            salt = protocol.new_salt_hex()
            key = protocol.derive_auth_key("secret123", salt, protocol.KDF_ITERATIONS)
            self.application.storage.create_user(
                "teacher01", salt, key.hex(), protocol.KDF_ITERATIONS)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), SyncRequestHandler)
        self.httpd.application = self.application
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        # Re-point existing clients at the new port.
        for record in getattr(self, "_clients", {}).values():
            user_dir = record["user_dir"]
            settings_path = user_dir / "sync-settings.json"
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            settings["port"] = self.port
            settings_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
            material = record.get("material", {})
            material["port"] = self.port
        return self.port

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def close_server(self):
        self.application.storage.close()

    def client_env(self, name):
        user_dir = Path(tempfile.mkdtemp(prefix=f"wenyan-client-{name}-"))
        bank_access = FakeBankAccess(make_bank())
        material = {"host": "127.0.0.1", "port": self.port, "username": "teacher01",
                    "auth_key": protocol.derive_auth_key(
                        "secret123",
                        self.application.storage.get_user("teacher01")["salt_hex"],
                        protocol.KDF_ITERATIONS).hex(),
                    "salt": self.application.storage.get_user("teacher01")["salt_hex"],
                    "iterations": protocol.KDF_ITERATIONS}
        settings = {"enabled": True, "host": "127.0.0.1", "port": self.port,
                    "username": "teacher01", "clientId": f"client_{name}",
                    "deviceName": name, "lastRevision": 0}
        worker = SyncWorker(bank_access)
        patchers = [
            patch.object(server_sync, "_user_data_dir", return_value=user_dir),
            patch.object(server_sync, "load_credential", return_value=material),
        ]
        (user_dir / "sync-settings.json").write_text(
            json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        if not hasattr(self, "_clients"):
            self._clients = {}
        self._clients[name] = {"worker": worker, "bank": bank_access,
                               "settings": settings, "patchers": patchers,
                               "material": material, "user_dir": user_dir, "live": []}
        return worker, bank_access, settings

    def activate(self, name):
        """Only one client's global patches may be live at a time."""
        for record in self._clients.values():
            for patcher in record.get("live", []):
                patcher.stop()
            record["live"] = []
        record = self._clients[name]
        record["live"] = list(record["patchers"])
        for patcher in record["live"]:
            patcher.start()
        self.addCleanup(self._deactivate_all)

    def _deactivate_all(self):
        for record in getattr(self, "_clients", {}).values():
            for patcher in record.get("live", []):
                try:
                    patcher.stop()
                except RuntimeError:
                    pass
            record["live"] = []

    def bootstrap_as(self, name, action):
        self.activate(name)
        worker = self._clients[name]["worker"]
        if action == "server_empty":
            return server_sync.confirm_bootstrap_server_empty(worker._bank)
        return server_sync.confirm_bootstrap_local_empty(worker._bank)

    def cycle_as(self, name):
        self.activate(name)
        worker = self._clients[name]["worker"]
        worker._session = None
        return worker.cycle_once()


class SyncScenarioTests(unittest.TestCase, LiveServerMixin):
    def setUp(self):
        self._clients = {}
        self.application = None
        self.start_server()

    def tearDown(self):
        try:
            self.stop_server()
        except Exception:
            pass
        if getattr(self, "application", None) is not None:
            self.close_server()

    def test_office_lectern_split_reviews_converge(self):
        """§109/110: A→passed on lectern reaches office within one cycle."""
        _, office_bank, _ = self.client_env("office")
        _, lectern_bank, _ = self.client_env("lectern")
        # Bootstrap: office uploads baseline, lectern adopts it.
        self.bootstrap_as("office", "server_empty")
        self.bootstrap_as("lectern", "local_empty")
        self.assertEqual(office_bank.bank["bankId"], lectern_bank.bank["bankId"])
        # Lectern reviews q1; office reviews nothing new.
        lectern_bank.bank = set_review(lectern_bank.bank, "q1", "passed")
        self.cycle_as("lectern")
        self.cycle_as("office")
        self.assertEqual(office_bank.bank["workflow"]["reviews"]["q1"]["status"], "passed")
        # Split reviews without conflict converge both ways.
        office_bank.bank = set_review(office_bank.bank, "q1", "passed")
        office_bank.bank = set_review(office_bank.bank, "q2", "needs_revision")
        lectern_bank.bank = set_review(lectern_bank.bank, "q1", "passed")
        self.cycle_as("office")
        self.cycle_as("lectern")
        self.cycle_as("office")
        for access in (office_bank, lectern_bank):
            reviews = access.bank["workflow"]["reviews"]
            self.assertEqual(reviews["q1"]["status"], "passed")
            self.assertEqual(reviews["q2"]["status"], "needs_revision")
        state = self.application.storage.get_state()
        self.assertEqual(
            state["bank"]["workflow"]["reviews"]["q2"]["status"], "needs_revision")

    def test_review_conflict_blocks_and_resolves(self):
        """§111: divergent reviews become a shared conflict; E blocked."""
        _, office_bank, _ = self.client_env("office")
        _, lectern_bank, _ = self.client_env("lectern")
        self.bootstrap_as("office", "server_empty")
        self.bootstrap_as("lectern", "local_empty")
        # Lectern goes first while office is offline.
        lectern_bank.bank = set_review(lectern_bank.bank, "q1", "needs_revision")
        self.cycle_as("lectern")
        # Office diverged from the old baseline.
        office_bank.bank = set_review(office_bank.bank, "q1", "passed")
        self.cycle_as("office")
        conflicts = self.application.storage.list_open_conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["entity_id"], "q1")
        # Office sees the conflict and blocks the question locally.
        status = self.cycle_as("office")
        self.assertEqual(status["phase"], "conflict")
        self.activate("office")
        office_state = server_sync.load_state()
        self.assertIn("q1", office_state["blockedQuestionIds"])
        # Resolve to office (incoming): all clients converge, block clears.
        resolved = server_sync.resolve_conflict(conflicts[0]["conflict_id"], "incoming")
        self.assertEqual(resolved["status"], "resolved")
        self.cycle_as("office")
        self.cycle_as("lectern")
        for access in (office_bank, lectern_bank):
            self.assertEqual(access.bank["workflow"]["reviews"]["q1"]["status"], "passed")
        self.activate("office")
        office_state = server_sync.load_state()
        self.assertNotIn("q1", office_state.get("blockedQuestionIds", []))

    def test_offline_queues_then_catches_up(self):
        """§112: offline reviews sync automatically after reconnect."""
        _, office_bank, _ = self.client_env("office")
        self.bootstrap_as("office", "server_empty")
        self.stop_server()
        office_bank.bank = set_review(office_bank.bank, "q1", "passed")
        office_bank.bank = set_review(office_bank.bank, "q2", "needs_revision")
        self.activate("office")
        try:
            worker = self._clients["office"]["worker"]
            worker._session = None
            worker.cycle_once()
        except server_sync.SyncOffline:
            pass
        status = server_sync.public_sync_status()
        self.assertEqual(status["phase"], "offline")
        self.assertGreaterEqual(status["pendingLocal"], 1)
        # Local quiz data untouched by the outage.
        self.assertEqual(office_bank.bank["workflow"]["reviews"]["q1"]["status"], "passed")
        self.start_server()
        result = self.cycle_as("office")
        self.assertIn(result["phase"], ("connected", "conflict"))
        state = self.application.storage.get_state()
        self.assertEqual(state["bank"]["workflow"]["reviews"]["q1"]["status"], "passed")

    def test_backup_leaves_live_revision_alone(self):
        """§113: upload/list/download without touching live state or bank."""
        _, office_bank, _ = self.client_env("office")
        self.bootstrap_as("office", "server_empty")
        self.activate("office")
        worker = self._clients["office"]["worker"]
        before = self.application.storage.get_state()["revision"]
        result = server_sync.upload_backup(worker._bank)
        self.assertIn("backup_id", result)
        self.assertEqual(self.application.storage.get_state()["revision"], before)
        backups = server_sync.list_remote_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["question_count"], 2)
        filename, content = server_sync.download_backup(backups[0]["backup_id"])
        self.assertTrue(filename.endswith(".json"))
        downloaded = json.loads(content.decode("utf-8"))
        self.assertEqual(downloaded["bankId"], "bank_sync")
        self.assertEqual(len(downloaded["questions"]), 2)
        # Local bank untouched by download.
        self.assertEqual(office_bank.bank["bankId"], "bank_sync")

    def test_bank_id_drift_pauses_sync(self):
        _, office_bank, _ = self.client_env("office")
        self.bootstrap_as("office", "server_empty")
        drifted = dict(office_bank.bank, bankId="bank_other")
        office_bank.bank = drifted
        status = self.cycle_as("office")
        self.assertEqual(status["phase"], "paused")
        # Nothing was pushed.
        self.assertEqual(self.application.storage.get_state()["revision"], 1)

    def test_crash_before_shadow_is_retry_safe(self):
        """Server accepted, client crashed before shadow: retry is a noop."""
        _, office_bank, _ = self.client_env("office")
        self.bootstrap_as("office", "server_empty")
        self.activate("office")
        shadow = server_sync.load_shadow()
        local = set_review(office_bank.bank, "q1", "passed")
        operations = protocol.build_sync_operations(shadow["bank"], local, "client_office")
        self.assertEqual(len(operations), 1)
        session = server_sync.login_with_material({
            "host": "127.0.0.1", "port": self.port, "username": "teacher01",
            "auth_key": protocol.derive_auth_key(
                "secret123",
                self.application.storage.get_user("teacher01")["salt_hex"],
                protocol.KDF_ITERATIONS).hex(),
            "salt": self.application.storage.get_user("teacher01")["salt_hex"],
            "iterations": protocol.KDF_ITERATIONS})
        first = session.call("POST", "/api/v1/sync/push", {
            "client_id": "client_office", "device_name": "office", "operations": operations})
        rev_after_first = first["revision"]
        # Crash: shadow never updated. Retry resends the identical operation.
        retry = session.call("POST", "/api/v1/sync/push", {
            "client_id": "client_office", "device_name": "office", "operations": operations})
        self.assertEqual(retry["revision"], rev_after_first)
        self.assertEqual(
            self.application.storage.get_state()["revision"], rev_after_first)


if __name__ == "__main__":
    unittest.main()
