"""Pure mathematical functions for instrument analytics.

These functions are deliberately I/O-free: each takes a pandas Series
(prices, returns, etc.) and returns a number. That makes them trivial to
unit-test and reuse from notebooks without spinning up the database.

Conventions:
    - `prices` is a pandas Series indexed by date, values are adjusted
      close prices (i.e. corporate-action adjusted, the right input for
      total-return analytics).
    - We assume 252 trading days per year (ASX standard).
    - Returns are computed on adjusted close so dividends and splits
      are already baked in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

# RBA cash rate as of 2025-2026. In production we'd refresh this from
# the RBA API on each run; for a portfolio project, a constant is fine.
RISK_FREE_RATE = 0.0435


def daily_returns(prices: pd.Series) -> pd.Series:
    """Simple daily returns from a price series."""
    return prices.pct_change().dropna()


def annualised_return(prices: pd.Series, years: float | None = None) -> float | None:
    """Compound annual growth rate (CAGR) over the supplied window.

    If `years` is None, uses the full price history. If `years` is set
    but the price series doesn't cover that long, returns None rather
    than extrapolating misleadingly.
    """
    if prices is None or len(prices) < 2:
        return None
    if years is not None:
        cutoff = prices.index.max() - pd.DateOffset(years=int(years))
        prices = prices[prices.index >= cutoff]
        if len(prices) < 2:
            return None
        # Require at least 80% of the requested window to be covered.
        actual_span = (prices.index.max() - prices.index.min()).days / 365.25
        if actual_span < 0.8 * years:
            return None
    start, end = prices.iloc[0], prices.iloc[-1]
    if start <= 0 or end <= 0:
        return None
    span_years = (prices.index.max() - prices.index.min()).days / 365.25
    if span_years <= 0:
        return None
    return float((end / start) ** (1 / span_years) - 1)


def annualised_volatility(prices: pd.Series, years: float | None = None) -> float | None:
    """Annualised stdev of daily returns over the window."""
    if prices is None or len(prices) < 30:
        return None
    if years is not None:
        cutoff = prices.index.max() - pd.DateOffset(years=int(years))
        prices = prices[prices.index >= cutoff]
        if len(prices) < 30:
            return None
    rets = daily_returns(prices)
    if len(rets) < 30:
        return None
    return float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(prices: pd.Series, years: float | None = None,
                 risk_free: float = RISK_FREE_RATE) -> float | None:
    """(Annualised return - risk free rate) / annualised volatility."""
    ar = annualised_return(prices, years)
    av = annualised_volatility(prices, years)
    if ar is None or av is None or av == 0:
        return None
    return float((ar - risk_free) / av)


def max_drawdown(prices: pd.Series, years: float | None = None) -> float | None:
    """Largest peak-to-trough decline as a negative fraction (e.g. -0.34)."""
    if prices is None or len(prices) < 2:
        return None
    if years is not None:
        cutoff = prices.index.max() - pd.DateOffset(years=int(years))
        prices = prices[prices.index >= cutoff]
        if len(prices) < 2:
            return None
    running_max = prices.cummax()
    drawdowns = prices / running_max - 1.0
    return float(drawdowns.min())


def beta(asset_prices: pd.Series, market_prices: pd.Series,
         years: float | None = None) -> float | None:
    """OLS beta of asset returns against market returns.

    Aligns the two series on date, then computes Cov(asset, market) / Var(market)
    on daily returns over the requested window.
    """
    if asset_prices is None or market_prices is None:
        return None
    df = pd.concat({"asset": asset_prices, "market": market_prices}, axis=1).dropna()
    if years is not None:
        cutoff = df.index.max() - pd.DateOffset(years=int(years))
        df = df[df.index >= cutoff]
    if len(df) < 60:  # ~3 months minimum
        return None
    rets = df.pct_change().dropna()
    if len(rets) < 60:
        return None
    var_market = rets["market"].var(ddof=1)
    if var_market == 0 or np.isnan(var_market):
        return None
    cov = rets.cov().loc["asset", "market"]
    return float(cov / var_market)


def trailing_dividend_yield(prices: pd.Series, dividends: pd.Series) -> float | None:
    """Sum of dividends in the trailing 12 months / latest price."""
    if prices is None or prices.empty:
        return None
    latest_price = float(prices.iloc[-1])
    if latest_price <= 0:
        return None
    if dividends is None or dividends.empty:
        return 0.0
    cutoff = prices.index.max() - pd.DateOffset(years=1)
    ttm_divs = dividends[dividends.index >= cutoff].sum()
    return float(ttm_divs) / latest_price
