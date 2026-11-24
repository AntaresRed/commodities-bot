from datetime import date

import pytest

from bot.backtest.costs import CostModel
from bot.config import Costs


@pytest.fixture
def model(settings):
    return CostModel(settings.costs)


def test_brokerage_is_lower_of_pct_and_cap(model):
    assert model.brokerage(56_120) == pytest.approx(16.836)      # 0.03% < Rs 20
    assert model.brokerage(1_00_000) == 20                        # 0.03% = 30 -> capped
    assert model.brokerage(12_500) == pytest.approx(3.75)         # Gold Petal: tiny
    flat = CostModel(Costs(brokerage_mode="flat"))
    assert flat.brokerage(12_500) == 20


def test_round_trip_crudeoilm_long_hand_computed(model, inst):
    """PLAN §3.6 example: buy 1 CRUDEOILM at 5,612, sell at 5,694 (multiplier 10)."""
    c = model.round_trip(inst["CRUDEOILM"], 5612, 5694, lots=1, direction="long")
    buy_t, sell_t = 56_120, 56_940
    assert c.brokerage == pytest.approx(0.0003 * buy_t + 0.0003 * sell_t)          # 33.918
    assert c.exchange == pytest.approx(0.00002 * (buy_t + sell_t))                 # 2.2612
    assert c.sebi == pytest.approx((buy_t + sell_t) * 10 / 1e7)                    # 0.11306
    assert c.ctt == pytest.approx(0.0001 * sell_t)                                 # sell side only
    assert c.stamp == pytest.approx(0.00002 * buy_t)                               # buy side only
    assert c.gst == pytest.approx(0.18 * (c.brokerage + c.exchange + c.sebi))
    assert c.slippage == pytest.approx((1 + 1) * 1 * 10 + 1 * 1 * 10)              # entry has manual extra
    assert c.total == pytest.approx(79.6412668)


def test_short_trade_pays_ctt_on_entry(model, inst):
    c = model.round_trip(inst["CRUDEOILM"], 5694, 5612, lots=1, direction="short")
    assert c.ctt == pytest.approx(0.0001 * 56_940)
    assert c.stamp == pytest.approx(0.00002 * 56_120)


def test_net_pnl(model, inst):
    net, cost = model.net_pnl(inst["CRUDEOILM"], 5612, 5694, 1, "long")
    assert net == pytest.approx(820 - cost.total)
    net_s, _ = model.net_pnl(inst["CRUDEOILM"], 5612, 5694, 1, "short")
    assert net_s < -820


def test_natgas_has_extra_slippage_and_events_double_it(model, inst):
    ng = inst["NATGASMINI"]                       # tick 0.10 x 250 = Rs 25 per tick per lot
    assert model.slippage(1, ng, entry=False) == pytest.approx(2 * 25)
    assert model.slippage(1, ng, entry=True) == pytest.approx(3 * 25)
    assert model.slippage(1, ng, entry=True, event=True) == pytest.approx(6 * 25)


def test_stress_multiplier_doubles_everything(settings, inst):
    base = CostModel(settings.costs).round_trip(inst["GOLDTEN"], 76_000, 77_000, 1, "long")
    stressed_cfg = settings.costs.model_copy(update={"stress_multiplier": 2.0})
    stressed = CostModel(stressed_cfg).round_trip(inst["GOLDTEN"], 76_000, 77_000, 1, "long")
    assert stressed.total == pytest.approx(2 * base.total)


def test_data_plan_fixed_cost(model):
    assert model.fixed_costs(date(2026, 1, 1), date(2026, 12, 31)) == pytest.approx(6000, rel=0.01)
    assert model.fixed_costs(date(2026, 1, 2), date(2026, 1, 1)) == 0


def test_lots_scale_linearly_except_capped_brokerage(model, inst):
    one = model.round_trip(inst["GOLDPETAL"], 12_500, 12_600, 1, "long")
    four = model.round_trip(inst["GOLDPETAL"], 12_500, 12_600, 4, "long")
    assert four.ctt == pytest.approx(4 * one.ctt)
    assert four.slippage == pytest.approx(4 * one.slippage)
