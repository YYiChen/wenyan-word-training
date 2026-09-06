"""Apply a verified code-only update without touching user data.

The helper is intentionally independent from the browser UI. It performs a
small transaction: replace the files declared by the package manifest, start
the requested version, verify its local health contract, and roll back the
program files when the verification fails.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath


MANIFEST_NAME = "update-manifest.json"
FORBIDDEN_PARTS = {"data", "public-data", "release", ".git"}
# Exact data file names that must never be installed by an update package.
# Substring matching is deliberately NOT used here: legitimate code files
# such as admin-questions.js or tools/server_questions.py only contain the
# word "questions" in their names and must remain installable.
FORBIDDEN_FILE_NAMES = {
    "questions.json",
    "question-reviews.json",
    "question-bank-history.json",
    "question_bank.json",
    "expanded_question_specs.json",
}
# Snapshot retention for pre-update data backups.
UPDATE_DATA_SNAPSHOT_DIRNAME = "user-data"
UPDATE_BACKUP_KEEP_COUNT = 10
UPDATE_BACKUP_KEEP_DAYS = 30
# LocalAppData files that a new version may migrate or prune on startup.
# The whole user-data directory is never copied: updater-runtime and
# update-backups must not be recursively snapshotted into themselves.
PROTECTED_LOCALAPPDATA_FILES = (
    "leaderboard.json",
    "answer-records.json",
    "admin-settings.json",
)
DEFAULT_EXPECTED_APP = "wenyan-word-training"
HEALTH_TIMEOUT_SECONDS = 25.0
HEALTH_POLL_SECONDS = 0.35
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
ERROR_INVALID_PARAMETER = 87
ERROR_NOT_FOUND = 1168


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="文言实词训练更新助手")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--previous-version", default="")
    parser.add_argument("--expected-app", default=DEFAULT_EXPECTED_APP)
    parser.add_argument("--health-url", default="")
    parser.add_argument("--restart-executable", type=Path, required=True)
    parser.add_argument("--restart-arg", action="append", default=[])
    # Kept as a no-op for older callers; updates no longer open a browser URL.
    parser.add_argument("--restart-url", default="", help=argparse.SUPPRESS)
    return parser.parse_args()


def _wait_for_process_exit_windows(pid: int, timeout: float) -> None:
    """Wait for a Windows process handle instead of using Unix kill semantics."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code in {ERROR_INVALID_PARAMETER, ERROR_NOT_FOUND}:
            return
        raise OSError(error_code, f"无法打开父进程句柄：{pid}")
    try:
        result = kernel32.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
    finally:
        kernel32.CloseHandle(handle)
    if result == WAIT_OBJECT_0:
        return
    if result == WAIT_TIMEOUT:
        raise TimeoutError("原程序未能在规定时间内退出。")
    raise OSError(ctypes.get_last_error(), "等待原程序退出失败。")


def wait_for_process_exit(pid: int, timeout: float = 60.0) -> None:
    """Wait for a process to exit with a bounded timeout on every platform."""

    if pid <= 0:
        return
    if os.name == "nt":
        _wait_for_process_exit_windows(pid, timeout)
        return
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("原程序未能在规定时间内退出。")
        time.sleep(min(0.25, remaining))


# Compatibility name used by older tests and locally generated helpers.
wait_for_parent = wait_for_process_exit


def normalize_member(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"更新包包含不安全路径：{name}")
    if path.parts[0].lower() in FORBIDDEN_PARTS:
        raise ValueError(f"更新包不得覆盖用户数据：{name}")
    if path.name.lower() in FORBIDDEN_FILE_NAMES:
        raise ValueError(f"更新包包含题库相关文件：{name}")
    return "/".join(path.parts)


