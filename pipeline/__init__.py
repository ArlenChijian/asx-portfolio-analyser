"""ASX data pipeline.

Sub-modules:
    universe  - defines the set of instruments to track (ASX 200 + ETFs).
    storage   - SQLite read/write helpers.
    fetch     - downloads prices, dividends, and metadata from Yahoo Finance.
    run_pipeline - orchestrator script that runs the full pipeline end-to-end.
"""
