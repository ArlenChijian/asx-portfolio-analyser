"""CLI test harness: run a sample user profile end-to-end.

Examples:
    python -m portfolio.run_portfolio
    python -m portfolio.run_portfolio --capital 50000 --risk growth --horizon 10
    python -m portfolio.run_portfolio --capital 100000 --risk conservative --horizon 3 --income
    python -m portfolio.run_portfolio --max-holdings 5 --geo au_heavy
"""
from __future__ import annotations

import argparse
import logging
import sys

from portfolio.profile import GeoTilt, RiskProfile, UserProfile
from portfolio.construct import construct


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build a candidate portfolio.")
    p.add_argument("--capital", type=float, default=20000.0)
    p.add_argument("--risk", default="balanced",
                   choices=[r.value for r in RiskProfile])
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--income", action="store_true")
    p.add_argument("--esg", action="store_true")
    p.add_argument("--etfs-only", action="store_true")
    p.add_argument("--hedged", action="store_true",
                   help="Prefer AUD-hedged international ETFs.")
    p.add_argument("--geo", default="neutral",
                   choices=[g.value for g in GeoTilt])
    p.add_argument("--min-yield", type=float, default=0.0,
                   help="Minimum dividend yield (e.g. 0.03 for 3%).")
    p.add_argument("--max-vol", type=float, default=None,
                   help="Maximum annualised volatility (e.g. 0.25 for 25%).")
    p.add_argument("--max-holdings", type=int, default=8)
    p.add_argument("--max-position", type=float, default=0.15)
    p.add_argument("--exclude", action="append", default=[])
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
        prefer_hedged=args.hedged,
        geo_tilt=GeoTilt(args.geo),
        min_dividend_yield=args.min_yield,
        max_volatility=args.max_vol,
        max_holdings=args.max_holdings,
        max_position_size=args.max_position,
        exclude_sectors=tuple(args.exclude),
    )

    print(f"\nProfile: ${profile.capital:,.0f} | {profile.risk_profile.value} | "
          f"{profile.horizon_years}y | geo={profile.geo_tilt.value} | "
          f"max-holdings={profile.max_holdings}")

    result = construct(profile)

    print(f"\nHoldings ({len(result.holdings)}):")
    print(f"  {'Ticker':<10s} {'Asset class':<16s} {'Weight':>7s} {'$AUD':>10s}  {'1y':>7s} {'5y':>7s}  Name")
    for h in result.holdings:
        def pct(x): return f"{x*100:.1f}%" if x is not None else "  n/a"
        print(f"  {h.ticker:<10s} {h.asset_class:<16s} "
              f"{h.weight*100:>6.0f}% {h.dollars:>10,.0f}  "
              f"{pct(h.return_1y):>7s} {pct(h.return_5y):>7s}  {h.name}")

    print(f"\nExpected:  return {pct_(result.expected_return)}  "
          f"vol {pct_(result.expected_volatility)}  "
          f"drawdown {pct_(result.expected_max_drawdown)}  "
          f"yield {pct_(result.expected_dividend_yield)}")

    if result.projection:
        pj = result.projection
        print(f"\nProjection over {pj.horizon_years}y (lognormal model):")
        print(f"  Pessimistic (P10):  ${pj.low:>12,.0f}")
        print(f"  Median       (P50): ${pj.median:>12,.0f}   ({pj.median_return_pct*100:.2f}% CAGR)")
        print(f"  Optimistic   (P90): ${pj.high:>12,.0f}")

    if result.notes:
        print("\nNotes:")
        for n in result.notes:
            print(f"  - {n}")

    print("\n*Educational analysis only. Not financial advice.*")
    return 0


def pct_(x):
    return f"{x*100:.2f}%" if x is not None else "n/a"


if __name__ == "__main__":
    raise SystemExit(main())
