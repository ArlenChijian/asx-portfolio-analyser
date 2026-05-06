"""Analytics layer.

Pure functions in `metrics.py`; pipeline-orchestrator code in `compute.py`
and `run_analysis.py`. The analytics layer reads from the SQLite database
created by the data pipeline and writes results back to a `metrics` table.
"""
