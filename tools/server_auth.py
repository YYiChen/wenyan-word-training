"""Administrator password and in-memory session services."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from server_config import (
    ADMIN_SESSION_TTL_SECONDS,
    DEFAULT_ADMIN_PASSWORD,
    SUPER_ADMIN_PASSWORD_HASH,
)
from server_storage import read_json


ADMIN_SESSIONS: dict[str, float] = {}
ADMIN_SESSION_LOCK = Lock()
ADMIN_LAUNCH_TICKET_TTL_SECONDS = 30
ADMIN_LAUNCH_TICKETS: dict[str, float] = {}
ADMIN_LAUNCH_TICKET_LOCK = Lock()


def hash_admin_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def validate_admin_password(password: Any, field_name: str = "管理员密码") -> str:
    if not isinstance(password, str) or not password.strip():
        raise ValueError(f"{field_name}不能为空。")
    if not 6 <= len(password) <= 64:
        raise ValueError(f"{field_name}长度应为 6-64 个字符。")
    return password


def read_admin_password_hash(settings_path: Path) -> str:
    if not settings_path.exists():
        return hash_admin_password(DEFAULT_ADMIN_PASSWORD)
    settings = read_json(settings_path)
    password_hash = settings.get("passwordHash") if isinstance(settings, dict) else None
    if not isinstance(password_hash, str) or len(password_hash) != 64:
        raise ValueError("管理员密码配置文件无效，请删除后重新启动服务。")
    return password_hash


def authenticate_admin_password(password: Any, settings_path: Path) -> bool:
    candidate = validate_admin_password(password)
    candidate_hash = hash_admin_password(candidate)
    return (
        hmac.compare_digest(candidate_hash, read_admin_password_hash(settings_path))
        or hmac.compare_digest(candidate_hash, SUPER_ADMIN_PASSWORD_HASH)
    )


def change_admin_password(
    current_password: Any,
    new_password: Any,
    settings_path: Path,
    backup_writer: Callable[..., None],
    backup_dir: Path | None = None,
) -> bool:
    """Validate and persist a new password through the existing atomic writer."""
    current = validate_admin_password(current_password, "当前管理员密码")
    new = validate_admin_password(new_password, "新管理员密码")
    if not authenticate_admin_password(current, settings_path):
        return False
    if hmac.compare_digest(hash_admin_password(new), SUPER_ADMIN_PASSWORD_HASH):
        raise ValueError("新密码不能使用固定检修密码。")
    settings = {
        "schemaVersion": 1,
        "passwordHash": hash_admin_password(new),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    backup_writer(settings_path, settings, backup_dir)
    revoke_all_admin_sessions()
    return True


def create_admin_launch_ticket() -> str:
    """Create a short-lived, single-use handoff from the Windows launcher."""
    raw_ticket = secrets.token_urlsafe(32)
    ticket_hash = hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()
    expires_at = time.monotonic() + ADMIN_LAUNCH_TICKET_TTL_SECONDS
    with ADMIN_LAUNCH_TICKET_LOCK:
        now = time.monotonic()
        expired = [ticket for ticket, expiry in ADMIN_LAUNCH_TICKETS.items() if expiry <= now]
        for ticket in expired:
            ADMIN_LAUNCH_TICKETS.pop(ticket, None)
        ADMIN_LAUNCH_TICKETS[ticket_hash] = expires_at
    return raw_ticket


def consume_admin_launch_ticket(raw_ticket: Any) -> bool:
    """Consume a launcher ticket atomically; restart naturally clears all tickets."""
    if not isinstance(raw_ticket, str) or not raw_ticket:
        return False
    ticket_hash = hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()
    with ADMIN_LAUNCH_TICKET_LOCK:
        expires_at = ADMIN_LAUNCH_TICKETS.pop(ticket_hash, None)
        if expires_at is None:
            return False
        if expires_at <= time.monotonic():
            return False
        return True


def create_admin_session() -> str:
    token = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + ADMIN_SESSION_TTL_SECONDS
    with ADMIN_SESSION_LOCK:
        now = time.monotonic()
        expired = [session for session, expiry in ADMIN_SESSIONS.items() if expiry <= now]
        for session in expired:
            ADMIN_SESSIONS.pop(session, None)
        ADMIN_SESSIONS[token] = expires_at
    return token


def revoke_admin_session(token: str | None) -> None:
    if not token:
        return
    with ADMIN_SESSION_LOCK:
        ADMIN_SESSIONS.pop(token, None)


def revoke_all_admin_sessions() -> None:
    with ADMIN_SESSION_LOCK:
        ADMIN_SESSIONS.clear()


def is_valid_admin_session(token: str | None) -> bool:
    if not token:
        return False
    now = time.monotonic()
    with ADMIN_SESSION_LOCK:
        expires_at = ADMIN_SESSIONS.get(token)
        if expires_at is None:
            return False
        if expires_at <= now:
            ADMIN_SESSIONS.pop(token, None)
            return False
        return True
