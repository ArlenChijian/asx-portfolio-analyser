"""Defines the universe of instruments tracked by the pipeline.

The universe = current All Ordinaries / ASX 300 (scraped from Wikipedia)
            + a curated, expanded list of ~90 ASX-listed ETFs and LICs.

We try the All Ordinaries page first (top 500 by market cap), falling
back to ASX 300 then ASX 200 if the layout has shifted. This gives us
broader small/mid-cap coverage than ASX 300 alone.

The ETF/LIC list now spans 12 asset classes plus a curated set of
Listed Investment Companies (LICs), which are popular with Australian
retail investors as long-term core holdings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import StringIO

import pandas as pd
import requests

log = logging.getLogger(__name__)

_USER_AGENT = (
    "ASX-Portfolio-Analyser/0.6 "
    "(educational portfolio project; contact arlenchijian03@gmail.com)"
)

# Wikipedia maintains constituent tables for ASX 200 and ASX 50; not for
# ASX 300 / All Ordinaries (those pages only have summary metadata).
_UNIVERSE_SOURCES = [
    "https://en.wikipedia.org/wiki/S%26P/ASX_200",
]

# Hardcoded supplement of popular mid/small-cap AU stocks. Many also appear
# in ASX 200 - dedup handles overlap. Picked to expand the screening universe
# beyond the index without depending on a fragile scrape. Selected for
# liquidity, retail popularity, and Yahoo data availability.
EXTENDED_AU_STOCKS: tuple[tuple[str, str, str], ...] = (
    # (ticker, name, GICS sector)
    ("BBN", "Baby Bunting Group", "Consumer Discretionary"),
    ("BRG", "Breville Group", "Consumer Discretionary"),
    ("BRN", "Brainchip Holdings", "Information Technology"),
    ("CAR", "CAR Group (Carsales)", "Communication Services"),
    ("CKF", "Collins Foods", "Consumer Discretionary"),
    ("CWY", "Cleanaway Waste Management", "Industrials"),
    ("DMP", "Domino's Pizza Enterprises", "Consumer Discretionary"),
    ("HUB", "Hub24", "Financials"),
    ("HVN", "Harvey Norman Holdings", "Consumer Discretionary"),
    ("IFL", "Insignia Financial", "Financials"),
    ("IGO", "IGO Limited", "Materials"),
    ("ILU", "Iluka Resources", "Materials"),
    ("IPL", "Incitec Pivot", "Materials"),
    ("JBH", "JB Hi-Fi", "Consumer Discretionary"),
    ("LOV", "Lovisa Holdings", "Consumer Discretionary"),
    ("LYC", "Lynas Rare Earths", "Materials"),
    ("MIN", "Mineral Resources", "Materials"),
    ("MTS", "Metcash", "Consumer Staples"),
    ("NCK", "Nick Scali", "Consumer Discretionary"),
    ("NHF", "NIB Holdings", "Financials"),
    ("NIC", "Nickel Industries", "Materials"),
    ("PME", "Pro Medicus", "Health Care"),
    ("PMV", "Premier Investments", "Consumer Discretionary"),
    ("PRU", "Perseus Mining", "Materials"),
    ("QAN", "Qantas Airways", "Industrials"),
    ("REA", "REA Group", "Communication Services"),
    ("REH", "Reece", "Industrials"),
    ("RMD", "ResMed", "Health Care"),
    ("SDF", "Steadfast Group", "Financials"),
    ("SEK", "Seek", "Communication Services"),
    ("SGM", "Sims Limited", "Materials"),
    ("SIG", "Sigma Healthcare", "Health Care"),
    ("SOL", "Washington H. Soul Pattinson", "Financials"),
    ("SPK", "Spark New Zealand", "Communication Services"),
    ("SUL", "Super Retail Group", "Consumer Discretionary"),
    ("SUN", "Suncorp Group", "Financials"),
    ("TLX", "Telix Pharmaceuticals", "Health Care"),
    ("TPG", "TPG Telecom", "Communication Services"),
    ("TWE", "Treasury Wine Estates", "Consumer Staples"),
    ("VEA", "Viva Energy", "Energy"),
    ("WAF", "West African Resources", "Materials"),
    ("WBT", "Weebit Nano", "Information Technology"),
    ("WEB", "Webjet", "Consumer Discretionary"),
    ("WHC", "Whitehaven Coal", "Energy"),
    ("WOR", "Worley", "Energy"),
    ("WTC", "WiseTech Global", "Information Technology"),
    ("XRO", "Xero", "Information Technology"),
    ("ZIP", "Zip Co", "Financials"),
    ("ALU", "Altium (where listed)", "Information Technology"),
    ("AMC", "Amcor", "Materials"),
    ("APE", "Eagers Automotive", "Consumer Discretionary"),
    ("ARB", "ARB Corporation", "Consumer Discretionary"),
    ("BLD", "Boral", "Materials"),
    ("BPT", "Beach Energy", "Energy"),
    ("CIA", "Champion Iron", "Materials"),
    ("CMM", "Capricorn Metals", "Materials"),
    ("CMW", "Cromwell Property Group", "Real Estate"),
    ("COE", "Cooper Energy", "Energy"),
    ("COF", "Centuria Office REIT", "Real Estate"),
    ("CSR", "CSR Limited", "Materials"),
    ("DEG", "De Grey Mining", "Materials"),
    ("DOW", "Downer EDI", "Industrials"),
    ("DUR", "Duratec", "Industrials"),
    ("EBO", "EBOS Group", "Health Care"),
    ("EVN", "Evolution Mining", "Materials"),
    ("FCL", "Fineos Corporation", "Information Technology"),
    ("FLT", "Flight Centre Travel", "Consumer Discretionary"),
    ("GNC", "GrainCorp", "Consumer Staples"),
    ("HLI", "Helia Group", "Financials"),
    ("HUM", "Humm Group", "Financials"),
    ("IEL", "IDP Education", "Consumer Discretionary"),
    ("INA", "Ingenia Communities", "Real Estate"),
    ("LIC", "Lifestyle Communities", "Real Estate"),
    ("LNW", "Light & Wonder", "Consumer Discretionary"),
    ("MGH", "Maas Group Holdings", "Industrials"),
    ("MQG", "Macquarie Group", "Financials"),
    ("NAN", "Nanosonics", "Health Care"),
    ("NUF", "Nufarm", "Materials"),
    ("ORA", "Orora", "Materials"),
    ("PNI", "Pinnacle Investment Mgmt", "Financials"),
    ("RWC", "Reliance Worldwide Corp", "Industrials"),
    ("SDR", "SiteMinder", "Information Technology"),
    ("SGP", "Stockland", "Real Estate"),
    ("SLR", "Silver Lake Resources", "Materials"),
    ("SUN", "Suncorp Group", "Financials"),
    ("TAH", "Tabcorp Holdings", "Consumer Discretionary"),
    ("TLC", "The Lottery Corporation", "Consumer Discretionary"),
    ("UMG", "United Malt Group", "Consumer Staples"),
    ("URW", "Unibail-Rodamco-Westfield", "Real Estate"),
    ("VEN", "Vintage Energy", "Energy"),
    ("VNT", "Ventia Services Group", "Industrials"),
    ("VUK", "Virgin Money UK", "Financials"),
    ("WTN", "Wagners Holding", "Materials"),
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
    CuratedETF("SSO",  "SPDR Small Ordinaries",                   "AU equity",     "ASX small-cap exposure"),
    CuratedETF("EX20", "Betashares ASX 200 ex-Top-20",            "AU equity",     "Excludes mega-caps for diversity"),

    # --- AU Listed Investment Companies (LICs) -----------------------
    # Long-running closed-end funds; popular with retail for steady dividends.
    CuratedETF("AFI",  "Australian Foundation Investment Co",     "AU equity",     "Largest LIC, ~100yr history"),
    CuratedETF("ARG",  "Argo Investments",                        "AU equity",     "Major dividend-paying LIC"),
    CuratedETF("MLT",  "Milton Corporation",                      "AU equity",     "Now WAM Leaders (WLE)"),
    CuratedETF("WHF",  "Whitefield",                              "AU equity",     "Industrial-focused LIC"),
    CuratedETF("DJW",  "Djerriwarrh Investments",                 "AU equity",     "High-yield writing LIC"),
    CuratedETF("BKI",  "BKI Investment Company",                  "AU equity",     "Income-focused LIC"),
    CuratedETF("MIR",  "Mirrabooka Investments",                  "AU equity",     "Smaller-companies LIC"),
    CuratedETF("WAM",  "WAM Capital",                             "AU equity",     "Active small/mid-cap LIC"),
    CuratedETF("WLE",  "WAM Leaders",                             "AU equity",     "Large-cap LIC"),
    CuratedETF("WAX",  "WAM Research",                            "AU equity",     "Research-active LIC"),
    CuratedETF("WGB",  "WAM Global",                              "Global equity", "Global equity LIC"),
    CuratedETF("MFF",  "MFF Capital Investments",                 "Global equity", "Magellan Flagship Fund"),

    # --- AU sector ETFs -----------------------------------------------
    CuratedETF("OZF",  "SPDR S&P/ASX 200 Financials",             "AU equity",     "AU financials sector"),
    CuratedETF("OZR",  "SPDR S&P/ASX 200 Resources",              "AU equity",     "AU mining/resources"),
    CuratedETF("QFN",  "Betashares S&P/ASX 200 Financials",       "AU equity",     "AU financials"),
    CuratedETF("MVB",  "VanEck Australian Banks",                 "AU equity",     "Big-four banks weighted"),
    CuratedETF("MNRS", "Betashares Resources Sector",             "AU equity",     "Diversified resources"),

    # --- Broad international equity -----------------------------------
    CuratedETF("VGS",  "Vanguard MSCI Intl ex-Aus",               "Global equity", "Developed-market ex-AU"),
    CuratedETF("IWLD", "iShares Core MSCI World All Cap",         "Global equity", "Broadest global equity"),
    CuratedETF("VEU",  "Vanguard All-World ex-US",                "Global equity", "Global ex-US"),
    CuratedETF("IOO",  "iShares Global 100",                      "Global equity", "Top 100 global mega-caps"),
    CuratedETF("VGAD", "Vanguard MSCI Intl ex-Aus Hedged",        "Global equity", "AUD-hedged version of VGS"),
    CuratedETF("HGBL", "Betashares Global Quality Hedged",        "Global equity", "AUD-hedged quality factor"),
    CuratedETF("MOAT", "VanEck Wide Moat Research",               "Global equity", "Quality / wide-moat companies"),
    CuratedETF("QLTY", "Betashares Global Quality Leaders",       "Global equity", "Quality-factor global"),

    # --- US equity ----------------------------------------------------
    CuratedETF("IVV",  "iShares S&P 500",                         "US equity",     "Largest US equity ETF on ASX"),
    CuratedETF("IHVV", "iShares S&P 500 AUD Hedged",              "US equity",     "AUD-hedged S&P 500"),
    CuratedETF("VTS",  "Vanguard US Total Market",                "US equity",     "Whole US market incl. small-caps"),
    CuratedETF("NDQ",  "Betashares Nasdaq 100",                   "US equity",     "Tech-heavy Nasdaq 100"),
    CuratedETF("HNDQ", "Betashares Nasdaq 100 Hedged",            "US equity",     "AUD-hedged Nasdaq 100"),
    CuratedETF("QUS",  "Betashares S&P 500 Equal Weight",         "US equity",     "Equal-weight US 500"),
    CuratedETF("YANK", "Betashares US Equities Strong Bear",      "US equity",     "Inverse US equity (hedge)"),

    # --- Emerging markets / Asia / Europe -----------------------------
    CuratedETF("VGE",  "Vanguard FTSE Emerging Markets",          "EM equity",     "Broad EM"),
    CuratedETF("IEM",  "iShares MSCI Emerging Markets",           "EM equity",     "Broad EM"),
    CuratedETF("IZZ",  "iShares China Large-Cap",                 "EM equity",     "Chinese mega-caps"),
    CuratedETF("IJP",  "iShares MSCI Japan",                      "EM equity",     "Japanese equities"),
    CuratedETF("IEU",  "iShares Europe",                          "EM equity",     "European equities"),
    CuratedETF("IIND", "iShares MSCI India",                      "EM equity",     "Indian equities"),
    CuratedETF("ASIA", "Betashares Asia Tech Tigers",             "EM equity",     "Asian tech leaders"),

    # --- Thematic / sector --------------------------------------------
    CuratedETF("HACK", "Betashares Global Cybersecurity",         "Thematic",      "Cybersecurity"),
    CuratedETF("ROBO", "Global Robotics & AI",                    "Thematic",      "Robotics + AI"),
    CuratedETF("ETHI", "Betashares Global Sustainability Leaders","Thematic",      "ESG global equity"),
    CuratedETF("FAIR", "Betashares Australian Sustainability",    "Thematic",      "ESG Australian equity"),
    CuratedETF("CRYP", "Betashares Crypto Innovators",            "Thematic",      "Crypto-exposed companies"),
    CuratedETF("DRUG", "Betashares Global Healthcare ex-AU",      "Thematic",      "Global healthcare"),
    CuratedETF("FOOD", "Betashares Global Agriculture",           "Thematic",      "Agriculture/food"),
    CuratedETF("IXJ",  "iShares Global Healthcare",               "Thematic",      "Global healthcare"),
    CuratedETF("INCM", "Betashares Global Income Leaders",        "Thematic",      "Global high-yield equity"),
    CuratedETF("ATEC", "Betashares S&P/ASX Australian Tech",      "Thematic",      "AU technology"),

    # --- Fixed income -------------------------------------------------
    CuratedETF("VAF",  "Vanguard Australian Fixed Interest",      "AU bonds",      "Investment-grade AU bonds"),
    CuratedETF("VGB",  "Vanguard Australian Government Bond",     "AU bonds",      "Government bonds only"),
    CuratedETF("BOND", "PIMCO Australian Bond",                   "AU bonds",      "Active fixed income"),
    CuratedETF("IAF",  "iShares Core Composite Bond",             "AU bonds",      "Broad AU fixed income"),
    CuratedETF("SUBD", "VanEck Australian Subordinated Debt",     "AU bonds",      "Bank subordinated debt"),
    CuratedETF("HBRD", "Betashares Active Australian Hybrids",    "AU bonds",      "AU bank hybrids"),
    CuratedETF("CRED", "Betashares Investment Grade Corp Bond",   "AU bonds",      "AU investment grade credit"),
    CuratedETF("AGVT", "Betashares Australian Govt Bond",         "AU bonds",      "Government-only"),
    CuratedETF("FLOT", "Vanguard Australian Floating Rate Notes", "AU bonds",      "Floating-rate, low duration"),
    CuratedETF("QPON", "Betashares Bank Senior Floating Rate",    "AU bonds",      "Senior bank debt, floating"),
    CuratedETF("VIF",  "Vanguard International Fixed Interest",   "Global bonds",  "Hedged to AUD"),
    CuratedETF("IHEB", "iShares J.P.Morgan USD EM Bond",          "Global bonds",  "EM USD-denominated bonds"),
    CuratedETF("IHHY", "iShares Global High Yield Bond",          "Global bonds",  "Global high-yield credit"),

    # --- Cash ---------------------------------------------------------
    CuratedETF("AAA",  "Betashares Australian High Interest Cash","Cash",          "Bank deposits, daily liquidity"),
    CuratedETF("MMKT", "Betashares Australian Cash Plus",         "Cash",          "Cash plus short-dated bonds"),
    CuratedETF("ISEC", "iShares Core Cash",                       "Cash",          "iShares cash equivalent"),

    # --- Property / Infrastructure ------------------------------------
    CuratedETF("VAP",  "Vanguard Australian Property",            "AU property",   "Australian REITs"),
    CuratedETF("MVA",  "VanEck Australian Property",              "AU property",   "AU REITs, equal-weighted"),
    CuratedETF("DJRE", "SPDR Global REIT",                        "Global property","Hedged global REITs"),
    CuratedETF("REIT", "VanEck Global REIT",                      "Global property","Global REITs"),
    CuratedETF("GLIN", "VanEck Global Infrastructure",            "Global property","Global infrastructure"),
    CuratedETF("VBLD", "Vanguard Global Infrastructure Index",    "Global property","Global infra index"),
    CuratedETF("IFRA", "VanEck Global Listed Infrastructure",     "Global property","Listed infrastructure"),

    # --- Commodities --------------------------------------------------
    CuratedETF("GOLD", "Global X Physical Gold",                  "Commodities",   "Backed by physical bullion"),
    CuratedETF("QAU",  "Betashares Gold Bullion AUD-Hedged",      "Commodities",   "AUD-hedged gold"),
    CuratedETF("ETPMAG","Global X Physical Silver",               "Commodities",   "Backed by physical silver"),
    CuratedETF("OOO",  "Betashares Crude Oil ETF",                "Commodities",   "Crude oil futures"),
    CuratedETF("QCB",  "Betashares Commodities Basket",           "Commodities",   "Diversified commodities"),
)


def _scrape_constituents(url: str) -> pd.DataFrame:
    """Try to scrape an index constituents table from Wikipedia."""
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
    """Try All Ordinaries / ASX 300 / ASX 200 in turn."""
    last_exc = None
    for url in _UNIVERSE_SOURCES:
        try:
            log.info("Fetching ASX constituents from %s", url)
            constituents = _scrape_constituents(url)
            keep = [c for c in ("ticker", "name", "sector") if c in constituents.columns]
            df = constituents[keep].copy()
            df["ticker"] = df["ticker"].astype(str).str.strip().str.upper() + ".AX"
            df["type"] = "stock"
            if "sector" not in df.columns:
                df["sector"] = pd.NA
            log.info("Loaded %d ASX constituents from %s.", len(df), url)
            return df
        except Exception as exc:
            log.warning("  fetch failed (%s); trying next source.", exc)
            last_exc = exc
    raise RuntimeError(f"All universe sources failed; last error: {last_exc}")


def etf_dataframe() -> pd.DataFrame:
    """Curated ETF/LIC list as a DataFrame matching fetch_asx_tickers()."""
    rows = [
        {"ticker": f"{e.ticker}.AX",
         "name": e.name,
         "sector": e.asset_class,
         "type": "etf"}
        for e in CURATED_ETFS
    ]
    return pd.DataFrame(rows)


def extended_au_dataframe() -> pd.DataFrame:
    """Curated supplement of mid/small-cap AU stocks beyond ASX 200."""
    rows = [
        {"ticker": f"{t}.AX", "name": n, "sector": s, "type": "stock"}
        for t, n, s in EXTENDED_AU_STOCKS
    ]
    return pd.DataFrame(rows)


def build_universe() -> pd.DataFrame:
    """Combine ASX 200 stocks + extended mid/small-cap supplement + ETFs/LICs."""
    asx200 = fetch_asx_tickers()
    extended = extended_au_dataframe()
    etfs = etf_dataframe()
    universe = pd.concat([asx200, extended, etfs], ignore_index=True)
    # Dedup keeps the *first* (ASX 200 entry wins for overlaps).
    universe = universe.drop_duplicates(subset="ticker", keep="first").reset_index(drop=True)
    log.info("Universe size: %d instruments (%d stocks, %d ETFs/LICs).",
             len(universe),
             (universe["type"] == "stock").sum(),
             (universe["type"] == "etf").sum())
    return universe


fetch_asx200_tickers = fetch_asx_tickers  # backward-compat alias
