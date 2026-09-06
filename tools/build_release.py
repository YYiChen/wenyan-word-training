"""Build code-only source and runnable Windows release archives.

Release artifacts intentionally exclude the local question bank and review
data.  A newly extracted copy creates a blank local data directory and the
teacher can import a question bank through the administrator page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


RUNTIME_WEB_FILES = [
    "index.html",
    "admin.html",
    "app.js",
    "admin.js",
    "scoring.js",
    "question_identity.js",
    "feedback-effects.js",
    "student-shared.js",
    "student-records.js",
    "student-quiz.js",
    "student-pk.js",
    "admin-guide-data.js",
    "admin-shared.js",
    "admin-auth.js",
    "admin-questions.js",
    "admin-reviews.js",
    "admin-records.js",
    "admin-update.js",
    "admin-settings.js",
    "pk-finish-effects.css",
    "pk-finish-effects.js",
    "style.css",
    "admin.css",
    "version.json",
    "assets/wenyan-word-training.ico",
]

RUNTIME_PYTHON_FILES = [
    "tools/server_config.py",
    "tools/server_auth.py",
    "tools/server_storage.py",
    "tools/server_validators.py",
    "tools/server_question_import.py",
    "tools/server_questions.py",
    "tools/server_records.py",
    "tools/run_server.py",
    "tools/launcher.py",
    "tools/update_helper.py",
    "tools/update_service.py",
]

SOURCE_FILES = [
    ".gitignore",
    *RUNTIME_WEB_FILES,
    "免Python版使用说明.txt",
    *RUNTIME_PYTHON_FILES,
]

PUBLIC_README = """# 文言实词限时训练

这是一个本地浏览器答题系统：本地服务提供学生答题页和管理员后台，题库与答题数据保存在用户自己的设备上。

## 运行

- 源码版：`python tools/run_server.py --port 8000`，然后打开 <http://127.0.0.1:8000>。
- Windows 免 Python 版：解压后双击 `文言实词限时训练.exe`，会打开一个小型启动窗口并自动打开学生答题页；不需要 BAT，也不会出现黑色控制台窗口。

GitHub 仓库中的 `public-data/questions.json` 只是一份公开示例题库，每篇文章保留 1-3 道已复核题目；本发布包不会携带这份示例题库，也不包含完整教师题库、审查记录或导入历史。首次运行时会在应用旁边创建空白本地数据，教师可从 Windows 启动窗口打开管理员后台后导入自己的题库；排行榜、答题记录和管理员配置仍保存在用户数据目录中。管理员验证和密码修改只在启动窗口的原生窗口中完成，浏览器后台通过短寿命一次性 ticket 换取内存会话。

如需获取完整题库或技术支持，请联系原作者：`yyyyyc001@gmail.com`

学生结果通过本机服务的幂等接口保存；答题记录只通过管理员授权接口读取，学生端不公开历史记录。排行榜成绩会记录教材/篇目范围、答题时长和计分规则快照，同分时先提交者优先。普通训练与双人 PK 答题记录共享最近一个月最多 100 条的总配额，自动备份保留最近 100 份且不超过 90 天。

学生端可以从当前选择的教材册和篇目进入双人 PK，支持比时间（30/60/90/120 秒）或比题数（5/10/15/20/30 题）。双方使用同一题目集合、独立打乱顺序和选项，计分沿用后台当前规则；PK 记录单独保存，不计入普通排行榜。

## 检查更新

程序启动后会在后台查询 `YYiChen/wenyan-word-training` 的稳定 GitHub Release；只有进入管理员后台且存在更高版本时才提示。右上角“检查更新”可手动检查。更新完成后不会自动打开 admin.html；请从 Windows 启动窗口再次打开管理后台。

确认更新后，程序会下载并校验代码包，再由独立更新助手等待旧程序退出、替换程序文件、启动新版并检查 `/api/health`。只有新版健康检查通过才算成功，失败时会自动回滚。更新只覆盖清单中的程序文件，不触碰应用旁的 `data/` 或 `%LOCALAPPDATA%/WenyanQuiz/` 中的排行榜、答题记录和管理员配置。源码目录有未提交修改时会跳过自动替换。

## 发布

