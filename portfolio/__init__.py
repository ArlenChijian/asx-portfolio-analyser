"""Portfolio construction layer.

Reads from the SQLite database populated by `pipeline` and analysed by
`analysis`. Given a `UserProfile`, returns a candidate portfolio:
a list of (ticker, weight, dollar_amount, rationale) entries plus the
expected portfolio-level metrics.

This is *educational analysis*, not financial advice. Every step is
deterministic, documented, and inspectable.
"""
