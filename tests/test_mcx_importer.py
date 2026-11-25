import pandas as pd
import pytest

from bot.data import mcx_bhavcopy as mb

BHAVCOPY_CSV = """MCX Bhavcopy for 20 Nov 2026
,
Date,Instrument Name,Symbol,Expiry Date,Option Type,Strike Price,Open,High,Low,Close,Previous Close,Volume(Lots),Volume(In 000's),Value(In Lakhs),Open Interest(Lots)
20 Nov 2026,FUTCOM,CRUDEOILM,18 Dec 2026,,0,"5,601","5,640","5,580","5,612","5,590",12000,120,"6,734.40",8000
20 Nov 2026,FUTCOM,CRUDEOILM,19 Jan 2027,,0,0,0,0,"5,650","5,630",0,0,0,10
20 Nov 2026,OPTFUT,CRUDEOILM,18 Dec 2026,CE,5600,100,120,90,110,100,50,1,1,5
20 Nov 2026,FUTCOM,COTTONCNDY,30 Nov 2026,,0,1,1,1,1,1,1,1,1,1
20 Nov 2026,FUTCOM,GOLDPETAL,30 Nov 2026,,0,"12,500","12,560","12,480","12,530","12,490",5000,0.005,"626.50",3000
bad date,FUTCOM,GOLDPETAL,30 Nov 2026,,0,1,1,1,1,1,1,1,1,1
"""

HISTORICAL_CSV = """Date,Symbol,Expiry Date,Open,High,Low,Close,PreviousClose,Volume (Lots),Value (In Lakhs),Open Interest (Lots)
19/11/2026,CRUDEOILM,18/12/2026,5580,5600,5550,5590,5570,11000,6100,7900
20/11/2026,CRUDEOILM,18/12/2026,5601,5640,5580,5612,5590,12000,6734,8000
"""


def test_reads_bhavcopy_with_title_lines(tmp_path):
    p = tmp_path / "bhav.csv"
    p.write_text(BHAVCOPY_CSV, encoding="utf-8")
    raw = mb.read_mcx_file(p)
    df, bad = mb.clean_mcx(raw, symbols={"CRUDEOILM", "GOLDPETAL"}, source=p.name)
    assert bad == 1
    assert set(df["symbol"]) == {"CRUDEOILM", "GOLDPETAL"}        # cotton dropped, option dropped
    crude = df[df["symbol"] == "CRUDEOILM"].set_index("expiry")
    near = crude.loc[pd.Timestamp("2026-12-18")]
    assert (near["open"], near["close"], near["volume_lots"]) == (5601, 5612, 12000)
    assert near["traded"]
    far = crude.loc[pd.Timestamp("2027-01-19")]                    # untraded: O/H/L filled with settlement
    assert not far["traded"] and far["open"] == far["high"] == far["low"] == 5650
    assert df["date"].iloc[0] == pd.Timestamp("2026-11-20")


def test_reads_historical_format_and_merges(tmp_path):
    (tmp_path / "a.csv").write_text(BHAVCOPY_CSV, encoding="utf-8")
    (tmp_path / "b.csv").write_text(HISTORICAL_CSV, encoding="utf-8")
    (tmp_path / "notes.csv").write_text("hello,world\n1,2\n", encoding="utf-8")
    df, rep = mb.import_folder(tmp_path, symbols={"CRUDEOILM", "GOLDPETAL"})
    assert "notes.csv" in rep.skipped_files
    assert rep.duplicates_dropped == 1                             # 20 Nov CRUDEOILM Dec in both files
    assert len(df[(df.symbol == "CRUDEOILM") & (df.expiry == "2026-12-18")]) == 2
    cov = mb.coverage(df).set_index("symbol")
    assert cov.loc["CRUDEOILM", "contracts"] == 2


def test_reads_excel(tmp_path):
    df = pd.read_csv(pd.io.common.StringIO(HISTORICAL_CSV))
    p = tmp_path / "h.xlsx"
    df.to_excel(p, index=False)
    out, _ = mb.clean_mcx(mb.read_mcx_file(p))
    assert len(out) == 2 and out["close"].tolist() == [5590, 5612]


def test_missing_columns_rejected(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Date,Symbol,Close\n20 Nov 2026,GOLDPETAL,1\n", encoding="utf-8")
    with pytest.raises(mb.MCXFormatError, match="expiry"):
        mb.read_mcx_file(p)


def test_column_aliases():
    m = mb.map_columns(["Trade Date", "SYMBOL", "Expiry Date", "Open Price", "High", "Low", "Close Price",
                        "Open Interest (Lots)"])
    assert set(m.values()) == {"date", "symbol", "expiry", "open", "high", "low", "close", "oi_lots"}


def test_empty_folder(tmp_path):
    df, rep = mb.import_folder(tmp_path)
    assert df.empty and rep.files == []


# Column layout of mcxindia.com -> Market Data -> Bhav Copy (checked 25 Nov 2026)
SITE_BHAVCOPY_CSV = """Date,Instrument,Commodity,Expiry Date,Option Type,Strike Price,Open,High,Low,Close,PCP,Vol (Lots),Volume (000's),Value (Lakhs),OI (Lots)
24 Nov 2026,FUTCOM,GOLDTEN,30NOV2026,-,-,"76,100","76,400","75,900","76,250","76,050",1500,15.000 KGS,"11,437.50",900
24 Nov 2026,FUTCOM,GOLDTEN,26FEB2027,-,-,,,,"77,300","77,300",0,0.000 KGS,0.00,0
"""


def test_reads_site_bhavcopy_layout(tmp_path):
    p = tmp_path / "site.csv"
    p.write_text(SITE_BHAVCOPY_CSV, encoding="utf-8")
    df, bad = mb.clean_mcx(mb.read_mcx_file(p), symbols={"GOLDTEN"})
    assert bad == 0 and len(df) == 2
    near = df[df["expiry"] == pd.Timestamp("2026-11-30")].iloc[0]
    assert (near["close"], near["volume_lots"], near["oi_lots"]) == (76250, 1500, 900)
    far = df[df["expiry"] == pd.Timestamp("2027-02-26")].iloc[0]
    assert not far["traded"] and far["open"] == far["low"] == 77300
