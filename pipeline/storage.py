"""SQLite storage layer for the pipeline.

We use a single SQLite file (`data/market.sqlite`) with three tables:

    instruments  - one row per ticker, with metadata (name, sector, type, ...)
    prices       - daily OHLCV rows, keyed on (ticker, date)
    dividends    - dividend payments, keyed on (ticker, date)

The layer is intentionally thin: pandas DataFrames in, pandas DataFrames
out. We don't use an ORM because the schema is small and the operations
are bulk reads/writes that pandas handles natively.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# DB location can be overridden with the ASX_DB_PATH environment variable.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("ASX_DB_PATH",
                              str(PROJECT_ROOT / "data" / "market.sqlite")))


@contextmanager
def connect():
    """Context-managed SQLite connection. Auto-creates the data folder."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema() -> None:
    """Create tables and indices if they don't yet exist."""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                ticker      TEXT PRIMARY KEY,
                name        TEXT,
                sector      TEXT,
                industry    TEXT,
                type        TEXT,
                market_cap  REAL,
                expense_ratio REAL,
                currency    TEXT,
                last_updated TEXT
            );

            CREATE TABLE IF NOT EXISTS prices (
                ticker    TEXT NOT NULL,
                date      TEXT NOT NULL,
                open      REAL,
                high      REAL,
                low       REAL,
                close     REAL,
                adj_close REAL,
                volume    INTEGER,
                PRIMARY KEY (ticker, date)
            );
            CREATE INDEX IF NOT EXISTS prices_ticker_idx ON prices(ticker);
            CREATE INDEX IF NOT EXISTS prices_date_idx   ON prices(date);

            CREATE TABLE IF NOT EXISTS dividends (
                ticker TEXT NOT NULL,
                date   TEXT NOT NULL,
                amount REAL,
                PRIMARY KEY (ticker, date)
            );
            """
        )
    log.info("Schema initialised at %s", DB_PATH)


def _na_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Convert pd.NA / NaN / NaT to Python None so sqlite3 can bind values."""
    return df.astype(object).where(df.notna(), None)


def upsert_instruments(df: pd.DataFrame) -> None:
    """Insert or update instrument metadata rows."""
    if df.empty:
        return
    cols = ["ticker", "name", "sector", "industry", "type",
            "market_cap", "expense_ratio", "currency", "last_updated"]
    df = df.reindex(columns=cols)
    df = _na_to_none(df)
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    placeholders = ",".join("?" * len(cols))
    with connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO instruments ({','.join(cols)}) VALUES ({placeholders})",
            rows,
        )
    log.info("Upserted %d instruments.", len(rows))


def replace_prices(ticker: str, prices: pd.DataFrame) -> None:
    """Replace all price rows for a ticker. Idempotent."""
    if prices is None or prices.empty:
        return
    df = prices.copy()
    df["ticker"] = ticker
    df["date"] = df.index.strftime("%Y-%m-%d")
    df = df.reset_index(drop=True)
    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]
    df = df.reindex(columns=cols)
    df = _na_to_none(df)
    with connect() as conn:
        conn.execute("DELETE FROM prices WHERE ticker = ?", (ticker,))
        df.to_sql("prices", conn, if_exists="append", index=False)


def replace_dividends(ticker: str, dividends: pd.Series) -> None:
    """Replace all dividend rows for a ticker."""
    if dividends is None or len(dividends) == 0:
        return
    df = pd.DataFrame({
        "ticker": ticker,
        "date": pd.to_datetime(dividends.index).strftime("%Y-%m-%d"),
        "amount": dividends.values,
    })
    with connect() as conn:
        conn.execute("DELETE FROM dividends WHERE ticker = ?", (ticker,))
        df.to_sql("dividends", conn, if_exists="append", index=False)


def load_instruments() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql("SELECT * FROM instruments", conn)


def load_prices(ticker: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM prices"
    params: tuple = ()
    if ticker is not None:
        query += " WHERE ticker = ?"
        params = (ticker,)
    query += " ORDER BY ticker, date"
    with connect() as conn:
        df = pd.read_sql(query, conn, params=params, parse_dates=["date"])
    return df
