"""Small file-backed storage primitives used by the local server."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from server_config import BACKUP_MAX_COUNT, BACKUP_RETENTION_DAYS


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def backup_and_write(path: Path, payload: Any, backup_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / f"{path.stem}-{timestamp}.json"
        shutil.copy2(path, backup)

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        prune_backups(path, backup_dir)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def prune_backups(path: Path, backup_dir: Path) -> None:
    """Keep automatic backups recoverable without allowing unbounded growth."""
    try:
        candidates = sorted(
            backup_dir.glob(f"{path.stem}-*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        cutoff = time.time() - BACKUP_RETENTION_DAYS * 24 * 60 * 60
        for index, candidate in enumerate(candidates):
            if index < BACKUP_MAX_COUNT and candidate.stat().st_mtime >= cutoff:
                continue
            try:
                candidate.unlink()
            except OSError:
                pass
    except OSError:
        # A backup cleanup failure must not make the newly written data look
        # like it failed to save.
        return

