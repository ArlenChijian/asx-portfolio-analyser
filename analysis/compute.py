"""Computes per-instrument metrics and writes them to the database.

This module reads adjusted-close prices from the SQLite database created
by the data pipeline, runs every function in `metrics.py` over each
instrument, and writes the results to a `metrics` table.

Beta is computed against STW.AX (SPDR S&P/ASX 200 ETF), which we use as
the market proxy because:
  - It's already in our universe (no extra fetches needed).
  - It tracks the ASX 200 by construction, with a tiny tracking error.
  - Yahoo's data on it is reliable and goes back far enough.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

import pandas as pd

from pipeline import storage
from analysis import metrics

log = logging.getLogger(__name__)

BETA_BENCHMARK_TICKER = "STW.AX"


def init_metrics_schema() -> None:
    """Create the metrics table if it doesn't exist."""
    with storage.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                ticker              TEXT PRIMARY KEY,
                return_1y           REAL,
                return_3y           REAL,
                return_5y           REAL,
                return_10y          REAL,
                volatility_1y       REAL,
                volatility_3y       REAL,
                sharpe_1y           REAL,
                sharpe_3y           REAL,
                max_drawdown_5y     REAL,
                beta_5y             REAL,
                dividend_yield_ttm  REAL,
                last_computed       TEXT
            );
            """
        )


def _load_adj_close(conn: sqlite3.Connection, ticker: str) -> pd.Series:
    """Load adjusted-close prices for one ticker as a date-indexed Series."""
    df = pd.read_sql(
        "SELECT date, adj_close FROM prices WHERE ticker = ? ORDER BY date",
        conn, params=(ticker,), parse_dates=["date"],
    )
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("date")["adj_close"].astype(float)


def _load_dividends(conn: sqlite3.Connection, ticker: str) -> pd.Series:
    df = pd.read_sql(
        "SELECT date, amount FROM dividends WHERE ticker = ? ORDER BY date",
        conn, params=(ticker,), parse_dates=["date"],
    )
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("date")["amount"].astype(float)


def compute_for_ticker(conn: sqlite3.Connection, ticker: str,
                       benchmark_prices: pd.Series) -> dict | None:
    """Compute every metric for one ticker. Returns a row-dict or None."""
    prices = _load_adj_close(conn, ticker)
    if prices.empty or len(prices) < 30:
        return None
    dividends = _load_dividends(conn, ticker)
    return {
        "ticker":             ticker,
        "return_1y":          metrics.annualised_return(prices, years=1),
        "return_3y":          metrics.annualised_return(prices, years=3),
        "return_5y":          metrics.annualised_return(prices, years=5),
        "return_10y":         metrics.annualised_return(prices, years=10),
        "volatility_1y":      metrics.annualised_volatility(prices, years=1),
        "volatility_3y":      metrics.annualised_volatility(prices, years=3),
        "sharpe_1y":          metrics.sharpe_ratio(prices, years=1),
        "sharpe_3y":          metrics.sharpe_ratio(prices, years=3),
        "max_drawdown_5y":    metrics.max_drawdown(prices, years=5),
        "beta_5y":            metrics.beta(prices, benchmark_prices, years=5),
        "dividend_yield_ttm": metrics.trailing_dividend_yield(prices, dividends),
        "last_computed":      date.today().isoformat(),
    }


def compute_all() -> pd.DataFrame:
    """Compute metrics for every ticker in the instruments table."""
    init_metrics_schema()
    with storage.connect() as conn:
        instruments = pd.read_sql("SELECT ticker FROM instruments", conn)
        log.info("Computing metrics for %d instruments...", len(instruments))

        benchmark = _load_adj_close(conn, BETA_BENCHMARK_TICKER)
        if benchmark.empty:
            log.warning("Benchmark %s has no price data; betas will be NULL.",
                        BETA_BENCHMARK_TICKER)

        rows: list[dict] = []
        for i, ticker in enumerate(instruments["ticker"], start=1):
            row = compute_for_ticker(conn, ticker, benchmark)
            if row is None:
                log.debug("[%d/%d] %s: skipped (insufficient data)",
                          i, len(instruments), ticker)
                continue
            rows.append(row)
            if i % 25 == 0:
                log.info("  progress: %d/%d", i, len(instruments))

    if not rows:
        log.warning("No metrics computed.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    cols = ["ticker", "return_1y", "return_3y", "return_5y", "return_10y",
            "volatility_1y", "volatility_3y", "sharpe_1y", "sharpe_3y",
            "max_drawdown_5y", "beta_5y", "dividend_yield_ttm", "last_computed"]
    df = df.reindex(columns=cols)
    df = storage._na_to_none(df)

    placeholders = ",".join("?" * len(cols))
    rows_tup = [tuple(r) for r in df.itertuples(index=False, name=None)]
    with storage.connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO metrics ({','.join(cols)}) VALUES ({placeholders})",
            rows_tup,
        )

    log.info("Wrote %d metric rows.", len(df))
    return df
