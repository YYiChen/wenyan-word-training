"""Apply a verified code-only update without touching user data."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


MANIFEST_NAME = "update-manifest.json"
FORBIDDEN_PARTS = {"data", "release", ".git"}
FORBIDDEN_NAME_PATTERN = ("questions", "question-reviews", "expanded_question_specs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="文言实词训练更新助手")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--restart-executable", type=Path, required=True)
    parser.add_argument("--restart-arg", action="append", default=[])
    parser.add_argument("--restart-url", required=True)
    return parser.parse_args()


def wait_for_parent(pid: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return
        time.sleep(0.25)
    raise TimeoutError("原程序未能及时退出。")


def normalize_member(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"更新包包含不安全路径：{name}")
    if path.parts[0].lower() in FORBIDDEN_PARTS:
        raise ValueError(f"更新包不得覆盖用户数据：{name}")
    lowered = "/".join(path.parts).lower()
    if any(token in lowered for token in FORBIDDEN_NAME_PATTERN):
        raise ValueError(f"更新包包含题库相关文件：{name}")
    return "/".join(path.parts)


def read_manifest(archive: zipfile.ZipFile) -> list[str]:
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
    return files


def backup_path(user_data_dir: Path, version: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return user_data_dir / "update-backups" / f"{version}-{stamp}"


def write_result(install_dir: Path, version: str, ok: bool, message: str) -> None:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return
    path = Path(local_appdata) / "WenyanQuiz" / "update-result.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": version, "ok": ok, "message": message, "updatedAt": datetime.now().isoformat(timespec="seconds")},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def apply_update(options: argparse.Namespace) -> None:
    install_dir = options.install_dir.resolve()
    archive_path = options.archive.resolve()
    if not install_dir.is_dir() or not archive_path.is_file():
        raise FileNotFoundError("更新目录或更新包不存在。")
    user_data_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "WenyanQuiz"
    backup_root = backup_path(user_data_root, options.version)
    extracted_root = Path(tempfile.mkdtemp(prefix="wenyan-update-apply-"))
    touched: list[tuple[Path, Path | None]] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            files = read_manifest(archive)
            for member in files:
                target = install_dir / Path(member)
                backup = backup_root / Path(member)
                backup.parent.mkdir(parents=True, exist_ok=True)
                extracted = extracted_root / Path(member)
                extracted.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, extracted.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                if target.exists():
                    shutil.copy2(target, backup)
                    touched.append((target, backup))
                else:
                    touched.append((target, None))
        for member in files:
            target = install_dir / Path(member)
            staged = extracted_root / Path(member)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".update-tmp")
            if temporary.exists():
                temporary.unlink()
            shutil.copy2(staged, temporary)
            os.replace(temporary, target)
        write_result(install_dir, options.version, True, "更新成功")
    except Exception:
        for target, backup in reversed(touched):
            try:
                if backup and backup.exists():
                    shutil.copy2(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
        write_result(install_dir, options.version, False, "更新失败，已尝试回滚")
        raise
    finally:
        shutil.rmtree(extracted_root, ignore_errors=True)
        try:
            archive_path.unlink()
        except OSError:
            pass


def restart(options: argparse.Namespace) -> None:
    command = [str(options.restart_executable), *[str(arg) for arg in options.restart_arg]]
    subprocess.Popen(command, cwd=str(options.install_dir), close_fds=True)
    time.sleep(1.2)
    try:
        webbrowser.open(options.restart_url)
    except Exception:
        pass


def main() -> int:
    options = parse_args()
    try:
        wait_for_parent(options.parent_pid)
        apply_update(options)
        restart(options)
        return 0
    except Exception as error:
        write_result(options.install_dir, options.version, False, str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
