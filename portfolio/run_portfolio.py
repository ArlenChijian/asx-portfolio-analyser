"""CLI test harness: run a sample user profile and print the candidate portfolio.

Usage:
    python -m portfolio.run_portfolio
    python -m portfolio.run_portfolio --capital 50000 --risk growth --horizon 10
    python -m portfolio.run_portfolio --capital 100000 --risk conservative --horizon 3 --income
"""
from __future__ import annotations

import argparse
import logging
import sys

from portfolio.profile import RiskProfile, UserProfile
from portfolio.construct import construct


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build a candidate portfolio.")
    p.add_argument("--capital", type=float, default=20000.0,
                   help="Starting capital in AUD (default 20000).")
    p.add_argument("--risk", default="balanced",
                   choices=[r.value for r in RiskProfile],
                   help="Risk profile (default balanced).")
    p.add_argument("--horizon", type=int, default=10,
                   help="Investment horizon in years (default 10).")
    p.add_argument("--income", action="store_true", help="Tilt toward income.")
    p.add_argument("--esg", action="store_true", help="ESG-only screen.")
    p.add_argument("--etfs-only", action="store_true",
                   help="Exclude individual stocks.")
    p.add_argument("--exclude", action="append", default=[],
                   help="Sector to exclude (repeatable).")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)
    configure_logging(args.verbose)

    profile = UserProfile(
        capital=args.capital,
        risk_profile=RiskProfile(args.risk),
        horizon_years=args.horizon,
        prefer_income=args.income,
        esg_only=args.esg,
        etfs_only=args.etfs_only,
        exclude_sectors=tuple(args.exclude),
    )

    print(f"\nBuilding portfolio for: ${profile.capital:,.0f} | "
          f"{profile.risk_profile.value} | {profile.horizon_years}y horizon")
    if profile.prefer_income: print("  + income tilt")
    if profile.esg_only:      print("  + ESG-only screen")
    if profile.etfs_only:     print("  + ETFs only")
    if profile.exclude_sectors: print(f"  + excluded sectors: {profile.exclude_sectors}")

    result = construct(profile)

    print(f"\nTarget asset allocation:")
    for ac, w in result.target_allocation.items():
        print(f"  {ac:<20s} {w:>6.1%}")

    print(f"\nRealised allocation:")
    for ac, w in result.realised_allocation.items():
        print(f"  {ac:<20s} {w:>6.1%}")

    print(f"\nHoldings ({len(result.holdings)} total):")
    print(f"  {'Ticker':<10s} {'Asset class':<18s} {'Weight':>7s} {'$AUD':>10s}  Name")
    for h in result.holdings:
        print(f"  {h.ticker:<10s} {h.asset_class:<18s} "
              f"{h.weight:>6.1%} {h.dollars:>10,.0f}  {h.name}")

    print(f"\nPortfolio expected metrics (weighted average of holdings):")
    def fmt(x, pct=True):
        if x is None: return "n/a"
        return f"{x:.2%}" if pct else f"{x:.2f}"
    print(f"  Expected return    {fmt(result.expected_return)}")
    print(f"  Expected volatility {fmt(result.expected_volatility)}")
    print(f"  Expected drawdown   {fmt(result.expected_max_drawdown)}")
    print(f"  Expected yield (TTM) {fmt(result.expected_dividend_yield)}")

    if result.notes:
        print(f"\nNotes:")
        for n in result.notes:
            print(f"  - {n}")

    print("\n*Educational analysis only. Not financial advice.*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
