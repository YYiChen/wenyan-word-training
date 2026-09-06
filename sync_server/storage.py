"""SQLite storage for the classroom sync server.

Single-workspace store (§6) with a process-level write lock (§18): every
accepted operation runs read-validate-write-log inside one SQLite
transaction (§19), so the bank and the operation log can never diverge.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    salt_hex TEXT NOT NULL,
    auth_key_hex TEXT NOT NULL,
    iterations INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0,
    bank_json TEXT,
    bank_etag TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    revision INTEGER,
    client_id TEXT NOT NULL,
    username TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_conflicts (
    conflict_id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    server_value_json TEXT NOT NULL,
    incoming_value_json TEXT NOT NULL,
    source_client_id TEXT NOT NULL,
    source_username TEXT NOT NULL,
    source_device TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT,
    resolution TEXT
);
CREATE TABLE IF NOT EXISTS resolutions (
    conflict_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    value_json TEXT NOT NULL,
    resolved_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_metadata (
    backup_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    bank_id TEXT NOT NULL,
    question_count INTEGER NOT NULL,
    review_summary_json TEXT NOT NULL,
    username TEXT NOT NULL,
    client_id TEXT NOT NULL,
    device_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO workspace_state (id, revision, bank_json, bank_etag, updated_at)
VALUES (1, 0, NULL, NULL, '');
"""


class StaleConflict(Exception):
    def __init__(self, conflict: dict[str, Any]) -> None:
        self.conflict = conflict
        super().__init__(f"冲突已处理：{conflict.get('conflict_id')}")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# Entity read/write lives in sync_protocol so client and server share it.
try:
    from sync_protocol import get_entity_value as _entity_value
    from sync_protocol import set_entity_value as _set_entity_value
except ImportError:  # pragma: no cover
    _entity_value = None  # type: ignore[assignment]
    _set_entity_value = None  # type: ignore[assignment]


def _review_status(review: Any) -> str:
    if isinstance(review, dict) and review.get("status") in (
        "pending", "passed", "needs_revision", "skipped",
    ):
        return review["status"]
    return "pending"


