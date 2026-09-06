"""Small Tkinter launcher for the packaged local quiz service.

The browser remains the actual student and administrator UI.  This module only
owns the local service lifecycle and gives the packaged application a small,
quiet Windows window instead of a console window.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import BOTH, LEFT, RIGHT, X, Button, Entry, StringVar, Tk, Toplevel, messagebox
from tkinter import Frame, Label, font as tkfont
from urllib.parse import quote

import run_server


APP_TITLE = "文言实词限时训练"
APP_SUBTITLE = "本地教学答题工具"
DEFAULT_PORT = 8000
STARTUP_TIMEOUT_SECONDS = 20.0
HEALTH_POLL_MS = 250
# The updater health timeout is ~25s and the parent-exit wait can be longer;
# keep consuming the final result for at least 60s so a late success or
# rollback is still displayed instead of silently dropped.
UPDATE_RESULT_POLL_MS = 250
UPDATE_RESULT_MAX_ATTEMPTS = 240

COLOR_WINDOW = "#f3f8f7"
COLOR_CARD = "#ffffff"
COLOR_TEXT = "#183e45"
COLOR_MUTED = "#6d8589"
COLOR_ACCENT = "#207b84"
COLOR_ACCENT_DARK = "#17636b"
COLOR_BORDER = "#d5e5e4"
COLOR_SUCCESS = "#20845f"
COLOR_ERROR = "#bf5b4e"
COLOR_WARNING = "#b47a31"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="文言实词限时训练图形启动器")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开学生答题页")
    return parser.parse_args()


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "WenyanQuiz"


def cleanup_stale_updater_runtime(max_age_seconds: float = 24 * 60 * 60) -> None:
    """Remove only old temporary updater copies owned by this application."""

    runtime_root = user_data_dir() / "updater-runtime"
    try:
        entries = list(runtime_root.iterdir())
    except OSError:
        return
    now = time.time()
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        try:
            if now - entry.stat().st_mtime <= max_age_seconds:
                continue
            shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass
    try:
        if not any(runtime_root.iterdir()):
            runtime_root.rmdir()
    except OSError:
        pass


def read_update_result() -> dict | None:
    """Consume one updater result without exposing any secret-bearing fields."""

    path = user_data_dir() / "update-result.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    # The updater writes this marker before launching the new version. Keep it
    # on disk until the final success/rollback result replaces it.
    if isinstance(payload, dict) and payload.get("phase") == "verifying":
        return None
    try:
        path.unlink()
    except OSError:
        pass
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
        return None
    allowed_keys = {"version", "previousVersion", "ok", "rolledBack", "message", "updatedAt", "phase"}
    return {key: payload[key] for key in allowed_keys if key in payload}


def health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/health"


def student_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def admin_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/admin.html"


def read_health(port: int, timeout: float = 1.0) -> dict | None:
    request = urllib.request.Request(health_url(port), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def shutdown_http_service(port: int, timeout: float = 2.0) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
        return True
    except (OSError, urllib.error.URLError):
        return False


def stop_existing_project_service(port: int) -> None:
    """Take over an older copy only when the health endpoint identifies this app."""
    payload = read_health(port)
    if not is_project_health(payload):
        return
    shutdown_http_service(port)
    for _ in range(12):
        if read_health(port, timeout=0.25) is None:
            return
        time.sleep(0.1)


def is_project_health(payload: dict | None, expected_version: str | None = None) -> bool:
    if not payload or payload.get("ok") is not True or payload.get("app") != run_server.APP_NAME:
        return False
    return expected_version is None or payload.get("version") == expected_version


def friendly_start_error(error: BaseException, port: int = DEFAULT_PORT) -> str:
    message = str(error).strip()
    if "占用" in message or "Address already in use" in message or "10048" in message:
        return f"服务启动失败：{port} 端口可能已被其他程序占用。"
    if message:
        return f"服务启动失败：{message}"
    return "服务启动失败，请检查程序文件是否完整。"


def choose_ui_font(root: Tk) -> str:
    """Prefer a Windows Chinese UI font, with a safe fallback for development."""
    try:
        available = set(tkfont.families(root))
    except Exception:
        available = set()
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"):
        if not available or family in available:
            return family
    return "TkDefaultFont"


class ServiceRestarter:
    """Testable restart state machine for the launcher HTTP service.

    Only the injected ``read_health`` / ``shutdown`` / ``start`` callbacks
    are used: a restart never touches persistent data files.  The Tk wiring
    lives in ``LauncherApp``; ``on_done`` is invoked from the worker thread
    and the UI must hop back to the main thread itself.
    """

    def __init__(
        self,
        *,
        read_health,
        shutdown,
        start,
        on_done,
        sleep=time.sleep,
        gone_timeout: float = 5.0,
        health_timeout: float = 8.0,
        poll_interval: float = 0.2,
    ) -> None:
        self._read_health = read_health
        self._shutdown = shutdown
        self._start = start
        self._on_done = on_done
        self._sleep = sleep
        self._gone_timeout = gone_timeout
        self._health_timeout = health_timeout
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self.restarting = False

    def request_restart(self) -> bool:
        """Start one restart worker; return False when a restart is running."""

        with self._lock:
            if self.restarting:
                return False
            self.restarting = True
        worker = threading.Thread(target=self._worker, name="wenyan-restart", daemon=True)
        worker.start()
        return True

    def _wait_until(self, condition, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if condition():
                return True
            if time.monotonic() >= deadline:
                return False
            self._sleep(self._poll_interval)

    def _worker(self) -> None:
        try:
            outcome = self._run_restart()
        except Exception as error:  # Never leave the UI waiting silently.
            outcome = {"ok": False, "message": f"服务重启失败：{error}", "old_alive": False}
        finally:
            with self._lock:
                self.restarting = False
        try:
            self._on_done(outcome)
        except Exception:
            pass

    def _run_restart(self) -> dict:
        try:
            payload = self._read_health()
        except Exception:
            payload = None
        if payload is not None and not is_project_health(payload):
            return {
                "ok": False,
                "message": "端口被其他程序占用，无法安全重启服务。",
                "old_alive": False,
            }
        if payload is not None:
            try:
                self._shutdown()
            except Exception:
                pass
            gone = self._wait_until(lambda: self._safe_health() is None, self._gone_timeout)
            if not gone:
                return {
                    "ok": False,
                    "message": "旧服务未能在规定时间内退出，未启动新服务。",
                    "old_alive": True,
                }
        try:
            self._start()
        except Exception as error:
            return {"ok": False, "message": f"新服务启动失败：{error}", "old_alive": False}
        healthy = self._wait_until(
            lambda: is_project_health(self._safe_health(), expected_version=run_server.APP_VERSION),
            self._health_timeout,
        )
        if not healthy:
            return {"ok": False, "message": "新服务启动后未能通过健康检查。", "old_alive": False}
        return {"ok": True, "message": "服务正在运行", "old_alive": False}

    def _safe_health(self):
        try:
            return self._read_health()
        except Exception:
            return None


class LauncherApp:
    def __init__(self, root: Tk, options: argparse.Namespace) -> None:
        self.root = root
        self.options = options
        self.port = options.port
        self.closing = False
        self.ready = False
        self.starting = False
        self.server_error: BaseException | None = None
        self.server_thread: threading.Thread | None = None
        self._update_result: dict | None = None
        self._update_result_attempts = 0
        self._update_result_applied = False
        self.restarting = False
        self.allow_auto_open = True
        self._state_lock = threading.Lock()
        self.restarter = ServiceRestarter(
            read_health=lambda: read_health(self.port),
            shutdown=lambda: shutdown_http_service(self.port),
            start=self._start_restarted_server,
            on_done=self._on_restart_done_from_worker,
        )
        self.ui_font = choose_ui_font(root)
        self.mono_font = "Consolas"

        self.status_text = StringVar(value="正在启动……")
        self.detail_text = StringVar(value="正在准备本地答题服务，请稍候。")
        self.status_color = COLOR_WARNING
        self._build_window()
        self.root.after(80, self.start_service)
        self.root.after(HEALTH_POLL_MS, self.poll_service)
        self.root.after(UPDATE_RESULT_POLL_MS, self.poll_update_result)

    def _build_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=COLOR_WINDOW)
        self.root.resizable(False, False)
        self.root.minsize(660, 470)
        self.root.geometry("660x470")
        icon = application_root() / "wenyan-word-training.ico"
        if icon.is_file():
            try:
                self.root.iconbitmap(str(icon))
            except Exception:
                pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        outer = Frame(self.root, bg=COLOR_WINDOW, padx=32, pady=25)
        outer.pack(fill=BOTH, expand=True)

        header = Frame(outer, bg=COLOR_WINDOW)
        header.pack(fill=X)
        Label(
            header,
            text=APP_TITLE,
            bg=COLOR_WINDOW,
            fg=COLOR_TEXT,
            font=(self.ui_font, 21, "bold"),
            anchor="w",
        ).pack(fill=X)
        Label(
            header,
            text=f"{APP_SUBTITLE} · v{run_server.APP_VERSION}",
            bg=COLOR_WINDOW,
            fg=COLOR_MUTED,
            font=(self.ui_font, 10),
            anchor="w",
        ).pack(fill=X, pady=(3, 18))

        status_card = Frame(
            outer,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_BORDER,
            highlightthickness=1,
            padx=18,
            pady=15,
        )
        status_card.pack(fill=X)
        status_row = Frame(status_card, bg=COLOR_CARD)
        status_row.pack(fill=X)
        self.status_dot = Label(
            status_row,
            text="●",
            bg=COLOR_CARD,
            fg=self.status_color,
            font=(self.ui_font, 13),
        )
        self.status_dot.pack(side=LEFT, padx=(0, 8))
        self.status_label = Label(
            status_row,
            textvariable=self.status_text,
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            font=(self.ui_font, 12, "bold"),
            anchor="w",
        )
        self.status_label.pack(side=LEFT, fill=X, expand=True)
        Label(
            status_card,
            textvariable=self.detail_text,
            bg=COLOR_CARD,
            fg=COLOR_MUTED,
            font=(self.ui_font, 9),
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill=X, pady=(7, 0))

        address = Frame(outer, bg=COLOR_WINDOW)
        address.pack(fill=X, pady=(18, 12))
        Label(
            address,
            text="服务地址",
            bg=COLOR_WINDOW,
            fg=COLOR_MUTED,
            font=(self.ui_font, 9),
            anchor="w",
        ).pack(fill=X)
        Label(
            address,
            text=f"http://127.0.0.1:{self.port}/",
            bg=COLOR_WINDOW,
            fg=COLOR_TEXT,
            font=(self.mono_font, 10),
            anchor="w",
        ).pack(fill=X, pady=(3, 0))

        action_row = Frame(outer, bg=COLOR_WINDOW)
        action_row.pack(fill=X, pady=(2, 13))
        self.student_button = self._make_button(
            action_row,
            text="打开学生答题页",
            command=lambda: self.open_page(student_url(self.port)),
            primary=True,
        )
        self.student_button.pack(side=LEFT, padx=(0, 10))
        self.admin_button = self._make_button(
            action_row,
            text="打开管理后台",
            command=self.open_admin,
        )
        self.admin_button.pack(side=LEFT)
        self.password_button = self._make_button(
            action_row,
            text="修改管理员密码",
            command=self.open_password_change,
        )
        self.password_button.pack(side=LEFT, padx=(10, 0))

        restart_row = Frame(outer, bg=COLOR_WINDOW)
        restart_row.pack(fill=X, pady=(0, 13))
        self.restart_button = self._make_button(
            restart_row,
            text="重启服务",
            command=self.restart_service,
        )
        self.restart_button.pack(side=LEFT)

        footer = Frame(outer, bg=COLOR_WINDOW)
        footer.pack(fill=X, side="bottom")
        Label(
            footer,
            text="关闭本窗口将结束本地答题服务。",
            bg=COLOR_WINDOW,
            fg=COLOR_MUTED,
            font=(self.ui_font, 9),
            anchor="w",
        ).pack(side=LEFT, fill=X, expand=True)
        self.exit_button = self._make_button(footer, text="退出程序", command=self.on_close, danger=True)
        self.exit_button.pack(side=RIGHT)
        self.set_buttons(False)

    def _make_button(
        self,
        parent: Frame,
        *,
        text: str,
        command: object,
        primary: bool = False,
        danger: bool = False,
    ) -> Button:
        if primary:
            background = COLOR_ACCENT
            active_background = COLOR_ACCENT_DARK
            foreground = "#ffffff"
            disabled_background = "#dce8e7"
            disabled_foreground = "#8ea8a8"
            border = COLOR_ACCENT
        elif danger:
            background = COLOR_CARD
            active_background = "#fff3f1"
            foreground = COLOR_ERROR
            disabled_background = "#f1f4f3"
            disabled_foreground = "#b7c2c1"
            border = COLOR_BORDER
        else:
            background = COLOR_CARD
            active_background = "#eaf4f3"
            foreground = COLOR_TEXT
            disabled_background = "#f1f4f3"
            disabled_foreground = "#a7b7b6"
            border = COLOR_BORDER
        return Button(
            parent,
            text=text,
            command=command,
            font=(self.ui_font, 10),
            bg=background,
            fg=foreground,
            activebackground=active_background,
            activeforeground=foreground,
            disabledforeground=disabled_foreground,
            highlightbackground=border,
            highlightcolor=border,
            highlightthickness=1,
            relief="flat",
            bd=0,
            padx=17,
            pady=7,
            cursor="hand2",
        )

    def set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.student_button.configure(state=state)
        self.admin_button.configure(state=state)
        self.password_button.configure(state=state)
        self._set_restart_button(enabled and not self.restarting)
        if self.closing:
            self.exit_button.configure(state="disabled")

    def _set_restart_button(self, enabled: bool) -> None:
        self.restart_button.configure(state="normal" if enabled else "disabled")

    def set_status(self, text: str, detail: str, color: str) -> None:
        self.status_text.set(text)
        self.detail_text.set(detail)
        self.status_color = color
        self.status_dot.configure(fg=color)

    def poll_update_result(self) -> None:
        if self._update_result is None:
            self._update_result = read_update_result()
        if self._update_result is not None and self.ready and not self._update_result_applied:
            result = self._update_result
            self._update_result_applied = True
            if result.get("ok") is True:
                self.set_status("更新成功", str(result["message"]), COLOR_SUCCESS)
            elif result.get("rolledBack") is True:
                self.set_status("更新失败，已回滚", str(result["message"]), COLOR_ERROR)
            else:
                self.set_status("更新失败", str(result["message"]), COLOR_ERROR)
            return
        if self._update_result_applied:
            return
        self._update_result_attempts += 1
        if self._update_result_attempts < UPDATE_RESULT_MAX_ATTEMPTS:
            self.root.after(UPDATE_RESULT_POLL_MS, self.poll_update_result)

    def start_service(self) -> None:
        if self.closing or self.starting:
            return
        self.starting = True
        self.ready = False
        self.server_error = None
        self.set_buttons(False)
        self.set_status("正在启动……", "正在准备本地答题服务，请稍候。", COLOR_WARNING)
        stop_existing_project_service(self.port)
        self.server_thread = threading.Thread(target=self._run_server, name="wenyan-server", daemon=True)
        self.server_thread.start()

    def _run_server(self) -> None:
        try:
            run_server.main(["--port", str(self.port), "--no-browser"])
        except BaseException as error:
            with self._state_lock:
                self.server_error = error
            try:
                log_path = user_data_dir() / "launcher.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as log:
                    traceback.print_exc(file=log)
            except OSError:
                pass

    def poll_service(self) -> None:
        if self.closing:
            return
        if self.restarting:
            # A restart worker owns the service lifecycle; only reschedule.
            self.root.after(HEALTH_POLL_MS, self.poll_service)
            return
        if self.server_thread is not None and not self.server_thread.is_alive():
            with self._state_lock:
                error = self.server_error
            update_manager = run_server.UPDATE_MANAGER
            update_phase = update_manager.status().get("phase") if update_manager else None
            if update_phase == "applying":
                self.begin_close(confirm=False)
                return
            if not self.ready:
                self.starting = False
                self.set_status(
                    "启动失败",
                    friendly_start_error(error or RuntimeError("服务线程已停止。"), self.port),
                    COLOR_ERROR,
                )
                self.exit_button.configure(state="normal")
                return
            self.ready = False
            self.starting = False
            self.set_buttons(False)
            self.set_status("服务已停止", "本地服务已经结束运行，可以点击右上角关闭窗口。", COLOR_ERROR)
            self.exit_button.configure(state="normal")
            return

        payload = read_health(self.port)
        if is_project_health(payload, expected_version=run_server.APP_VERSION):
            if not self.ready:
                self.ready = True
                self.starting = False
                self.set_buttons(True)
                self.set_status("服务正在运行", "学生答题页已准备好，可以在浏览器中使用。", COLOR_SUCCESS)
                if not self.options.no_browser and self.allow_auto_open:
                    self.open_page(student_url(self.port))
        elif self.starting:
            self.set_status("正在启动……", "正在等待本地服务响应。", COLOR_WARNING)
        self.root.after(HEALTH_POLL_MS, self.poll_service)

    def open_page(self, url: str) -> None:
        if not self.ready:
            return
        try:
            webbrowser.open(url)
        except Exception as error:
            self.set_status("服务正在运行", f"浏览器打开失败，请手动访问：{url}（{error}）", COLOR_WARNING)

    def open_admin(self) -> None:
        if not self.ready:
            return
        dialog = Toplevel(self.root)
        dialog.title("管理员验证")
        dialog.configure(bg=COLOR_WINDOW)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = Frame(dialog, bg=COLOR_WINDOW, padx=24, pady=22)
        outer.pack(fill=BOTH, expand=True)
        Label(
            outer,
            text="管理员验证",
            bg=COLOR_WINDOW,
            fg=COLOR_TEXT,
            font=(self.ui_font, 15, "bold"),
            anchor="w",
        ).pack(fill=X)
        Label(
            outer,
            text="请输入管理员密码后打开管理后台。",
            bg=COLOR_WINDOW,
            fg=COLOR_MUTED,
            font=(self.ui_font, 9),
            anchor="w",
        ).pack(fill=X, pady=(5, 14))
        Label(
            outer,
            text="管理密码",
            bg=COLOR_WINDOW,
            fg=COLOR_TEXT,
            font=(self.ui_font, 10),
            anchor="w",
        ).pack(fill=X)
        password_entry = Entry(
            outer,
            show="●",
            font=(self.ui_font, 11),
            relief="flat",
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT,
            highlightthickness=1,
        )
        password_entry.pack(fill=X, pady=(5, 0))
        error_text = StringVar(value="")
        Label(
            outer,
            textvariable=error_text,
            bg=COLOR_WINDOW,
            fg=COLOR_ERROR,
            font=(self.ui_font, 9),
            anchor="w",
        ).pack(fill=X, pady=(7, 0))
        actions = Frame(outer, bg=COLOR_WINDOW)
        actions.pack(fill=X, pady=(16, 0))

        def cancel() -> None:
            password_entry.delete(0, "end")
            dialog.destroy()

        def submit() -> None:
            password = password_entry.get()
            password_entry.delete(0, "end")
            try:
                authenticated = run_server.authenticate_admin_password(password)
            except Exception:
                authenticated = False
            password = ""
            if not authenticated:
                error_text.set("管理密码不正确。")
                password_entry.focus_set()
                return
            try:
                ticket = run_server.create_admin_launch_ticket()
            except Exception:
                error_text.set("暂时无法打开管理后台，请稍后重试。")
                return
            dialog.destroy()
            self.open_page(f"{admin_url(self.port)}#launch={quote(ticket, safe='')}")

        self._make_button(actions, text="取消", command=cancel).pack(side=RIGHT)
        self._make_button(actions, text="进入管理后台", command=submit, primary=True).pack(side=RIGHT, padx=(0, 10))
        dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        password_entry.focus_set()

    def open_password_change(self) -> None:
        if not self.ready:
            return
        dialog = Toplevel(self.root)
        dialog.title("修改管理员密码")
        dialog.configure(bg=COLOR_WINDOW)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = Frame(dialog, bg=COLOR_WINDOW, padx=24, pady=22)
        outer.pack(fill=BOTH, expand=True)
        Label(
            outer,
            text="修改管理员密码",
            bg=COLOR_WINDOW,
            fg=COLOR_TEXT,
            font=(self.ui_font, 15, "bold"),
            anchor="w",
        ).pack(fill=X)
        Label(
            outer,
            text="修改成功后，已有后台授权会立即失效。",
            bg=COLOR_WINDOW,
            fg=COLOR_MUTED,
            font=(self.ui_font, 9),
            anchor="w",
        ).pack(fill=X, pady=(5, 14))

        entries: dict[str, Entry] = {}
        for key, label in (
            ("current", "当前密码"),
            ("new", "新密码"),
            ("confirm", "确认新密码"),
        ):
            Label(
                outer,
                text=label,
                bg=COLOR_WINDOW,
                fg=COLOR_TEXT,
                font=(self.ui_font, 10),
                anchor="w",
            ).pack(fill=X, pady=(8 if entries else 0, 0))
            entry = Entry(
                outer,
                show="●",
                font=(self.ui_font, 11),
                relief="flat",
                highlightbackground=COLOR_BORDER,
                highlightcolor=COLOR_ACCENT,
                highlightthickness=1,
            )
            entry.pack(fill=X, pady=(5, 0))
            entries[key] = entry

        error_text = StringVar(value="")
        Label(
            outer,
            textvariable=error_text,
            bg=COLOR_WINDOW,
            fg=COLOR_ERROR,
            font=(self.ui_font, 9),
            anchor="w",
            justify="left",
            wraplength=360,
        ).pack(fill=X, pady=(7, 0))
        actions = Frame(outer, bg=COLOR_WINDOW)
        actions.pack(fill=X, pady=(16, 0))

        def clear_entries() -> None:
            for entry in entries.values():
                entry.delete(0, "end")

        def cancel() -> None:
            clear_entries()
            dialog.destroy()

        def submit() -> None:
            current = entries["current"].get()
            new = entries["new"].get()
            confirm = entries["confirm"].get()
            clear_entries()
            if new != confirm:
                error_text.set("两次输入的新密码不一致。")
                entries["new"].focus_set()
                return
            try:
                with run_server.WRITE_LOCK:
                    changed = run_server.change_admin_password(current, new)
            except ValueError as error:
                error_text.set(str(error))
                entries["current"].focus_set()
                return
            except OSError:
                error_text.set("密码保存失败，请检查应用目录权限。")
                entries["current"].focus_set()
                return
            except Exception:
                error_text.set("密码保存失败，请稍后重试。")
                entries["current"].focus_set()
                return
            current = ""
            new = ""
            confirm = ""
            if not changed:
                error_text.set("当前密码不正确。")
                entries["current"].focus_set()
                return
            dialog.destroy()
            messagebox.showinfo(
                APP_TITLE,
                "管理员密码已修改。\n已有管理后台授权已失效，请使用新密码重新打开管理后台。",
                parent=self.root,
            )

        self._make_button(actions, text="取消", command=cancel).pack(side=RIGHT)
        self._make_button(actions, text="保存新密码", command=submit, primary=True).pack(side=RIGHT, padx=(0, 10))
        dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        entries["current"].focus_set()

    def restart_service(self) -> None:
        """Restart the local HTTP service without exiting the launcher."""

        if self.closing or self.restarting or not self.ready:
            return
        accepted = messagebox.askyesno(
            APP_TITLE,
            "重启服务会中断当前正在答题或使用后台的浏览器页面，页面可能需要刷新。是否继续？",
            parent=self.root,
        )
        if not accepted:
            return
        self.restarting = True
        self.ready = False
        self.starting = False
        self.allow_auto_open = False
        self.set_buttons(False)
        self.exit_button.configure(state="disabled")
        self.set_status("正在重启服务……", "正在停止旧服务并重新启动，请稍候。", COLOR_WARNING)
        if not self.restarter.request_restart():
            self.restarting = False
            self.ready = True
            self.exit_button.configure(state="normal")
            self.set_buttons(True)

    def _start_restarted_server(self) -> None:
        # The old service has confirmed exited; start exactly one new thread.
        self.server_error = None
        self.server_thread = threading.Thread(target=self._run_server, name="wenyan-server", daemon=True)
        self.server_thread.start()

    def _on_restart_done_from_worker(self, outcome: dict) -> None:
        try:
            self.root.after(0, lambda: self._finish_restart(outcome))
        except Exception:
            pass

    def _finish_restart(self, outcome: dict) -> None:
        if self.closing:
            return
        self.restarting = False
        self.exit_button.configure(state="normal")
        if outcome.get("ok") is True:
            self.ready = True
            self.set_buttons(True)
            self.set_status(
                "服务正在运行",
                "服务已重启。后台登录会话已重置，如需管理请重新从启动窗口打开后台。",
                COLOR_SUCCESS,
            )
            return
        message = str(outcome.get("message") or "服务重启失败。")
        if outcome.get("old_alive") is True:
            # The old service is still healthy: restore the ready state.
            self.ready = True
            self.set_buttons(True)
            self.set_status("服务正在运行", f"{message}旧服务仍在运行。", COLOR_WARNING)
        else:
            self.ready = False
            self.set_buttons(False)
            self._set_restart_button(True)
            self.set_status("服务重启失败", f"{message}可以再次点击“重启服务”重试，或退出程序。", COLOR_ERROR)

    def begin_close(self, confirm: bool = True) -> None:
        if self.closing or self.restarting:
            return
        if confirm and (self.ready or self.starting):
            accepted = messagebox.askyesno(
                APP_TITLE,
                "关闭文言实词限时训练？\n关闭后浏览器中的答题页面将无法继续使用。",
                parent=self.root,
            )
            if not accepted:
                return
        self.closing = True
        self.set_buttons(False)
        self.exit_button.configure(state="disabled")
        self.set_status("正在关闭……", "正在停止本地答题服务，请稍候。", COLOR_WARNING)
        threading.Thread(target=self._shutdown_worker, name="wenyan-shutdown", daemon=True).start()

    def _shutdown_worker(self) -> None:
        server = run_server.HTTP_SERVER
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        thread = self.server_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self.root.after(0, self._finish_close)

    def _finish_close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass

    def on_close(self) -> None:
        self.begin_close(confirm=True)


def redirect_frozen_output() -> object | None:
    if not getattr(sys, "frozen", False):
        return None
    path = user_data_dir() / "launcher.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
        sys.stdout = handle  # type: ignore[assignment]
        sys.stderr = handle  # type: ignore[assignment]
        return handle
    except OSError:
        return None


def main() -> int:
    options = parse_args()
    cleanup_stale_updater_runtime()
    log_handle = redirect_frozen_output()
    root = Tk()
    LauncherApp(root, options)
    root.mainloop()
    if log_handle is not None:
        log_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
