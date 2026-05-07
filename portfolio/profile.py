"""User profile + risk-profile presets.

A `UserProfile` captures everything the portfolio constructor needs to
know about the user. Each `RiskProfile` preset declares a target asset
allocation (the % of capital that should sit in each asset class).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RiskProfile(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    GROWTH = "growth"
    AGGRESSIVE = "aggressive"


class GeoTilt(str, Enum):
    AU_ONLY = "au_only"             # ignore international entirely
    AU_HEAVY = "au_heavy"           # boost AU exposure
    NEUTRAL = "neutral"
    GLOBAL_HEAVY = "global_heavy"   # boost international
    GLOBAL_ONLY = "global_only"     # ignore AU equity entirely


class AssetClass(str, Enum):
    AU_STOCKS = "AU stocks"
    AU_EQUITY = "AU equity"
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


AU_CLASSES = {AssetClass.AU_STOCKS, AssetClass.AU_EQUITY, AssetClass.AU_BONDS,
              AssetClass.AU_PROPERTY}
GLOBAL_CLASSES = {AssetClass.GLOBAL_EQUITY, AssetClass.US_EQUITY,
                  AssetClass.EM_EQUITY, AssetClass.GLOBAL_BONDS,
                  AssetClass.GLOBAL_PROPERTY, AssetClass.THEMATIC}


# Themes that map to specific tickers (used by the preferred_themes field).
THEME_TICKERS: dict[str, set[str]] = {
    "cybersecurity":   {"HACK.AX"},
    "robotics_ai":     {"ROBO.AX"},
    "esg":             {"ETHI.AX", "FAIR.AX"},
    "healthcare":      {"DRUG.AX", "IXJ.AX"},
    "agriculture":     {"FOOD.AX"},
    "crypto":          {"CRYP.AX"},
    "income":          {"INCM.AX", "VHY.AX", "IHD.AX", "HBRD.AX"},
    "small_caps":      {"SMLL.AX"},
    "banks":           {"MVB.AX", "QFN.AX", "OZF.AX"},
    "resources":       {"OZR.AX"},
    "infrastructure":  {"GLIN.AX"},
    "gold":            {"GOLD.AX", "QAU.AX"},
}


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
    capital: float
    risk_profile: RiskProfile
    horizon_years: int

    # Tilts and screens.
    prefer_income: bool = False
    esg_only: bool = False
    etfs_only: bool = False
    exclude_sectors: tuple[str, ...] = ()
    include_only_sectors: tuple[str, ...] = ()  # NEW: positive sector filter
    exclude_tickers: tuple[str, ...] = ()       # NEW: skip specific tickers
    preferred_themes: tuple[str, ...] = ()      # NEW: theme keys (see THEME_TICKERS)
    geo_tilt: GeoTilt = GeoTilt.NEUTRAL
    prefer_hedged: bool = False
    min_dividend_yield: float = 0.0
    max_volatility: Optional[float] = None
    min_history_years: int = 3                  # NEW: data quality filter (default 3y)

    # Holdings count + concentration controls.
    max_holdings: int = 8
    max_position_size: float = 0.15

    def __post_init__(self):
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.horizon_years < 0:
            raise ValueError("horizon_years cannot be negative")
        if not 0 < self.max_position_size <= 1.0:
            raise ValueError("max_position_size must be in (0, 1]")
        if not 3 <= self.max_holdings <= 30:
            raise ValueError("max_holdings must be between 3 and 30")
        if self.min_dividend_yield < 0:
            raise ValueError("min_dividend_yield cannot be negative")
        if self.max_volatility is not None and self.max_volatility <= 0:
            raise ValueError("max_volatility must be positive if set")
        if not 1 <= self.min_history_years <= 10:
            raise ValueError("min_history_years must be between 1 and 10")

    def target_allocation(self) -> dict[AssetClass, float]:
        """Asset-class targets, with horizon and geographic-tilt overlays."""
        base = dict(TARGET_ALLOCATIONS[self.risk_profile])

        # Horizon override: short horizons force more defensive holdings.
        defensive = {AssetClass.AU_BONDS, AssetClass.GLOBAL_BONDS, AssetClass.CASH}
        defensive_weight = sum(base.get(ac, 0) for ac in defensive)
        target_min_defensive = 0.0
        if self.horizon_years < 2:
            target_min_defensive = 0.6
        elif self.horizon_years < 5:
            target_min_defensive = 0.3

        if defensive_weight < target_min_defensive and target_min_defensive > 0:
            shortfall = target_min_defensive - defensive_weight
            equity = {ac: w for ac, w in base.items() if ac not in defensive}
            equity_total = sum(equity.values())
            if equity_total > 0:
                scale = max(0.0, (equity_total - shortfall) / equity_total)
                for ac in equity:
                    base[ac] *= scale
                base[AssetClass.CASH] = base.get(AssetClass.CASH, 0) + shortfall

        # Geographic-tilt overlay.
        if self.geo_tilt == GeoTilt.AU_ONLY:
            for ac in list(base.keys()):
                if ac in GLOBAL_CLASSES:
                    base[ac] = 0
        elif self.geo_tilt == GeoTilt.GLOBAL_ONLY:
            for ac in list(base.keys()):
                if ac in AU_CLASSES and ac != AssetClass.CASH:
                    base[ac] = 0
        elif self.geo_tilt == GeoTilt.AU_HEAVY:
            for ac in list(base.keys()):
                if ac in AU_CLASSES:
                    base[ac] *= 1.30
                elif ac in GLOBAL_CLASSES:
                    base[ac] *= 0.75
        elif self.geo_tilt == GeoTilt.GLOBAL_HEAVY:
            for ac in list(base.keys()):
                if ac in AU_CLASSES:
                    base[ac] *= 0.75
                elif ac in GLOBAL_CLASSES:
                    base[ac] *= 1.30

        # Drop near-zero entries.
        base = {ac: w for ac, w in base.items() if w > 0.001}

        total = sum(base.values())
        return {ac: w / total for ac, w in base.items()}