def _read_manifest_payload(archive: zipfile.ZipFile) -> tuple[dict[str, object], list[str]]:
    names = {normalize_member(info.filename): info for info in archive.infolist() if not info.is_dir()}
    if MANIFEST_NAME not in names:
        raise ValueError("更新包缺少更新清单。")
    manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("更新清单格式无效。")
    files: list[str] = []
    for value in manifest["files"]:
        if not isinstance(value, str):
            raise ValueError("更新清单包含无效文件名。")
        normalized = normalize_member(value)
        if normalized == MANIFEST_NAME or normalized not in names or normalized in files:
            raise ValueError("更新清单与 ZIP 内容不一致。")
        files.append(normalized)
    actual = set(names)
    allowed = set(files) | {MANIFEST_NAME}
    if actual != allowed:
        raise ValueError("更新包包含未声明的文件。")
    return manifest, files


def read_manifest(archive: zipfile.ZipFile) -> list[str]:
    return _read_manifest_payload(archive)[1]


def read_installed_manifest(install_dir: Path) -> list[str]:
    """Read only the old manifest's managed files for safe obsolete cleanup."""

    path = install_dir / MANIFEST_NAME
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return []
        files: list[str] = []
        for value in values:
            if not isinstance(value, str):
                return []
            normalized = normalize_member(value)
            if normalized == MANIFEST_NAME or normalized in files:
                return []
            files.append(normalized)
        return files
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        # A corrupt old manifest must never turn into an arbitrary directory scan.
        return []


