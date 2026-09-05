"""Small Tkinter launcher for the packaged local quiz service.

The browser remains the actual student and administrator UI.  This module only
owns the local service lifecycle and gives the packaged application a small,
quiet Windows window instead of a console window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import BOTH, LEFT, RIGHT, X, Button, StringVar, Tk, messagebox
from tkinter import Frame, Label, font as tkfont

import run_server


APP_TITLE = "文言实词限时训练"
APP_SUBTITLE = "本地教学答题工具"
DEFAULT_PORT = 8000
STARTUP_TIMEOUT_SECONDS = 20.0
HEALTH_POLL_MS = 250

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


def is_project_health(payload: dict | None) -> bool:
    return bool(payload and payload.get("ok") is True and payload.get("app") == run_server.APP_NAME)


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
        self._state_lock = threading.Lock()
        self.ui_font = choose_ui_font(root)
        self.mono_font = "Consolas"

        self.status_text = StringVar(value="正在启动……")
        self.detail_text = StringVar(value="正在准备本地答题服务，请稍候。")
        self.status_color = COLOR_WARNING
        self._build_window()
        self.root.after(80, self.start_service)
        self.root.after(HEALTH_POLL_MS, self.poll_service)

    def _build_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=COLOR_WINDOW)
        self.root.resizable(False, False)
        self.root.minsize(600, 420)
        self.root.geometry("600x420")
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
            text=APP_SUBTITLE,
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
            wraplength=510,
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
            command=lambda: self.open_page(admin_url(self.port)),
        )
        self.admin_button.pack(side=LEFT)

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
        if self.closing:
            self.exit_button.configure(state="disabled")

    def set_status(self, text: str, detail: str, color: str) -> None:
        self.status_text.set(text)
        self.detail_text.set(detail)
        self.status_color = color
        self.status_dot.configure(fg=color)

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
        if is_project_health(payload):
            if not self.ready:
                self.ready = True
                self.starting = False
                self.set_buttons(True)
                self.set_status("服务正在运行", "学生答题页已准备好，可以在浏览器中使用。", COLOR_SUCCESS)
                if not self.options.no_browser:
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

    def begin_close(self, confirm: bool = True) -> None:
        if self.closing:
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
    log_handle = redirect_frozen_output()
    root = Tk()
    LauncherApp(root, options)
    root.mainloop()
    if log_handle is not None:
        log_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
