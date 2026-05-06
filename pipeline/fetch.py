"""Fetches market data from Yahoo Finance via the `yfinance` library.

Design notes:
    - We process tickers one at a time rather than in batch mode so a single
      bad ticker doesn't poison the rest of the run. yfinance's batch mode
      is faster but harder to error-handle gracefully.
    - We sleep briefly between requests to be polite to Yahoo's servers.
    - All errors are logged and skipped; the pipeline doesn't abort on a
      single failure. We surface the count of failed tickers at the end.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import yfinance as yf

from pipeline import storage

log = logging.getLogger(__name__)

# How many years of history to pull. 10 years is enough to cover one full
# market cycle including drawdowns (e.g. COVID 2020) and gives reliable
# annualised statistics.
YEARS_OF_HISTORY = 10

# Polite delay between Yahoo requests.
SLEEP_SECONDS = 0.25


def _fetch_one(ticker: str) -> dict | None:
    """Fetch metadata, prices, and dividends for a single ticker.

    Returns a metadata dict on success, or None if the ticker fails entirely.
    Side-effect: writes prices and dividends straight into the database.
    """
    try:
        yt = yf.Ticker(ticker)

        # `history(period="10y")` returns a DataFrame with OHLCV columns.
        # auto_adjust=False keeps the unadjusted close, with adj_close separate.
        prices = yt.history(period=f"{YEARS_OF_HISTORY}y", auto_adjust=False)
        if prices.empty:
            log.warning("[%s] No price data returned. Skipping.", ticker)
            return None

        prices = prices.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
        })[["open", "high", "low", "close", "adj_close", "volume"]]

        storage.replace_prices(ticker, prices)
        storage.replace_dividends(ticker, yt.dividends)

        # `info` is a dict of metadata. yfinance occasionally returns a sparse
        # dict for thinly-traded tickers; we tolerate missing keys via .get().
        info = yt.info or {}
        return {
            "ticker": ticker,
            "industry":     info.get("industry"),
            "market_cap":   info.get("marketCap"),
            "expense_ratio": info.get("annualReportExpenseRatio"),
            "currency":     info.get("currency"),
            "last_updated": date.today().isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - log and continue
        log.warning("[%s] Fetch failed: %s", ticker, exc)
        return None


def fetch_universe(universe: pd.DataFrame) -> pd.DataFrame:
    """Fetch every ticker in the universe, write prices/dividends, and
    return a DataFrame of refreshed instrument metadata.

    `universe` is the DataFrame from `pipeline.universe.build_universe()`.
    """
    if universe.empty:
        return universe

    storage.init_schema()

    # Seed the instruments table with what we know from the universe (name,
    # sector, type) so we have something even if yfinance.info fails.
    seed = universe.assign(
        industry=pd.NA, market_cap=pd.NA, expense_ratio=pd.NA,
        currency=pd.NA, last_updated=date.today().isoformat(),
    )
    storage.upsert_instruments(seed)

    log.info("Fetching %d tickers from Yahoo Finance...", len(universe))
    metadata_rows: list[dict] = []
    failures = 0

    for i, row in universe.iterrows():
        ticker = row["ticker"]
        log.info("[%d/%d] %s", i + 1, len(universe), ticker)
        meta = _fetch_one(ticker)
        if meta is None:
            failures += 1
        else:
            # Merge with what we already had in the universe row.
            meta["name"] = row["name"]
            meta["sector"] = row["sector"]
            meta["type"] = row["type"]
            metadata_rows.append(meta)
        time.sleep(SLEEP_SECONDS)

    log.info("Fetched %d/%d successfully; %d failed.",
             len(metadata_rows), len(universe), failures)

    if metadata_rows:
        meta_df = pd.DataFrame(metadata_rows)
        storage.upsert_instruments(meta_df)

    return pd.DataFrame(metadata_rows)
