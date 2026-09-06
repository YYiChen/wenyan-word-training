from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_release  # noqa: E402
import run_server  # noqa: E402


class RuntimeAssetTests(unittest.TestCase):
    def test_runtime_manifest_has_no_duplicate_entries(self) -> None:
        self.assertEqual(len(build_release.RUNTIME_WEB_FILES), len(set(build_release.RUNTIME_WEB_FILES)))
        self.assertEqual(len(build_release.RUNTIME_PYTHON_FILES), len(set(build_release.RUNTIME_PYTHON_FILES)))
        self.assertTrue(set(build_release.RUNTIME_WEB_FILES).issubset(build_release.SOURCE_FILES))
        self.assertTrue(set(build_release.RUNTIME_PYTHON_FILES).issubset(build_release.SOURCE_FILES))

    def test_runtime_manifest_files_exist(self) -> None:
        for relative in [*build_release.RUNTIME_WEB_FILES, *build_release.RUNTIME_PYTHON_FILES]:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_html_local_assets_exist(self) -> None:
        local_asset_pattern = re.compile(r"(?:src|href)=\"([^\"]+)\"")
        for html_name in ("index.html", "admin.html"):
            html = (ROOT / html_name).read_text(encoding="utf-8")
            for reference in local_asset_pattern.findall(html):
                relative = reference.split("?", 1)[0].split("#", 1)[0]
                if not relative or "://" in relative or relative.startswith("#"):
                    continue
                self.assertTrue((ROOT / relative).is_file(), f"{html_name}: {reference}")

    def test_version_json_is_runtime_version_source(self) -> None:
        version_payload = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(run_server.APP_VERSION, version_payload["version"])

    def test_launcher_and_admin_show_runtime_version_contract(self) -> None:
        launcher_source = (ROOT / "tools" / "launcher.py").read_text(encoding="utf-8")
        admin_source = (ROOT / "admin.js").read_text(encoding="utf-8")
        self.assertIn("run_server.APP_VERSION", launcher_source)
        self.assertIn("expected_version=run_server.APP_VERSION", launcher_source)
        self.assertIn("read_update_result", launcher_source)
        self.assertIn('health: "./api/health"', admin_source)
        self.assertIn("healthData", admin_source)

    def test_sync_server_package_is_self_contained(self) -> None:
        import subprocess
        import tempfile
        import urllib.request

        for relative in build_release.SERVER_FILES:
            self.assertTrue((ROOT / relative).is_file(), relative)
        self.assertEqual(len(build_release.SERVER_FILES), len(set(build_release.SERVER_FILES)))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = build_release.build_server_archive(
                ROOT, tmp_path, "9.9.9-test", tmp_path / "work")
            names = zipfile.ZipFile(archive).namelist()
            for relative in build_release.SERVER_FILES:
                self.assertIn(relative.replace("\\", "/"), names, relative)
            # No question-bank data may leak into the server package.
            for name in names:
                lowered = name.lower()
                self.assertFalse(lowered.endswith("questions.json"), name)
                self.assertFalse(lowered.endswith("question-reviews.json"), name)
                self.assertFalse(lowered.endswith("question-bank-history.json"), name)
            # The extracted layout must boot: tools/ beside sync_server/.
            extract_dir = tmp_path / "extracted"
            with zipfile.ZipFile(archive) as archive_file:
                archive_file.extractall(extract_dir)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            proc = subprocess.Popen(
                [sys.executable, "sync_server/server.py",
                 "--data-dir", str(tmp_path / "srvdata"),
                 "serve", "--host", "127.0.0.1", "--port", "17823"],
                cwd=str(extract_dir),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                health = None
                early_output = ""
                for _ in range(60):
                    if proc.poll() is not None:
                        early_output = proc.communicate()[0]
                        break
                    try:
                        with opener.open("http://127.0.0.1:17823/api/v1/health",
                                         timeout=1) as response:
                            health = json.loads(response.read().decode("utf-8"))
                        break
                    except OSError:
                        import time
                        time.sleep(0.3)
                self.assertIsNotNone(
                    health, f"sync server from package did not start: {early_output[-2000:]}")
                self.assertEqual(health.get("service"), "wenyan-sync")
                self.assertEqual(health.get("protocolVersion"), 1)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                if proc.stdout:
                    proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
