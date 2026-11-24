import numpy as np
import pandas as pd
import pytest

from bot.data.continuous import build_continuous


def _contracts():
    """Two contracts. Old expires 2026-01-15; new trades 100 points higher throughout."""
    days = pd.bdate_range("2026-01-01", "2026-01-30")
    rows = []
    for i, d in enumerate(days):
        base = 1000 + i * 10
        if d <= pd.Timestamp("2026-01-15"):
            rows.append(dict(date=d, symbol="X", expiry=pd.Timestamp("2026-01-15"),
                             open=base, high=base + 5, low=base - 5, close=base))
        rows.append(dict(date=d, symbol="X", expiry=pd.Timestamp("2026-02-16"),
                         open=base + 100, high=base + 105, low=base + 95, close=base + 100))
    return pd.DataFrame(rows), days


@pytest.mark.parametrize("roll_days", [0, 2, 5])
def test_roll_happens_n_trading_days_before_expiry(roll_days):
    df, days = _contracts()
    res = build_continuous(df, roll_days=roll_days)
    s = res.series
    expiry_pos = list(days).index(pd.Timestamp("2026-01-15"))
    first_new = days[expiry_pos - roll_days]
    assert (s.loc[s["date"] < first_new, "contract_expiry"] == pd.Timestamp("2026-01-15")).all()
    assert (s.loc[s["date"] >= first_new, "contract_expiry"] == pd.Timestamp("2026-02-16")).all()
    assert len(res.rolls) == 1 and res.rolls[0].gap == pytest.approx(100)
    assert s["is_roll_day"].sum() == 1
    assert s.loc[s["is_roll_day"], "date"].iloc[0] == first_new


def test_difference_adjustment_keeps_actual_prices():
    df, days = _contracts()
    s = build_continuous(df, roll_days=2).series
    # adjusted series is a smooth +10/day line: no jump at the roll
    assert np.allclose(s["adj_close"].diff().dropna(), 10)
    # latest prices are unadjusted
    assert s["adj_close"].iloc[-1] == s["close"].iloc[-1]
    # actual prices are kept for P&L: old-contract rows still show the old contract's real close
    old = s[s["contract_expiry"] == pd.Timestamp("2026-01-15")]
    assert (old["adj_close"] - old["close"] == 100).all()
    assert (old["close"] == 1000 + 10 * np.arange(len(old))).all()
    assert (s["adj_high"] - s["adj_low"] == s["high"] - s["low"]).all()


def test_ratio_adjustment_preserves_returns():
    df, _ = _contracts()
    s = build_continuous(df, roll_days=2, method="ratio").series
    roll = s.index[s["is_roll_day"]][0]
    r_adj = s["adj_close"].iloc[roll] / s["adj_close"].iloc[roll - 1] - 1
    # return across the roll equals the new contract's own day-on-day return
    new = df[df["expiry"] == pd.Timestamp("2026-02-16")].set_index("date")["close"]
    r_new = new[s["date"].iloc[roll]] / new[s["date"].iloc[roll - 1]] - 1
    assert r_adj == pytest.approx(r_new)
    assert s["adj_close"].iloc[-1] == s["close"].iloc[-1]


def test_three_contracts_cumulative_offsets():
    rows = []
    days = pd.bdate_range("2026-01-01", "2026-03-31")
    exps = [pd.Timestamp("2026-01-20"), pd.Timestamp("2026-02-19"), pd.Timestamp("2026-03-19"),
            pd.Timestamp("2026-04-20")]
    for d in days:
        for k, e in enumerate(exps):
            if d <= e:
                rows.append(dict(date=d, expiry=e, open=500 + 7 * k, high=500 + 7 * k, low=500 + 7 * k,
                                 close=500 + 7 * k))
    res = build_continuous(pd.DataFrame(rows), roll_days=1)
    assert len(res.rolls) == 3
    s = res.series
    assert (s["adj_close"] == s["adj_close"].iloc[-1]).all()   # flat price in every contract -> flat adjusted
    assert s["adj_offset"].iloc[0] == pytest.approx(21)


def test_missing_bar_for_held_contract_is_reported():
    df, _ = _contracts()
    drop = (df["date"] == pd.Timestamp("2026-01-06")) & (df["expiry"] == pd.Timestamp("2026-01-15"))
    res = build_continuous(df[~drop], roll_days=2)
    assert any("2026-01-06" in m for m in res.issues)


def test_rejects_multiple_symbols():
    df, _ = _contracts()
    df.loc[df.index[0], "symbol"] = "Y"
    with pytest.raises(ValueError):
        build_continuous(df)


def test_empty_input():
    assert build_continuous(pd.DataFrame()).series.empty
