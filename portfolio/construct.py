"""Portfolio constructor: UserProfile -> candidate portfolio.

Algorithm:
    1. Build target asset allocation from risk profile + horizon + geo tilt.
    2. Distribute the user's max_holdings budget across asset classes
       proportionally to target weight (smaller sleeves get 1, bigger 2-3).
    3. For each asset class, screen the universe (ETFs-only, ESG, sector
       includes/excludes, ticker excludes, min yield, max vol, min history).
    4. Within each sleeve: rank by Sharpe (5y > 3y, requiring real history),
       take top N, weight by inverse volatility.
    5. Apply asset-class target weight.
    6. Cap any single holding at user.max_position_size.
    7. Compute portfolio-level metrics + horizon projection.

Note on data quality (v0.4): we now require min_history_years of price
history to consider an instrument. Default is 3 years. This eliminates
the noisy 1-2 year extrapolation that was inflating projections.
We also prefer 5-year returns where available - longer windows give
more stable estimates than shorter ones.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pipeline import storage
from portfolio.profile import (AssetClass, GeoTilt, RiskProfile, UserProfile,
                               THEME_TICKERS, AU_CLASSES, GLOBAL_CLASSES)

log = logging.getLogger(__name__)

MIN_SHARPE = -1.5

ESG_TICKERS = {"ETHI.AX", "FAIR.AX"}
INCOME_TICKERS_PREFERRED = {"VHY.AX", "IHD.AX", "INCM.AX", "HBRD.AX"}
HEDGED_TICKERS_PREFERRED = {"VIF.AX", "DJRE.AX", "QAU.AX",
                            "VGAD.AX", "IHVV.AX", "HNDQ.AX", "HGBL.AX"}


@dataclass
class Holding:
    ticker: str
    name: str
    asset_class: str
    weight: float
    dollars: float
    sharpe_used: float | None
    rationale: str
    return_1y: float | None = None
    return_3y: float | None = None
    return_5y: float | None = None
    volatility_1y: float | None = None
    max_drawdown_5y: float | None = None
    dividend_yield_ttm: float | None = None


@dataclass
class Projection:
    horizon_years: int
    median: float
    low: float
    high: float
    median_return_pct: float


@dataclass
class PortfolioResult:
    holdings: list[Holding]
    target_allocation: dict[str, float]
    realised_allocation: dict[str, float]
    expected_return: float | None
    expected_volatility: float | None
    expected_max_drawdown: float | None
    expected_dividend_yield: float | None
    capital: float
    notes: list[str] = field(default_factory=list)
    projection: Projection | None = None

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([h.__dict__ for h in self.holdings])


# -------------------------------------------------------------------------
# Data loading + history filter

def _load_full_table(min_history_years: int) -> pd.DataFrame:
    """Load instruments+metrics, filtered to those with at least N years of history."""
    with storage.connect() as conn:
        df = pd.read_sql(
            """
            SELECT i.ticker, i.name, i.sector, i.type,
                   m.return_1y, m.return_3y, m.return_5y, m.return_10y,
                   m.volatility_1y, m.volatility_3y,
                   m.sharpe_1y, m.sharpe_3y,
                   m.max_drawdown_5y, m.beta_5y, m.dividend_yield_ttm
            FROM instruments i
            INNER JOIN metrics m ON m.ticker = i.ticker
            """, conn,
        )
    num_cols = [c for c in df.columns
                if c not in ("ticker", "name", "sector", "type")]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Data-quality filter: require enough history.
    if min_history_years >= 5:
        df = df[df["return_5y"].notna()]
    elif min_history_years >= 3:
        df = df[df["return_3y"].notna()]
    elif min_history_years >= 1:
        df = df[df["return_1y"].notna()]

    return df


# -------------------------------------------------------------------------
# Holdings-budget allocation

def _allocate_holding_counts(target_alloc: dict[AssetClass, float],
                             max_holdings: int) -> dict[AssetClass, int]:
    sleeves = sorted(target_alloc.items(), key=lambda kv: -kv[1])
    n_sleeves = len(sleeves)
    if max_holdings < n_sleeves:
        counts = {ac: 0 for ac, _ in sleeves}
        for ac, _ in sleeves[:max_holdings]:
            counts[ac] = 1
        return counts

    counts = {ac: 1 for ac, _ in sleeves}
    remaining = max_holdings - n_sleeves
    i = 0
    while remaining > 0:
        ac, _ = sleeves[i % n_sleeves]
        if counts[ac] < 3:
            counts[ac] += 1
            remaining -= 1
        i += 1
        if i > n_sleeves * 5:
            break
    return counts


# -------------------------------------------------------------------------
# Per-sleeve screening + selection

def _asset_class_pool(df: pd.DataFrame, asset_class: AssetClass,
                      profile: UserProfile) -> pd.DataFrame:
    if asset_class == AssetClass.AU_STOCKS:
        pool = df[df["type"] == "stock"].copy()
    else:
        pool = df[(df["type"] == "etf") & (df["sector"] == asset_class.value)].copy()

    if profile.etfs_only:
        pool = pool[pool["type"] == "etf"]

    if profile.esg_only and asset_class in (AssetClass.AU_EQUITY,
                                            AssetClass.GLOBAL_EQUITY,
                                            AssetClass.AU_STOCKS):
        pool = pool[pool["ticker"].isin(ESG_TICKERS)]

    # Exclude sectors (only applies to individual stocks, since ETF "sector"
    # is the asset class, not the GICS sector).
    if profile.exclude_sectors and asset_class == AssetClass.AU_STOCKS:
        pool = pool[~pool["sector"].isin(profile.exclude_sectors)]

    # Include-only sectors (positive filter, only individual stocks).
    if profile.include_only_sectors and asset_class == AssetClass.AU_STOCKS:
        pool = pool[pool["sector"].isin(profile.include_only_sectors)]

    # Exclude specific tickers (any sleeve).
    if profile.exclude_tickers:
        excl_set = {t.upper() for t in profile.exclude_tickers}
        pool = pool[~pool["ticker"].str.upper().isin(excl_set)]

    if profile.min_dividend_yield > 0:
        pool = pool[pool["dividend_yield_ttm"].fillna(0) >= profile.min_dividend_yield]

    if profile.max_volatility is not None:
        vol = pool["volatility_1y"].fillna(pool["volatility_3y"])
        pool = pool[vol.fillna(0) <= profile.max_volatility]

    # Prefer 3y Sharpe (we already filtered to instruments with 3y data).
    pool["sharpe_used"] = pool["sharpe_3y"].fillna(pool["sharpe_1y"])
    pool = pool[pool["sharpe_used"].notna()]
    pool = pool[pool["sharpe_used"] >= MIN_SHARPE]
    return pool.sort_values("sharpe_used", ascending=False)


def _select_holdings(pool: pd.DataFrame, n: int,
                     profile: UserProfile,
                     prefer_tickers: set[str] | None = None) -> list[dict]:
    if pool.empty or n <= 0:
        return []
    if prefer_tickers:
        preferred = pool[pool["ticker"].isin(prefer_tickers)]
        rest = pool[~pool["ticker"].isin(prefer_tickers)]
        pool = pd.concat([preferred, rest])

    selected = pool.head(n).copy()
    vol = selected["volatility_1y"].fillna(selected["volatility_3y"])
    vol = vol.where(vol > 0, np.nan)
    if vol.notna().sum() == 0 or vol.sum() == 0:
        weights = np.full(len(selected), 1.0 / len(selected))
    else:
        inv_vol = 1.0 / vol
        inv_vol = inv_vol.fillna(inv_vol.mean())
        weights = (inv_vol / inv_vol.sum()).values
    selected["weight_in_sleeve"] = weights
    return selected.to_dict("records")


# -------------------------------------------------------------------------
# Position cap + portfolio metrics + projection

def _enforce_position_cap(holdings: list[Holding], cap: float) -> list[Holding]:
    if not holdings:
        return holdings
    for _ in range(10):
        excess = 0.0
        for h in holdings:
            if h.weight > cap + 1e-9:
                excess += h.weight - cap
                h.weight = cap
        if excess <= 1e-9:
            break
        eligible = [h for h in holdings if h.weight < cap - 1e-9]
        eligible_total = sum(h.weight for h in eligible)
        if eligible_total > 0:
            for h in eligible:
                h.weight += excess * (h.weight / eligible_total)
        else:
            break
    return holdings


def _portfolio_metrics(holdings: list[Holding],
                       df: pd.DataFrame) -> dict[str, float | None]:
    if not holdings:
        return {"return": None, "volatility": None, "drawdown": None, "yield": None}
    metrics = df.set_index("ticker")
    rows = pd.DataFrame([{"ticker": h.ticker, "weight": h.weight}
                         for h in holdings]).set_index("ticker").join(metrics)

    def w_avg(col: str) -> float | None:
        s = rows[col].astype(float)
        w = rows["weight"].astype(float)
        mask = s.notna()
        if not mask.any():
            return None
        return float((s[mask] * w[mask]).sum() / w[mask].sum())

    # Prefer the longest available window for return/vol estimates -
    # 5y is more stable than 3y is more stable than 1y.
    return {
        "return":     w_avg("return_5y") or w_avg("return_3y") or w_avg("return_1y"),
        "volatility": w_avg("volatility_3y") or w_avg("volatility_1y"),
        "drawdown":   w_avg("max_drawdown_5y"),
        "yield":      w_avg("dividend_yield_ttm"),
    }


def _projection(capital: float, expected_return: float | None,
                expected_vol: float | None, horizon_years: int) -> Projection | None:
    if expected_return is None or horizon_years <= 0:
        return None
    sigma = expected_vol if expected_vol and expected_vol > 0 else 0.0
    T = horizon_years
    drift = (expected_return - 0.5 * sigma ** 2) * T
    spread = 1.2816 * sigma * math.sqrt(T)
    median = capital * math.exp(drift)
    low    = capital * math.exp(drift - spread)
    high   = capital * math.exp(drift + spread)
    median_return = (median / capital) ** (1 / T) - 1 if T > 0 else 0.0
    return Projection(
        horizon_years=T,
        median=round(median, 2),
        low=round(low, 2),
        high=round(high, 2),
        median_return_pct=median_return,
    )


# -------------------------------------------------------------------------
# Top-level entry point

def construct(profile: UserProfile) -> PortfolioResult:
    df = _load_full_table(profile.min_history_years)
    if df.empty:
        raise RuntimeError(
            "No analysis data found. Run pipeline + analysis first. "
            f"(Looking for instruments with at least {profile.min_history_years}y of history.)"
        )

    target_alloc = profile.target_allocation()
    counts = _allocate_holding_counts(target_alloc, profile.max_holdings)

    holdings: list[Holding] = []
    notes: list[str] = []
    metrics_idx = df.set_index("ticker")

    # Build the preferred-tickers set from income/hedged/themes preferences.
    base_prefer: set[str] = set()
    if profile.prefer_hedged:
        base_prefer |= HEDGED_TICKERS_PREFERRED
    for theme in profile.preferred_themes:
        base_prefer |= THEME_TICKERS.get(theme, set())

    for asset_class, target_weight in target_alloc.items():
        n = counts.get(asset_class, 0)
        if n == 0:
            continue

        prefer = set(base_prefer)
        if profile.prefer_income:
            prefer |= INCOME_TICKERS_PREFERRED

        pool = _asset_class_pool(df, asset_class, profile)
        selected = _select_holdings(pool, n, profile, prefer)

        if not selected:
            notes.append(
                f"No instruments matched the screen for {asset_class.value} "
                f"(target {target_weight:.0%}). Try relaxing your filters."
            )
            continue

        for row in selected:
            t = row["ticker"]
            sharpe = row.get("sharpe_used")
            sharpe_text = (f"Sharpe {sharpe:.2f}"
                           if sharpe is not None and not pd.isna(sharpe)
                           else "no Sharpe")
            rationale = (
                f"Top-ranked in {asset_class.value} sleeve ({sharpe_text}). "
                f"Sleeve target {target_weight:.0%}; "
                f"inverse-volatility weighted within the sleeve."
            )
            holdings.append(Holding(
                ticker=t,
                name=row.get("name", t),
                asset_class=asset_class.value,
                weight=target_weight * row["weight_in_sleeve"],
                dollars=0.0,
                sharpe_used=None if pd.isna(sharpe) else float(sharpe),
                rationale=rationale,
                return_1y=_safe_float(metrics_idx.loc[t, "return_1y"]) if t in metrics_idx.index else None,
                return_3y=_safe_float(metrics_idx.loc[t, "return_3y"]) if t in metrics_idx.index else None,
                return_5y=_safe_float(metrics_idx.loc[t, "return_5y"]) if t in metrics_idx.index else None,
                volatility_1y=_safe_float(metrics_idx.loc[t, "volatility_1y"]) if t in metrics_idx.index else None,
                max_drawdown_5y=_safe_float(metrics_idx.loc[t, "max_drawdown_5y"]) if t in metrics_idx.index else None,
                dividend_yield_ttm=_safe_float(metrics_idx.loc[t, "dividend_yield_ttm"]) if t in metrics_idx.index else None,
            ))

    # Combine duplicates.
    by_ticker: dict[str, Holding] = {}
    for h in holdings:
        if h.ticker in by_ticker:
            by_ticker[h.ticker].weight += h.weight
        else:
            by_ticker[h.ticker] = h
    holdings = list(by_ticker.values())

    holdings = _enforce_position_cap(holdings, profile.max_position_size)

    total = sum(h.weight for h in holdings)
    if total > 0:
        for h in holdings:
            h.weight /= total
            h.dollars = round(h.weight * profile.capital, 2)

    pm = _portfolio_metrics(holdings, df)
    proj = _projection(profile.capital, pm["return"], pm["volatility"],
                       profile.horizon_years)

    realised: dict[str, float] = {}
    for h in holdings:
        realised[h.asset_class] = realised.get(h.asset_class, 0) + h.weight

    holdings.sort(key=lambda h: h.weight, reverse=True)

    return PortfolioResult(
        holdings=holdings,
        target_allocation={k.value: v for k, v in target_alloc.items()},
        realised_allocation=realised,
        expected_return=pm["return"],
        expected_volatility=pm["volatility"],
        expected_max_drawdown=pm["drawdown"],
        expected_dividend_yield=pm["yield"],
        capital=profile.capital,
        notes=notes,
        projection=proj,
    )


def _safe_float(x) -> float | None:
    try:
        if x is None or pd.isna(x):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None
