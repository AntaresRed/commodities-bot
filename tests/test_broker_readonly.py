"""v1 is signals-only: no code path may place, modify or cancel an order."""

import re

import pytest

from bot import paths
from bot.broker.broker_base import FORBIDDEN_METHODS, ReadOnlyViolation, assert_read_only
from bot.broker.zerodha import ZerodhaReadOnly
from bot.data import kite_history


class FakeKite:
    def __init__(self, api_key):
        self.token = None

    def set_access_token(self, t):
        self.token = t

    def instruments(self, exchange):
        return [{"tradingsymbol": "CRUDEOILM26DECFUT", "lot_size": 10, "tick_size": 1.0, "exchange": exchange}]

    def positions(self):
        return {"net": [{"tradingsymbol": "GOLDPETAL26DECFUT", "quantity": 1}], "day": []}

    def margins(self, segment):
        return {"available": {"live_balance": 150000.0}, "segment": segment}

    # The real SDK has these; the adapter must not expose them.
    def place_order(self, **kw):
        raise AssertionError("must never be called")


@pytest.fixture
def broker():
    return ZerodhaReadOnly("k", "t", kite_factory=FakeKite)


def test_adapter_has_no_order_methods(broker):
    assert_read_only(broker)
    public = {n for n in dir(broker) if not n.startswith("_")}
    assert not public & FORBIDDEN_METHODS
    assert not hasattr(broker, "kite")   # raw SDK client is not exposed


def test_assert_read_only_catches_order_method():
    class Bad:
        def place_order(self):
            pass
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(Bad())


def test_read_wrappers(broker):
    assert broker.instruments("MCX").iloc[0]["lot_size"] == 10
    assert broker.positions().iloc[0]["tradingsymbol"] == "GOLDPETAL26DECFUT"
    assert broker.funds()["segment"] == "commodity"
    assert "***" in repr(broker)


def test_key_dependent_parts_are_stubbed(broker):
    from datetime import date
    with pytest.raises(NotImplementedError, match="Phase 1b"):
        broker.historical(1, date(2026, 1, 1), date(2026, 2, 1))
    with pytest.raises(NotImplementedError):
        broker.stream_ticks([1], lambda ticks: None)
    with pytest.raises(NotImplementedError):
        kite_history.activity_scan(None)
    with pytest.raises(NotImplementedError):
        kite_history.MinuteBarRecorder(broker, ["CRUDEOILM"])


def test_no_order_calls_anywhere_in_source():
    """Scan every source file for Kite order API calls."""
    pattern = re.compile(r"\.(place_order|modify_order|cancel_order|exit_order|place_gtt|modify_gtt|delete_gtt|"
                         r"convert_position)\s*\(")
    offenders = []
    for f in (paths.ROOT / "src").rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, offenders
