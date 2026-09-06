from __future__ import annotations

import inspect
import json
import os
import socket
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import update_helper  # noqa: E402
import update_service  # noqa: E402
import launcher  # noqa: E402


class UpdateServiceTests(unittest.TestCase):
    def test_semver_comparison_handles_two_digit_minor(self) -> None:
        self.assertGreater(update_service.parse_version("v1.10.0"), update_service.parse_version("1.9.0"))
        self.assertIsNone(update_service.parse_version("release-latest"))
        self.assertEqual(update_service.normalize_version("v1.3.0"), "1.3.0")

    def test_release_selection_ignores_prerelease_and_uses_digest(self) -> None:
        releases = [
            {
                "tag_name": "v1.4.0-rc.1",
                "prerelease": True,
                "assets": [],
            },
            {
                "tag_name": "v1.3.1",
                "name": "修复版",
                "body": "- 修复后台问题",
                "published_at": "2026-09-05T00:00:00Z",
                "html_url": "https://github.com/YYiChen/wenyan-word-training/releases/tag/v1.3.1",
                "assets": [
                    {
                        "name": "wenyan-word-training-v1.3.1-source.zip",
                        "browser_download_url": "https://github.com/YYiChen/wenyan-word-training/releases/download/v1.3.1/wenyan-word-training-v1.3.1-source.zip",
                        "size": 123,
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            },
        ]
        candidate = update_service.choose_release_candidate(
            releases,
            current_version="1.3.0",
            mode="source",
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["version"], "1.3.1")
        self.assertEqual(candidate["sha256"], "a" * 64)

    def test_checksum_fallback_is_supported(self) -> None:
        asset_name = "wenyan-word-training-v1.3.1-windows.zip"
        releases = [
            {
                "tag_name": "v1.3.1",
                "assets": [
                    {
                        "name": asset_name,
                        "browser_download_url": "https://github.com/YYiChen/wenyan-word-training/releases/download/v1.3.1/" + asset_name,
                        "size": 10,
                    }
                ],
            }
        ]
        candidate = update_service.choose_release_candidate(
            releases,
            current_version="1.3.0",
            mode="windows",
            checksum_map={asset_name: "b" * 64},
        )
        self.assertEqual(candidate["sha256"], "b" * 64)

    def test_force_check_bypasses_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "version.json").write_text(
                json.dumps({"version": "1.3.0", "repository": "YYiChen/wenyan-word-training"}),
                encoding="utf-8",
            )
            manager = update_service.UpdateManager(
                root=root,
                user_data_dir=root / "user-data",
                port=8000,
                frozen=False,
            )
            manager._last_check_at = time.monotonic()
            with patch("update_service.threading.Thread") as thread:
                manager.check_async(force=True)
            thread.assert_called_once()

    def test_update_user_agent_has_no_stale_version(self) -> None:
        self.assertNotRegex(update_service.USER_AGENT, r"/\d+\.\d+\.\d+")


class UpdateHelperTests(unittest.TestCase):
    def make_archive(self, root: Path, entries: dict[str, bytes], version: str = "1.3.1") -> Path:
        archive_path = root / "update.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            files = sorted(entries)
            archive.writestr(update_helper.MANIFEST_NAME, json.dumps({"version": version, "files": files}))
            for name, content in entries.items():
                archive.writestr(name, content)
        return archive_path

    def make_options(self, install_dir: Path, archive_path: Path) -> Namespace:
        return Namespace(
            parent_pid=0,
            install_dir=install_dir,
            archive=archive_path,
            version="1.3.1",
            restart_executable=Path(sys.executable),
            restart_arg=[],
            restart_url="http://127.0.0.1:8000/admin.html",
            previous_version="1.3.0",
            expected_app=update_helper.DEFAULT_EXPECTED_APP,
            health_url="http://127.0.0.1:8000/api/health",
        )

    def test_apply_update_preserves_all_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_dir = root / "install"
            data_dir = install_dir / "data"
            data_dir.mkdir(parents=True)
            (install_dir / "app.js").write_text("old", encoding="utf-8")
            (data_dir / "questions.json").write_text('{"private":true}', encoding="utf-8")
            user_data = root / "localappdata"
            leaderboard = user_data / "WenyanQuiz" / "leaderboard.json"
            leaderboard.parent.mkdir(parents=True)
            leaderboard.write_text("[1]", encoding="utf-8")
            archive_path = self.make_archive(root, {"app.js": b"new"})
            options = self.make_options(install_dir, archive_path)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(user_data)}, clear=False):
                update_helper.apply_update(options)
            self.assertEqual((install_dir / "app.js").read_text(encoding="utf-8"), "new")
            self.assertEqual((data_dir / "questions.json").read_text(encoding="utf-8"), '{"private":true}')
            self.assertEqual(leaderboard.read_text(encoding="utf-8"), "[1]")
            self.assertTrue((user_data / "WenyanQuiz" / "update-result.json").exists())

    def test_apply_update_replaces_manifest_and_removes_only_obsolete_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_dir = root / "install"
            install_dir.mkdir()
            (install_dir / "app.js").write_text("old", encoding="utf-8")
            (install_dir / "obsolete.js").write_text("obsolete", encoding="utf-8")
            (install_dir / "teacher-note.txt").write_text("keep", encoding="utf-8")
            (install_dir / update_helper.MANIFEST_NAME).write_text(
                json.dumps({"version": "1.3.0", "files": ["app.js", "obsolete.js"]}),
                encoding="utf-8",
            )
            data_dir = install_dir / "data"
            data_dir.mkdir()
            (data_dir / "questions.json").write_text('{"private":true}', encoding="utf-8")
            archive_path = self.make_archive(root, {"app.js": b"new", "new.js": b"new file"})
            options = self.make_options(install_dir, archive_path)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "localappdata")}, clear=False):
                transaction = update_helper.apply_update(options)
                self.assertEqual((install_dir / "app.js").read_text(encoding="utf-8"), "new")
                self.assertTrue((install_dir / "new.js").is_file())
                self.assertFalse((install_dir / "obsolete.js").exists())
                self.assertTrue((install_dir / "teacher-note.txt").is_file())
                self.assertEqual(
                    json.loads((install_dir / update_helper.MANIFEST_NAME).read_text(encoding="utf-8"))["version"],
                    "1.3.1",
                )
                self.assertEqual((data_dir / "questions.json").read_text(encoding="utf-8"), '{"private":true}')
                self.assertTrue(transaction.rollback())
                self.assertEqual((install_dir / "app.js").read_text(encoding="utf-8"), "old")
                self.assertTrue((install_dir / "obsolete.js").is_file())
                self.assertFalse((install_dir / "new.js").exists())
                self.assertEqual(
                    json.loads((install_dir / update_helper.MANIFEST_NAME).read_text(encoding="utf-8"))["version"],
                    "1.3.0",
                )

    def test_apply_update_can_replace_installed_updater(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_dir = root / "install"
            install_dir.mkdir()
            updater = install_dir / "文言实词更新助手.exe"
            updater.write_bytes(b"old updater")
            archive_path = self.make_archive(root, {"文言实词更新助手.exe": b"new updater"})
            options = self.make_options(install_dir, archive_path)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "localappdata")}, clear=False):
                update_helper.apply_update(options)
            self.assertEqual(updater.read_bytes(), b"new updater")

    def test_windows_updater_is_copied_outside_install_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_dir = root / "install"
            install_dir.mkdir()
            helper = install_dir / "文言实词更新助手.exe"
            helper.write_bytes(b"updater")
            user_data = root / "localappdata" / "WenyanQuiz"
            copied = update_service.prepare_runtime_updater(user_data, helper)
            try:
                self.assertNotEqual(copied.resolve(), helper.resolve())
                self.assertTrue(copied.is_file())
                self.assertTrue(str(copied).startswith(str(user_data / "updater-runtime")))
            finally:
                shutil.rmtree(user_data / "updater-runtime", ignore_errors=True)

    def test_launcher_keeps_verifying_marker_until_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            local_appdata = Path(temp)
            result_path = local_appdata / "WenyanQuiz" / "update-result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text(
                json.dumps({"version": "1.3.1", "ok": False, "phase": "verifying", "message": "正在验证"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}, clear=False):
                self.assertIsNone(launcher.read_update_result())
                self.assertTrue(result_path.exists())
                result_path.write_text(
                    json.dumps({"version": "1.3.1", "ok": True, "message": "更新成功", "token": "should-not-leak"}),
                    encoding="utf-8",
                )
                result = launcher.read_update_result()
            self.assertEqual(result["ok"], True)
            self.assertNotIn("token", result)
            self.assertFalse(result_path.exists())

    def test_launcher_cleanup_only_removes_old_updater_runtime_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            local_appdata = Path(temp)
            runtime_root = local_appdata / "WenyanQuiz" / "updater-runtime"
            old_dir = runtime_root / "run-old"
            recent_dir = runtime_root / "run-recent"
            unrelated_file = runtime_root / "keep.txt"
            old_dir.mkdir(parents=True)
            recent_dir.mkdir()
            unrelated_file.write_text("keep", encoding="utf-8")
            old_time = time.time() - (2 * 24 * 60 * 60)
            os.utime(old_dir, (old_time, old_time))
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}, clear=False):
                launcher.cleanup_stale_updater_runtime()
            self.assertFalse(old_dir.exists())
            self.assertTrue(recent_dir.exists())
            self.assertTrue(unrelated_file.exists())

    def test_process_wait_has_bounded_timeout_and_windows_api_path(self) -> None:
        source = inspect.getsource(update_helper.wait_for_process_exit)
        windows_source = inspect.getsource(update_helper._wait_for_process_exit_windows)
        self.assertIn("timeout", source)
        self.assertIn("WaitForSingleObject", windows_source)
        self.assertIn('if os.name == "nt":', source)
        with patch.object(update_helper.os, "name", "posix"), patch.object(update_helper.os, "kill") as kill:
            with self.assertRaises(TimeoutError):
                update_helper.wait_for_process_exit(12345, timeout=0)
            kill.assert_called_once_with(12345, 0)

    class _Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def test_health_verification_requires_app_and_target_version(self) -> None:
        opener = lambda _request, timeout: self._Response({
            "ok": True,
            "app": update_helper.DEFAULT_EXPECTED_APP,
            "version": "1.3.1",
        })
        payload = update_helper.wait_for_health(
            "http://127.0.0.1:8000/api/health",
            expected_app=update_helper.DEFAULT_EXPECTED_APP,
            expected_version="1.3.1",
            timeout=0.1,
            poll_interval=0.01,
            opener=opener,
        )
        self.assertEqual(payload["version"], "1.3.1")

    def test_health_verification_rejects_wrong_version_and_times_out(self) -> None:
        opener = lambda _request, timeout: self._Response({
            "ok": True,
            "app": update_helper.DEFAULT_EXPECTED_APP,
            "version": "1.3.0",
        })
        with self.assertRaises(TimeoutError):
            update_helper.wait_for_health(
                "http://127.0.0.1:8000/api/health",
                expected_app=update_helper.DEFAULT_EXPECTED_APP,
                expected_version="1.3.1",
                timeout=0.06,
                poll_interval=0.01,
                opener=opener,
            )

    def test_real_restart_process_passes_health_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_dir = root / "install"
            install_dir.mkdir()
            archive_path = self.make_archive(root, {"app.js": b"new"})
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            health_server = (
                "import http.server,json,sys\n"
                "payload={'ok': True, 'app': 'wenyan-word-training', 'version': '1.3.1'}\n"
                "class Handler(http.server.BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        body=json.dumps(payload).encode()\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Type','application/json')\n"
                "        self.send_header('Content-Length',str(len(body)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(body)\n"
                "    def log_message(self,*args):\n"
                "        pass\n"
                "server=http.server.ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)\n"
                "server.serve_forever()\n"
            )
            options = self.make_options(install_dir, archive_path)
            options.restart_executable = Path(sys.executable)
            options.restart_arg = ["-c", health_server, str(port)]
            process = None
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "localappdata")}, clear=False):
                try:
                    update_helper.apply_update(options)
                    process = update_helper.start_version(options)
                    payload = update_helper.wait_for_health(
                        f"http://127.0.0.1:{port}/api/health",
                        expected_app=update_helper.DEFAULT_EXPECTED_APP,
                        expected_version="1.3.1",
                        timeout=5,
                        poll_interval=0.05,
                    )
                    self.assertEqual(payload["version"], "1.3.1")
                finally:
                    update_helper.stop_started_process(process)

    def test_failed_health_rolls_back_and_restarts_old_version(self) -> None:
        options = Namespace(
            parent_pid=123,
            install_dir=Path("C:/install"),
            archive=Path("C:/update.zip"),
            version="1.3.1",
            previous_version="1.3.0",
            expected_app=update_helper.DEFAULT_EXPECTED_APP,
            health_url="http://127.0.0.1:8000/api/health",
            restart_executable=Path("C:/install/文言实词限时训练.exe"),
            restart_arg=[],
        )
        transaction = Mock()
        transaction.rollback.return_value = True
        new_process = Mock()
        old_process = Mock()
        with patch.object(update_helper, "wait_for_process_exit"), \
                patch.object(update_helper, "apply_update", return_value=transaction), \
                patch.object(update_helper, "start_version", side_effect=[new_process, old_process]), \
                patch.object(update_helper, "wait_for_health", side_effect=[TimeoutError("health timeout"), {"ok": True}]), \
                patch.object(update_helper, "stop_started_process"), \
                patch.object(update_helper, "write_result") as write_result, \
                patch.object(update_helper, "log_update_event"):
            self.assertFalse(update_helper.run_update_transaction(options))
        transaction.rollback.assert_called_once_with()
        self.assertTrue(write_result.call_args.kwargs["rolled_back"])
        self.assertFalse(write_result.call_args.args[2])

    def test_success_result_is_written_only_after_health_verification(self) -> None:
        options = Namespace(
            parent_pid=123,
            install_dir=Path("C:/install"),
            archive=Path("C:/update.zip"),
            version="1.3.1",
            previous_version="1.3.0",
            expected_app=update_helper.DEFAULT_EXPECTED_APP,
            health_url="http://127.0.0.1:8000/api/health",
            restart_executable=Path("C:/install/文言实词限时训练.exe"),
            restart_arg=[],
        )
        transaction = Mock()
        process = Mock()
        with patch.object(update_helper, "wait_for_process_exit"), \
                patch.object(update_helper, "apply_update", return_value=transaction), \
                patch.object(update_helper, "start_version", return_value=process), \
                patch.object(update_helper, "wait_for_health", return_value={"ok": True}), \
                patch.object(update_helper, "write_result") as write_result, \
                patch.object(update_helper, "log_update_event"):
            self.assertTrue(update_helper.run_update_transaction(options))
        self.assertEqual(write_result.call_args.kwargs["previous_version"], "1.3.0")
        self.assertFalse(write_result.call_args.kwargs["rolled_back"])
        self.assertTrue(write_result.call_args.args[2])

    def test_update_package_rejects_question_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = self.make_archive(root, {"data/questions.json": b"secret"})
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaises(ValueError):
                    update_helper.read_manifest(archive)

    def test_update_package_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = self.make_archive(root, {"../outside.txt": b"bad"})
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaises(ValueError):
                    update_helper.read_manifest(archive)


if __name__ == "__main__":
    unittest.main()
