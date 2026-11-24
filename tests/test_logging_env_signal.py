import logging
from datetime import date

import pytest

from bot import env as envmod
from bot.logging_setup import RedactSecretsFilter, setup_logging
from bot.signals.signal import Signal, inr, make_signal_id


def test_secrets_are_redacted_from_logs(tmp_path):
    secret = "123456:FAKE-token-for-tests"
    setup_logging(secrets=[secret], log_dir=tmp_path, filename="t.log")
    logging.getLogger("x").info("calling https://api.telegram.org/bot%s/sendMessage", secret)
    for h in logging.getLogger().handlers:
        h.flush()
    text = (tmp_path / "t.log").read_text(encoding="utf-8")
    assert secret not in text and "bot***/sendMessage" in text
    setup_logging(log_dir=tmp_path, filename="t.log")  # reset handlers
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)
        h.close()


def test_redaction_ignores_short_values():
    f = RedactSecretsFilter(["abc", ""])
    rec = logging.LogRecord("x", logging.INFO, "", 0, "abc def", (), None)
    f.filter(rec)
    assert rec.getMessage() == "abc def"


def test_env_reports_names_only(tmp_path, monkeypatch):
    for k in envmod.ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / ".env"
    p.write_text("KITE_API_KEY=abc123secretvalue\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
    env = envmod.load_env(p)
    assert envmod.missing(envmod.KITE_KEYS, env) == ["KITE_API_SECRET"]
    assert envmod.missing(envmod.TELEGRAM_KEYS, env) == ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    lines = "\n".join(envmod.status_lines(env))
    assert "abc123secretvalue" not in lines and "KITE_API_KEY: set" in lines


def test_env_example_has_key_names_only():
    from bot import paths
    text = (paths.ROOT / ".env.example").read_text(encoding="utf-8")
    for k in envmod.ALL_KEYS:
        assert f"\n{k}=\n" in text or text.startswith(f"{k}=\n")


def test_gitignore_protects_secrets_and_data():
    from bot import paths
    lines = (paths.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines and "/data/" in lines and "/logs/" in lines


def test_signal_id_and_format():
    sid = make_signal_id("S2", date(2026, 11, 24), "CRUDEOILM", 1)
    assert sid == "S2-20261124-CRM-01"
    s = Signal(signal_id=sid, strategy="S2", side="BUY", symbol="CRUDEOILM", contract="DEC-26", lots=1,
               entry_type="buy-stop / limit", entry=5612, valid_till="19:55", stop=5571, risk_inr=410,
               equity_inr=200000, target=5694, target_label="2R", squareoff_alert="22:45",
               reason="US-session range 5,571–5,606 broken; trend filter ✔")
    text = s.format()
    assert "🟢 S2 BUY  CRUDEOILM DEC-26  · 1 lot" in text
    assert "₹5,612 (valid till 19:55)" in text
    assert "risk ₹410 = 0.20% of equity" in text      # 410 / 2,00,000 = 0.205%
    assert "Target: ₹5,694   (2R)   · Square-off alert at 22:45" in text
    assert text.endswith(f"ID: {sid}")


def test_signal_rejects_wrong_side_stop():
    with pytest.raises(ValueError):
        Signal(signal_id="x", strategy="S1", side="BUY", symbol="GOLDTEN", contract="DEC-26", lots=1,
               entry=100, stop=101, risk_inr=1, equity_inr=100, reason="")
    with pytest.raises(ValueError):
        Signal(signal_id="x", strategy="S1", side="SELL", symbol="GOLDTEN", contract="DEC-26", lots=0,
               entry=100, stop=101, risk_inr=1, equity_inr=100, reason="")


@pytest.mark.parametrize("x,d,out", [(5612, 0, "5,612"), (1234567.8, 1, "12,34,567.8"), (999, 0, "999"),
                                     (-150000, 0, "-1,50,000"), (100000, 2, "1,00,000.00")])
def test_inr_grouping(x, d, out):
    assert inr(x, d) == out
