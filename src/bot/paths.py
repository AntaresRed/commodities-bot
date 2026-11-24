"""Project folder locations. Everything is relative to the project root."""

from __future__ import annotations

import os
from pathlib import Path

# src/bot/paths.py -> project root is two levels above src/
ROOT = Path(os.environ.get("MCXBOT_ROOT", Path(__file__).resolve().parents[2]))

CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SESSION_DIR = DATA_DIR / "session"      # Kite access token (git-ignored)
JOURNAL_DIR = DATA_DIR / "journal"      # signal log + Taken/Skipped taps
LOG_DIR = ROOT / "logs"
REPORTS_DIR = ROOT / "reports"
ENV_FILE = ROOT / ".env"

MCX_RAW_DIR = RAW_DIR / "mcx"           # put manually downloaded MCX files here
BENCHMARK_RAW_DIR = RAW_DIR / "benchmarks"


def ensure_dirs() -> None:
    for d in (RAW_DIR, PROCESSED_DIR, SESSION_DIR, JOURNAL_DIR, LOG_DIR,
              REPORTS_DIR, MCX_RAW_DIR, BENCHMARK_RAW_DIR):
        d.mkdir(parents=True, exist_ok=True)