使用 `python tools/build_release.py --output <目录>` 默认生成不含题库的源码包、Windows 更新包和 `SHA256SUMS.txt`；也可以使用 `--github-only` 或 `--source-only` 单独生成。发布前请检查 ZIP 条目和校验和，并按 SemVer 创建稳定 Release。
"""
FORBIDDEN_PARTS = {"data", "release", ".git", "__pycache__"}
FORBIDDEN_FILE_NAMES = {
    "questions.json",
    "question-reviews.json",
    "question-bank-history.json",
    "question_bank.json",
    "expanded_question_specs.json",
}
FORBIDDEN_TOKENS = ("question_bank", "question-bank", "expanded_question_specs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成不含题库的文言实词训练源码包或 Windows 包")
    parser.add_argument("--output", type=Path, required=True, help="输出目录")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-only", action="store_true", help="只生成源码更新包")
    parser.add_argument("--github-only", action="store_true", help="只生成不带题库的 GitHub Windows 更新包")
    return parser.parse_args()


def safe_relative(path: str) -> str:
    normalized = "/".join(PurePosixPath(path.replace("\\", "/")).parts)
    parts = PurePosixPath(normalized).parts
    if not parts or PurePosixPath(normalized).is_absolute() or ".." in parts:
        raise ValueError(f"不安全的发布路径：{path}")
    lowered = normalized.lower()
    if (
        parts[0].lower() in FORBIDDEN_PARTS
        or PurePosixPath(normalized).name.lower() in FORBIDDEN_FILE_NAMES
        or any(token in lowered for token in FORBIDDEN_TOKENS)
    ):
        raise ValueError(f"发布包包含禁止路径：{path}")
    return normalized


def load_version(root: Path) -> str:
    payload = json.loads((root / "version.json").read_text(encoding="utf-8"))
    version = str(payload.get("version", "")).strip()
    if not version or any(character not in "0123456789." for character in version):
        raise ValueError("version.json 中的版本号无效。")
    return version


def write_manifest(directory: Path, version: str, files: list[str]) -> None:
    manifest = {"schemaVersion": 1, "version": version, "files": sorted(files)}
    (directory / "update-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_zip(source_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = safe_relative(path.relative_to(source_dir).as_posix())
            archive.write(path, relative)


def copy_allowlist(root: Path, destination: Path) -> list[str]:
    files: list[str] = []
    for relative in SOURCE_FILES:
        source = root / Path(relative)
        if not source.is_file():
            raise FileNotFoundError(source)
        safe = safe_relative(relative)
        target = destination / Path(safe)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(safe)
    readme = destination / "README.md"
    readme.write_text(PUBLIC_README, encoding="utf-8")
    files.append("README.md")
    return files


def run_pyinstaller(
    root: Path,
    build_root: Path,
    *,
    script: Path,
    name: str,
    onefile: bool,
    data_files: list[str],
    icon_file: Path | None = None,
    noconsole: bool = False,
) -> Path:
    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        raise RuntimeError("未找到 pyinstaller，无法生成 Windows 更新包。")
    dist = build_root / ("dist-onefile" if onefile else "dist-onedir")
    work = build_root / ("work-onefile" if onefile else "work-onedir")
    args = [
        pyinstaller,
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(build_root),
        "--paths",
        str(root / "tools"),
    ]
    if noconsole:
        args.append("--noconsole")
    if icon_file is not None:
        args.extend(["--icon", str(icon_file)])
    for data_file in data_files:
        # PyInstaller 6 accepts the platform-independent SOURCE:DEST form.
        args.append(f"--add-data={root / data_file}:.")
    args.append(str(script))
    subprocess.run(args, cwd=str(root), check=True)
    artifact = dist / (f"{name}.exe" if onefile else name)
    if not artifact.exists():
        raise FileNotFoundError(artifact)
    return artifact


def build_source_archive(root: Path, output_dir: Path, version: str, temp_root: Path) -> Path:
    source_dir = temp_root / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    files = copy_allowlist(root, source_dir)
    write_manifest(source_dir, version, files)
    output = output_dir / f"wenyan-word-training-v{version}-source.zip"
    create_zip(source_dir, output)
    return output


def build_windows_archive(
    root: Path,
    output_dir: Path,
    version: str,
    temp_root: Path,
) -> Path:
    build_root = temp_root / "pyinstaller"
    launcher_exe = run_pyinstaller(
        root,
        build_root,
        script=root / "tools" / "launcher.py",
        name="文言实词限时训练",
        onefile=True,
        data_files=RUNTIME_WEB_FILES,
        icon_file=root / "assets" / "wenyan-word-training.ico",
        noconsole=True,
    )
    updater_exe = run_pyinstaller(
        root,
        build_root,
        script=root / "tools" / "update_helper.py",
        name="文言实词更新助手",
        onefile=True,
        data_files=[],
        icon_file=root / "assets" / "wenyan-word-training.ico",
        noconsole=True,
    )
    package_dir = temp_root / "windows"
    package_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher_exe, package_dir / "文言实词限时训练.exe")
    shutil.copy2(updater_exe, package_dir / "文言实词更新助手.exe")
    instructions = root / "免Python版使用说明.txt"
    if instructions.is_file():
        shutil.copy2(instructions, package_dir / instructions.name)
    files = [
        safe_relative(path.relative_to(package_dir).as_posix())
        for path in package_dir.rglob("*")
        if path.is_file()
    ]
    write_manifest(package_dir, version, files)
    output = output_dir / f"wenyan-word-training-v{version}-windows.zip"
    create_zip(package_dir, output)
    return output


def write_checksums(output_dir: Path, archives: list[Path]) -> Path:
    checksum_path = output_dir / "SHA256SUMS.txt"
    lines = []
    for archive in sorted(archives):
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        lines.append(f"{digest}  {archive.name}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def main() -> int:
    options = parse_args()
    selected_modes = sum([bool(options.source_only), bool(options.github_only)])
    if selected_modes > 1:
        raise SystemExit("--source-only、--github-only 只能选择一个。")
    root = options.repo_root.resolve()
    output_dir = options.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = load_version(root)
    with tempfile.TemporaryDirectory(prefix="wenyan-release-") as temp:
        temp_root = Path(temp)
        archives: list[Path] = []
        if options.source_only:
            archives.append(build_source_archive(root, output_dir, version, temp_root))
        elif options.github_only:
            archives.append(build_windows_archive(
                root,
                output_dir,
                version,
                temp_root,
            ))
        else:
            archives.extend([
                build_source_archive(root, output_dir, version, temp_root),
                build_windows_archive(
                    root,
                    output_dir,
                    version,
                    temp_root,
                ),
            ])
    checksum_path = write_checksums(output_dir, archives)
    for path in [*archives, checksum_path]:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
