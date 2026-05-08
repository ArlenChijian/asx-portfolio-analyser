"""Fetches market data + fundamentals from Yahoo Finance via yfinance.

For each ticker we capture three layers of data:
    1. Daily OHLCV history (last 10 years) -> `prices` table.
    2. Dividend history -> `dividends` table.
    3. Fundamentals (P/E, P/B, ROE, etc.) from yfinance.info -> `fundamentals`.

We also pull a small set of macro indicators (RBA cash rate proxy, AUD/USD,
ASX 200 index level) into the `macro` table for use on the homepage.

yfinance.info is well-known to be sparse for some ASX tickers; missing
fields are stored as NULL rather than treated as errors.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import yfinance as yf

from pipeline import storage

log = logging.getLogger(__name__)

YEARS_OF_HISTORY = 10
SLEEP_SECONDS = 0.25


def _safe_num(x):
    """Coerce yfinance.info values to float or None."""
    if x is None:
        return None
    try:
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fetch_one(ticker: str) -> tuple[dict | None, dict | None]:
    """Fetch metadata, prices, dividends, and fundamentals for one ticker.

    Returns (instrument_meta, fundamentals_row), either may be None on failure.
    """
    try:
        yt = yf.Ticker(ticker)

        prices = yt.history(period=f"{YEARS_OF_HISTORY}y", auto_adjust=False)
        if prices.empty:
            log.warning("[%s] No price data. Skipping.", ticker)
            return None, None

        prices = prices.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
        })[["open", "high", "low", "close", "adj_close", "volume"]]

        storage.replace_prices(ticker, prices)
        storage.replace_dividends(ticker, yt.dividends)

        info = yt.info or {}
        today = date.today().isoformat()

        instrument_meta = {
            "ticker": ticker,
            "industry":      info.get("industry"),
            "market_cap":    _safe_num(info.get("marketCap")),
            "expense_ratio": _safe_num(info.get("annualReportExpenseRatio")),
            "currency":      info.get("currency"),
            "last_updated":  today,
        }

        fundamentals = {
            "ticker": ticker,
            "trailing_pe":             _safe_num(info.get("trailingPE")),
            "forward_pe":              _safe_num(info.get("forwardPE")),
            "price_to_book":           _safe_num(info.get("priceToBook")),
            "return_on_equity":        _safe_num(info.get("returnOnEquity")),
            "profit_margin":           _safe_num(info.get("profitMargins")),
            "debt_to_equity":          _safe_num(info.get("debtToEquity")),
            "forward_dividend_yield":  _safe_num(info.get("dividendYield")),
            "payout_ratio":            _safe_num(info.get("payoutRatio")),
            "eps_trailing":            _safe_num(info.get("trailingEps")),
            "revenue_growth":          _safe_num(info.get("revenueGrowth")),
            "last_updated":            today,
        }
        return instrument_meta, fundamentals
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] Fetch failed: %s", ticker, exc)
        return None, None


def fetch_universe(universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return universe

    storage.init_schema()

    # Seed instruments with what we already know (name, sector, type).
    seed = universe.assign(
        industry=pd.NA, market_cap=pd.NA, expense_ratio=pd.NA,
        currency=pd.NA, last_updated=date.today().isoformat(),
    )
    storage.upsert_instruments(seed)

    log.info("Fetching %d tickers from Yahoo Finance...", len(universe))
    metadata_rows: list[dict] = []
    fund_rows: list[dict] = []
    failures = 0

    for i, row in universe.iterrows():
        ticker = row["ticker"]
        log.info("[%d/%d] %s", i + 1, len(universe), ticker)
        meta, fund = _fetch_one(ticker)
        if meta is None:
            failures += 1
        else:
            meta["name"] = row["name"]
            meta["sector"] = row["sector"]
            meta["type"] = row["type"]
            metadata_rows.append(meta)
            if fund is not None:
                fund_rows.append(fund)
        time.sleep(SLEEP_SECONDS)

    log.info("Fetched %d/%d successfully; %d failed.",
             len(metadata_rows), len(universe), failures)

    if metadata_rows:
        storage.upsert_instruments(pd.DataFrame(metadata_rows))
    if fund_rows:
        storage.upsert_fundamentals(pd.DataFrame(fund_rows))

    fetch_macro()
    return pd.DataFrame(metadata_rows)


def fetch_macro() -> None:
    """Pull a small set of macro indicators useful for portfolio context."""
    today = date.today().isoformat()
    rows = []

    series = [
        # (key, yfinance_ticker, multiplier, description)
        ("audusd",    "AUDUSD=X", 1.0,   "AUD/USD exchange rate"),
        ("asx200",    "^AXJO",    1.0,   "ASX 200 index level"),
        ("vix",       "^VIX",     1.0,   "CBOE volatility index (US fear gauge)"),
        ("gold_usd",  "GC=F",     1.0,   "Gold spot price (USD/oz, COMEX)"),
        ("us10y",     "^TNX",     0.01,  "US 10-year Treasury yield (decimal)"),
    ]
    for key, ytkr, mult, desc in series:
        try:
            t = yf.Ticker(ytkr)
            hist = t.history(period="5d", auto_adjust=False)
            if not hist.empty:
                latest = float(hist["Close"].iloc[-1]) * mult
                rows.append({"key": key, "value": latest,
                             "description": desc, "last_updated": today})
        except Exception as exc:  # noqa: BLE001
            log.warning("Macro fetch failed for %s: %s", key, exc)
        time.sleep(0.1)

    # RBA cash rate as a constant; we don't have a free API for it.
    rows.append({"key": "rba_cash_rate", "value": 0.0435,
                 "description": "RBA cash rate (manual; update periodically)",
                 "last_updated": today})

    if rows:
        storage.upsert_macro(rows)
