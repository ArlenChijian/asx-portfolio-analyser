"""User profile + risk-profile presets.

A `UserProfile` captures everything the portfolio constructor needs to
know about the user. Each `RiskProfile` preset declares a target asset
allocation (the % of capital that should sit in each asset class), which
is the dominant determinant of long-run risk-adjusted returns according
to the asset-allocation literature (Brinson, Hood, Beebower 1986).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RiskProfile(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    GROWTH = "growth"
    AGGRESSIVE = "aggressive"


# Asset classes used by the screener and allocator. These match the
# `sector` field used for ETFs in `pipeline.universe.CURATED_ETFS`,
# plus a synthetic "AU equity (stocks)" class for individual ASX shares.
class AssetClass(str, Enum):
    AU_STOCKS = "AU stocks"        # individual ASX-listed equities
    AU_EQUITY = "AU equity"        # broad AU equity ETFs (VAS, IOZ, etc.)
    GLOBAL_EQUITY = "Global equity"
    US_EQUITY = "US equity"
    EM_EQUITY = "EM equity"
    THEMATIC = "Thematic"
    AU_BONDS = "AU bonds"
    GLOBAL_BONDS = "Global bonds"
    CASH = "Cash"
    AU_PROPERTY = "AU property"
    GLOBAL_PROPERTY = "Global property"
    COMMODITIES = "Commodities"


# Target asset allocations by risk profile. Numbers must sum to 1.0
# within each profile. These are loosely modelled on Vanguard's
# lifecycle fund glide path (more equity for higher risk tolerance,
# more bonds/cash for lower).
TARGET_ALLOCATIONS: dict[RiskProfile, dict[AssetClass, float]] = {
    RiskProfile.CONSERVATIVE: {
        AssetClass.AU_EQUITY:       0.18,
        AssetClass.GLOBAL_EQUITY:   0.10,
        AssetClass.US_EQUITY:       0.04,
        AssetClass.AU_BONDS:        0.30,
        AssetClass.GLOBAL_BONDS:    0.15,
        AssetClass.CASH:            0.18,
        AssetClass.AU_PROPERTY:     0.05,
    },
    RiskProfile.BALANCED: {
        AssetClass.AU_EQUITY:       0.22,
        AssetClass.AU_STOCKS:       0.10,
        AssetClass.GLOBAL_EQUITY:   0.18,
        AssetClass.US_EQUITY:       0.10,
        AssetClass.EM_EQUITY:       0.03,
        AssetClass.AU_BONDS:        0.18,
        AssetClass.GLOBAL_BONDS:    0.07,
        AssetClass.CASH:            0.07,
        AssetClass.AU_PROPERTY:     0.05,
    },
    RiskProfile.GROWTH: {
        AssetClass.AU_EQUITY:       0.22,
        AssetClass.AU_STOCKS:       0.18,
        AssetClass.GLOBAL_EQUITY:   0.20,
        AssetClass.US_EQUITY:       0.15,
        AssetClass.EM_EQUITY:       0.05,
        AssetClass.THEMATIC:        0.05,
        AssetClass.AU_BONDS:        0.05,
        AssetClass.CASH:            0.03,
        AssetClass.AU_PROPERTY:     0.05,
        AssetClass.COMMODITIES:     0.02,
    },
    RiskProfile.AGGRESSIVE: {
        AssetClass.AU_EQUITY:       0.18,
        AssetClass.AU_STOCKS:       0.30,
        AssetClass.GLOBAL_EQUITY:   0.20,
        AssetClass.US_EQUITY:       0.18,
        AssetClass.EM_EQUITY:       0.07,
        AssetClass.THEMATIC:        0.05,
        AssetClass.COMMODITIES:     0.02,
    },
}


@dataclass
class UserProfile:
    """Everything needed to construct a candidate portfolio."""
    capital: float                          # AUD
    risk_profile: RiskProfile
    horizon_years: int                      # how long they intend to hold

    # Preferences (all optional; defaults match a "no preferences" user).
    prefer_income: bool = False             # tilt toward high-yield instruments
    esg_only: bool = False                  # only ESG-tilted instruments
    etfs_only: bool = False                 # exclude individual stocks
    exclude_sectors: tuple[str, ...] = ()   # GICS sector names to exclude
    max_position_size: float = 0.10         # cap any single holding at 10%

    def __post_init__(self):
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.horizon_years < 0:
            raise ValueError("horizon_years cannot be negative")
        if not 0 < self.max_position_size <= 1.0:
            raise ValueError("max_position_size must be in (0, 1]")

    def target_allocation(self) -> dict[AssetClass, float]:
        """Asset-class targets for this profile, with horizon adjustment.

        Short horizons reduce equity exposure and increase cash/bonds,
        regardless of stated risk profile, because short-horizon equity
        exposure is dominated by drawdown risk.
        """
        base = dict(TARGET_ALLOCATIONS[self.risk_profile])

        # Horizon override: under 5 years, force at least 30% defensive.
        # Under 2 years, force at least 60% defensive.
        defensive = {AssetClass.AU_BONDS, AssetClass.GLOBAL_BONDS, AssetClass.CASH}
        defensive_weight = sum(base.get(ac, 0) for ac in defensive)
        target_min_defensive = 0.0
        if self.horizon_years < 2:
            target_min_defensive = 0.6
        elif self.horizon_years < 5:
            target_min_defensive = 0.3

        if defensive_weight < target_min_defensive and target_min_defensive > 0:
            shortfall = target_min_defensive - defensive_weight
            # Scale equity down proportionally and feed it into cash.
            equity = {ac: w for ac, w in base.items() if ac not in defensive}
            equity_total = sum(equity.values())
            if equity_total > 0:
                scale = max(0.0, (equity_total - shortfall) / equity_total)
                for ac in equity:
                    base[ac] *= scale
                base[AssetClass.CASH] = base.get(AssetClass.CASH, 0) + shortfall

        # Normalise to 1.0 (rounding/scaling can drift).
        total = sum(base.values())
        return {ac: w / total for ac, w in base.items() if w > 0}
