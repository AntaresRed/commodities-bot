"""MCX daily history: importer for files you download by hand from mcxindia.com.

WHY MANUAL: on 24 Nov 2026 the MCX website (Akamai) returned "403 Access
Denied" to every scripted request, including the bhavcopy page, its data
endpoint and even robots.txt. The bot does not try to get around that block.
`check_mcx_access()` makes ONE plain request so you can see if this changes.

How to download (README "MCX history"): save the CSV/Excel files from MCX
(Market Data -> Bhavcopy, or Market Data -> Historical Data) into
data/raw/mcx/ and run `python -m bot.data.mcx_bhavcopy` (or start_bot.bat).

The importer accepts the usual MCX column names in any order, finds the header
row even if the file has title lines above it, keeps only commodity futures
(FUTCOM) of the symbols in config/instruments.yaml, and writes
data/processed/mcx_daily.parquet.
"""

from __future__ import annotations

import csv
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from bot import paths

log = logging.getLogger(__name__)

MCX_BHAVCOPY_PAGE = "https://www.mcxindia.com/market-data/bhavcopy"
MCX_HISTORICAL_PAGE = "https://www.mcxindia.com/market-data/historical-data"

CANONICAL = ["date", "symbol", "instrument", "expiry", "option_type", "strike",
             "open", "high", "low", "close", "settle", "prev_close",
             "volume_lots", "value_lakhs", "oi_lots"]
REQUIRED = ["date", "symbol", "expiry", "open", "high", "low", "close"]
OUTPUT_COLUMNS = ["date", "symbol", "expiry", "open", "high", "low", "close",
                  "volume_lots", "oi_lots", "value_lakhs", "traded", "source_file"]

# normalised header (lowercase, letters+digits only) -> canonical name
ALIASES: dict[str, str] = {}
for canon, names in {
    "date": ["date", "tradedate", "tradingdate", "businessdate", "bhavdate", "timestamp"],
    "symbol": ["symbol", "symbolname", "commodity", "commodityname"],
    "instrument": ["instrumentname", "instrument", "insttype", "instrumenttype"],
    "expiry": ["expirydate", "expiry", "contractexpiry", "expdate", "expirydt"],
    "option_type": ["optiontype", "opttype", "optiontyp"],
    "strike": ["strikeprice", "strike", "strikepr"],
    "open": ["open", "openprice", "openingprice"],
    "high": ["high", "highprice"],
    "low": ["low", "lowprice"],
    "close": ["close", "closeprice", "closingprice"],
    "settle": ["settlementprice", "settleprice", "settlement"],
    "prev_close": ["previousclose", "prevclose", "previousclosingprice", "pclose"],
    "volume_lots": ["volumelots", "volume", "volumeinlots", "tradedvolume", "tradedquantitylots",
                    "tradedqtylots", "contracts", "noofcontracts"],
    "value_lakhs": ["valueinlakhs", "valuelakhs", "valuerslakhs", "value", "turnoverlakhs"],
    "oi_lots": ["openinterestlots", "openinterest", "oi", "openinterestinlots", "oilots"],
}.items():
    for n in names:
        ALIASES[n] = canon


def _norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


class MCXFormatError(ValueError):
    pass


@dataclass
class ImportReport:
    files: list[str] = field(default_factory=list)
    rows_read: int = 0
    rows_kept: int = 0
    rows_bad_date: int = 0
    duplicates_dropped: int = 0
    skipped_files: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        s = (f"MCX import: {len(self.files)} file(s), {self.rows_read} rows read, {self.rows_kept} futures rows kept, "
             f"{self.duplicates_dropped} duplicates dropped, {self.rows_bad_date} rows with unreadable dates")
        for f, why in self.skipped_files.items():
            s += f"\n  skipped {f}: {why}"
        return s


