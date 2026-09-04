from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import update_helper  # noqa: E402
import update_service  # noqa: E402


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


class UpdateHelperTests(unittest.TestCase):
    def make_archive(self, root: Path, entries: dict[str, bytes]) -> Path:
        archive_path = root / "update.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            files = sorted(entries)
            archive.writestr(update_helper.MANIFEST_NAME, json.dumps({"version": "1.3.1", "files": files}))
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
