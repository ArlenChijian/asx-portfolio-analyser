"""Portfolio constructor: UserProfile -> candidate portfolio.

Algorithm:
    1. Build the target asset allocation from the user's risk profile
       and horizon (delegated to UserProfile.target_allocation).
    2. For each asset class with a non-zero target, screen the universe
       to candidate instruments (filters: ETFs-only, ESG, sector
       exclusions, etc.).
    3. Within each asset class:
         - rank candidates by Sharpe ratio (3y if available, else 1y)
         - take the top N (N=3 by default)
         - weight them by inverse volatility ("risk parity" within sleeve)
    4. Apply the asset-class target weight on top.
    5. Cap any single holding at user.max_position_size; redistribute.
    6. Compute portfolio-level expected return, volatility, drawdown.

The output is a `PortfolioResult` containing one `Holding` per ticker
plus the portfolio-level summary, ready for the website to render.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pipeline import storage
from portfolio.profile import AssetClass, RiskProfile, UserProfile

log = logging.getLogger(__name__)

# Number of instruments to hold per asset-class sleeve.
HOLDINGS_PER_SLEEVE = 3

# Minimum Sharpe ratio to keep an instrument in consideration. Removes
# the truly underperforming names from the screen.
MIN_SHARPE = -1.5  # very lenient; really just removing extreme cases

# Tickers we consider "ESG" for the esg_only filter. In a fuller version
# we'd source this from a third-party ESG database; for the project
# version we use the explicitly ESG-themed ETFs.
ESG_TICKERS = {"ETHI.AX", "FAIR.AX"}

# Tickers preferred for income (high-yield).
INCOME_TICKERS_PREFERRED = {"VHY.AX"}


@dataclass
class Holding:
    ticker: str
    name: str
    asset_class: str
    weight: float                # fraction of portfolio (0..1)
    dollars: float
    sharpe_used: float | None
    rationale: str


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

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([h.__dict__ for h in self.holdings])


def _load_full_table() -> pd.DataFrame:
    """Inner-join instruments + metrics into a single DataFrame."""
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
            """,
            conn,
        )
    # Coerce numeric columns (they're stored as object via _na_to_none).
    num_cols = [c for c in df.columns
                if c not in ("ticker", "name", "sector", "type")]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _asset_class_pool(df: pd.DataFrame, asset_class: AssetClass,
                      profile: UserProfile) -> pd.DataFrame:
    """Return the pool of candidates for one asset class after screening."""
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

    if profile.exclude_sectors:
        pool = pool[~pool["sector"].isin(profile.exclude_sectors)]

    # Pick the best Sharpe we have. Prefer 3y, fall back to 1y.
    pool["sharpe_used"] = pool["sharpe_3y"].fillna(pool["sharpe_1y"])
    pool = pool[pool["sharpe_used"].notna()]
    pool = pool[pool["sharpe_used"] >= MIN_SHARPE]
    return pool.sort_values("sharpe_used", ascending=False)


def _select_holdings(pool: pd.DataFrame, sleeve_target: float,
                     profile: UserProfile,
                     prefer_tickers: set[str] | None = None) -> list[dict]:
    """Pick top instruments from a pool, weight by inverse-volatility.

    Returns a list of dicts {ticker, weight_in_sleeve, sharpe_used, ...}.
    """
    if pool.empty:
        return []

    # If we have preferred tickers (e.g. VHY for income), promote them
    # to the top of the list.
    if prefer_tickers:
        preferred = pool[pool["ticker"].isin(prefer_tickers)]
        rest = pool[~pool["ticker"].isin(prefer_tickers)]
        pool = pd.concat([preferred, rest])

    selected = pool.head(HOLDINGS_PER_SLEEVE).copy()

    # Inverse-volatility weights ("risk parity" within sleeve).
    vol = selected["volatility_1y"].fillna(selected["volatility_3y"])
    vol = vol.where(vol > 0, np.nan)
    if vol.notna().sum() == 0 or vol.sum() == 0:
        # No volatility data: fall back to equal weight.
        weights = np.full(len(selected), 1.0 / len(selected))
    else:
        inv_vol = 1.0 / vol
        inv_vol = inv_vol.fillna(inv_vol.mean())
        weights = (inv_vol / inv_vol.sum()).values
    selected["weight_in_sleeve"] = weights
    return selected.to_dict("records")