def backup_path(user_data_dir: Path, version: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return user_data_dir / "update-backups" / f"{version}-{stamp}"


def _user_data_root() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = str(Path.home() / "AppData" / "Local")
    return Path(local_appdata) / "WenyanQuiz"


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def update_result_path() -> Path:
    return _user_data_root() / "update-result.json"


def write_result(
    install_dir: Path,
    version: str,
    ok: bool,
    message: str,
    *,
    previous_version: str | None = None,
    rolled_back: bool = False,
    phase: str | None = None,
) -> None:
    del install_dir  # Kept in the signature for compatibility with old helpers.
    payload: dict[str, object] = {
        "version": version,
        "previousVersion": previous_version or None,
        "ok": bool(ok),
        "rolledBack": bool(rolled_back),
        "message": message,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    if phase:
        payload["phase"] = phase
    try:
        _write_json_atomic(update_result_path(), payload)
    except OSError:
        pass


def log_update_event(version: str, phase: str, message: str, *, rolled_back: bool | None = None) -> None:
    payload: dict[str, object] = {
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "phase": phase,
        "message": message,
    }
    if rolled_back is not None:
        payload["rolledBack"] = rolled_back
    try:
        path = _user_data_root() / "update.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _copy_data_tree(source: Path, destination: Path) -> None:
    """Copy a data tree without following symlinks or reparse points.

    Symlinked entries are skipped entirely so an unusual user layout can
    never turn the snapshot into a recursive copy of another disk area.
    """

    for entry in sorted(source.iterdir()):
        if entry.is_symlink():
            continue
        target = destination / entry.name
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_data_tree(entry, target)
        elif entry.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)


def snapshot_update_data(install_dir: Path, backup_root: Path) -> Path | None:
    """Capture a pre-update snapshot of every data file startup may migrate.

    Covers the whole ``install_dir/data/`` tree plus the LocalAppData files
    a new version may prune, repair or migrate on first launch.  Returns the
    snapshot directory, or None when there was nothing to capture.
    """

    snapshot_root = backup_root / UPDATE_DATA_SNAPSHOT_DIRNAME
    captured = False
    data_dir = install_dir / "data"
    if data_dir.is_dir() and not data_dir.is_symlink():
        _copy_data_tree(data_dir, snapshot_root / "data")
        captured = True
    local_root = _user_data_root()
    for name in PROTECTED_LOCALAPPDATA_FILES:
        source = local_root / name
        if source.is_file() and not source.is_symlink():
            destination = snapshot_root / "local-app-data" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            captured = True
    return snapshot_root if captured else None


def preserve_failed_data_state(install_dir: Path, backup_root: Path) -> None:
    """Keep the failed new version's data state before restoring the snapshot."""

    try:
        data_dir = install_dir / "data"
        if data_dir.is_dir() and not data_dir.is_symlink():
            _copy_data_tree(data_dir, backup_root / "failed-new-data-state" / "data")
    except OSError:
        pass


def restore_update_data(snapshot_root: Path, install_dir: Path) -> bool:
    """Restore a pre-update snapshot; True only when everything succeeded."""

    ok = True
    data_snapshot = snapshot_root / "data"
    if data_snapshot.is_dir():
        target = install_dir / "data"
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            _copy_data_tree(data_snapshot, target)
        except OSError:
            ok = False
    local_snapshot = snapshot_root / "local-app-data"
    if local_snapshot.is_dir():
        for name in PROTECTED_LOCALAPPDATA_FILES:
            source = local_snapshot / name
            if source.is_file():
                try:
                    shutil.copy2(source, _user_data_root() / name)
                except OSError:
                    ok = False
    return ok


def prune_update_backups(user_data_root: Path) -> None:
    """Retain recent update backups without unbounded growth.

    Keeps the newest UPDATE_BACKUP_KEEP_COUNT entries and everything younger
    than UPDATE_BACKUP_KEEP_DAYS; older surplus entries are removed.
    """

    root = user_data_root / "update-backups"
    try:
        entries = [entry for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink()]
    except OSError:
        return

    def mtime(entry: Path) -> float:
        try:
            return entry.stat().st_mtime
        except OSError:
            return 0.0

    entries.sort(key=mtime, reverse=True)
    now = time.time()
    for position, entry in enumerate(entries):
        if position < UPDATE_BACKUP_KEEP_COUNT:
            continue
        if now - mtime(entry) <= UPDATE_BACKUP_KEEP_DAYS * 24 * 60 * 60:
            continue
        shutil.rmtree(entry, ignore_errors=True)


@dataclass
class UpdateTransaction:
    install_dir: Path
    backup_root: Path
    touched: list[tuple[Path, Path | None]]
    temporary_paths: list[Path] = field(default_factory=list)
    rolled_back: bool = False
    data_snapshot: Path | None = None

    def cleanup_temporary_paths(self) -> None:
        for path in self.temporary_paths:
            try:
                path.unlink()
            except OSError:
                pass
        self.temporary_paths.clear()

    def rollback(self) -> bool:
        success = True
        for target, backup in reversed(self.touched):
            try:
                if backup is not None and backup.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                success = False
        self.cleanup_temporary_paths()
        self.rolled_back = success
        return success

    def rollback_data(self) -> bool:
        """Restore the pre-update data snapshot; True when nothing was needed."""

        if self.data_snapshot is None:
            return True
        preserve_failed_data_state(self.install_dir, self.backup_root)
        return restore_update_data(self.data_snapshot, self.install_dir)


def _validate_manifest_version(manifest: dict[str, object], expected_version: str) -> None:
    declared = manifest.get("version")
    if declared is None:
        return
    if not isinstance(declared, str) or declared.removeprefix("v") != expected_version.removeprefix("v"):
        raise ValueError("更新清单版本与目标版本不一致。")


def apply_update(options: argparse.Namespace) -> UpdateTransaction:
    """Apply files and return a transaction that can still be rolled back."""

    install_dir = options.install_dir.resolve()
    archive_path = options.archive.resolve()
    if not install_dir.is_dir() or not archive_path.is_file():
        raise FileNotFoundError("更新目录或更新包不存在。")
    user_data_root = _user_data_root()
    backup_root = backup_path(user_data_root, str(options.version))
    extracted_root = Path(tempfile.mkdtemp(prefix="wenyan-update-apply-"))
    touched: list[tuple[Path, Path | None]] = []
    temporary_paths: list[Path] = []
    transaction: UpdateTransaction | None = None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest, files = _read_manifest_payload(archive)
            _validate_manifest_version(manifest, str(options.version))
            install_files = [*files, MANIFEST_NAME]
            old_files = read_installed_manifest(install_dir)
            obsolete_files = [member for member in old_files if member not in install_files]
            changed_files = list(dict.fromkeys([*install_files, *obsolete_files]))

            for member in install_files:
                extracted = extracted_root / Path(member)
                extracted.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, extracted.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

            backup_root.mkdir(parents=True, exist_ok=True)
            for member in changed_files:
                target = install_dir / Path(member)
                if target.exists():
                    if not target.is_file():
                        raise IsADirectoryError(f"程序文件路径不是普通文件：{target}")
                    backup = backup_root / Path(member)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    touched.append((target, backup))
                else:
                    touched.append((target, None))

            # The old service has fully exited and the new program has not
            # started yet: capture every data file startup may migrate before
            # any program file is replaced.  A snapshot failure aborts the
            # update instead of risking an unrestorable data migration.
            data_snapshot = snapshot_update_data(install_dir, backup_root)

        transaction = UpdateTransaction(install_dir, backup_root, touched, temporary_paths, data_snapshot=data_snapshot)
        for member in install_files:
            target = install_dir / Path(member)
            staged = extracted_root / Path(member)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".update-tmp")
            temporary_paths.append(temporary)
            try:
                temporary.unlink()
            except OSError:
                pass
            shutil.copy2(staged, temporary)
            os.replace(temporary, target)

        for member in obsolete_files:
            target = install_dir / Path(member)
            if target.exists():
                target.unlink()

        write_result(
            install_dir,
            str(options.version),
            False,
            f"正在验证更新到 v{options.version}。",
            previous_version=getattr(options, "previous_version", "") or None,
            phase="verifying",
        )
        log_update_event(str(options.version), "verifying", "程序文件已替换，等待新版健康检查。")
        return transaction
    except Exception as error:
        if transaction is None:
            transaction = UpdateTransaction(install_dir, backup_root, touched, temporary_paths)
        rollback_ok = transaction.rollback()
        # Preserve the transaction outcome for the outer workflow. Without
        # this, an exception during extraction/replacement would be reported
        # as if no rollback had happened because apply_update cannot return.
        setattr(error, "_update_transaction", transaction)
        setattr(error, "_rollback_ok", rollback_ok)
        log_update_event(str(options.version), "rollback", "文件替换失败，已执行文件回滚。", rolled_back=transaction.rolled_back)
        raise
    finally:
        shutil.rmtree(extracted_root, ignore_errors=True)
        try:
            archive_path.unlink()
        except OSError:
            pass


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)


