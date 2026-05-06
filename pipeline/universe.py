"""Defines the universe of instruments tracked by the pipeline.

The universe = current S&P/ASX 200 (scraped from Wikipedia)
            + a curated list of major ASX-listed ETFs.

We re-scrape the ASX 200 list on each pipeline run so the project stays
current as constituents change quarterly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import StringIO

import pandas as pd
import requests

log = logging.getLogger(__name__)

_ASX200_WIKI_URL = "https://en.wikipedia.org/wiki/S%26P/ASX_200"
_USER_AGENT = (
    "ASX-Portfolio-Analyser/0.1 "
    "(educational portfolio project; contact arlenchijian03@gmail.com)"
)


@dataclass(frozen=True)
class CuratedETF:
    ticker: str
    name: str
    asset_class: str
    notes: str = ""


CURATED_ETFS: tuple[CuratedETF, ...] = (
    CuratedETF("VAS",  "Vanguard Australian Shares Index",        "AU equity",     "Tracks ASX 300"),
    CuratedETF("A200", "Betashares Australia 200",                "AU equity",     "Tracks ASX 200, lowest fee"),
    CuratedETF("IOZ",  "iShares Core S&P/ASX 200",                "AU equity",     "Tracks ASX 200"),
    CuratedETF("STW",  "SPDR S&P/ASX 200",                        "AU equity",     "Original ASX 200 tracker"),
    CuratedETF("VHY",  "Vanguard Australian Shares High Yield",   "AU equity",     "Income-tilted Australian equity"),
    CuratedETF("VGS",  "Vanguard MSCI Intl ex-Aus",               "Global equity", "Developed-market ex-AU"),
    CuratedETF("IWLD", "iShares Core MSCI World All Cap",         "Global equity", ""),
    CuratedETF("VEU",  "Vanguard All-World ex-US",                "Global equity", ""),
    CuratedETF("IVV",  "iShares S&P 500",                         "US equity",     "Largest US equity ETF on ASX"),
    CuratedETF("VTS",  "Vanguard US Total Market",                "US equity",     ""),
    CuratedETF("NDQ",  "Betashares Nasdaq 100",                   "US equity",     "Tech-tilted"),
    CuratedETF("VGE",  "Vanguard FTSE Emerging Markets",          "EM equity",     ""),
    CuratedETF("IEM",  "iShares MSCI Emerging Markets",           "EM equity",     ""),
    CuratedETF("HACK", "Betashares Global Cybersecurity",         "Thematic",      "Cybersecurity"),
    CuratedETF("ROBO", "Global Robotics & AI",                    "Thematic",      "Robotics + AI"),
    CuratedETF("ETHI", "Betashares Global Sustainability Leaders","Thematic",      "ESG global equity"),
    CuratedETF("FAIR", "Betashares Australian Sustainability",    "Thematic",      "ESG Australian equity"),
    CuratedETF("VAF",  "Vanguard Australian Fixed Interest",      "AU bonds",      "Investment-grade AU bonds"),
    CuratedETF("VGB",  "Vanguard Australian Government Bond",     "AU bonds",      "Government bonds only"),
    CuratedETF("BOND", "PIMCO Australian Bond",                   "AU bonds",      "Active fixed income"),
    CuratedETF("VIF",  "Vanguard International Fixed Interest",   "Global bonds",  "Hedged to AUD"),
    CuratedETF("AAA",  "Betashares Australian High Interest Cash","Cash",          "Bank deposits, daily liquidity"),
    CuratedETF("VAP",  "Vanguard Australian Property",            "AU property",   "Australian REITs"),
    CuratedETF("DJRE", "SPDR Global REIT",                        "Global property","Hedged global REITs"),
    CuratedETF("GOLD", "Global X Physical Gold",                  "Commodities",   "Backed by physical bullion"),
    CuratedETF("QAU",  "Betashares Gold Bullion AUD-Hedged",      "Commodities",   "AUD-hedged gold"),
)


def fetch_asx200_tickers() -> pd.DataFrame:
    """Scrape the current S&P/ASX 200 constituents from Wikipedia."""
    log.info("Fetching ASX 200 constituents from Wikipedia.")
    resp = requests.get(_ASX200_WIKI_URL, headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))

    constituents = None
    for table in tables:
        cols_lower = [str(c).lower() for c in table.columns]
        if any("code" in c for c in cols_lower):
            constituents = table
            break
    if constituents is None:
        raise RuntimeError("Could not find constituents table on Wikipedia page.")

    rename_map = {}
    for col in constituents.columns:
        c = str(col).strip().lower()
        if c in ("code", "ticker", "asx code", "symbol"):
            rename_map[col] = "ticker"
        elif c in ("company", "company name", "name"):
            rename_map[col] = "name"
        elif "gics" in c and "sector" in c and "sub" not in c:
            rename_map[col] = "sector"
        elif c == "sector":
            rename_map[col] = "sector"
    constituents = constituents.rename(columns=rename_map)

    keep = [c for c in ("ticker", "name", "sector") if c in constituents.columns]
    df = constituents[keep].copy()

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper() + ".AX"
    df["type"] = "stock"
    if "sector" not in df.columns:
        df["sector"] = pd.NA

    log.info("Loaded %d ASX 200 constituents.", len(df))
    return df


def etf_dataframe() -> pd.DataFrame:
    """Return the curated ETF list as a DataFrame matching fetch_asx200_tickers."""
    rows = [
        {
            "ticker": f"{e.ticker}.AX",
            "name": e.name,
            "sector": e.asset_class,
            "type": "etf",
        }
        for e in CURATED_ETFS
    ]
    return pd.DataFrame(rows)


def build_universe() -> pd.DataFrame:
    """Combine ASX 200 stocks and curated ETFs into one DataFrame.

    Columns: ticker, name, sector, type.
    Deduped on ticker; ETFs win if a ticker collides.
    """
    asx200 = fetch_asx200_tickers()
    etfs = etf_dataframe()
    universe = pd.concat([asx200, etfs], ignore_index=True)
    universe = universe.drop_duplicates(subset="ticker", keep="last").reset_index(drop=True)
    log.info("Universe size: %d instruments (%d stocks, %d ETFs).",
             len(universe),
             (universe["type"] == "stock").sum(),
             (universe["type"] == "etf").sum())
    return universe
