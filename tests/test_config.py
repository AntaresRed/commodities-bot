import copy

import pytest
import yaml

from bot import paths
from bot.config import ConfigError, load_instruments, load_settings, TROY_OUNCE_GRAMS


def _settings_dict():
    return yaml.safe_load((paths.CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"))


def _write(tmp_path, data, name="settings.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_shipped_settings_are_valid(settings):
    assert settings.mode == "signals_only"
    assert settings.capital_inr == 200000
    assert settings.risk_per_trade_pct in (0.5, 1.0)
    assert settings.risk_per_trade_inr == pytest.approx(1000)
    assert settings.limits.kill_switch_drawdown_pct == 15
    assert len(settings.intraday_windows.candidates) == 5
    assert settings.costs.brokerage_cap_inr == 20


def test_shipped_instruments_are_valid(instruments):
    tradeable = {i.symbol for i in instruments.tradeable()}
    assert {"CRUDEOILM", "NATGASMINI", "GOLDPETAL", "GOLDGUINEA", "GOLDTEN", "SILVER100", "SILVERMIC"} == tradeable
    for i in instruments.tradeable():
        assert i.history_proxy, f"{i.symbol} should name a longer-history proxy"


def test_benchmark_factor(inst):
    assert inst["CRUDEOILM"].benchmark_factor == 1.0
    assert inst["GOLDTEN"].benchmark_factor == pytest.approx(10 / TROY_OUNCE_GRAMS)
    assert inst["SILVERMIC"].benchmark_factor == pytest.approx(1000 / TROY_OUNCE_GRAMS)


@pytest.mark.parametrize("mode", ["live", "auto", "orders", ""])
def test_any_mode_other_than_signals_only_is_rejected(tmp_path, mode):
    d = _settings_dict()
    d["mode"] = mode
    with pytest.raises(ConfigError, match="mode"):
        load_settings(_write(tmp_path, d))


@pytest.mark.parametrize("path,value,msg", [
    (("risk_per_trade_pct",), 5, "risk_per_trade_pct"),
    (("risk_per_trade_pct",), 0, "risk_per_trade_pct"),
    (("capital_inr",), -1, "capital_inr"),
    (("strategy_budget_pct", "S1_trend"), 60, "add up to 100"),
    (("limits", "daily_loss_pct"), 9, "daily <= weekly"),
    (("schedule", "morning_message_time"), "8.30", "HH:MM"),
    (("intraday_windows", "enabled"), ["W9_NOPE"], "intraday_windows"),
    (("intraday_windows", "enabled"), ["W1_MCX_OPEN", "W2_LONDON", "US_SESSION", "W5_EIA"], "max_live_windows"),
])
def test_bad_values_give_readable_errors(tmp_path, path, value, msg):
    d = _settings_dict()
    target = d
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    with pytest.raises(ConfigError) as e:
        load_settings(_write(tmp_path, d))
    assert msg in str(e.value)
    assert "settings.yaml" in str(e.value)


def test_unknown_key_is_rejected(tmp_path):
    d = _settings_dict()
    d["risk_per_trade"] = 1  # typo
    with pytest.raises(ConfigError, match="risk_per_trade"):
        load_settings(_write(tmp_path, d))


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="Missing"):
        load_settings(tmp_path / "nope.yaml")
    p = tmp_path / "settings.yaml"
    p.write_text("capital_inr: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_settings(p)


def test_instrument_rules(tmp_path):
    d = yaml.safe_load((paths.CONFIG_DIR / "instruments.yaml").read_text(encoding="utf-8"))
    bad = copy.deepcopy(d)
    bad["instruments"][2].pop("quote_grams")  # GOLDPETAL
    with pytest.raises(ConfigError, match="quote_grams"):
        load_instruments(_write(tmp_path, bad, "instruments.yaml"))
    bad = copy.deepcopy(d)
    bad["instruments"].append(copy.deepcopy(bad["instruments"][0]))
    with pytest.raises(ConfigError, match="duplicate"):
        load_instruments(_write(tmp_path, bad, "instruments.yaml"))