def start_version(options: argparse.Namespace) -> subprocess.Popen:
    command = [str(options.restart_executable), *[str(arg) for arg in getattr(options, "restart_arg", [])]]
    return subprocess.Popen(
        command,
        cwd=str(options.install_dir),
        close_fds=True,
        creationflags=_creationflags(),
    )


def restart(options: argparse.Namespace) -> subprocess.Popen:
    """Compatibility wrapper; restarting no longer opens a browser URL."""

    return start_version(options)


def stop_started_process(process: subprocess.Popen | None, health_url: str = "") -> None:
    """Stop only the process started by this updater."""

    del health_url  # Kept for compatibility; process ownership is the guard.
    if process is None:
        return
    try:
        process.wait(timeout=2.0)
        return
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        process.terminate()
        process.wait(timeout=3.0)
    except (subprocess.TimeoutExpired, OSError):
        try:
            process.kill()
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def wait_for_health(
    health_url: str,
    *,
    expected_app: str,
    expected_version: str | None,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
    poll_interval: float = HEALTH_POLL_SECONDS,
    opener=urllib.request.urlopen,
) -> dict[str, object]:
    deadline = time.monotonic() + max(0.0, timeout)
    last_error: BaseException | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f"（{last_error}）" if last_error else ""
            raise TimeoutError(f"新版健康检查超时{detail}")
        request = urllib.request.Request(health_url, headers={"Accept": "application/json"})
        try:
            with opener(request, timeout=min(2.0, max(0.1, remaining))) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("ok") is True
                and payload.get("app") == expected_app
                and (expected_version is None or payload.get("version") == expected_version)
            ):
                return payload
            last_error = ValueError("健康接口返回的应用名或版本不匹配")
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(min(max(0.05, poll_interval), max(0.05, deadline - time.monotonic())))


