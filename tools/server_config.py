"""Shared paths and immutable server configuration.

This module contains no HTTP or persistence behavior.  Keeping the runtime
layout here lets the request handler and the small domain services use the
same paths while preserving the original ``run_server`` import surface.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


APP_NAME = "wenyan-word-training"


if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    DATA_DIR = Path(sys.executable).resolve().parent / "data"
else:
    ROOT = Path(__file__).resolve().parents[1]
    DATA_DIR = ROOT / "data"


def _load_version() -> str:
    version_path = ROOT / "version.json"
    if not version_path.is_file():
        raise FileNotFoundError(f"缺少版本文件：{version_path}")
    payload = json.loads(version_path.read_text(encoding="utf-8"))
    version = str(payload.get("version", "")).strip()
    if not version or any(character not in "0123456789." for character in version):
        raise ValueError(f"版本文件无效：{version_path}")
    return version


APP_VERSION = _load_version()

QUESTIONS_PATH = DATA_DIR / "questions.json"
PUBLIC_QUESTION_BANK_PATH = ROOT / "public-data" / "questions.json"
QUESTION_REVIEWS_PATH = DATA_DIR / "question-reviews.json"
QUESTION_BANK_HISTORY_PATH = DATA_DIR / "question-bank-history.json"
LEGACY_LEADERBOARD_PATH = DATA_DIR / "leaderboard.json"
BACKUP_DIR = DATA_DIR / "backups"


def get_user_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME")
        if not base:
            base = str(Path.home() / ".local" / "share")
    return Path(base) / "WenyanQuiz"


USER_DATA_DIR = get_user_data_dir()
LEADERBOARD_PATH = USER_DATA_DIR / "leaderboard.json"
LEADERBOARD_BACKUP_DIR = USER_DATA_DIR / "backups"
ANSWER_RECORDS_PATH = USER_DATA_DIR / "answer-records.json"
ANSWER_RECORDS_BACKUP_DIR = USER_DATA_DIR / "answer-records-backups"
ADMIN_SETTINGS_PATH = USER_DATA_DIR / "admin-settings.json"
ADMIN_SETTINGS_BACKUP_DIR = USER_DATA_DIR / "backups"
PID_PATH = USER_DATA_DIR / "service.pid"

DEFAULT_ADMIN_PASSWORD = "pc123456"
# 只保存检修密码的哈希，不在网页、配置接口或普通管理员密码页面中返回。
SUPER_ADMIN_PASSWORD_HASH = "067cca8c5ce5ecd2830907acf8b4b1be805e5d62a3e700d4b2e701b732491cba"
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60

VALID_TYPES = {"context_meaning", "single_choice", "select_correct", "select_incorrect"}
VALID_REVIEW_STATUSES = {"pending", "passed", "needs_revision", "skipped"}
VALID_DUPLICATE_REVIEW_STATUSES = {"pending", "kept", "skipped"}
VALID_QUESTION_BANK_HISTORY_KINDS = {"import", "export", "revoke"}
VALID_OPTION_KEYS = {"A", "B", "C", "D"}
DEFAULT_SCORING_CONFIG = {
    "mode": "fixed",
    "baseCorrect": 1,
    "baseWrongPenalty": 1,
    "correctStreakAfter": 2,
    "correctStreakScore": 2,
    "wrongStreakAfter": 2,
    "wrongStreakPenalty": 2,
}
MIN_DURATION_SECONDS = 10
MAX_DURATION_SECONDS = 3600
MAX_STREAK_THRESHOLD = 5
ANSWER_RECORD_RETENTION_DAYS = 30
# Ordinary and PK records share one retention cap.
ANSWER_RECORD_MAX_COUNT = 100
BACKUP_MAX_COUNT = 100
BACKUP_RETENTION_DAYS = 90
