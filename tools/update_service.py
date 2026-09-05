"""GitHub Release update discovery and download orchestration.

This module deliberately has no dependency on the question bank.  It only
fetches public release metadata, downloads a verified code-only archive, and
starts the external update helper after the HTTP response has been sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
GITHUB_API_BASE = "https://api.github.com"
USER_AGENT = "WenyanWordTraining/1.4.8 update-checker"
MAX_RELEASE_NOTES = 12_000
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
CHECK_COOLDOWN_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 6


def parse_version(value: Any) -> tuple[int, int, int] | None:
    """Return a comparable SemVer tuple, or None for unsupported versions."""

    if not isinstance(value, str):
        return None
    match = VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def normalize_version(value: Any) -> str | None:
    parsed = parse_version(value)
    if parsed is None:
        return None
    return ".".join(str(part) for part in parsed)


def read_version_metadata(root: Path) -> dict[str, str]:
    path = root / "version.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"版本清单读取失败：{error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("版本清单必须是 JSON 对象。")
    version = normalize_version(payload.get("version"))
    repository = payload.get("repository")
    channel = payload.get("updateChannel", "stable")
    if version is None or not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("版本清单的 version 或 repository 无效。")
    if channel != "stable":
        raise RuntimeError("当前只支持 stable 更新通道。")
    return {"version": version, "repository": repository, "updateChannel": channel}


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        return None
    return value


def _read_url(url: str, *, timeout: float, accept: str, opener: Callable[..., Any]) -> bytes:
    request = Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    response = opener(request, timeout=timeout)
    try:
        return response.read(MAX_DOWNLOAD_BYTES)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _parse_checksum_text(raw: bytes, asset_name: str) -> str | None:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1].lstrip("*")
        if name == asset_name and SHA256_PATTERN.fullmatch(digest):
            return digest.lower()
    return None


def _asset_digest(asset: dict[str, Any], checksum_map: dict[str, str]) -> str | None:
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.lower().startswith("sha256:"):
        candidate = digest[7:].lower()
        if SHA256_PATTERN.fullmatch(candidate):
            return candidate
    name = asset.get("name")
    if isinstance(name, str):
        return checksum_map.get(name)
    return None


def source_tree_is_clean(root: Path) -> bool:
    """Refuse silent source replacement when a Git checkout has local edits."""

    if not (root / ".git").exists():
        return True
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and not result.stdout.strip()


def choose_release_candidate(
    releases: Any,
    *,
    current_version: str,
    mode: str,
    checksum_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Choose the highest stable release with a verified mode-specific asset."""

    current = parse_version(current_version)
    if current is None or mode not in {"source", "windows"} or not isinstance(releases, list):
        return None
    checksum_map = checksum_map or {}
    candidates: list[tuple[tuple[int, int, int], dict[str, Any], str]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        version = parse_version(release.get("tag_name"))
        if version is None or version <= current:
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        asset_name = f"wenyan-word-training-v{version[0]}.{version[1]}.{version[2]}-{mode}.zip"
        asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == asset_name), None)
        if not isinstance(asset, dict):
            continue
        asset_url = _safe_url(asset.get("browser_download_url"))
        digest = _asset_digest(asset, checksum_map)
        try:
            asset_size = int(asset.get("size"))
        except (TypeError, ValueError):
            asset_size = 0
        if not asset_url or not digest or asset_size <= 0 or asset_size > MAX_DOWNLOAD_BYTES:
            continue
        candidates.append((version, release, asset_name))
    if not candidates:
        return None
    version, release, asset_name = max(candidates, key=lambda item: item[0])
    assets = release["assets"]
    asset = next(item for item in assets if isinstance(item, dict) and item.get("name") == asset_name)
    digest = _asset_digest(asset, checksum_map)
    return {
        "version": ".".join(str(part) for part in version),
        "tag": str(release.get("tag_name", "")),
        "title": str(release.get("name") or release.get("tag_name") or "新版本"),
        "publishedAt": str(release.get("published_at") or ""),
        "notes": str(release.get("body") or "").strip()[:MAX_RELEASE_NOTES],
        "htmlUrl": _safe_url(release.get("html_url")) or "",
        "assetName": asset_name,
        "assetUrl": _safe_url(asset.get("browser_download_url")) or "",
        "assetSize": int(asset.get("size")),
        "sha256": digest,
    }


