from __future__ import annotations

import json
import re
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
