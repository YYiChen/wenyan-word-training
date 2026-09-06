"""Local-first classroom sync client.

Runs inside the local Python server process as a single background worker
(§25).  The browser never talks to the remote server (§25): it only uses
the local admin APIs below, while this worker speaks to the sync server.

Local-first rules (§9, §61-62):
- every local edit is saved to ``data/questions.json`` first, UI first;
- the worker discovers changes by diffing ``sync-shadow.json`` vs local;
- offline only flips status; nothing is blocked and nothing is lost.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


SYNC_INTERVAL_SECONDS = 2.0
BACKOFF_STEPS = (2.0, 5.0, 10.0, 30.0)
MAX_BACKOFF_SECONDS = 30.0
PORT_MIN, PORT_MAX = 7501, 65535


def _user_data_dir() -> Path:
    from server_config import get_user_data_dir

    return get_user_data_dir()


def sync_paths() -> dict[str, Path]:
    root = _user_data_dir()
    return {
        "settings": root / "sync-settings.json",
        "shadow": root / "sync-shadow.json",
        "state": root / "sync-state.json",
        "credential": root / "sync-credential.bin",
        "log": root / "sync.log",
    }


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return copy.deepcopy(default)


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def log_sync_event(message: str, **fields: Any) -> None:
    """Client sync log without secrets or bank content (§121)."""
    try:
        path = sync_paths()["log"]
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now().isoformat(timespec="seconds"), "message": message}
        record.update(fields)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# -- settings / shadow / state (§20-22) ------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "host": "39.171.79.237",
    "port": 10001,
    "username": "",
    "clientId": "",
    "deviceName": "",
    "lastRevision": 0,
}


def default_device_name() -> str:
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return name or "本机电脑"


def load_settings() -> dict[str, Any]:
    raw = _read_json_file(sync_paths()["settings"], {})
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key in ("enabled", "host", "port", "username", "clientId", "deviceName", "lastRevision"):
            if key in raw:
                settings[key] = raw[key]
    settings["enabled"] = bool(settings["enabled"])
    try:
        settings["port"] = int(settings["port"])
    except (TypeError, ValueError):
        settings["port"] = 10001
    if not settings["port"] or not 1 <= settings["port"] <= 65535:
        settings["port"] = 10001
    try:
        settings["lastRevision"] = int(settings["lastRevision"])
    except (TypeError, ValueError):
        settings["lastRevision"] = 0
    return settings


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(DEFAULT_SETTINGS)
    for key in cleaned:
        if key in settings:
            cleaned[key] = settings[key]
    cleaned["enabled"] = bool(cleaned["enabled"])
    _write_json_file(sync_paths()["settings"], cleaned)
    return cleaned


def load_shadow() -> dict[str, Any] | None:
    raw = _read_json_file(sync_paths()["shadow"], None)
    if not isinstance(raw, dict) or not isinstance(raw.get("bank"), dict):
        return None
    return raw


def save_shadow(bank: dict[str, Any], revision: int) -> None:
    _write_json_file(sync_paths()["shadow"], {
        "bankId": bank.get("bankId", ""),
        "revision": revision,
        "savedAt": datetime.now().isoformat(timespec="seconds"),
        "bank": bank,
    })


def load_state() -> dict[str, Any]:
    return _read_json_file(sync_paths()["state"], {
        "blockedQuestionIds": [],
        "suppressions": {},
        "lastStatus": {"phase": "disabled"},
        "pendingLocal": 0,
    })


def save_state(state: dict[str, Any]) -> None:
    _write_json_file(sync_paths()["state"], state)


def blocked_question_ids() -> set[str]:
    """Ids the student pool must treat as temporarily blocked (§55)."""
    state = load_state()
    raw = state.get("blockedQuestionIds", [])
    return {str(item) for item in raw if isinstance(item, str) and item}


# -- DPAPI credential store (§73-74) -----------------------------------------

def _dpapi_available() -> bool:
    return os.name == "nt"


def dpapi_protect(payload: bytes) -> bytes | None:
    """Encrypt with Windows DPAPI (user scope). None when unavailable."""
    if not _dpapi_available():
        return None
    try:
        import ctypes.wintypes  # noqa: F401

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
        source = DATA_BLOB(len(payload), ctypes.cast(
            ctypes.create_string_buffer(payload), ctypes.POINTER(ctypes.c_byte)))
        target = DATA_BLOB()
        if not crypt32.CryptProtectData(
                ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(target)):
            return None
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            crypt32.LocalFree(target.pbData)
    except (OSError, AttributeError, ValueError):
        return None


def dpapi_unprotect(blob: bytes) -> bytes | None:
    if not _dpapi_available():
        return None
    try:
        import ctypes.wintypes  # noqa: F401

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
        source = DATA_BLOB(len(blob), ctypes.cast(
            ctypes.create_string_buffer(blob), ctypes.POINTER(ctypes.c_byte)))
        target = DATA_BLOB()
        try:
            if not crypt32.CryptUnprotectData(
                    ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
                return None
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            try:
                crypt32.LocalFree(target.pbData)
            except OSError:
                pass
    except (OSError, AttributeError, ValueError):
        return None


def save_credential(host: str, port: int, username: str, auth_key_hex: str,
                    salt_hex: str, iterations: int) -> bool:
    """Persist derived auth material via DPAPI; never the password (§73)."""
    payload = json.dumps({
        "host": host, "port": port, "username": username,
        "auth_key": auth_key_hex, "salt": salt_hex, "iterations": iterations,
    }).encode("utf-8")
    blob = dpapi_protect(payload)
    if blob is None:
        return False
    path = sync_paths()["credential"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(blob)
    os.replace(temporary, path)
    return True


def load_credential() -> dict[str, Any] | None:
    try:
        blob = sync_paths()["credential"].read_bytes()
    except OSError:
        return None
    payload = dpapi_unprotect(blob)
    if payload is None:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("username") or not data.get("auth_key"):
        return None
    return data


def clear_credential() -> None:
    try:
        sync_paths()["credential"].unlink()
    except OSError:
        pass


# -- signed session client ----------------------------------------------------

class SyncError(Exception):
    pass


class SyncOffline(SyncError):
    pass


class SyncSession:
    """HMAC-signed remote session kept in process memory only."""

    def __init__(self, host: str, port: int, username: str,
                 session_id: str, session_secret_hex: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.session_id = session_id
        self._secret = bytes.fromhex(session_secret_hex)
        self._seq = 0
        self._lock = threading.Lock()

    def _sign(self, seq: int, method: str, path: str, body: bytes) -> str:
        import hashlib
        import hmac as hmac_module

        body_hash = hashlib.sha256(body or b"").hexdigest()
        message = f"{self.session_id}|{seq}|{method}|{path}|{body_hash}"
        return hmac_module.new(self._secret, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def request(self, method: str, path: str, body: bytes | None = None,
                timeout: float = 10.0) -> tuple[int, bytes, dict[str, str]]:
        import hashlib

        raw = body or b""
        with self._lock:
            self._seq += 1
            seq = self._seq
        url = f"http://{self.host}:{self.port}{path}"
        request = urllib.request.Request(
            url, data=raw if method != "GET" else None, method=method,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Sync-Session": self.session_id,
                "X-Sync-Seq": str(seq),
                "X-Sync-Signature": self._sign(seq, method, path, raw),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
                status = response.status
                resp_signature = response.headers.get("X-Sync-Resp-Signature", "")
        except urllib.error.HTTPError as error:
            content = error.read()
            status = error.code
            if status in (401, 403):
                raise SyncError("同步会话已失效，请重新连接。")
            return status, content, {}
        except (OSError, TimeoutError) as error:
            raise SyncOffline(f"同步服务器不可达：{error}")
        if status == 401:
            raise SyncError("同步会话已失效，请重新连接。")
        # Verify response integrity when the server signs (§72).
        if resp_signature and resp_signature != self._sign_response(seq, content):
            raise SyncError("同步响应签名无效，已丢弃。")
        # Verify response integrity when the server signs (§72).
        expected = self._sign_response(seq, content)
        return status, content, {"resp_signature": expected}

    def _sign_response(self, seq: int, body: bytes) -> str:
        import hashlib
        import hmac as hmac_module

        body_hash = hashlib.sha256(body or b"").hexdigest()
        return hmac_module.new(
            self._secret, f"{self.session_id}|{seq}|{body_hash}".encode("utf-8"),
            hashlib.sha256).hexdigest()

    def call(self, method: str, path: str, payload: Any = None,
             raw: bytes | None = None, timeout: float = 10.0) -> Any:
        body = raw if raw is not None else (
            json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
            if method != "GET" else b"")
        status, content, _headers = self.request(method, path, body, timeout)
        if status >= 400:
            try:
                message = json.loads(content.decode("utf-8")).get("error", f"HTTP {status}")
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = f"HTTP {status}"
            raise SyncError(message)
        if not content:
            return {}
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SyncError("同步响应不是有效 JSON。") from error


def remote_call(host: str, port: int, path: str, payload: Any,
                timeout: float = 10.0) -> Any:
    """Unsigned call for challenge/health endpoints."""
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://{host}:{port}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read().decode("utf-8")).get("error", f"HTTP {error.code}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = f"HTTP {error.code}"
        raise SyncError(message)
    except (OSError, TimeoutError) as error:
        raise SyncOffline(f"同步服务器不可达：{error}")


def check_protocol(host: str, port: int, timeout: float = 6.0) -> None:
    from sync_protocol import SYNC_PROTOCOL_VERSION

    request = urllib.request.Request(f"http://{host}:{port}/api/v1/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SyncOffline(f"同步服务器不可达：{error}")
    if not isinstance(payload, dict) or payload.get("service") != "wenyan-sync":
        raise SyncError("目标不是题库同步服务器。")
    if payload.get("protocolVersion") != SYNC_PROTOCOL_VERSION:
        raise SyncError("同步服务器版本与客户端不兼容。")


def login_with_password(host: str, port: int, username: str, password: str,
                        timeout: float = 10.0) -> tuple[SyncSession, dict[str, Any]]:
    """Full login; returns the session plus derived material for DPAPI."""
    from sync_protocol import KDF_ITERATIONS, derive_auth_key, hmac_hex

    check_protocol(host, port, timeout=timeout)
    challenge = remote_call(host, port, "/api/v1/auth/challenge", {"username": username},
                            timeout=timeout)
    salt = challenge["salt"]
    iterations = int(challenge.get("iterations") or KDF_ITERATIONS)
    auth_key = derive_auth_key(password, salt, iterations)
    proof = hmac_hex(auth_key, f"{challenge['challenge_id']}|{challenge['nonce']}|{username}")
    session_payload = remote_call(host, port, "/api/v1/auth/login", {
        "username": username, "challenge_id": challenge["challenge_id"], "proof": proof,
    }, timeout=timeout)
    session = SyncSession(host, port, username,
                          session_payload["session_id"], session_payload["session_secret"])
    material = {"host": host, "port": port, "username": username,
                "auth_key": auth_key.hex(), "salt": salt, "iterations": iterations}
    return session, material


def login_with_material(material: dict[str, Any], timeout: float = 10.0) -> SyncSession:
    from sync_protocol import KDF_ITERATIONS, hmac_hex

    host = material["host"]
    port = int(material["port"])
    username = material["username"]
    check_protocol(host, port, timeout=timeout)
    challenge = remote_call(host, port, "/api/v1/auth/challenge", {"username": username},
                            timeout=timeout)
    auth_key = bytes.fromhex(material["auth_key"])
    proof = hmac_hex(auth_key, f"{challenge['challenge_id']}|{challenge['nonce']}|{username}")
    session_payload = remote_call(host, port, "/api/v1/auth/login", {
        "username": username, "challenge_id": challenge["challenge_id"], "proof": proof,
    }, timeout=timeout)
    return SyncSession(host, port, username,
                       session_payload["session_id"], session_payload["session_secret"])


# -- remote application -------------------------------------------------------

def apply_pushed_operation_results(suppressions: dict[str, Any],
                                   results: list[dict[str, Any]]) -> dict[str, Any]:
    """Record suppressions for conflicted entities to avoid push loops."""
    from sync_protocol import canonical_hash

    for result in results:
        conflict = (result or {}).get("conflict")
        if (result or {}).get("status") == "conflict" and conflict:
            key = f"{conflict.get('entity_kind')}:{conflict.get('entity_id')}"
            incoming = conflict.get("incoming_value", {}).get("value")
            suppressions[key] = {
                "incoming_hash": canonical_hash(incoming),
                "conflict_id": conflict.get("conflict_id", ""),
            }
    return suppressions


def apply_remote_to_local(bank: dict[str, Any], operation: dict[str, Any]) -> bool:
    """Apply one accepted server operation to the local bank copy."""
    from sync_protocol import set_entity_value, validate_operation_shape

    operation = validate_operation_shape(operation)
    set_entity_value(bank, operation["entity_kind"], operation["entity_id"], operation["new"])
    return True


# -- high-level operations (called by local admin APIs) ------------------------

_PENDING_BOOTSTRAP: dict[str, Any] = {}


class LocalBankAccess:
    """Implemented by run_server and injected (avoids import cycles)."""

    def read_bank(self) -> dict[str, Any]:
        raise NotImplementedError

    def write_bank(self, bank: dict[str, Any]) -> None:
        raise NotImplementedError


def _ephemeral_session(settings: dict[str, Any]) -> SyncSession:
    material = load_credential()
    if material is None:
        raise SyncError("缺少同步凭据，请重新连接。")
    if (material.get("host") != settings["host"]
            or int(material.get("port", 0)) != settings["port"]
            or material.get("username") != settings["username"]):
        raise SyncError("同步配置已变更，请重新连接。")
    return login_with_material(material)


def public_sync_status() -> dict[str, Any]:
    """Status snapshot for the admin UI; never includes secrets (§88)."""
    settings = load_settings()
    state = load_state()
    status = dict(state.get("lastStatus", {"phase": "disabled"}))
    conflicts = state.get("blockedQuestionIds", [])
    return {
        "enabled": settings["enabled"],
        "phase": status.get("phase", "disabled"),
        "host": settings["host"],
        "port": settings["port"],
        "username": settings["username"],
        "deviceName": settings.get("deviceName", ""),
        "clientId": settings.get("clientId", ""),
        "serverRevision": status.get("serverRevision", settings.get("lastRevision", 0)),
        "pendingLocal": state.get("pendingLocal", 0),
        "openConflicts": len(conflicts) if isinstance(conflicts, list) else status.get("openConflicts", 0),
        "lastSyncAt": status.get("lastSyncAt", ""),
        "lastError": status.get("lastError", ""),
        "message": status.get("message", ""),
        "hasCredential": load_credential() is not None,
    }


def validate_sync_endpoint(host: str, port: int, username: str) -> tuple[str, int, str]:
    host = (host or "").strip()
    username = (username or "").strip()
    if not host or len(host) > 255:
        raise SyncError("服务器地址无效。")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SyncError("端口必须是 7501～65535 的整数。")
    if not PORT_MIN <= port <= PORT_MAX:
        raise SyncError("端口必须是 7501～65535 的整数。")
    if not username or len(username) > 64:
        raise SyncError("账号无效。")
    return host, port, username


def test_sync_connection(host: str, port: int, username: str, password: str) -> dict[str, Any]:
    """[测试连接]: protocol + challenge + login, stores nothing."""
    from sync_protocol import SYNC_PROTOCOL_VERSION

    host, port, username = validate_sync_endpoint(host, port, username)
    if not password:
        raise SyncError("请输入同步密码。")
    session, _material = login_with_password(host, port, username, password)
    snapshot = session.call("GET", "/api/v1/sync/snapshot")
    return {"ok": True, "protocolVersion": SYNC_PROTOCOL_VERSION,
            "serverRevision": snapshot.get("revision", 0),
            "serverHasBank": snapshot.get("bank") is not None}


def configure_sync(host: str, port: int, username: str, password: str,
                   device_name: str = "") -> dict[str, Any]:
    """[连接并启用同步] step 1: verify, persist config + DPAPI credential."""
    host, port, username = validate_sync_endpoint(host, port, username)
    if not password:
        raise SyncError("请输入同步密码。")
    session, material = login_with_password(host, port, username, password)
    settings = load_settings()
    client_id = settings.get("clientId") or f"client_{secrets.token_hex(8)}"
    settings.update({"enabled": False, "host": host, "port": port,
                     "username": username, "clientId": client_id,
                     "deviceName": (device_name or "").strip()[:40] or default_device_name()})
    save_settings(settings)
    dpapi_ok = save_credential(host, port, username, material["auth_key"],
                               material["salt"], material["iterations"])
    if not dpapi_ok:
        log_sync_event("credential-memory-only",
                       reason="DPAPI unavailable; restart will ask for password")
    snapshot = session.call("GET", "/api/v1/sync/snapshot")
    return {"ok": True, "dpapi": dpapi_ok,
            "serverRevision": snapshot.get("revision", 0),
            "serverHasBank": snapshot.get("bank") is not None}


def connect_sync(bank_access: LocalBankAccess) -> dict[str, Any]:
    """Decide the bootstrap case or resume quietly (§35-37, §132)."""
    from sync_protocol import summarize_bank

    settings = load_settings()
    session = _ephemeral_session(settings)
    snapshot = session.call("GET", "/api/v1/sync/snapshot")
    server_bank = snapshot.get("bank")
    try:
        local_bank = bank_access.read_bank()
    except (OSError, ValueError) as error:
        raise SyncError(f"本机题库不可读：{error}")
    local_empty = len(local_bank.get("questions", [])) == 0
    if server_bank is None:
        return {"case": "server_empty",
                "local": summarize_bank(local_bank)}
    shadow = load_shadow()
    if local_empty:
        return {"case": "local_empty",
                "server": {**summarize_bank(server_bank),
                           "revision": snapshot.get("revision", 0)}}
    if (shadow is not None and shadow.get("bankId") == local_bank.get("bankId")
            == server_bank.get("bankId")
            and int(settings.get("lastRevision", 0)) == int(snapshot.get("revision", 0))):
        settings["enabled"] = True
        save_settings(settings)
        return {"case": "resumed", "serverRevision": snapshot.get("revision", 0)}
    return {"case": "both",
            "local": summarize_bank(local_bank),
            "server": {**summarize_bank(server_bank),
                       "revision": snapshot.get("revision", 0)}}


def confirm_bootstrap_server_empty(bank_access: LocalBankAccess) -> dict[str, Any]:
    """Upload the local bank as the shared baseline, revision 1 (§35)."""
    settings = load_settings()
    session = _ephemeral_session(settings)
    local_bank = bank_access.read_bank()
    result = session.call("POST", "/api/v1/sync/bootstrap", {
        "bank": local_bank, "base_etag": None,
        "client_id": settings["clientId"], "device_name": settings.get("deviceName", ""),
    })
    save_shadow(local_bank, int(result.get("revision", 1)))
    settings["enabled"] = True
    settings["lastRevision"] = int(result.get("revision", 1))
    save_settings(settings)
    return {"ok": True, "revision": settings["lastRevision"]}


def confirm_bootstrap_local_empty(bank_access: LocalBankAccess) -> dict[str, Any]:
    """Adopt the server bank locally (§36). Caller backs up first."""
    from server_validators import validate_question_bank_v4

    settings = load_settings()
    session = _ephemeral_session(settings)
    snapshot = session.call("GET", "/api/v1/sync/snapshot")
    if not snapshot.get("bank"):
        raise SyncError("服务器共享题库为空。")
    server_bank = validate_question_bank_v4(snapshot["bank"])
    bank_access.write_bank(server_bank)
    save_shadow(server_bank, int(snapshot.get("revision", 0)))
    settings["enabled"] = True
    settings["lastRevision"] = int(snapshot.get("revision", 0))
    save_settings(settings)
    return {"ok": True, "revision": settings["lastRevision"]}


def preview_first_sync(bank_access: LocalBankAccess) -> dict[str, Any]:
    """Merge preview with the server bank as shared lineage (§37-39)."""
    from server_validators import make_json_etag
    from server_question_import import build_import_preview

    settings = load_settings()
    session = _ephemeral_session(settings)
    snapshot = session.call("GET", "/api/v1/sync/snapshot")
    server_bank = snapshot.get("bank")
    if not server_bank:
        raise SyncError("服务器共享题库为空。")
    local_bank = bank_access.read_bank()
    preview = build_import_preview(server_bank, local_bank, mode="merge")
    _PENDING_BOOTSTRAP.clear()
    _PENDING_BOOTSTRAP.update({
        "server_etag": snapshot.get("etag", ""),
        "server_revision": int(snapshot.get("revision", 0)),
        "local_etag": make_json_etag(local_bank),
    })
    preview["perspective"] = "first-sync"
    return preview


def confirm_first_sync(bank_access: LocalBankAccess, review_resolutions: Any) -> dict[str, Any]:
    """Apply the merged baseline to both sides (§41: no silent skip)."""
    from server_validators import make_json_etag, validate_question_bank_v4
    from server_question_import import (
        REVIEW_RESOLUTION_CHOICES, UnresolvedReviewConflicts, merge_question_bank_v4)

    if not _PENDING_BOOTSTRAP:
        raise SyncError("请先预览首次同步。")
    if not isinstance(review_resolutions, dict) or any(
            value not in REVIEW_RESOLUTION_CHOICES for value in review_resolutions.values()):
        raise SyncError("审查冲突处理结果格式无效。")
    settings = load_settings()
    session = _ephemeral_session(settings)
    snapshot = session.call("GET", "/api/v1/sync/snapshot")
    if snapshot.get("etag", "") != _PENDING_BOOTSTRAP.get("server_etag"):
        raise SyncError("服务器共享题库已变化，请重新预览首次同步。")
    local_bank = bank_access.read_bank()
    if make_json_etag(local_bank) != _PENDING_BOOTSTRAP.get("local_etag"):
        raise SyncError("本机题库已变化，请重新预览首次同步。")
    try:
        merged = merge_question_bank_v4(
            snapshot["bank"], local_bank, mode="merge",
            strategy="preserve_local", review_resolutions=dict(review_resolutions))
    except UnresolvedReviewConflicts as error:
        raise SyncError(f"还有 {len(error.missing)} 道审查冲突未处理；首次同步必须全部明确选择，暂不处理将暂停本次连接。")
    result = session.call("POST", "/api/v1/sync/bootstrap", {
        "bank": merged["bank"], "base_etag": snapshot.get("etag", ""),
        "client_id": settings["clientId"], "device_name": settings.get("deviceName", ""),
    })
    final_bank = validate_question_bank_v4(merged["bank"])
    bank_access.write_bank(final_bank)
    save_shadow(final_bank, int(result.get("revision", 0)))
    settings["enabled"] = True
    settings["lastRevision"] = int(result.get("revision", 0))
    save_settings(settings)
    _PENDING_BOOTSTRAP.clear()
    return {"ok": True, "revision": settings["lastRevision"]}


def disconnect_sync(clear_credential: bool) -> dict[str, Any]:
    """§131: stop sync, keep local bank; optionally clear stored secret."""
    settings = load_settings()
    settings["enabled"] = False
    save_settings(settings)
    if clear_credential:
        clear_credential()
    try:
        state = load_state()
        state["lastStatus"] = {"phase": "disabled"}
        save_state(state)
    except OSError:
        pass
    return {"ok": True, "credentialCleared": bool(clear_credential)}


def fetch_conflicts() -> list[dict[str, Any]]:
    settings = load_settings()
    session = _ephemeral_session(settings)
    payload = session.call("GET", "/api/v1/sync/conflicts")
    conflicts = payload.get("conflicts", [])
    return conflicts if isinstance(conflicts, list) else []


def resolve_conflict(conflict_id: str, choice: str) -> dict[str, Any]:
    from sync_protocol import RESOLUTION_CHOICES

    if choice not in RESOLUTION_CHOICES:
        raise SyncError("冲突处理选项无效。")
    settings = load_settings()
    session = _ephemeral_session(settings)
    try:
        return session.call("POST", "/api/v1/sync/conflicts/resolve", {
            "conflict_id": conflict_id, "choice": choice})
    except SyncError as error:
        if "已处理" in str(error) or "409" in str(error):
            raise SyncError("该冲突已被处理，请刷新后查看最新状态。")
        raise


def list_remote_backups() -> list[dict[str, Any]]:
    settings = load_settings()
    session = _ephemeral_session(settings)
    payload = session.call("GET", "/api/v1/backup/list")
    backups = payload.get("backups", [])
    return backups if isinstance(backups, list) else []


def upload_backup(bank_access: LocalBankAccess) -> dict[str, Any]:
    """Manual whole-bank backup; never touches live revision (§12, §78)."""
    settings = load_settings()
    session = _ephemeral_session(settings)
    local_bank = bank_access.read_bank()
    raw = json.dumps(local_bank, ensure_ascii=False).encode("utf-8")
    if len(raw) > 50 * 1024 * 1024:
        raise SyncError("题库过大，无法上传备份。")
    url = f"http://{settings['host']}:{settings['port']}/api/v1/backup/upload"
    body_hash = hashlib.sha256(raw).hexdigest()
    with session._lock:
        session._seq += 1
        seq = session._seq
    import hmac as hmac_module

    signature = hmac_module.new(
        session._secret,
        f"{session.session_id}|{seq}|POST|/api/v1/backup/upload|{body_hash}".encode("utf-8"),
        hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url, data=raw, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Sync-Session": session.session_id,
                 "X-Sync-Seq": str(seq),
                 "X-Sync-Signature": signature,
                 "X-Backup-Client": settings["clientId"],
                 "X-Backup-Device": settings.get("deviceName", "")})
    try:
        with urllib.request.urlopen(request, timeout=60.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read().decode("utf-8")).get("error", f"HTTP {error.code}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = f"HTTP {error.code}"
        raise SyncError(message)
    except (OSError, TimeoutError) as error:
        raise SyncOffline(f"同步服务器不可达：{error}")
    return payload


def download_backup(backup_id: str) -> tuple[str, bytes]:
    """Download only; never auto-imports (§83)."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", backup_id or ""):
        raise SyncError("备份 id 无效。")
    settings = load_settings()
    session = _ephemeral_session(settings)
    status, content, _headers = session.request(
        "GET", f"/api/v1/backup/download?id={backup_id}", timeout=60.0)
    if status != 200:
        try:
            message = json.loads(content.decode("utf-8")).get("error", f"HTTP {status}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = f"HTTP {status}"
        raise SyncError(message)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"wenyan-question-bank-backup-{stamp}.json", content


class SyncWorker:
    """Single background worker (§25): push-before-pull every ~2s (§26-27)."""

    def __init__(self, bank_access: LocalBankAccess,
                 now: Callable[[], float] | None = None) -> None:
        self._bank = bank_access
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session: SyncSession | None = None
        self._failures = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wenyan-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _sleep_interval(self) -> float:
        if self._failures <= 0:
            return SYNC_INTERVAL_SECONDS
        index = min(self._failures - 1, len(BACKOFF_STEPS) - 1)
        return min(BACKOFF_STEPS[index], MAX_BACKOFF_SECONDS)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.cycle_once()
                self._failures = 0
            except SyncOffline as error:
                self._failures += 1
                self._note_offline(str(error))
            except SyncError as error:
                self._failures += 1
                self._note_offline(str(error))
            except Exception as error:  # Worker must never kill the server.
                self._failures += 1
                log_sync_event("worker-error", error=f"{type(error).__name__}: {error}")
                self._note_offline("同步内部错误，已跳过本轮。")
            self._stop.wait(self._sleep_interval())

    # -- one cycle ------------------------------------------------------
    def ensure_session(self, settings: dict[str, Any]) -> SyncSession:
        if self._session is not None:
            return self._session
        material = load_credential()
        if material is None:
            raise SyncError("缺少同步凭据，请重新连接。")
        if (material.get("host") != settings["host"]
                or int(material.get("port", 0)) != settings["port"]
                or material.get("username") != settings["username"]):
            raise SyncError("同步配置已变更，请重新连接。")
        self._session = login_with_material(material)
        return self._session

    def drop_session(self) -> None:
        self._session = None

    def cycle_once(self) -> dict[str, Any]:
        from sync_protocol import build_sync_operations, canonical_hash

        settings = load_settings()
        if not settings["enabled"]:
            # Default-off upgrade: leave no trace for users who never sync.
            if not sync_paths()["state"].exists():
                return {"phase": "disabled"}
            self._set_status({"phase": "disabled"})
            return {"phase": "disabled"}
        self._set_status({"phase": "syncing"})
        try:
            return self._cycle_guarded(settings)
        except (SyncOffline, SyncError) as error:
            self._note_offline(str(error))
            raise

    def _cycle_guarded(self, settings: dict[str, Any]) -> dict[str, Any]:
        from sync_protocol import build_sync_operations, canonical_hash

        try:
            session = self.ensure_session(settings)
        except (SyncOffline, SyncError):
            self.drop_session()
            raise
        try:
            local_bank = self._bank.read_bank()
        except (OSError, ValueError) as error:
            raise SyncError(f"本机题库不可读：{error}")
        shadow = load_shadow()
        if shadow is None:
            raise SyncError("缺少同步基线，请重新初始化同步。")
        if local_bank.get("bankId") != shadow.get("bankId"):
            status = {"phase": "paused", "reason": "bank_switched",
                      "message": "本机题库已切换，需要重新初始化同步。"}
            self._set_status(status)
            return status
        state = load_state()
        # 1) open conflicts → blocked ids + prune stale suppressions.
        try:
            conflicts_payload = session.call("GET", "/api/v1/sync/conflicts")
        except SyncError:
            self.drop_session()
            raise
        open_conflicts = conflicts_payload.get("conflicts", [])
        open_ids = {item.get("conflict_id") for item in open_conflicts if isinstance(item, dict)}
        blocked: set[str] = set()
        for item in open_conflicts:
            if not isinstance(item, dict):
                continue
            if item.get("entity_kind") in ("question", "review"):
                blocked.add(str(item.get("entity_id", "")))
        suppressions = {key: value for key, value in state.get("suppressions", {}).items()
                        if isinstance(value, dict) and value.get("conflict_id") in open_ids}
        # 2) push local changes first (§27).
        operations = build_sync_operations(
            shadow["bank"], local_bank, settings["clientId"])
        pending = []
        for operation in operations:
            key = f"{operation['entity_kind']}:{operation['entity_id']}"
            suppressed = suppressions.get(key)
            if suppressed and suppressed.get("incoming_hash") == canonical_hash(operation["new"]):
                continue
            pending.append(operation)
        if pending:
            push_payload = session.call("POST", "/api/v1/sync/push", {
                "client_id": settings["clientId"],
                "device_name": settings.get("deviceName", ""),
                "operations": pending,
            })
            suppressions = apply_pushed_operation_results(
                suppressions, push_payload.get("results", []))
        # 3) pull remote changes (§64).
        last_revision = int(settings.get("lastRevision", 0))
        changed_local = False
        working = copy.deepcopy(local_bank)
        while True:
            changes = session.call(
                "GET", f"/api/v1/sync/changes?after={last_revision}&limit=500")
            for entry in changes.get("operations", []):
                operation = entry.get("operation", {})
                try:
                    if apply_remote_to_local(working, operation):
                        changed_local = True
                except ValueError as error:
                    log_sync_event("pull-skip", error=str(error))
            for resolution in changes.get("resolutions", []):
                try:
                    from sync_protocol import set_entity_value
                    raw_value = resolution.get("value_json")
                    value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                    set_entity_value(working, resolution["entity_kind"],
                                     resolution["entity_id"], value)
                    changed_local = True
                except (ValueError, KeyError, TypeError) as error:
                    log_sync_event("resolution-skip", error=str(error))
                key = f"{resolution.get('entity_kind')}:{resolution.get('entity_id')}"
                suppressions.pop(key, None)
            last_revision = int(changes.get("revision", last_revision))
            if len(changes.get("operations", [])) < 500:
                break
        if changed_local:
            from server_validators import make_json_etag

            fresh = self._bank.read_bank()
            if make_json_etag(fresh) != make_json_etag(local_bank):
                # Local edits landed mid-cycle: skip the write, retry next round.
                log_sync_event("pull-deferred", reason="local-changed-mid-cycle")
            else:
                validated = self._validated_bank(working)
                self._bank.write_bank(validated)
                working = validated
        settings["lastRevision"] = last_revision
        save_settings(settings)
        save_shadow(working if changed_local else local_bank, last_revision)
        open_count = len(open_conflicts)
        status = {
            "phase": "conflict" if open_count else "connected",
            "serverRevision": last_revision,
            "pendingLocal": 0,
            "openConflicts": open_count,
            "lastSyncAt": datetime.now().isoformat(timespec="seconds"),
        }
        state.update({
            "blockedQuestionIds": sorted(blocked),
            "suppressions": suppressions,
            "lastStatus": status,
            "pendingLocal": 0,
        })
        save_state(state)
        return status

    def _validated_bank(self, bank: dict[str, Any]) -> dict[str, Any]:
        from server_validators import validate_question_bank_v4

        return validate_question_bank_v4(bank)

    def _set_status(self, patch: dict[str, Any]) -> None:
        try:
            state = load_state()
            merged = dict(state.get("lastStatus", {}))
            merged.update(patch)
            state["lastStatus"] = merged
            save_state(state)
        except OSError:
            pass

    def _note_offline(self, message: str) -> None:
        try:
            settings = load_settings()
            pending = 0
            if settings["enabled"]:
                try:
                    local_bank = self._bank.read_bank()
                    shadow = load_shadow()
                    if shadow is not None and local_bank.get("bankId") == shadow.get("bankId"):
                        from sync_protocol import build_sync_operations
                        pending = len(build_sync_operations(
                            shadow["bank"], local_bank, settings["clientId"]))
                except (OSError, ValueError):
                    pending = 0
            state = load_state()
            merged = dict(state.get("lastStatus", {}))
            merged.update({"phase": "offline" if settings["enabled"] else "disabled",
                           "lastError": message})
            state["lastStatus"] = merged
            state["pendingLocal"] = pending
            save_state(state)
        except OSError:
            pass
        self.drop_session()