def fetch_release_candidate(
    repository: str,
    *,
    current_version: str,
    mode: str,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any] | None:
    api_url = f"{GITHUB_API_BASE}/repos/{repository}/releases?per_page=20"
    raw = _read_url(api_url, timeout=timeout, accept="application/vnd.github+json", opener=opener)
    releases = json.loads(raw.decode("utf-8"))
    if not isinstance(releases, list):
        return None
    checksum_map: dict[str, str] = {}
    checksum_asset: dict[str, Any] | None = None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        candidate = next((item for item in assets if isinstance(item, dict) and item.get("name") == "SHA256SUMS.txt"), None)
        if isinstance(candidate, dict):
            checksum_asset = candidate
            break
    if checksum_asset:
        checksum_url = _safe_url(checksum_asset.get("browser_download_url"))
        if checksum_url:
            try:
                checksum_raw = _read_url(checksum_url, timeout=timeout, accept="text/plain", opener=opener)
                for release in releases:
                    assets = release.get("assets") if isinstance(release, dict) else None
                    if not isinstance(assets, list):
                        continue
                    for asset in assets:
                        if isinstance(asset, dict) and isinstance(asset.get("name"), str):
                            digest = _parse_checksum_text(checksum_raw, asset["name"])
                            if digest:
                                checksum_map[asset["name"]] = digest
            except (OSError, ValueError, json.JSONDecodeError):
                checksum_map = {}
    return choose_release_candidate(
        releases,
        current_version=current_version,
        mode=mode,
        checksum_map=checksum_map,
    )