def _enforce_position_cap(holdings: list[Holding], cap: float) -> list[Holding]:
    """Cap each holding at `cap` and redistribute the excess pro-rata."""
    if not holdings:
        return holdings
    excess = 0.0
    for h in holdings:
        if h.weight > cap:
            excess += h.weight - cap
            h.weight = cap
    if excess <= 0:
        return holdings
    # Distribute excess to holdings still below the cap, pro-rata to their weight.
    eligible = [h for h in holdings if h.weight < cap]
    eligible_total = sum(h.weight for h in eligible)
    if eligible_total > 0:
        for h in eligible:
            h.weight += excess * (h.weight / eligible_total)
    return holdings


def _portfolio_metrics(holdings: list[Holding],
                       df: pd.DataFrame) -> dict[str, float | None]:
    """Weighted-average expected return, volatility, drawdown, yield.

    Note: weighted-average volatility is an upper-bound proxy; the true
    portfolio volatility depends on the covariance matrix. We use the
    upper bound for transparency in the educational tool.
    """
    if not holdings:
        return {"return": None, "volatility": None,
                "drawdown": None, "yield": None}
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

    return {
        "return":     w_avg("return_3y") or w_avg("return_1y"),
        "volatility": w_avg("volatility_3y") or w_avg("volatility_1y"),
        "drawdown":   w_avg("max_drawdown_5y"),
        "yield":      w_avg("dividend_yield_ttm"),
    }


def construct(profile: UserProfile) -> PortfolioResult:
    """Top-level entry point. UserProfile -> PortfolioResult."""
    df = _load_full_table()
    if df.empty:
        raise RuntimeError(
            "No analysis data found. Run `python -m pipeline.run_pipeline` "
            "and `python -m analysis.run_analysis` first."
        )

    target_alloc = profile.target_allocation()
    holdings: list[Holding] = []
    notes: list[str] = []

    for asset_class, target_weight in target_alloc.items():
        prefer = INCOME_TICKERS_PREFERRED if profile.prefer_income else None
        pool = _asset_class_pool(df, asset_class, profile)
        selected = _select_holdings(pool, target_weight, profile, prefer)
        if not selected:
            notes.append(
                f"No instruments matched the screen for {asset_class.value} "
                f"(target {target_weight:.0%}); allocation reassigned to cash."
            )
            # Push the unfilled weight to cash if cash exists, else AU bonds.
            fallback_cls = (AssetClass.CASH if AssetClass.CASH in target_alloc
                            else AssetClass.AU_BONDS)
            if fallback_cls in target_alloc:
                target_alloc[fallback_cls] = target_alloc.get(fallback_cls, 0) + target_weight
            continue

        for row in selected:
            sharpe = row.get("sharpe_used")
            sharpe_text = f"Sharpe {sharpe:.2f}" if sharpe is not None and not pd.isna(sharpe) else "no Sharpe"
            rationale = (
                f"Top-ranked instrument in {asset_class.value} sleeve "
                f"({sharpe_text}). Sleeve target {target_weight:.0%}, "
                f"weighted by inverse-volatility within the sleeve."
            )
            holdings.append(Holding(
                ticker=row["ticker"],
                name=row.get("name", row["ticker"]),
                asset_class=asset_class.value,
                weight=target_weight * row["weight_in_sleeve"],
                dollars=0.0,  # filled below
                sharpe_used=None if pd.isna(sharpe) else float(sharpe),
                rationale=rationale,
            ))

    # Combine duplicate tickers (rare, but possible if a ticker fits two sleeves).
    by_ticker: dict[str, Holding] = {}
    for h in holdings:
        if h.ticker in by_ticker:
            by_ticker[h.ticker].weight += h.weight
        else:
            by_ticker[h.ticker] = h
    holdings = list(by_ticker.values())

    holdings = _enforce_position_cap(holdings, profile.max_position_size)

    # Renormalise to 1.0 in case rounding pushed us away.
    total = sum(h.weight for h in holdings)
    if total > 0:
        for h in holdings:
            h.weight /= total
            h.dollars = round(h.weight * profile.capital, 2)

    pm = _portfolio_metrics(holdings, df)

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
    )
