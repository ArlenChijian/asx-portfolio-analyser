"""Defines the universe of instruments tracked by the pipeline.

The universe = current S&P/ASX 300 (scraped from Wikipedia)
            + a curated, expanded list of ~50 major ASX-listed ETFs.

We re-scrape the ASX 300 list on each pipeline run so the project stays
current as constituents change quarterly. The ASX 300 covers ~95% of
the Australian equity market by capitalisation, giving users meaningful
small/mid-cap exposure on top of the large-caps in the ASX 200.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import StringIO

import pandas as pd
import requests

log = logging.getLogger(__name__)

# Wikipedia maintains a current S&P/ASX 300 constituents table.
_ASX_WIKI_URL = "https://en.wikipedia.org/wiki/S%26P/ASX_300"
# Fallback to ASX 200 if 300 page is unavailable.
_ASX_FALLBACK_URL = "https://en.wikipedia.org/wiki/S%26P/ASX_200"
_USER_AGENT = (
    "ASX-Portfolio-Analyser/0.3 "
    "(educational portfolio project; contact arlenchijian03@gmail.com)"
)


@dataclass(frozen=True)
class CuratedETF:
    ticker: str
    name: str
    asset_class: str
    notes: str = ""


CURATED_ETFS: tuple[CuratedETF, ...] = (
    # --- Broad Australian equity --------------------------------------
    CuratedETF("VAS",  "Vanguard Australian Shares Index",        "AU equity",     "Tracks ASX 300, lowest cost broad AU"),
    CuratedETF("A200", "Betashares Australia 200",                "AU equity",     "Tracks ASX 200, lowest fee"),
    CuratedETF("IOZ",  "iShares Core S&P/ASX 200",                "AU equity",     "Tracks ASX 200"),
    CuratedETF("STW",  "SPDR S&P/ASX 200",                        "AU equity",     "Original ASX 200 tracker"),
    CuratedETF("VHY",  "Vanguard Australian Shares High Yield",   "AU equity",     "Income-tilted Australian equity"),
    CuratedETF("IHD",  "iShares S&P/ASX Dividend Opportunities",  "AU equity",     "High-dividend AU stocks"),
    CuratedETF("MVW",  "VanEck Equal Weight",                     "AU equity",     "Equal-weight ASX large-caps"),
    CuratedETF("SFY",  "SPDR S&P/ASX 50",                         "AU equity",     "Largest 50 stocks only"),
    CuratedETF("SMLL", "Betashares Australian Small Companies",   "AU equity",     "ASX small-cap exposure"),

    # --- AU sector ETFs -----------------------------------------------
    CuratedETF("OZF",  "SPDR S&P/ASX 200 Financials",             "AU equity",     "AU financials sector"),
    CuratedETF("OZR",  "SPDR S&P/ASX 200 Resources",              "AU equity",     "AU mining/resources"),
    CuratedETF("QFN",  "Betashares S&P/ASX 200 Financials",       "AU equity",     "AU financials"),
    CuratedETF("MVB",  "VanEck Australian Banks",                 "AU equity",     "Big-four banks weighted"),

    # --- Broad international equity -----------------------------------
    CuratedETF("VGS",  "Vanguard MSCI Intl ex-Aus",               "Global equity", "Developed-market ex-AU"),
    CuratedETF("IWLD", "iShares Core MSCI World All Cap",         "Global equity", "Broadest global equity"),
    CuratedETF("VEU",  "Vanguard All-World ex-US",                "Global equity", "Global ex-US"),
    CuratedETF("IOO",  "iShares Global 100",                      "Global equity", "Top 100 global mega-caps"),
    CuratedETF("VGAD", "Vanguard MSCI Intl ex-Aus Hedged",        "Global equity", "AUD-hedged version of VGS"),
    CuratedETF("HGBL", "Betashares Global Quality Hedged",        "Global equity", "AUD-hedged quality factor"),

    # --- US equity ----------------------------------------------------
    CuratedETF("IVV",  "iShares S&P 500",                         "US equity",     "Largest US equity ETF on ASX"),
    CuratedETF("IHVV", "iShares S&P 500 AUD Hedged",              "US equity",     "AUD-hedged S&P 500"),
    CuratedETF("VTS",  "Vanguard US Total Market",                "US equity",     "Whole US market incl. small-caps"),
    CuratedETF("NDQ",  "Betashares Nasdaq 100",                   "US equity",     "Tech-heavy Nasdaq 100"),
    CuratedETF("HNDQ", "Betashares Nasdaq 100 Hedged",            "US equity",     "AUD-hedged Nasdaq 100"),
    CuratedETF("QUS",  "Betashares S&P 500 Equal Weight",         "US equity",     "Equal-weight US 500"),

    # --- Emerging markets ---------------------------------------------
    CuratedETF("VGE",  "Vanguard FTSE Emerging Markets",          "EM equity",     "Broad EM"),
    CuratedETF("IEM",  "iShares MSCI Emerging Markets",           "EM equity",     "Broad EM"),
    CuratedETF("IZZ",  "iShares China Large-Cap",                 "EM equity",     "Chinese mega-caps"),
    CuratedETF("IJP",  "iShares MSCI Japan",                      "EM equity",     "Japanese equities"),
    CuratedETF("IEU",  "iShares Europe",                          "EM equity",     "European equities"),

    # --- Thematic / sector --------------------------------------------
    CuratedETF("HACK", "Betashares Global Cybersecurity",         "Thematic",      "Cybersecurity"),
    CuratedETF("ROBO", "Global Robotics & AI",                    "Thematic",      "Robotics + AI"),
    CuratedETF("ETHI", "Betashares Global Sustainability Leaders","Thematic",      "ESG global equity"),
    CuratedETF("FAIR", "Betashares Australian Sustainability",    "Thematic",      "ESG Australian equity"),
    CuratedETF("CRYP", "Betashares Crypto Innovators",            "Thematic",      "Crypto-exposed companies (high vol)"),
    CuratedETF("DRUG", "Betashares Global Healthcare ex-AU",      "Thematic",      "Global healthcare"),
    CuratedETF("FOOD", "Betashares Global Agriculture",           "Thematic",      "Agriculture/food"),
    CuratedETF("IXJ",  "iShares Global Healthcare",               "Thematic",      "Global healthcare"),
    CuratedETF("INCM", "Betashares Global Income Leaders",        "Thematic",      "Global high-yield equity"),

    # --- Fixed income -------------------------------------------------
    CuratedETF("VAF",  "Vanguard Australian Fixed Interest",      "AU bonds",      "Investment-grade AU bonds"),
    CuratedETF("VGB",  "Vanguard Australian Government Bond",     "AU bonds",      "Government bonds only"),
    CuratedETF("BOND", "PIMCO Australian Bond",                   "AU bonds",      "Active fixed income"),
    CuratedETF("IAF",  "iShares Core Composite Bond",             "AU bonds",      "Broad AU fixed income"),
    CuratedETF("SUBD", "VanEck Australian Subordinated Debt",     "AU bonds",      "Bank subordinated debt"),
    CuratedETF("HBRD", "Betashares Active Australian Hybrids",    "AU bonds",      "AU bank hybrids"),
    CuratedETF("VIF",  "Vanguard International Fixed Interest",   "Global bonds",  "Hedged to AUD"),
    CuratedETF("IHEB", "iShares J.P.Morgan USD EM Bond",          "Global bonds",  "EM USD-denominated bonds"),

    # --- Cash ---------------------------------------------------------
    CuratedETF("AAA",  "Betashares Australian High Interest Cash","Cash",          "Bank deposits, daily liquidity"),
    CuratedETF("MMKT", "Betashares Australian Cash Plus",         "Cash",          "Cash plus short-dated bonds"),

    # --- Property / Infrastructure ------------------------------------
    CuratedETF("VAP",  "Vanguard Australian Property",            "AU property",   "Australian REITs"),
    CuratedETF("MVA",  "VanEck Australian Property",              "AU property",   "AU REITs, equal-weighted"),
    CuratedETF("DJRE", "SPDR Global REIT",                        "Global property","Hedged global REITs"),
    CuratedETF("REIT", "VanEck Global REIT",                      "Global property","Global REITs"),
    CuratedETF("GLIN", "VanEck Global Infrastructure",            "Global property","Global infrastructure"),

    # --- Commodities --------------------------------------------------
    CuratedETF("GOLD", "Global X Physical Gold",                  "Commodities",   "Backed by physical bullion"),
    CuratedETF("QAU",  "Betashares Gold Bullion AUD-Hedged",      "Commodities",   "AUD-hedged gold"),
    CuratedETF("ETPMAG","Global X Physical Silver",               "Commodities",   "Backed by physical silver"),
    CuratedETF("OOO",  "Betashares Crude Oil ETF",                "Commodities",   "Crude oil futures"),
)


def _scrape_constituents(url: str) -> pd.DataFrame:
    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))

    constituents = None
    for table in tables:
        cols_lower = [str(c).lower() for c in table.columns]
        if any("code" in c or "ticker" in c or "symbol" in c for c in cols_lower):
            constituents = table
            break
    if constituents is None:
        raise RuntimeError(f"Could not find constituents table at {url}")

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
    return constituents.rename(columns=rename_map)


def fetch_asx_tickers() -> pd.DataFrame:
    """Scrape the current ASX 300 (preferred) or ASX 200 (fallback)."""
    log.info("Fetching ASX 300 constituents from Wikipedia.")
    try:
        constituents = _scrape_constituents(_ASX_WIKI_URL)
    except Exception as exc:
        log.warning("ASX 300 fetch failed (%s); falling back to ASX 200.", exc)
        constituents = _scrape_constituents(_ASX_FALLBACK_URL)

    keep = [c for c in ("ticker", "name", "sector") if c in constituents.columns]
    df = constituents[keep].copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper() + ".AX"
    df["type"] = "stock"
    if "sector" not in df.columns:
        df["sector"] = pd.NA

    log.info("Loaded %d ASX constituents.", len(df))
    return df


def etf_dataframe() -> pd.DataFrame:
    """Return the curated ETF list as a DataFrame matching fetch_asx_tickers."""
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
    """Combine ASX stocks and curated ETFs into one DataFrame."""
    stocks = fetch_asx_tickers()
    etfs = etf_dataframe()
    universe = pd.concat([stocks, etfs], ignore_index=True)
    universe = universe.drop_duplicates(subset="ticker", keep="last").reset_index(drop=True)
    log.info("Universe size: %d instruments (%d stocks, %d ETFs).",
             len(universe),
             (universe["type"] == "stock").sum(),
             (universe["type"] == "etf").sum())
    return universe


# Backward-compat alias for any code that still imports the old name.
fetch_asx200_tickers = fetch_asx_tickers
