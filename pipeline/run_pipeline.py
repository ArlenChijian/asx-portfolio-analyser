"""Orchestrator: run the full data pipeline end-to-end.

Usage (from the project root with the venv active):

    python -m pipeline.run_pipeline             # full run
    python -m pipeline.run_pipeline --dry-run   # build universe, don't fetch
    python -m pipeline.run_pipeline --limit 10  # only fetch first 10 tickers (testing)

The `-m pipeline.run_pipeline` form treats `pipeline` as a Python package
and runs `run_pipeline.py` as its main module. This is the right way to
run a script that has internal `from pipeline import ...` imports.
"""
from __future__ import annotations

import argparse
import logging
import sys

from pipeline import storage, universe, fetch


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ASX data pipeline.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the universe and print it, but don't fetch from Yahoo.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only fetch the first N tickers (handy for testing).")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    configure_logging(args.verbose)
    log = logging.getLogger("pipeline")

    log.info("Initialising database schema...")
    storage.init_schema()

    log.info("Building universe...")
    u = universe.build_universe()
    log.info("Universe preview:\n%s", u.head(10).to_string(index=False))

    if args.dry_run:
        log.info("Dry run requested; not fetching market data. Exiting.")
        return 0

    if args.limit:
        log.info("Limit applied: fetching first %d tickers only.", args.limit)
        u = u.head(args.limit)

    fetch.fetch_universe(u)
    log.info("Pipeline complete. Database: %s", storage.DB_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