class SyncStorage:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- users ---------------------------------------------------------
    def create_user(self, username: str, salt_hex: str, auth_key_hex: str, iterations: int) -> None:
        with self._lock, self._db:
            try:
                self._db.execute(
                    "INSERT INTO users (username, salt_hex, auth_key_hex, iterations, enabled, created_at)"
                    " VALUES (?, ?, ?, ?, 1, ?)",
                    (username, salt_hex, auth_key_hex, iterations, _now()),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"账号已存在：{username}")

    def set_user_enabled(self, username: str, enabled: bool) -> None:
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE users SET enabled = ? WHERE username = ?", (1 if enabled else 0, username)
            )
            if cursor.rowcount == 0:
                raise ValueError(f"账号不存在：{username}")

    def update_user_secret(self, username: str, salt_hex: str, auth_key_hex: str, iterations: int) -> None:
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE users SET salt_hex = ?, auth_key_hex = ?, iterations = ? WHERE username = ?",
                (salt_hex, auth_key_hex, iterations, username),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"账号不存在：{username}")

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT username, enabled, created_at FROM users ORDER BY username"
            ).fetchall()
            return [dict(row) for row in rows]

    # -- workspace ------------------------------------------------------
    def get_state(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT revision, bank_json, bank_etag, updated_at FROM workspace_state WHERE id = 1").fetchone()
            state = dict(row)
            state["bank"] = json.loads(state["bank_json"]) if state["bank_json"] else None
            return state

    def _write_state(self, bank: dict[str, Any], etag: str, revision: int) -> None:
        self._db.execute(
            "UPDATE workspace_state SET revision = ?, bank_json = ?, bank_etag = ?, updated_at = ? WHERE id = 1",
            (revision, json.dumps(bank, ensure_ascii=False), etag, _now()),
        )

    # -- operations ------------------------------------------------------
    def apply_operation(
        self, operation: dict[str, Any], client_id: str, username: str, validate_bank=None,
        device: str = "",
    ) -> dict[str, Any]:
        """Three-way merge one operation inside a single transaction (§47)."""
        from sync_protocol import canonical_hash, validate_operation_shape

        operation = validate_operation_shape(operation)
        operation_id = str(operation.get("operation_id") or "")
        if not operation_id:
            raise ValueError("operation 缺少 operation_id。")
        entity_kind = operation["entity_kind"]
        entity_id = operation["entity_id"]
        base = operation["base"]
        new = operation["new"]
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT status, revision, operation_json FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing:
                stored = json.loads(existing["operation_json"])
                return {
                    "operation_id": operation_id,
                    "status": existing["status"],
                    "revision": existing["revision"],
                    "conflict": stored.get("_conflict"),
                }
            state = self.get_state()
            if state["bank"] is None:
                raise ValueError("共享题库尚未初始化。")
            bank = copy.deepcopy(state["bank"])
            current = _entity_value(bank, entity_kind, entity_id)
            current_hash = canonical_hash(current)
            base_hash = canonical_hash(base)
            new_hash = canonical_hash(new)
            if current_hash == base_hash:
                outcome = self._accept(bank, state, operation, client_id, username, validate_bank)
                return outcome
            if current_hash == new_hash:
                self._log_operation(operation_id, state["revision"], operation,
                                    client_id, username, "noop", None)
                return {"operation_id": operation_id, "status": "noop", "revision": state["revision"]}
            # Diverged: review gets merge rules, everything else conflicts.
            if entity_kind == "review":
                return self._merge_review(bank, state, operation, entity_kind, entity_id,
                                          current, base, new, client_id, username,
                                          validate_bank, device)
            conflict = self._record_conflict(
                entity_kind, entity_id, current, new, client_id, username,
                kind="content" if entity_kind == "question" else "entity",
                device=device,
            )
            self._log_operation(operation_id, None, operation, client_id, username,
                                "conflict", conflict)
            return {"operation_id": operation_id, "status": "conflict", "revision": None,
                    "conflict": conflict}

    def _accept(self, bank, state, operation, client_id, username, validate_bank) -> dict[str, Any]:
        from sync_protocol import canonical_hash

        _set_entity_value(bank, operation["entity_kind"], operation["entity_id"], operation["new"])
        if validate_bank is not None:
            bank = validate_bank(bank)
        revision = state["revision"] + 1
        etag = canonical_hash(bank)
        self._write_state(bank, etag, revision)
        self._log_operation(operation["operation_id"], revision, operation,
                            client_id, username, "accepted", None)
        return {"operation_id": operation["operation_id"], "status": "accepted", "revision": revision}

    def _merge_review(self, bank, state, operation, entity_kind, entity_id,
                      current, base, new, client_id, username, validate_bank,
                      device="") -> dict[str, Any]:
        cur_status = _review_status(current)
        new_status = _review_status(new)
        operation_id = operation["operation_id"]
        if cur_status == "pending" and new_status != "pending":
            return self._accept(bank, state, operation, client_id, username, validate_bank)
        if cur_status != "pending" and new_status == "pending":
            self._log_operation(operation_id, state["revision"], operation,
                                client_id, username, "noop", None)
            return {"operation_id": operation_id, "status": "noop", "revision": state["revision"]}
        if cur_status == new_status:
            # Same conclusion incl. metadata-only drift: keep server whole (§49).
            self._log_operation(operation_id, state["revision"], operation,
                                client_id, username, "noop", None)
            return {"operation_id": operation_id, "status": "noop", "revision": state["revision"]}
        conflict = self._record_conflict(
            entity_kind, entity_id, current, new, client_id, username, kind="review",
            device=device)
        self._log_operation(operation_id, None, operation, client_id, username, "conflict", conflict)
        return {"operation_id": operation_id, "status": "conflict", "revision": None,
                "conflict": conflict}

    def _record_conflict(self, entity_kind, entity_id, server_value, incoming_value,
                         client_id, username, kind, device="") -> dict[str, Any]:
        # A newer submission for the same entity supersedes older open ones.
        self._db.execute(
            "UPDATE sync_conflicts SET status = 'superseded', resolved_at = ?,"
            " resolved_by = 'system-superseded', resolution = 'superseded'"
            " WHERE entity_kind = ? AND entity_id = ? AND status = 'open'",
            (_now(), entity_kind, entity_id),
        )
        conflict_id = "sc_" + uuid.uuid4().hex[:16]
        record = {
            "conflict_id": conflict_id,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
            "kind": kind,
            "server_value": copy.deepcopy(server_value),
            "incoming_value": copy.deepcopy(incoming_value),
            "source_client_id": client_id,
            "source_username": username,
            "source_device": device,
            "status": "open",
            "created_at": _now(),
        }
        self._db.execute(
            "INSERT INTO sync_conflicts (conflict_id, entity_kind, entity_id, server_value_json,"
            " incoming_value_json, source_client_id, source_username, source_device,"
            " status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (conflict_id, entity_kind, entity_id,
             json.dumps({"kind": kind, "value": server_value}, ensure_ascii=False),
             json.dumps({"kind": kind, "value": incoming_value}, ensure_ascii=False),
             client_id, username, device, record["created_at"]),
        )
        return record

    def _log_operation(self, operation_id, revision, operation, client_id, username, status, conflict) -> None:
        stored = copy.deepcopy(operation)
        if conflict is not None:
            stored["_conflict"] = {
                "conflict_id": conflict["conflict_id"],
                "entity_kind": conflict["entity_kind"],
                "entity_id": conflict["entity_id"],
            }
        self._db.execute(
            "INSERT INTO operations (operation_id, revision, client_id, username, operation_type,"
            " entity_kind, entity_id, operation_json, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (operation_id, revision, client_id, username, operation.get("operation_type", ""),
             operation.get("entity_kind", ""), operation.get("entity_id", ""),
             json.dumps(stored, ensure_ascii=False), status, _now()),
        )

    # -- changes ----------------------------------------------------------
    def list_changes(self, after_revision: int, limit: int = 500) -> dict[str, Any]:
        with self._lock:
            state = self.get_state()
            rows = self._db.execute(
                "SELECT revision, operation_json, status FROM operations"
                " WHERE revision IS NOT NULL AND revision > ? ORDER BY revision ASC LIMIT ?",
                (after_revision, max(1, min(limit, 500))),
            ).fetchall()
            operations = [
                {"revision": row["revision"], "status": row["status"],
                 "operation": json.loads(row["operation_json"])}
                for row in rows
            ]
            resolutions = self._db.execute(
                "SELECT conflict_id, revision, entity_kind, entity_id, value_json, resolved_by, created_at"
                " FROM resolutions WHERE revision > ? ORDER BY revision ASC",
                (after_revision,),
            ).fetchall()
            return {
                "revision": state["revision"],
                "operations": operations,
                "resolutions": [dict(row) for row in resolutions],
            }

    # -- conflicts ----------------------------------------------------------
    def list_open_conflicts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM sync_conflicts WHERE status = 'open' ORDER BY created_at ASC"
            ).fetchall()
            return [self._conflict_dict(row) for row in rows]

    def _conflict_dict(self, row) -> dict[str, Any]:
        record = dict(row)
        record["server_value"] = json.loads(record.pop("server_value_json"))
        record["incoming_value"] = json.loads(record.pop("incoming_value_json"))
        return record

    def resolve_conflict(self, conflict_id: str, choice: str, username: str, validate_bank=None) -> dict[str, Any]:
        from sync_protocol import canonical_hash

        if choice not in ("server", "incoming"):
            raise ValueError("conflict 处理选项无效。")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM sync_conflicts WHERE conflict_id = ?", (conflict_id,)
            ).fetchone()
            if row is None:
                raise ValueError("冲突不存在。")
            conflict = self._conflict_dict(row)
            if conflict["status"] != "open":
                raise StaleConflict(conflict)
            state = self.get_state()
            bank = copy.deepcopy(state["bank"])
            server_wrapped = conflict["server_value"]
            incoming_wrapped = conflict["incoming_value"]
            if choice == "server":
                value = server_wrapped.get("value")
                current = _entity_value(bank, conflict["entity_kind"], conflict["entity_id"])
                if canonical_hash(current) != canonical_hash(value):
                    # Entity moved on after the conflict: close without clobbering.
                    self._db.execute(
                        "UPDATE sync_conflicts SET status = 'resolved-moved', resolved_at = ?,"
                        " resolved_by = ?, resolution = 'server' WHERE conflict_id = ?",
                        (_now(), username, conflict_id),
                    )
                    return {"conflict_id": conflict_id, "status": "resolved-moved",
                            "revision": state["revision"]}
            else:
                value = incoming_wrapped.get("value")
                _set_entity_value(bank, conflict["entity_kind"], conflict["entity_id"], value)
                if validate_bank is not None:
                    bank = validate_bank(bank)
            revision = state["revision"] + 1
            self._write_state(bank, canonical_hash(bank), revision)
            self._db.execute(
                "UPDATE sync_conflicts SET status = 'resolved', resolved_at = ?,"
                " resolved_by = ?, resolution = ? WHERE conflict_id = ?",
                (_now(), username, choice, conflict_id),
            )
            self._db.execute(
                "INSERT INTO resolutions (conflict_id, revision, entity_kind, entity_id,"
                " value_json, resolved_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conflict_id, revision, conflict["entity_kind"], conflict["entity_id"],
                 json.dumps(value, ensure_ascii=False), username, _now()),
            )
            return {"conflict_id": conflict_id, "status": "resolved",
                    "choice": choice, "revision": revision}

    # -- bootstrap ----------------------------------------------------------
    def bootstrap(self, bank: dict[str, Any], base_etag: str | None, validate_bank=None) -> dict[str, Any]:
        from sync_protocol import canonical_hash

        with self._lock, self._db:
            state = self.get_state()
            if state["bank"] is None:
                if validate_bank is not None:
                    bank = validate_bank(bank)
                self._write_state(bank, canonical_hash(bank), 1)
                return {"revision": 1, "bank_id": bank.get("bankId", "")}
            if base_etag != state["bank_etag"]:
                raise ValueError("服务器共享题库已变化，请重新预览首次同步。")
            if validate_bank is not None:
                bank = validate_bank(bank)
            revision = state["revision"] + 1
            self._write_state(bank, canonical_hash(bank), revision)
            return {"revision": revision, "bank_id": bank.get("bankId", "")}

    # -- backups ----------------------------------------------------------
    def add_backup(self, metadata: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO backup_metadata (backup_id, file_name, sha256, size, bank_id,"
                " question_count, review_summary_json, username, client_id, device_name, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (metadata["backup_id"], metadata["file_name"], metadata["sha256"],
                 metadata["size"], metadata["bank_id"], metadata["question_count"],
                 json.dumps(metadata["review_summary"], ensure_ascii=False),
                 metadata["username"], metadata["client_id"], metadata["device_name"],
                 metadata["created_at"]),
            )

    def list_backups(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM backup_metadata ORDER BY created_at DESC"
            ).fetchall()
            records = []
            for row in rows:
                record = dict(row)
                record["review_summary"] = json.loads(record.pop("review_summary_json"))
                records.append(record)
            return records

    def get_backup(self, backup_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM backup_metadata WHERE backup_id = ?", (backup_id,)
            ).fetchone()
            if row is None:
                return None
            record = dict(row)
            record["review_summary"] = json.loads(record.pop("review_summary_json"))
            return record
