"""Classroom sync server: shared-state coordination center (§10).

Stdlib only: ``http.server.ThreadingHTTPServer`` + ``sqlite3`` + a
process-level write lock.  Serves one shared workspace (§6), realtime
entity operations, persistent sync conflicts and manual whole-bank backups.
Run with ``python sync_server/server.py serve --host 0.0.0.0 --port 10001``.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from sync_protocol import (  # noqa: E402
    KDF_ITERATIONS,
    MAX_OPERATION_BYTES,
    MAX_SNAPSHOT_BYTES,
    SYNC_PROTOCOL_VERSION,
    canonical_hash,
    derive_auth_key,
    new_salt_hex,
    summarize_bank,
    validate_operation_shape,
)
from storage import StaleConflict, SyncStorage  # noqa: E402
from auth import SyncAuth  # noqa: E402

try:
    from server_validators import validate_question_bank_v4  # noqa: E402
except ImportError:  # pragma: no cover - server always ships with tools/
    validate_question_bank_v4 = None


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 10001
DB_BACKUP_KEEP = 7


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class SyncLogger:
    """Append-only operational log without secrets or bank content (§120)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def event(self, **fields: object) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            record = {"time": _utcnow()}
            record.update(fields)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass


def backup_database(db_path: Path, backup_dir: Path, keep: int = DB_BACKUP_KEEP) -> Path | None:
    """Online SQLite backup on startup (§85); keeps a small recent set."""
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        destination = backup_dir / f"sync-{stamp}.db"
        source = sqlite3.connect(str(db_path))
        try:
            target = sqlite3.connect(str(destination))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        backups = sorted(backup_dir.glob("sync-*.db"))
        for stale in backups[:-keep] if len(backups) > keep else []:
            try:
                stale.unlink()
            except OSError:
                pass
        return destination
    except (OSError, sqlite3.Error):
        return None


