"""Orchestrator: compute and store per-instrument metrics.

Usage (from the project root with the venv active):

    python -m analysis.run_analysis
    python -m analysis.run_analysis --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys

from analysis import compute


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute per-instrument metrics.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    log = logging.getLogger("analysis")
    df = compute.compute_all()

    if df.empty:
        log.error("No metrics computed; check that the price data is populated.")
        return 1

    log.info("Sample metrics:\n%s", df.head(10).to_string(index=False))
    log.info("Metrics stats:")
    summary_cols = ["return_1y", "return_5y", "volatility_1y", "sharpe_1y",
                    "max_drawdown_5y", "beta_5y", "dividend_yield_ttm"]
    log.info("\n%s", df[summary_cols].apply(lambda c: c.astype(float)).describe().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
