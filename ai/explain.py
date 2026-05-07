"""Plain-English portfolio explanation generator.

Takes a UserProfile-shaped dict and a PortfolioResult-shaped dict, asks
Claude to produce a 2-3 paragraph explanation that:
    - Connects the user's stated profile to the actual allocation,
    - Calls out the most notable holdings and why they were picked,
    - Names the key risks (drawdown, volatility, concentration),
    - Always disclaims that this is educational, not advice.

Designed to read like an analyst's summary - factual, non-sales-y, ~200 words.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ai.client import DEFAULT_MODEL, get_client

log = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are an investment analyst writing a brief explanation \
of a candidate portfolio for a retail user. The portfolio was constructed \
deterministically from the user's stated profile - your job is to translate \
the result into plain English they can understand.

Write 2-3 short paragraphs (around 180-260 words total). Use this structure:

1. Lead with the user's situation and how the portfolio reflects it. Reference \
specific inputs (e.g. "your 10-year horizon and growth tilt led to a 70% equity \
allocation").

2. Pick out 2-3 of the most interesting holdings or design choices and explain \
why they made the cut (e.g. "VHY was selected for the Australian-equity sleeve \
because it tilts toward dividend payers, matching your income preference").

3. Name the key risks honestly: the historical drawdown, the volatility, and the \
fact that the projection band is wide. Close with one sentence reminding the user \
this is educational analysis, not financial advice, and that their situation may \
require an AFSL adviser.

Rules:
- Plain English. No jargon without explanation.
- Be honest about uncertainty. Do not promise outcomes.
- Do not recommend the user act on this. Frame it as "here's what the screen produced".
- Do not use bullet lists. Use flowing paragraphs.
- Do not use a heading or title.
- Write in the second person ('you', 'your')."""


def explain(profile: dict[str, Any], result: dict[str, Any]) -> str | None:
    """Return a plain-English explanation of the portfolio result.

    Returns None if the AI client is unavailable or the call fails.
    Both inputs should be JSON-serializable dicts (the API request and
    response shapes used by web/server.py work directly).
    """
    client = get_client()
    if client is None:
        return None

    # Compact the result a bit so we don't waste tokens on noise.
    holdings = result.get("holdings", [])
    summary = {
        "user_profile": profile,
        "expected_return": result.get("expected_return"),
        "expected_volatility": result.get("expected_volatility"),
        "expected_max_drawdown": result.get("expected_max_drawdown"),
        "expected_dividend_yield": result.get("expected_dividend_yield"),
        "projection": result.get("projection"),
        "realised_allocation": result.get("realised_allocation"),
        "holdings": [
            {
                "ticker": h.get("ticker"),
                "name": h.get("name"),
                "asset_class": h.get("asset_class"),
                "weight": round(h.get("weight", 0), 4),
                "sharpe_used": h.get("sharpe_used"),
                "return_5y": h.get("return_5y"),
                "dividend_yield_ttm": h.get("dividend_yield_ttm"),
            }
            for h in holdings
        ],
        "notes": result.get("notes", []),
    }

    user_msg = (
        "Explain this portfolio to the user. Their profile and the "
        "constructed result are below as JSON.\n\n"
        f"```json\n{json.dumps(summary, indent=2)}\n```"
    )

    try:
        msg = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=800,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("explain() call failed: %s", exc)
        return None

    parts: list[str] = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    text = "".join(parts).strip()
    return text or None