SAFE_BACKUP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SyncRequestHandler(BaseHTTPRequestHandler):
    server_version = "WenyanSync/1"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------
    def log_message(self, *args: object) -> None:
        pass

    @property
    def _app(self) -> "SyncApplication":
        return self.server.application  # type: ignore[attr-defined]

    def _send(self, status: int, payload: object, session_id: str = "", seq: int = 0) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            signature = self._app.auth.sign_response(session_id, seq, body)
            if signature:
                self.send_header("X-Sync-Resp-Signature", signature)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError):
            pass

    def _fail(self, status: int, message: str) -> None:
        self._send(status, {"ok": False, "error": message})

    def _read_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise ValueError("请求长度无效。")
        if length < 0 or length > limit:
            raise ValueError("请求内容过大。")
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _json_body(self, limit: int = MAX_OPERATION_BYTES) -> dict:
        try:
            payload = json.loads(self._read_body(limit).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("请求不是有效的 JSON。") from error
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return payload

    def _headers_lower(self) -> dict[str, str]:
        return {key.lower(): value for key, value in self.headers.items()}

    def _authed(self, body: bytes) -> tuple[str, int, str]:
        headers = self._headers_lower()
        try:
            seq = int(headers.get("x-sync-seq", ""))
        except (TypeError, ValueError):
            seq = 0
        try:
            username = self._app.auth.verify_request(headers, self.command, self.path, body)
        except ValueError as error:
            raise PermissionError(str(error)) from error
        return username, seq, headers.get("x-sync-session", "")

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if route == "/api/v1/health":
                self._send(200, {"ok": True, "service": "wenyan-sync",
                                 "protocolVersion": SYNC_PROTOCOL_VERSION})
                return
            raw_body = b""
            username, seq, session_id = self._authed(raw_body)
            if route == "/api/v1/sync/snapshot":
                state = self._app.storage.get_state()
                if state["bank"] is None:
                    self._send(200, {"ok": True, "revision": 0, "bank": None, "etag": None},
                               session_id, seq)
                else:
                    self._send(200, {"ok": True, "revision": state["revision"],
                                     "bank": state["bank"], "etag": state["bank_etag"]},
                               session_id, seq)
                return
            if route == "/api/v1/sync/changes":
                try:
                    after = int(query.get("after", "0"))
                except (TypeError, ValueError):
                    raise ValueError("after 参数无效。")
                try:
                    limit = int(query.get("limit", "500"))
                except (TypeError, ValueError):
                    limit = 500
                changes = self._app.storage.list_changes(after, limit)
                self._send(200, {"ok": True, **changes}, session_id, seq)
                return
            if route == "/api/v1/sync/conflicts":
                conflicts = self._app.storage.list_open_conflicts()
                self._send(200, {"ok": True, "conflicts": conflicts}, session_id, seq)
                return
            if route == "/api/v1/backup/list":
                backups = self._app.storage.list_backups()
                self._send(200, {"ok": True, "backups": backups}, session_id, seq)
                return
            if route == "/api/v1/backup/download":
                backup_id = query.get("id", "")
                if not SAFE_BACKUP_ID.fullmatch(backup_id):
                    raise ValueError("备份 id 无效。")
                record = self._app.storage.get_backup(backup_id)
                if record is None:
                    self._fail(404, "备份不存在。")
                    return
                path = self._app.backup_file(record["file_name"])
                try:
                    content = path.read_bytes()
                except OSError:
                    self._fail(404, "备份文件已缺失。")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-Backup-Filename", record["file_name"])
                self.end_headers()
                try:
                    self.wfile.write(content)
                except (ConnectionError, BrokenPipeError):
                    pass
                return
            self._fail(404, "未找到这个同步接口。")
        except PermissionError as error:
            self._fail(401, str(error))
        except ValueError as error:
            self._fail(400, str(error))
        except Exception as error:  # Never leak internals.
            self._app.logger.event(kind="error", route=route, error=type(error).__name__)
            self._fail(500, "同步服务器内部错误。")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        try:
            if route == "/api/v1/auth/challenge":
                payload = self._json_body()
                username = str(payload.get("username", "")).strip()
                client_ip = self.client_address[0] if self.client_address else ""
                if not self._app.auth.check_login_allowed(client_ip, username):
                    self._fail(429, "登录尝试过于频繁，请 60 秒后再试。")
                    return
                try:
                    challenge = self._app.auth.issue_challenge(username)
                except ValueError:
                    self._app.auth.record_login_failure(client_ip, username)
                    self._fail(401, "账号或密码不正确。")
                    return
                self._send(200, {"ok": True, "protocolVersion": SYNC_PROTOCOL_VERSION,
                                 **challenge})
                return
            if route == "/api/v1/auth/login":
                payload = self._json_body()
                username = str(payload.get("username", "")).strip()
                client_ip = self.client_address[0] if self.client_address else ""
                if not self._app.auth.check_login_allowed(client_ip, username):
                    self._fail(429, "登录尝试过于频繁，请 60 秒后再试。")
                    return
                try:
                    session = self._app.auth.verify_login(
                        username, str(payload.get("challenge_id", "")),
                        str(payload.get("proof", "")))
                except ValueError:
                    self._app.auth.record_login_failure(client_ip, username)
                    self._fail(401, "账号或密码不正确。")
                    return
                self._app.logger.event(kind="login", username=username)
                self._send(200, {"ok": True, **session})
                return
            limit = MAX_SNAPSHOT_BYTES if route in (
                "/api/v1/sync/bootstrap", "/api/v1/backup/upload") else MAX_OPERATION_BYTES
            raw_body = self._read_body(limit)
            username, seq, session_id = self._authed(raw_body)
            if route == "/api/v1/sync/bootstrap":
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("请求不是有效的 JSON。") from error
                if not isinstance(payload, dict) or not isinstance(payload.get("bank"), dict):
                    raise ValueError("缺少完整题库 bank。")
                result = self._app.storage.bootstrap(
                    payload["bank"], payload.get("base_etag"), self._app.validate_bank)
                self._app.logger.event(kind="bootstrap", username=username,
                                       client_id=str(payload.get("client_id", "")),
                                       revision=result["revision"])
                self._send(200, {"ok": True, **result}, session_id, seq)
                return
            if route == "/api/v1/sync/push":
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("请求不是有效的 JSON。") from error
                operations = payload.get("operations", [])
                if not isinstance(operations, list) or len(operations) > 500:
                    raise ValueError("operations 非法。")
                client_id = str(payload.get("client_id", ""))
                device_name = str(payload.get("device_name", ""))[:40]
                results = []
                for operation in operations:
                    try:
                        validate_operation_shape(operation)
                        outcome = self._app.storage.apply_operation(
                            operation, client_id or str(operation.get("client_id", "")),
                            username, self._app.validate_bank, device=device_name)
                        results.append({"ok": True, **outcome})
                        self._app.logger.event(
                            kind="operation", username=username, client_id=client_id,
                            operation_type=operation.get("operation_type"),
                            entity_kind=operation.get("entity_kind"),
                            entity_id=operation.get("entity_id"),
                            status=outcome.get("status"), revision=outcome.get("revision"))
                        if outcome.get("status") == "conflict" and outcome.get("conflict"):
                            self._app.logger.event(
                                kind="conflict", username=username, client_id=client_id,
                                conflict_id=outcome["conflict"].get("conflict_id"),
                                entity_kind=outcome["conflict"].get("entity_kind"),
                                entity_id=outcome["conflict"].get("entity_id"))
                    except ValueError as error:
                        results.append({"ok": False, "error": str(error)})
                state = self._app.storage.get_state()
                self._send(200, {"ok": True, "revision": state["revision"], "results": results},
                           session_id, seq)
                return
            if route == "/api/v1/sync/conflicts/resolve":
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("请求不是有效的 JSON。") from error
                try:
                    outcome = self._app.storage.resolve_conflict(
                        str(payload.get("conflict_id", "")), str(payload.get("choice", "")),
                        username, self._app.validate_bank)
                except StaleConflict as stale:
                    self._send(409, {"ok": False, "error": str(stale),
                                     "conflict": stale.conflict}, session_id, seq)
                    return
                self._app.logger.event(kind="resolve", username=username,
                                       conflict_id=outcome.get("conflict_id"),
                                       choice=outcome.get("choice"),
                                       revision=outcome.get("revision"))
                self._send(200, {"ok": True, **outcome}, session_id, seq)
                return
            if route == "/api/v1/backup/upload":
                try:
                    bank = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("备份不是有效的 JSON 题库。") from error
                validated = self._app.validate_bank(bank)
                digest = hashlib.sha256(raw_body).hexdigest()
                summary = summarize_bank(validated)
                backup_id = "bk_" + digest[:16]
                file_name = f"{backup_id}.json"
                headers = self._headers_lower()
                device = (headers.get("x-backup-device") or "")[:40] or "未知设备"
                client_id = (headers.get("x-backup-client") or "")[:80]
                self._app.write_backup_file(file_name, raw_body)
                self._app.storage.add_backup({
                    "backup_id": backup_id, "file_name": file_name, "sha256": digest,
                    "size": len(raw_body), "bank_id": summary["bank_id"],
                    "question_count": summary["question_count"],
                    "review_summary": summary["review_summary"], "username": username,
                    "client_id": client_id, "device_name": device,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                })
                self._app.logger.event(kind="backup", username=username, client_id=client_id,
                                       backup_id=backup_id, size=len(raw_body))
                self._send(200, {"ok": True, "backup_id": backup_id, "sha256": digest,
                                 "size": len(raw_body), **summary}, session_id, seq)
                return
            self._fail(404, "未找到这个同步接口。")
        except PermissionError as error:
            self._fail(401, str(error))
        except ValueError as error:
            self._fail(400, str(error))
        except Exception as error:
            self._app.logger.event(kind="error", route=route, error=type(error).__name__)
            self._fail(500, "同步服务器内部错误。")


class SyncApplication:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.storage = SyncStorage(data_dir / "sync.db")
        self.auth = SyncAuth(self.storage)
        self.logger = SyncLogger(data_dir / "sync-server.log")
        self.backup_dir = data_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_bank(bank: dict) -> dict:
        if validate_question_bank_v4 is None:
            raise ValueError("服务器缺少 v4 校验模块。")
        return validate_question_bank_v4(bank)

    def backup_file(self, file_name: str) -> Path:
        # Filenames are server-generated; never trust client path input (§124).
        if not re.fullmatch(r"bk_[0-9a-f]{16}\.json", file_name):
            raise ValueError("备份文件名非法。")
        return self.backup_dir / file_name

    def write_backup_file(self, file_name: str, content: bytes) -> None:
        path = self.backup_file(file_name)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)