def check_mcx_access(timeout: float = 15) -> tuple[bool, str]:
    """One plain request to the MCX bhavcopy page. No retries, no bot-check workarounds."""
    try:
        r = requests.get(MCX_BHAVCOPY_PAGE, timeout=timeout,
                         headers={"User-Agent": "mcx-signal-bot/0.1 (personal research)"})
    except requests.RequestException as e:
        return False, f"network error: {type(e).__name__}"
    if r.status_code == 200 and "Access Denied" not in r.text[:2000]:
        return True, "HTTP 200"
    return False, f"HTTP {r.status_code} (blocked for scripts)"


def map_columns(columns: Iterable[object]) -> dict[object, str]:
    out: dict[object, str] = {}
    used: set[str] = set()
    for c in columns:
        canon = ALIASES.get(_norm(c))
        if canon and canon not in used:
            out[c] = canon
            used.add(canon)
    return out


def _find_header_row(rows: list[list[object]]) -> int:
    for i, row in enumerate(rows[:30]):
        mapped = set(map_columns(row).values())
        if {"date", "symbol"} <= mapped and ("close" in mapped or "settle" in mapped):
            return i
    raise MCXFormatError("could not find a header row with Date, Symbol and Close columns")


def read_mcx_file(path: Path) -> pd.DataFrame:
    """Read one MCX CSV/Excel file into canonical column names (strings, unparsed)."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        # Title lines above the header can have fewer columns, so scan with the csv module.
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            head = [row for _, row in zip(range(30), csv.reader(f))]
        hdr = _find_header_row(head)
        df = pd.read_csv(path, skiprows=hdr, header=0, dtype=str, skip_blank_lines=True,
                         encoding="utf-8-sig", encoding_errors="replace")
    elif suffix in (".xlsx", ".xls"):
        raw = pd.read_excel(path, header=None, dtype=str, nrows=30)
        hdr = _find_header_row(raw.values.tolist())
        df = pd.read_excel(path, header=hdr, dtype=str)
    else:
        raise MCXFormatError(f"unsupported file type {suffix}")
    df = df.rename(columns=map_columns(df.columns))
    df = df[[c for c in df.columns if c in CANONICAL]]
    if "close" not in df.columns and "settle" in df.columns:
        df["close"] = df["settle"]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise MCXFormatError(f"missing required columns {missing}")
    return df


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).str.strip()
                         .replace({"": np.nan, "-": np.nan, "nan": np.nan, "None": np.nan}), errors="coerce")


def _to_date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    out = pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    rest = out.isna()
    if rest.any():
        out[rest] = pd.to_datetime(s[rest], dayfirst=True, format="mixed", errors="coerce")
    return out.dt.normalize()


def clean_mcx(df: pd.DataFrame, symbols: set[str] | None = None, source: str = "") -> tuple[pd.DataFrame, int]:
    """Canonical strings -> typed futures rows. Returns (rows, number of unreadable-date rows)."""
    df = df.copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    if "instrument" in df.columns:
        inst = df["instrument"].astype(str).str.strip().str.upper()
        df = df[inst.str.startswith("FUT") & ~inst.str.contains("IDX")]
    if "option_type" in df.columns:
        ot = df["option_type"].fillna("").astype(str).str.strip().str.upper()
        df = df[ot.isin(["", "-", "XX", "FF", "NAN", "NONE"])]
    if symbols:
        df = df[df["symbol"].isin(symbols)]
    df["date"] = _to_date(df["date"])
    df["expiry"] = _to_date(df["expiry"])
    bad = int(df["date"].isna().sum() + (df["date"].notna() & df["expiry"].isna()).sum())
    df = df.dropna(subset=["date", "expiry"])
    for c in ("open", "high", "low", "close", "settle", "prev_close", "volume_lots", "value_lakhs", "oi_lots"):
        if c in df.columns:
            df[c] = _to_num(df[c])
        elif c in ("volume_lots", "oi_lots", "value_lakhs"):
            df[c] = np.nan
    df = df.dropna(subset=["close"])
    # MCX lists untraded contracts with zero O/H/L and close = settlement price.
    no_trade = (df["open"].fillna(0) <= 0) & (df["high"].fillna(0) <= 0) & (df["low"].fillna(0) <= 0)
    if "volume_lots" in df.columns:
        no_trade &= df["volume_lots"].fillna(0) <= 0
    for c in ("open", "high", "low"):
        df.loc[no_trade, c] = df.loc[no_trade, "close"]
    df["traded"] = ~no_trade
    df["source_file"] = source
    return df[OUTPUT_COLUMNS].reset_index(drop=True), bad


def import_folder(folder: Path | None = None, symbols: set[str] | None = None,
                  existing: pd.DataFrame | None = None) -> tuple[pd.DataFrame, ImportReport]:
    folder = folder or paths.MCX_RAW_DIR
    rep = ImportReport()
    frames = [] if existing is None or existing.empty else [existing]
    for f in sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".csv", ".xlsx", ".xls")):
        try:
            raw = read_mcx_file(f)
        except (MCXFormatError, ValueError, OSError) as e:
            rep.skipped_files[f.name] = str(e)
            continue
        rep.files.append(f.name)
        rep.rows_read += len(raw)
        clean, bad = clean_mcx(raw, symbols, source=f.name)
        rep.rows_bad_date += bad
        frames.append(clean)
    if not frames:
        return pd.DataFrame(columns=OUTPUT_COLUMNS), rep
    df = pd.concat(frames, ignore_index=True)
    before = len(df)
    df = df.sort_values(["symbol", "expiry", "date"]).drop_duplicates(["date", "symbol", "expiry"], keep="last")
    rep.duplicates_dropped = before - len(df)
    rep.rows_kept = len(df)
    return df.reset_index(drop=True), rep


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Per symbol: first/last date, trading days, number of contracts."""
    if df.empty:
        return pd.DataFrame(columns=["symbol", "first", "last", "days", "contracts"])
    g = df.groupby("symbol")
    return pd.DataFrame({
        "first": g["date"].min().dt.date, "last": g["date"].max().dt.date,
        "days": g["date"].nunique(), "contracts": g["expiry"].nunique(),
    }).reset_index()


