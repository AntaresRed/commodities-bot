"""Read secrets from the git-ignored .env file.

Secret VALUES are never printed or logged. Functions here only ever report
key NAMES (e.g. "TELEGRAM_BOT_TOKEN is missing").
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from bot import paths

KITE_KEYS = ("KITE_API_KEY", "KITE_API_SECRET")
TELEGRAM_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
SECRET_KEYS = KITE_KEYS + ("TELEGRAM_BOT_TOKEN",)   # values that must be redacted from logs
ALL_KEYS = KITE_KEYS + TELEGRAM_KEYS


def load_env(path: Path | None = None) -> dict[str, str]:
    """Return non-empty values from .env, with real environment variables taking precedence."""
    path = path or paths.ENV_FILE
    values: dict[str, str] = {}
    if path.exists():
        values.update({k: v.strip() for k, v in dotenv_values(path).items() if v and v.strip()})
    for k in ALL_KEYS:
        if os.environ.get(k, "").strip():
            values[k] = os.environ[k].strip()
    return values


def missing(keys: tuple[str, ...], env: dict[str, str]) -> list[str]:
    return [k for k in keys if not env.get(k)]


def status_lines(env: dict[str, str]) -> list[str]:
    """Human-readable set/missing report, names only."""
    return [f"  {k}: {'set' if env.get(k) else 'MISSING'}" for k in ALL_KEYS]