def serve(host: str, port: int, data_dir: Path) -> None:
    application = SyncApplication(data_dir)
    backup_database(data_dir / "sync.db", data_dir / "db-backups")
    server = ThreadingHTTPServer((host, port), SyncRequestHandler)
    server.application = application  # type: ignore[attr-defined]
    server.daemon_threads = True
    print(f"同步服务器已启动：http://{host}:{port}（数据目录：{data_dir}）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n同步服务器已停止。")


def cmd_user(action: str, username: str, data_dir: Path) -> int:
    from sync_protocol import KDF_ITERATIONS

    application = SyncApplication(data_dir)
    username = username.strip()
    if not username or len(username) > 64:
        print("账号无效。")
        return 2
    if action == "list":
        for record in application.storage.list_users():
            state = "启用" if record["enabled"] else "停用"
            print(f"{record['username']}\t{state}\t{record['created_at']}")
        return 0
    if action in ("disable", "enable"):
        try:
            application.storage.set_user_enabled(username, action == "enable")
        except ValueError as error:
            print(error)
            return 2
        print(f"已{'启用' if action == 'enable' else '停用'}：{username}")
        return 0
    if action in ("add", "reset-password"):
        if action == "add" and application.storage.get_user(username):
            print(f"账号已存在：{username}")
            return 2
        password = getpass.getpass("同步密码（输入不可见）：")
        confirm = getpass.getpass("再次输入确认：")
        if password != confirm:
            print("两次输入不一致。")
            return 2
        if len(password) < 6:
            print("密码至少 6 位。")
            return 2
        salt_hex = new_salt_hex()
        auth_key = derive_auth_key(password, salt_hex, KDF_ITERATIONS)
        try:
            if action == "add":
                application.storage.create_user(username, salt_hex, auth_key.hex(), KDF_ITERATIONS)
            else:
                application.storage.update_user_secret(username, salt_hex, auth_key.hex(), KDF_ITERATIONS)
        except ValueError as error:
            print(error)
            return 2
        print(f"已{'创建' if action == 'add' else '重置密码'}：{username}")
        return 0
    print(f"未知账号操作：{action}")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文言实词题库同步服务器")
    parser.add_argument("--data-dir", type=Path, default=Path("sync-server-data"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve", help="启动同步服务")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    if not 1 <= DEFAULT_PORT <= 65535:
        raise SystemExit("默认端口无效。")
    user_parser = subparsers.add_parser("user", help="账号管理")
    user_parser.add_argument("action", choices=["add", "reset-password", "disable", "enable", "list"])
    user_parser.add_argument("username", nargs="?", default="")
    options = parser.parse_args(argv)
    if options.command == "serve":
        if not 1 <= options.port <= 65535:
            raise SystemExit("端口必须在 1-65535 范围内。")
        serve(options.host, options.port, options.data_dir)
        return 0
    return cmd_user(options.action, options.username, options.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
