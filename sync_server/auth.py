"""Challenge-response authentication for the classroom sync server.

No password ever travels the wire (§69): the client proves knowledge of a
password-derived key with HMAC over a single-use server challenge (§71).
Every authed request and response carries an HMAC signature over a
monotonic per-session sequence, which blocks simple forgery and replay
(§72).  Without TLS the bank content itself is still observable on the
network; that limitation is documented, not hidden (§70).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Any


LOGIN_FAIL_WINDOW_SECONDS = 60
LOGIN_FAIL_MAX = 5
LOGIN_BLOCK_SECONDS = 60


def _hmac_hex(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


class SyncAuth:
    def __init__(self, storage, session_ttl: int = 24 * 60 * 60, challenge_ttl: int = 60) -> None:
        self._storage = storage
        self._session_ttl = session_ttl
        self._challenge_ttl = challenge_ttl
        self._lock = threading.RLock()
        self._challenges: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._blocked_until: dict[tuple[str, str], float] = {}

    # -- rate limit (§75) -------------------------------------------------
    def _rate_key(self, ip: str, username: str) -> tuple[str, str]:
        return (ip or "", username or "")

    def check_login_allowed(self, ip: str, username: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._blocked_until.get(self._rate_key(ip, username), 0) > now:
                return False
            return True

    def record_login_failure(self, ip: str, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            key = self._rate_key(ip, username)
            recent = [moment for moment in self._failures.get(key, []) if now - moment < LOGIN_FAIL_WINDOW_SECONDS]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= LOGIN_FAIL_MAX:
                self._blocked_until[key] = now + LOGIN_BLOCK_SECONDS
                self._failures[key] = []

    # -- challenge / login (§71) -------------------------------------------
    def issue_challenge(self, username: str) -> dict[str, Any]:
        from sync_protocol import KDF_ITERATIONS

        user = self._storage.get_user(username)
        if user is None or not user.get("enabled"):
            raise ValueError("账号或密码不正确。")
        challenge_id = "ch_" + secrets.token_hex(8)
        nonce = secrets.token_hex(16)
        with self._lock:
            self._challenges[challenge_id] = {
                "username": username,
                "nonce": nonce,
                "expires": time.monotonic() + self._challenge_ttl,
            }
        return {
            "challenge_id": challenge_id,
            "nonce": nonce,
            "salt": user["salt_hex"],
            "iterations": user["iterations"] or KDF_ITERATIONS,
            "expires_in": self._challenge_ttl,
        }

    def verify_login(self, username: str, challenge_id: str, proof_hex: str) -> dict[str, Any]:
        user = self._storage.get_user(username)
        with self._lock:
            challenge = self._challenges.pop(challenge_id, None)
        if user is None or not user.get("enabled") or challenge is None:
            raise ValueError("账号或密码不正确。")
        if challenge.get("username") != username or challenge["expires"] < time.monotonic():
            raise ValueError("验证已过期，请重新登录。")
        auth_key = bytes.fromhex(user["auth_key_hex"])
        expected = _hmac_hex(
            auth_key, f"{challenge_id}|{challenge['nonce']}|{username}"
        )
        if not hmac.compare_digest(expected, proof_hex or ""):
            raise ValueError("账号或密码不正确。")
        session_id = "ss_" + secrets.token_hex(12)
        session_secret = secrets.token_hex(32)
        with self._lock:
            self._sessions[session_id] = {
                "username": username,
                "secret": session_secret,
                "seq": 0,
                "expires": time.time() + self._session_ttl,
            }
        return {"session_id": session_id, "session_secret": session_secret,
                "expires_in": self._session_ttl}

    # -- signed requests (§72) ----------------------------------------------
    def sign_request(self, session_secret: str, session_id: str, seq: int,
                     method: str, path: str, body_hash: str) -> str:
        return _hmac_hex(bytes.fromhex(session_secret),
                          f"{session_id}|{seq}|{method}|{path}|{body_hash}")

    def verify_request(self, headers: dict[str, str], method: str, path: str,
                       body: bytes) -> str:
        """Return the username, or raise."""
        session_id = headers.get("x-sync-session", "")
        try:
            seq = int(headers.get("x-sync-seq", ""))
        except (TypeError, ValueError):
            raise ValueError("缺少同步签名。")
        signature = headers.get("x-sync-signature", "")
        body_hash = hashlib.sha256(body or b"").hexdigest()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session["expires"] < time.time():
                if session_id in self._sessions:
                    del self._sessions[session_id]
                raise ValueError("同步会话已失效，请重新连接。")
            if seq <= session["seq"]:
                raise ValueError("重复或过期的同步请求。")
            expected = self.sign_request(session["secret"], session_id, seq, method, path, body_hash)
            if not hmac.compare_digest(expected, signature):
                raise ValueError("同步签名无效。")
            session["seq"] = seq
            return session["username"]

    def sign_response(self, session_id: str, seq: int, body: bytes) -> str | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return _hmac_hex(bytes.fromhex(session["secret"]),
                              f"{session_id}|{seq}|{hashlib.sha256(body or b'').hexdigest()}")

    def active_session_count(self) -> int:
        with self._lock:
            return len(self._sessions)
