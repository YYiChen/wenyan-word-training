from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_server as server  # noqa: E402
import server_auth as auth  # noqa: E402


class AdminAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temporary_directory.name)
        self.settings_path = self.temp_root / "admin-settings.json"
        self.leaderboard_path = self.temp_root / "leaderboard.json"
        self.leaderboard_path.write_text("[]\n", encoding="utf-8")
        self.settings_path.write_text(
            json.dumps({"schemaVersion": 1, "passwordHash": auth.hash_admin_password("oldpass")}),
            encoding="utf-8",
        )
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(server, "ADMIN_SETTINGS_PATH", self.settings_path))
        self.patches.enter_context(patch.object(server, "LEADERBOARD_PATH", self.leaderboard_path))
        self.patches.enter_context(patch.object(server, "ALLOW_BROWSER_ADMIN_LOGIN", False))
        auth.ADMIN_SESSIONS.clear()
        auth.ADMIN_LAUNCH_TICKETS.clear()
        self.http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.QuizRequestHandler)
        self.thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.http_server.server_address[1]}"

    def tearDown(self) -> None:
        self.http_server.shutdown()
        self.http_server.server_close()
        self.thread.join(timeout=2)
        auth.ADMIN_SESSIONS.clear()
        auth.ADMIN_LAUNCH_TICKETS.clear()
        self.patches.close()
        self.temporary_directory.cleanup()

    def request_json(
        self,
        method: str,
        route: str,
        payload: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(f"{self.base_url}{route}", data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))
        except RemoteDisconnected as error:
            self.fail(f"本地测试服务意外断开：{error}")

    def test_launch_ticket_is_single_use_and_expires(self) -> None:
        ticket = auth.create_admin_launch_ticket()
        self.assertTrue(auth.consume_admin_launch_ticket(ticket))
        self.assertFalse(auth.consume_admin_launch_ticket(ticket))

        with patch.object(auth.time, "monotonic", side_effect=[100.0, 100.0, 131.0]):
            expired_ticket = auth.create_admin_launch_ticket()
            self.assertFalse(auth.consume_admin_launch_ticket(expired_ticket))

        self.assertFalse(auth.consume_admin_launch_ticket("not-a-real-ticket"))

    def test_concurrent_ticket_consumption_succeeds_once(self) -> None:
        ticket = auth.create_admin_launch_ticket()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _index: auth.consume_admin_launch_ticket(ticket), range(8)))
        self.assertEqual(results.count(True), 1)

    def test_launch_exchange_returns_admin_token_for_existing_admin_api(self) -> None:
        ticket = auth.create_admin_launch_ticket()
        status, payload = self.request_json("POST", "/api/admin-launch-session", {"ticket": ticket})
        self.assertEqual(status, 200)
        token = payload["data"]["token"]
        self.assertTrue(auth.is_valid_admin_session(token))

        status, _payload = self.request_json(
            "GET",
            "/api/leaderboard",
            headers={"X-Wenyan-Admin-Token": token},
        )
        self.assertEqual(status, 200)

        status, _payload = self.request_json("POST", "/api/admin-launch-session", {"ticket": ticket})
        self.assertEqual(status, 401)

    def test_browser_password_auth_is_disabled_by_default_and_explicit_dev_flag_enables_it(self) -> None:
        status, _payload = self.request_json("POST", "/api/admin-auth", {"password": "oldpass"})
        self.assertEqual(status, 403)

        server.ALLOW_BROWSER_ADMIN_LOGIN = True
        status, payload = self.request_json("POST", "/api/admin-auth", {"password": "oldpass"})
        self.assertEqual(status, 200)
        self.assertTrue(auth.is_valid_admin_session(payload["data"]["token"]))

    def test_password_change_reuses_writer_and_revokes_existing_sessions(self) -> None:
        written: list[tuple[Path, Path | None]] = []

        def writer(path: Path, payload: object, backup_dir: Path | None = None) -> None:
            written.append((path, backup_dir))
            path.write_text(json.dumps(payload), encoding="utf-8")

        current_session = auth.create_admin_session()
        self.assertFalse(
            auth.change_admin_password("wrongpass", "newpass1", self.settings_path, writer, self.temp_root / "backups")
        )
        with self.assertRaises(ValueError):
            auth.change_admin_password("oldpass", "short", self.settings_path, writer, self.temp_root / "backups")

        self.assertTrue(
            auth.change_admin_password("oldpass", "newpass1", self.settings_path, writer, self.temp_root / "backups")
        )
        self.assertFalse(auth.is_valid_admin_session(current_session))
        self.assertFalse(auth.authenticate_admin_password("oldpass", self.settings_path))
        self.assertTrue(auth.authenticate_admin_password("newpass1", self.settings_path))
        self.assertEqual(written, [(self.settings_path, self.temp_root / "backups")])

    def test_admin_password_http_api_is_migrated_to_launcher(self) -> None:
        token = auth.create_admin_session()
        status, _payload = self.request_json(
            "PUT",
            "/api/admin-settings",
            {"currentPassword": "oldpass", "newPassword": "newpass1"},
            headers={"X-Wenyan-Admin-Token": token},
        )
        self.assertEqual(status, 403)

    def test_student_runtime_has_no_admin_navigation_and_admin_scripts_have_no_password_settings_ui(self) -> None:
        student_sources = [ROOT / "index.html", ROOT / "app.js", *ROOT.glob("student-*.js")]
        student_text = "\n".join(path.read_text(encoding="utf-8") for path in student_sources)
        self.assertNotIn("admin.html", student_text)
        self.assertNotIn('class="admin-link"', student_text)

        admin_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "admin.js", ROOT / "admin-auth.js", ROOT / "admin-settings.js")
        )
        self.assertNotIn("currentPassword", admin_text)
        self.assertNotIn("newPassword", admin_text)
        self.assertNotIn("confirmPassword", admin_text)
        self.assertNotIn('data-tab="security"', admin_text)


if __name__ == "__main__":
    unittest.main()