def run_update_transaction(options: argparse.Namespace) -> bool:
    transaction: UpdateTransaction | None = None
    new_process: subprocess.Popen | None = None
    version = str(options.version)
    previous_version = getattr(options, "previous_version", "") or None
    expected_app = str(getattr(options, "expected_app", "") or DEFAULT_EXPECTED_APP)
    health_url = str(getattr(options, "health_url", "") or "")
    try:
        log_update_event(version, "waiting", "等待旧版本完全退出。")
        wait_for_process_exit(int(options.parent_pid))
        transaction = apply_update(options)
        log_update_event(version, "starting", "启动待验证的新版本。")
        new_process = start_version(options)
        if not health_url:
            raise ValueError("缺少新版健康检查地址。")
        wait_for_health(
            health_url,
            expected_app=expected_app,
            expected_version=version,
        )
        try:
            prune_update_backups(_user_data_root())
        except OSError:
            pass
        write_result(
            options.install_dir,
            version,
            True,
            f"已成功更新到 v{version}。",
            previous_version=previous_version,
            rolled_back=False,
            phase="succeeded",
        )
        log_update_event(version, "succeeded", "新版已通过应用名和版本健康检查。", rolled_back=False)
        return True
    except Exception as error:
        if transaction is None:
            transaction = getattr(error, "_update_transaction", None)
        log_update_event(version, "failed", f"{type(error).__name__}: {error}")
        stop_started_process(new_process, health_url)
        rollback_ok = False
        if transaction is not None:
            recorded_rollback = getattr(error, "_rollback_ok", None)
            rollback_ok = bool(recorded_rollback) if recorded_rollback is not None else transaction.rollback()
            log_update_event(version, "rollback", "已恢复旧版本程序文件。", rolled_back=rollback_ok)
        # A rollback is only complete when the pre-update data state is
        # restored too: the new version may already have migrated local
        # data files before its health check failed.
        data_ok = True
        data_snapshot_dir: Path | None = getattr(transaction, "data_snapshot", None) if transaction is not None else None
        if transaction is not None and new_process is not None and data_snapshot_dir is not None:
            try:
                data_ok = bool(transaction.rollback_data())
            except OSError as data_error:
                data_ok = False
                log_update_event(version, "data-rollback-failed", f"{type(data_error).__name__}: {data_error}")
            log_update_event(
                version,
                "data-rollback",
                f"数据快照恢复{'成功' if data_ok else '失败'}：{data_snapshot_dir}",
                rolled_back=data_ok,
            )
        rolled_back = bool(rollback_ok and data_ok)

        restart_ok = False
        old_process: subprocess.Popen | None = None
        if rollback_ok:
            try:
                log_update_event(version, "restart-rollback", "启动回滚后的旧版本。")
                old_process = start_version(options)
                if health_url:
                    wait_for_health(
                        health_url,
                        expected_app=expected_app,
                        expected_version=previous_version,
                        timeout=HEALTH_TIMEOUT_SECONDS,
                    )
                restart_ok = True
            except Exception as restart_error:
                log_update_event(version, "restart-rollback-failed", f"{type(restart_error).__name__}: {restart_error}")
                stop_started_process(old_process)

        if rollback_ok and previous_version:
            message = f"更新 v{version} 失败，已恢复到 v{previous_version}。"
        elif rollback_ok:
            message = f"更新 v{version} 失败，已完成程序文件回滚。"
        else:
            message = f"更新 v{version} 失败，回滚未能完全确认。"
        if rollback_ok and not data_ok:
            message = (
                f"程序更新失败，数据自动恢复未完全成功，请不要继续操作，"
                f"并从备份恢复（{data_snapshot_dir}）。"
            )
        if rollback_ok and not restart_ok:
            message += "旧版本自动启动未能确认，请手动启动程序。"
        write_result(
            options.install_dir,
            version,
            False,
            message,
            previous_version=previous_version,
            rolled_back=rolled_back,
            phase="rolled_back" if rolled_back else "failed",
        )
        log_update_event(version, "failed-final", message, rolled_back=rolled_back)
        return False


def main() -> int:
    options = parse_args()
    try:
        return 0 if run_update_transaction(options) else 1
    finally:
        try:
            options.archive.resolve().unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