MANUAL_INSTRUCTIONS = f"""
The MCX website blocks automated downloads, so this step is done by hand (once,
then a few minutes a month to top up). Full steps are in README.md, "MCX history".

  1. Open {MCX_HISTORICAL_PAGE} (or {MCX_BHAVCOPY_PAGE}) in your browser.
  2. Choose Futures, then each symbol in turn: CRUDEOILM, CRUDEOIL, NATGASMINI,
     NATURALGAS, GOLDPETAL, GOLDGUINEA, GOLDTEN, GOLDM, GOLD, SILVERMIC, SILVERM,
     SILVER and the Silver 100 contract. Pick a date range and download CSV or Excel.
  3. Save every file into:  {paths.MCX_RAW_DIR}
  4. Run start_bot.bat again (or: python -m bot.data.mcx_bhavcopy).
"""


def main() -> int:
    from bot.config import load_instruments
    from bot.logging_setup import setup_logging

    setup_logging()
    paths.ensure_dirs()
    ok, why = check_mcx_access()
    print(f"MCX website for scripts: {'reachable' if ok else 'blocked'} ({why})")
    symbols = {i.symbol for i in load_instruments().instruments}
    out = paths.PROCESSED_DIR / "mcx_daily.parquet"
    existing = pd.read_parquet(out) if out.exists() else None
    df, rep = import_folder(symbols=symbols, existing=existing)
    print(rep.summary())
    if df.empty:
        print(MANUAL_INSTRUCTIONS)
        return 0
    df.to_parquet(out, index=False)
    print(f"Saved {len(df)} rows to {out}")
    print(coverage(df).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
