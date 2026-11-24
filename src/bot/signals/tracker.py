"""Signal journal: every signal is logged locally BEFORE it is sent, plus your
Taken/Skipped taps. Phase 3 adds model-vs-actual P&L on top of this (PLAN §3.6).

Storage: SQLite at data/journal/signals.db, plus a plain JSON-lines backup file
(data/journal/signals.jsonl) you can open in any text editor.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from bot import paths
from bot.signals.signal import Signal
from bot.timeutil import now_ist

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    created_ist TEXT NOT NULL,
    strategy TEXT, symbol TEXT, side TEXT, lots INTEGER,
    entry REAL, stop REAL, target REAL,
    payload TEXT NOT NULL,
    sent_ok INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS responses (
    signal_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('taken', 'skipped')),
    at_ist TEXT NOT NULL,
    user_id INTEGER
);
"""


class SignalJournal:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or paths.JOURNAL_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "signals.db"
        self.jsonl_path = self.dir / "signals.jsonl"
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _append_jsonl(self, record: dict) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def log_signal(self, signal: Signal, when: datetime | None = None) -> bool:
        """Record a new signal. Returns False if this ID was already logged (duplicate guard)."""
        when = when or now_ist()
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO signals (signal_id, created_ist, strategy, symbol, side, lots, entry, stop, target, payload)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (signal.signal_id, when.isoformat(), signal.strategy, signal.symbol, signal.side,
                     signal.lots, signal.entry, signal.stop, signal.target,
                     json.dumps(signal.to_dict(), ensure_ascii=False)))
        except sqlite3.IntegrityError:
            return False
        self._append_jsonl({"type": "signal", "at_ist": when.isoformat(), **signal.to_dict()})
        return True

    def mark_sent(self, signal_id: str, ok: bool) -> None:
        with self._conn() as c:
            c.execute("UPDATE signals SET sent_ok=? WHERE signal_id=?", (1 if ok else 0, signal_id))

    def exists(self, signal_id: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM signals WHERE signal_id=?", (signal_id,)).fetchone() is not None

    def record_response(self, signal_id: str, action: str, user_id: int | None = None,
                        when: datetime | None = None) -> None:
        if action not in ("taken", "skipped"):
            raise ValueError(f"unknown action {action!r}")
        when = when or now_ist()
        with self._conn() as c:
            c.execute("INSERT INTO responses (signal_id, action, at_ist, user_id) VALUES (?,?,?,?)",
                      (signal_id, action, when.isoformat(), user_id))
        self._append_jsonl({"type": "response", "signal_id": signal_id, "action": action,
                            "at_ist": when.isoformat()})

    def latest_response(self, signal_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT action FROM responses WHERE signal_id=? ORDER BY rowid DESC LIMIT 1",
                            (signal_id,)).fetchone()
        return row[0] if row else None
