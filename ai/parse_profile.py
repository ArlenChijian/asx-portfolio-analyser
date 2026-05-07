"""Free-text -> UserProfile fields, via Claude tool use.

Uses Anthropic's tool-use API with a forced tool call. The model has
exactly one tool available (`set_profile`) and is required to call it.
The tool's input_schema is the structured shape we want, so the model
is constrained to produce JSON matching that schema.

Returned fields are deliberately a *partial* mapping: anything the user
didn't mention is left absent so the form keeps its defaults.
"""
from __future__ import annotations

import logging
from typing import Any

from ai.client import DEFAULT_MODEL, get_client

log = logging.getLogger(__name__)


_PROFILE_TOOL = {
    "name": "set_profile",
    "description": (
        "Set the user's investment profile based on what they've described. "
        "Only set fields the user has actually mentioned or strongly implied. "
        "Leave a field unset if you're not confident."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "capital":         {"type": "number",  "minimum": 100,
                                "description": "Starting capital in AUD."},
            "risk_profile":    {"type": "string",
                                "enum": ["conservative", "balanced", "growth", "aggressive"],
                                "description": (
                                    "Map: 'safe/cautious/scared of loss' -> conservative; "
                                    "'mix/middle/some risk' -> balanced; "
                                    "'comfortable with risk/long horizon' -> growth; "
                                    "'high risk/young/aggressive' -> aggressive.")},
            "horizon_years":   {"type": "integer", "minimum": 0, "maximum": 60,
                                "description": (
                                    "Years until they want the money. If they mention "
                                    "an age now and a target age, compute the difference. "
                                    "If they say 'long-term' assume 20.")},
            "prefer_income":   {"type": "boolean",
                                "description": "True if they mention dividends, income, retirement spending."},
            "esg_only":        {"type": "boolean",
                                "description": "True if they mention ESG, ethical, sustainable, climate."},
            "etfs_only":       {"type": "boolean",
                                "description": "True if they say ETFs only / no individual stocks."},
            "geo_tilt":        {"type": "string",
                                "enum": ["au_only", "au_heavy", "neutral", "global_heavy", "global_only"],
                                "description": (
                                    "au_only = only Australian equities; "
                                    "au_heavy = boost AU; neutral = default; "
                                    "global_heavy = boost international; "
                                    "global_only = only international equities. "
                                    "Phrases like 'mainly AUD stocks' or 'only Australian' map to au_only or au_heavy.")},
            "prefer_hedged":   {"type": "boolean",
                                "description": "True if they mention currency hedging or AUD-hedged."},
            "min_dividend_yield": {"type": "number", "minimum": 0, "maximum": 0.20,
                                   "description": "Minimum dividend yield as a fraction (e.g. 0.04 for 4%)."},
            "max_volatility":  {"type": "number", "minimum": 0.05, "maximum": 1.0,
                                "description": "Maximum annualised volatility tolerance as a fraction."},
            "min_history_years": {"type": "integer", "minimum": 1, "maximum": 10,
                                  "description": "Minimum years of price history required (data quality filter). Default 3 if not stated."},
            "max_holdings":    {"type": "integer", "minimum": 3, "maximum": 30,
                                "description": "How many holdings the user wants total."},
            "max_position_size": {"type": "number", "minimum": 0.05, "maximum": 0.5,
                                  "description": "Maximum single-position size as a fraction (e.g. 0.10 for 10%)."},
            "exclude_sectors": {"type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "GICS sectors to exclude (Materials, Energy, Financials, Industrials, "
                                    "Information Technology, Health Care, Consumer Discretionary, "
                                    "Consumer Staples, Communication Services, Utilities, Real Estate). "
                                    "Map: 'no mining' -> Materials; 'no banks' -> Financials; "
                                    "'no oil/gas' -> Energy; 'no tobacco' -> Consumer Staples.")},
            "include_only_sectors": {"type": "array",
                                     "items": {"type": "string"},
                                     "description": (
                                         "GICS sectors the user *only* wants exposure to. "
                                         "Use sparingly - only when user is explicit ('only tech', 'just healthcare').")},
            "exclude_tickers": {"type": "array",
                                "items": {"type": "string"},
                                "description": "Specific tickers to exclude, e.g. ['CBA.AX', 'BHP.AX']. Add the .AX suffix."},
            "preferred_themes": {"type": "array",
                                 "items": {"type": "string",
                                           "enum": list({"cybersecurity", "robotics_ai", "esg",
                                                         "healthcare", "agriculture", "crypto",
                                                         "income", "small_caps", "banks",
                                                         "resources", "infrastructure", "gold"})},
                                 "description": (
                                     "Themes the user mentioned as interests. Map terms: "
                                     "'AI' -> robotics_ai; 'cyber/security' -> cybersecurity; "
                                     "'green/sustainable' -> esg; 'mining' -> resources; "
                                     "'medical/biotech' -> healthcare; 'small companies' -> small_caps.")},
        },
        "additionalProperties": False,
    },
}


_SYSTEM_PROMPT = """You are a portfolio profiling assistant. Your job is to read \
a free-text investment description from a user and extract structured fields by \
calling the set_profile tool.

Rules:
- Only extract fields the user actually mentions. Leave any field absent if unclear.
- For risk_profile, weigh both stated risk tolerance and horizon (long horizons \
nudge toward growth/aggressive; short horizons nudge toward conservative/balanced).
- If the user gives capital with $ or words like "k" or "thousand", convert to AUD.
- If the user mentions an age and a target age, compute horizon_years.
- "Mainly AU stocks", "only Australian", "Aussie focus" -> set geo_tilt='au_only' or 'au_heavy'.
- For exclude_sectors and include_only_sectors, map natural language to canonical GICS sector names.
- For preferred_themes, only use the enum values listed in the schema.
- Do not give financial advice. Do not editorialise. Just extract."""


def parse(description: str) -> dict[str, Any] | None:
    client = get_client()
    if client is None:
        return None
    text = (description or "").strip()
    if not text:
        return {}

    try:
        msg = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1000,
            system=_SYSTEM_PROMPT,
            tools=[_PROFILE_TOOL],
            tool_choice={"type": "tool", "name": "set_profile"},
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        log.warning("parse() call failed: %s", exc)
        return None

    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "set_profile":
            try:
                result = dict(block.input)
                log.info("Parsed %d fields from %d-char description.",
                         len(result), len(text))
                return result
            except Exception as exc:
                log.warning("Could not parse tool input: %s", exc)
                return None

    log.warning("Model did not call set_profile tool.")
    return None