class UpdateManager:
    """Thread-safe update state machine used by the local HTTP server."""

    def __init__(
        self,
        *,
        root: Path,
        user_data_dir: Path,
        port: int,
        frozen: bool,
        shutdown_callback: Callable[[], None] | None = None,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        metadata = read_version_metadata(root)
        self.root = root
        self.user_data_dir = user_data_dir
        self.port = port
        self.frozen = frozen
        self.mode = "windows" if frozen else "source"
        self.current_version = metadata["version"]
        self.repository = metadata["repository"]
        self.shutdown_callback = shutdown_callback
        self.opener = opener
        self._lock = threading.RLock()
        self._last_check_at = 0.0
        self._candidate: dict[str, Any] | None = None
        self._state: dict[str, Any] = {
            "phase": "idle",
            "available": False,
            "currentVersion": self.current_version,
            "latestVersion": None,
            "mode": self.mode,
            "progress": 0,
        }

    def start_background(self) -> None:
        self.check_async(force=False)

    def check_async(self, *, force: bool) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._state["phase"] in {"checking", "downloading", "applying"}:
                return self.status()
            if not force and now - self._last_check_at < CHECK_COOLDOWN_SECONDS:
                return self.status()
            if force and now - self._last_check_at < CHECK_COOLDOWN_SECONDS:
                return self.status()
            self._last_check_at = now
            self._state = {
                "phase": "checking",
                "available": False,
                "currentVersion": self.current_version,
                "latestVersion": None,
                "mode": self.mode,
                "progress": 0,
            }
        thread = threading.Thread(target=self._check_worker, name="wenyan-update-check", daemon=True)
        thread.start()
        return self.status()

    def _check_worker(self) -> None:
        try:
            candidate = fetch_release_candidate(
                self.repository,
                current_version=self.current_version,
                mode=self.mode,
                opener=self.opener,
            )
        except Exception:
            candidate = None
        with self._lock:
            self._candidate = candidate
            if candidate:
                self._state = {
                    "phase": "available",
                    "available": True,
                    "currentVersion": self.current_version,
                    "latestVersion": candidate["version"],
                    "mode": self.mode,
                    "progress": 0,
                    **candidate,
                }
            else:
                self._state = {
                    "phase": "up_to_date",
                    "available": False,
                    "currentVersion": self.current_version,
                    "latestVersion": None,
                    "mode": self.mode,
                    "progress": 0,
                }

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            if self.mode == "source":
                state["sourceClean"] = source_tree_is_clean(self.root)
                state["canApply"] = bool(state.get("available") and state["sourceClean"])
            else:
                state["sourceClean"] = True
                state["canApply"] = bool(state.get("available"))
            return state

    def apply_async(self) -> dict[str, Any]:
        with self._lock:
            if not self._candidate or self._state.get("phase") != "available":
                return self.status()
            if self.mode == "source" and not source_tree_is_clean(self.root):
                self._state = {**self._state, "phase": "blocked", "canApply": False}
                return self.status()
            candidate = dict(self._candidate)
            self._state = {**self._state, "phase": "downloading", "progress": 0}
        thread = threading.Thread(target=self._download_worker, args=(candidate,), name="wenyan-update-download", daemon=True)
        thread.start()
        return self.status()

    def _download_worker(self, candidate: dict[str, Any]) -> None:
        archive_path: Path | None = None
        temp_dir: Path | None = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="wenyan-update-"))
            archive_path = temp_dir / str(candidate["assetName"])
            request = Request(
                str(candidate["assetUrl"]),
                headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT},
            )
            response = self.opener(request, timeout=REQUEST_TIMEOUT_SECONDS)
            digest = hashlib.sha256()
            total = int(candidate["assetSize"])
            received = 0
            try:
                with archive_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES:
                            raise ValueError("更新包过大。")
                        digest.update(chunk)
                        output.write(chunk)
                        with self._lock:
                            self._state["progress"] = min(99, int(received * 100 / max(total, 1)))
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            if received != total or digest.hexdigest().lower() != str(candidate["sha256"]).lower():
                raise ValueError("更新包校验失败。")
            self._launch_helper(archive_path, candidate)
        except Exception:
            if archive_path and archive_path.exists():
                try:
                    archive_path.unlink()
                except OSError:
                    pass
            with self._lock:
                self._state = {**self._state, "phase": "failed", "available": bool(self._candidate), "progress": 0}
        finally:
            if temp_dir and not archive_path:
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass

    def _launch_helper(self, archive_path: Path, candidate: dict[str, Any]) -> None:
        install_dir = Path(sys.executable).resolve().parent if self.frozen else self.root
        restart_executable = Path(sys.executable).resolve() if self.frozen else Path(sys.executable).resolve()
        if self.frozen:
            helper = install_dir / "文言实词更新助手.exe"
            restart_args = ["--port", str(self.port), "--no-browser"]
        else:
            helper = self.root / "tools" / "update_helper.py"
            restart_args = [str(self.root / "tools" / "run_server.py"), "--port", str(self.port), "--no-browser"]
        if not helper.exists():
            raise FileNotFoundError(helper)
        if self.frozen:
            command = [str(helper)]
        else:
            command = [str(sys.executable), str(helper)]
        command.extend(
            [
                "--parent-pid",
                str(os.getpid()),
                "--install-dir",
                str(install_dir),
                "--archive",
                str(archive_path),
                "--version",
                str(candidate["version"]),
                "--restart-executable",
                str(restart_executable),
                "--restart-url",
                f"http://127.0.0.1:{self.port}/admin.html",
            ]
        )
        for arg in restart_args:
            command.extend(["--restart-arg", arg])
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(command, cwd=str(install_dir), close_fds=True, creationflags=creationflags)
        with self._lock:
            self._state = {**self._state, "phase": "applying", "progress": 100}
        if self.shutdown_callback:
            timer = threading.Timer(0.5, self.shutdown_callback)
            timer.daemon = True
            timer.start()
