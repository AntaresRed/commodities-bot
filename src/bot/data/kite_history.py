"""Kite history downloader (Phase 1b). READ-ONLY: market data only.

What it downloads (into data/raw/kite/, git-ignored):
  daily_continuous/<SYMBOL>.parquet  Kite's "continuous" daily candles: expired
      futures spliced front-month to front-month, going back years. Used by the
      S1/S3 backtest (python -m bot.backtest.proxy --source kite).
  minute/<TRADINGSYMBOL>.parquet     1-minute candles for contracts still
      trading (Kite has no minute data for expired contracts, PLAN §6).
  instruments_mcx.parquet            today's contract master (lot/tick sizes, expiries)

It also compares Kite's lot and tick sizes with config/instruments.yaml and
reports any difference (the file is never changed automatically).

Caveat: Kite's continuous series is NOT back-adjusted, so each roll to the next
contract shows as a price jump. For bullion the jumps are small (carry, ~0.5-1%);
for crude and natural gas they can be larger.

Run (after logging in with start_bot.bat or tools\\kite_login.bat):
  python -m bot.data.kite_history          (or tools\\kite_download.bat)

Still stubs: MinuteBarRecorder (live feed, Phase 3) and activity_scan (next step).
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from bot import paths
from bot.broker.broker_base import ReadOnlyBroker
from bot.config import InstrumentsFile

log = logging.getLogger(__name__)

KITE_DIR = paths.RAW_DIR / "kite"
DAILY_START = date(2003, 1, 1)
MINUTE_LOOKBACK_DAYS = 400
# Liquid contracts whose minute bars feed the S2 activity scan and research
MINUTE_SYMBOLS = ("CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI", "GOLDM", "GOLDPETAL", "SILVERM", "SILVERMIC")


def mcx_futures(instruments: pd.DataFrame) -> pd.DataFrame:
    """Only MCX commodity futures, with a proper expiry date."""
    df = instruments.copy()
    if "instrument_type" in df:
        df = df[df["instrument_type"] == "FUT"]
    if "segment" in df:
        df = df[df["segment"].astype(str).str.upper().str.contains("MCX")]
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.normalize()
    return df


def live_futures(instruments: pd.DataFrame, name: str, today: date, n: int = 2) -> pd.DataFrame:
    """The n nearest unexpired futures for a commodity name (e.g. 'GOLDM')."""
    f = mcx_futures(instruments)
    f = f[(f["name"] == name) & (f["expiry"] >= pd.Timestamp(today))]
    return f.sort_values("expiry").head(n)


def fetch_daily_continuous(broker: ReadOnlyBroker, instruments: pd.DataFrame, symbol: str,
                           start: date, end: date) -> pd.DataFrame:
    front = live_futures(instruments, symbol, end, n=1)
    if front.empty:
        raise LookupError(f"no live {symbol} futures contract in Kite's instrument list")
    token = int(front.iloc[0]["instrument_token"])
    df = broker.historical(token, start, end, "day", continuous=True, oi=True)
    return df.assign(symbol=symbol)


def fetch_minute(broker: ReadOnlyBroker, contract: pd.Series, start: date, end: date) -> pd.DataFrame:
    df = broker.historical(int(contract["instrument_token"]), start, end, "minute", oi=True)
    return df.assign(tradingsymbol=contract["tradingsymbol"], symbol=contract["name"],
                     expiry=pd.Timestamp(contract["expiry"]))


def compare_specs(instruments: pd.DataFrame, cfg: InstrumentsFile, today: date) -> list[str]:
    """Differences between Kite's lot/tick sizes and config/instruments.yaml."""
    out = []
    for i in cfg.instruments:
        live = live_futures(instruments, i.symbol, today, n=1)
        if live.empty:
            out.append(f"{i.symbol}: not found in Kite's MCX futures list (check the symbol name)")
            continue
        row = live.iloc[0]
        tick = float(row.get("tick_size", float("nan")))
        lot = row.get("lot_size")
        if abs(tick - i.tick_size) > 1e-9:
            out.append(f"{i.symbol}: tick size Kite {tick:g} vs config {i.tick_size:g}")
        out.append(f"{i.symbol}: Kite lot_size {lot} (config lot '{i.lot}', multiplier {i.multiplier:g}); "
                   f"front contract {row['tradingsymbol']} expires {pd.Timestamp(row['expiry']).date()}")
    return out


