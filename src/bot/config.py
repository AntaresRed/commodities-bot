"""Load and validate config/settings.yaml and config/instruments.yaml with pydantic.

Validation errors are turned into plain-English messages so a non-programmer
can see which line of settings.yaml to fix.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bot import paths

WindowName = Literal["W1_MCX_OPEN", "W2_LONDON", "US_SESSION", "W5_EIA", "W6_LATE"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typos in keys are errors, not silently ignored


class IntradayWindows(_Strict):
    candidates: list[WindowName]
    enabled: list[WindowName] = Field(default_factory=list)
    max_live_windows: int = Field(3, ge=1, le=3)

    @model_validator(mode="after")
    def _check(self) -> "IntradayWindows":
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("intraday_windows.candidates has duplicates")
        if len(self.candidates) > 5:
            raise ValueError("at most 5 candidate windows (PLAN §3.2)")
        extra = set(self.enabled) - set(self.candidates)
        if extra:
            raise ValueError(f"enabled windows {sorted(extra)} are not in candidates")
        if len(self.enabled) > self.max_live_windows:
            raise ValueError(f"{len(self.enabled)} windows enabled but max_live_windows is {self.max_live_windows}")
        return self


class Limits(_Strict):
    daily_loss_pct: float = Field(gt=0, le=10)
    weekly_loss_pct: float = Field(gt=0, le=20)
    monthly_loss_pct: float = Field(gt=0, le=30)
    kill_switch_drawdown_pct: float = Field(gt=0, le=50)
    s2_drawdown_halve_pct: float = Field(gt=0, le=30)
    s2_drawdown_restore_pct: float = Field(gt=0, le=30)
    max_margin_use_pct: float = Field(gt=0, le=80)
    max_same_direction_per_cluster: int = Field(ge=1, le=5)
    max_intraday_trades_per_day: int = Field(ge=1, le=10)
    max_trades_per_instrument_per_day: int = Field(ge=1, le=2)
    stale_data_seconds: int = Field(ge=5, le=600)

    @model_validator(mode="after")
    def _order(self) -> "Limits":
        if not (self.daily_loss_pct <= self.weekly_loss_pct <= self.monthly_loss_pct <= self.kill_switch_drawdown_pct):
            raise ValueError("loss limits must satisfy daily <= weekly <= monthly <= kill switch")
        if self.s2_drawdown_restore_pct >= self.s2_drawdown_halve_pct:
            raise ValueError("s2_drawdown_restore_pct must be below s2_drawdown_halve_pct")
        return self


class StrategyBudget(_Strict):
    S1_trend: float = Field(ge=0, le=100)
    S2_intraday: float = Field(ge=0, le=100)
    S3_ratio: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _sum(self) -> "StrategyBudget":
        total = self.S1_trend + self.S2_intraday + self.S3_ratio
        if abs(total - 100) > 1e-6:
            raise ValueError(f"strategy_budget_pct must add up to 100 (it adds up to {total:g})")
        return self


class Schedule(_Strict):
    morning_message_time: str
    squareoff_alert_minutes_before_broker_cutoff: int = Field(ge=1, le=60)
    broker_squareoff_minutes_before_close: int = Field(ge=0, le=90)

    @field_validator("morning_message_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("must be HH:MM in 24-hour time, e.g. \"08:30\"")
        return v


class Costs(_Strict):
    brokerage_mode: Literal["pct_capped", "flat"] = "pct_capped"
    brokerage_pct: float = Field(0.0003, ge=0, le=0.01)
    brokerage_cap_inr: float = Field(20.0, ge=0)
    brokerage_flat_inr: float = Field(20.0, ge=0)
    ctt_sell_pct: float = Field(0.0001, ge=0, le=0.01)
    exchange_txn_pct: float = Field(0.00002, ge=0, le=0.01)
    sebi_fee_per_crore_inr: float = Field(10.0, ge=0)
    stamp_duty_buy_pct: float = Field(0.00002, ge=0, le=0.01)
    gst_pct: float = Field(0.18, ge=0, le=0.5)
    slippage_ticks_per_side: float = Field(1.0, ge=0)
    slippage_ticks_by_symbol: dict[str, float] = Field(default_factory=dict)
    manual_entry_extra_ticks: float = Field(1.0, ge=0)
    event_slippage_multiplier: float = Field(2.0, ge=1)
    data_plan_inr_per_month: float = Field(500.0, ge=0)
    stress_multiplier: float = Field(1.0, ge=1, le=5)


class Settings(_Strict):
    capital_inr: float = Field(gt=0, le=100_000_000)
    risk_per_trade_pct: float = Field(gt=0, le=2.0)
    first_live_month_risk_pct: float = Field(gt=0, le=2.0)
    intraday_windows: IntradayWindows
    limits: Limits
    strategy_budget_pct: StrategyBudget
    schedule: Schedule
    costs: Costs = Field(default_factory=Costs)
    # Signals-only is a hard rule for v1 (PLAN §1, §12): no other value validates.
    mode: Literal["signals_only"]

    @model_validator(mode="after")
    def _risk(self) -> "Settings":
        if self.first_live_month_risk_pct > self.risk_per_trade_pct:
            raise ValueError("first_live_month_risk_pct should not exceed risk_per_trade_pct")
        return self

    @property
    def risk_per_trade_inr(self) -> float:
        return self.capital_inr * self.risk_per_trade_pct / 100.0


class Instrument(_Strict):
    symbol: str
    commodity: Literal["crude_oil", "natural_gas", "gold", "silver"]
    role: Literal["tradeable", "history_only"]
    lot: str
    quote_unit: str
    quote_grams: float | None = None
    multiplier: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    settlement: Literal["cash", "physical"]
    cluster: Literal["energy", "bullion"]
    strategies: list[Literal["S1", "S2", "S3"]] = Field(default_factory=list)
    benchmark: str
    history_proxy: str | None = None
    expiry_rule: str | None = None
    tender_period_days: int | None = Field(None, ge=0, le=30)
    roll_days_before_expiry: int = Field(ge=0, le=30)
    verified: bool = False

    @model_validator(mode="after")
    def _check(self) -> "Instrument":
        if self.commodity in ("gold", "silver") and self.quote_grams is None:
            raise ValueError(f"{self.symbol}: metals need quote_grams")
        if self.settlement == "physical" and self.tender_period_days is None:
            raise ValueError(f"{self.symbol}: physical-delivery contracts need tender_period_days")
        if self.role == "tradeable" and not self.strategies:
            raise ValueError(f"{self.symbol}: tradeable instruments need at least one strategy")
        return self

    @property
    def benchmark_factor(self) -> float:
        """Multiply (benchmark USD price x USD/INR) by this to get INR per MCX quote unit."""
        if self.quote_grams is None:
            return 1.0
        return self.quote_grams / TROY_OUNCE_GRAMS


TROY_OUNCE_GRAMS = 31.1034768


class Benchmark(_Strict):
    name: str
    unit: str


class InstrumentsFile(_Strict):
    instruments: list[Instrument]
    benchmarks: dict[str, Benchmark]

    @model_validator(mode="after")
    def _check(self) -> "InstrumentsFile":
        syms = [i.symbol for i in self.instruments]
        dupes = {s for s in syms if syms.count(s) > 1}
        if dupes:
            raise ValueError(f"duplicate instrument symbols: {sorted(dupes)}")
        for i in self.instruments:
            if i.benchmark not in self.benchmarks:
                raise ValueError(f"{i.symbol}: benchmark {i.benchmark} not listed under benchmarks")
            if i.history_proxy and i.history_proxy not in syms:
                raise ValueError(f"{i.symbol}: history_proxy {i.history_proxy} is not an instrument")
        if "INR=X" not in self.benchmarks:
            raise ValueError("benchmarks must include INR=X (USD/INR)")
        return self

    def by_symbol(self) -> dict[str, Instrument]:
        return {i.symbol: i for i in self.instruments}

    def tradeable(self) -> list[Instrument]:
        return [i for i in self.instruments if i.role == "tradeable"]


class ConfigError(Exception):
    """Raised with a human-readable explanation of what to fix."""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} should contain key: value settings")
    return data


def _explain(path: Path, err: ValidationError) -> ConfigError:
    lines = [f"{path.name} has {err.error_count()} problem(s):"]
    for e in err.errors():
        where = ".".join(str(p) for p in e["loc"]) or "(top level)"
        lines.append(f"  - {where}: {e['msg']}")
    return ConfigError("\n".join(lines))


def load_settings(path: Path | None = None) -> Settings:
    path = path or paths.CONFIG_DIR / "settings.yaml"
    try:
        return Settings.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise _explain(path, e) from None


def load_instruments(path: Path | None = None) -> InstrumentsFile:
    path = path or paths.CONFIG_DIR / "instruments.yaml"
    try:
        return InstrumentsFile.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise _explain(path, e) from None
