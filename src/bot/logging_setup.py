"""Logging to console + a rotating file in logs/, with secret redaction."""

from __future__ import annotations

import logging
import logging.handlers
from collections.abc import Iterable
from pathlib import Path

from bot import paths

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class RedactSecretsFilter(logging.Filter):
    """Replace any known secret value that appears in a log record with ***."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        # Ignore very short values to avoid redacting ordinary words/numbers.
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        redacted = msg
        for s in self._secrets:
            redacted = redacted.replace(s, "***")
        if redacted != msg:
            record.msg, record.args = redacted, ()
        return True


def setup_logging(level: int = logging.INFO, secrets: Iterable[str] = (),
                  log_dir: Path | None = None, filename: str = "bot.log") -> logging.Logger:
    log_dir = log_dir or paths.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    redact = RedactSecretsFilter(secrets)
    fmt = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redact)
    root.addHandler(console)

    fileh = logging.handlers.RotatingFileHandler(
        log_dir / filename, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fileh.setFormatter(fmt)
    fileh.addFilter(redact)
    root.addHandler(fileh)

    # Third-party HTTP libraries can log full URLs; the Telegram API URL contains the token.
    for noisy in ("urllib3", "requests", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root