def download_all(broker: ReadOnlyBroker, cfg: InstrumentsFile, today: date | None = None,
                 out_dir: Path | None = None, daily_start: date = DAILY_START,
                 minute_symbols=MINUTE_SYMBOLS, minute_lookback_days: int = MINUTE_LOOKBACK_DAYS) -> list[str]:
    today = today or date.today()
    out_dir = out_dir or KITE_DIR
    (out_dir / "daily_continuous").mkdir(parents=True, exist_ok=True)
    (out_dir / "minute").mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    ins = broker.instruments("MCX")
    ins_save = ins.copy()
    for c in ins_save.columns:
        if ins_save[c].dtype == object:
            ins_save[c] = ins_save[c].astype(str)
    ins_save.to_parquet(out_dir / "instruments_mcx.parquet", index=False)
    lines.append(f"Instrument list: {len(mcx_futures(ins))} MCX futures contracts")
    lines += ["Spec check: " + s for s in compare_specs(ins, cfg, today)]

    for i in cfg.instruments:
        try:
            df = fetch_daily_continuous(broker, ins, i.symbol, daily_start, today)
        except Exception as e:   # one symbol failing must not stop the rest
            lines.append(f"Daily {i.symbol}: FAILED ({type(e).__name__}: {e})")
            continue
        if df.empty:
            lines.append(f"Daily {i.symbol}: no data returned")
            continue
        df.to_parquet(out_dir / "daily_continuous" / f"{i.symbol}.parquet", index=False)
        lines.append(f"Daily {i.symbol}: {len(df)} days, {df['date'].min().date()} .. {df['date'].max().date()}")

    start = today - timedelta(days=minute_lookback_days)
    for sym in minute_symbols:
        for _, c in live_futures(ins, sym, today, n=2).iterrows():
            try:
                df = fetch_minute(broker, c, start, today)
            except Exception as e:
                lines.append(f"Minute {c['tradingsymbol']}: FAILED ({type(e).__name__}: {e})")
                continue
            if df.empty:
                lines.append(f"Minute {c['tradingsymbol']}: no data returned")
                continue
            df.to_parquet(out_dir / "minute" / f"{c['tradingsymbol']}.parquet", index=False)
            days = df["date"].dt.date.nunique()
            lines.append(f"Minute {c['tradingsymbol']}: {len(df):,} bars over {days} trading days "
                         f"({df['date'].min():%d %b %Y} .. {df['date'].max():%d %b %Y})")
    return lines


def load_daily_continuous(symbol: str, directory: Path | None = None) -> pd.DataFrame:
    p = (directory or KITE_DIR) / "daily_continuous" / f"{symbol}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"No Kite daily data for {symbol}: run tools\\kite_download.bat first")
    return pd.read_parquet(p)


class MinuteBarRecorder:
    """Saves every 1-minute bar from the live feed (Phase 3, with the WebSocket feed)."""

    def __init__(self, broker: ReadOnlyBroker, symbols: list[str]):
        raise NotImplementedError("Phase 3: needs the live WebSocket feed.")


def activity_scan(minute_bars: pd.DataFrame, slot_minutes: int = 15) -> pd.DataFrame:
    raise NotImplementedError("Next step: built once minute history has been downloaded.")


def main() -> int:
    from bot import env as envmod
    from bot.broker.kite_auth import KiteLoginError, daily_login
    from bot.broker.zerodha import ZerodhaReadOnly
    from bot.config import load_instruments
    from bot.logging_setup import setup_logging

    env = envmod.load_env()
    setup_logging(secrets=[env.get(k, "") for k in envmod.SECRET_KEYS])
    miss = envmod.missing(envmod.KITE_KEYS, env)
    if miss:
        print(f"Missing in .env: {', '.join(miss)}")
        return 2
    try:
        session = daily_login(env["KITE_API_KEY"], env["KITE_API_SECRET"])
    except KiteLoginError as e:
        print(f"Kite login failed: {e}")
        return 1
    broker = ZerodhaReadOnly(env["KITE_API_KEY"], session.access_token)
    print("Downloading MCX history from Kite (read-only). This takes a few minutes...")
    try:
        lines = download_all(broker, load_instruments())
    except Exception as e:
        print(f"Download failed: {type(e).__name__}: {e}")
        return 1
    for line in lines:
        print(line)
    report = paths.REPORTS_DIR / "kite_download.txt"
    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSummary saved to {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
