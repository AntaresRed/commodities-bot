"""End-to-end: MCX file -> import -> continuous -> quality report, and the start-up runner."""

import numpy as np
import pandas as pd
import pytest

from bot import env as envmod
from bot import paths
from bot.config import TROY_OUNCE_GRAMS


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for name, sub in [("DATA_DIR", "data"), ("RAW_DIR", "data/raw"), ("PROCESSED_DIR", "data/processed"),
                      ("SESSION_DIR", "data/session"), ("JOURNAL_DIR", "data/journal"), ("LOG_DIR", "logs"),
                      ("REPORTS_DIR", "reports"), ("MCX_RAW_DIR", "data/raw/mcx"),
                      ("BENCHMARK_RAW_DIR", "data/raw/benchmarks")]:
        monkeypatch.setattr(paths, name, tmp_path / sub)
    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / ".env")
    for k in envmod.ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    paths.ensure_dirs()
    return tmp_path


def _write_goldten_and_benchmarks(sandbox):
    days = pd.bdate_range("2026-01-01", "2026-06-30")
    rng = np.random.default_rng(1)
    usd = 2600 * np.cumprod(1 + rng.normal(0, 0.008, len(days)))
    fx = np.full(len(days), 85.0)
    inr10g = usd * fx * 10 / TROY_OUNCE_GRAMS * 1.09       # + duty premium
    rows = []
    expiries = [pd.Timestamp(f"2026-{m:02d}-05") for m in range(2, 9)]
    for i, d in enumerate(days):
        live = [e for e in expiries if e >= d][:2]
        for k, e in enumerate(live):
            c = round(inr10g[i] * (1 + 0.004 * k))
            rows.append([d.strftime("%d %b %Y"), "FUTCOM", "GOLDTEN", e.strftime("%d %b %Y"), "", 0,
                         c, c + 50, c - 50, c, c, 100, 1, 1, 50])
    hdr = ["Date", "Instrument Name", "Symbol", "Expiry Date", "Option Type", "Strike Price", "Open", "High",
           "Low", "Close", "Previous Close", "Volume(Lots)", "Volume(In 000's)", "Value(In Lakhs)",
           "Open Interest(Lots)"]
    pd.DataFrame(rows, columns=hdr).to_csv(paths.MCX_RAW_DIR / "goldten_2026.csv", index=False)
    bench = pd.concat([
        pd.DataFrame({"date": days, "ticker": "GC=F", "open": usd, "high": usd, "low": usd, "close": usd, "volume": 1}),
        pd.DataFrame({"date": days, "ticker": "INR=X", "open": fx, "high": fx, "low": fx, "close": fx, "volume": 0}),
    ])
    bench.to_parquet(paths.PROCESSED_DIR / "benchmarks_daily.parquet", index=False)


def test_pipeline_end_to_end(sandbox, instruments):
    from bot.data.pipeline import update_all

    _write_goldten_and_benchmarks(sandbox)
    lines = update_all(instruments, download=False)
    text = "\n".join(lines)
    assert "MCX GOLDTEN" in text and "Continuous GOLDTEN" in text
    cont = pd.read_parquet(paths.PROCESSED_DIR / "continuous" / "GOLDTEN.parquet")
    assert cont["date"].is_monotonic_increasing and cont["date"].is_unique
    assert cont["is_roll_day"].sum() >= 4
    assert {"close", "adj_close", "contract_expiry"} <= set(cont.columns)
    report = pd.read_csv(paths.REPORTS_DIR / "data_quality.csv")
    # clean synthetic data: no errors, and MCX tracks the benchmark (no mismatch warning)
    assert not (report["severity"] == "error").any(), report.to_string()
    assert "benchmark_mismatch" not in set(report["check"])


def test_runner_without_env(sandbox, capsys):
    from bot.live import runner

    assert runner.main(["--no-download", "--no-listen"]) == 0
    out = capsys.readouterr().out
    assert "SIGNALS ONLY" in out
    assert "KITE_API_KEY: MISSING" in out
    assert "Skipped: add KITE_API_KEY" in out and "Skipped: add TELEGRAM_BOT_TOKEN" in out


def test_runner_reports_bad_settings(sandbox, monkeypatch, capsys):
    from bot.config import ConfigError
    from bot.live import runner

    def boom(*a, **k):
        raise ConfigError("settings.yaml has 1 problem(s):\n  - risk_per_trade_pct: too big")
    monkeypatch.setattr(runner, "load_settings", boom)
    assert runner.main(["--no-download", "--no-listen"]) == 2
    assert "risk_per_trade_pct" in capsys.readouterr().out


def test_runner_telegram_path_with_mock(sandbox, monkeypatch, capsys):
    from bot.live import runner
    from bot.notify import telegram

    (sandbox / ".env").write_text("TELEGRAM_BOT_TOKEN=111:fake\nTELEGRAM_CHAT_ID=42\n", encoding="utf-8")
    mock = telegram.MockTransport()
    monkeypatch.setattr(telegram, "HttpTransport", lambda token: mock)
    assert runner.main(["--no-download", "--no-kite", "--no-listen"]) == 0
    assert "Sent 'bot started'" in capsys.readouterr().out
    assert mock.sent()[0]["chat_id"] == 42
