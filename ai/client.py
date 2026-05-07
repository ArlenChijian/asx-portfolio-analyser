"""Shared Anthropic client setup.

Reads ANTHROPIC_API_KEY from the environment (or from a .env file via
python-dotenv if present at the project root). Exposes a single helper,
`get_client()`, that returns an `Anthropic` instance or `None` if no
key is available.

We deliberately don't crash on missing key - the AI features are an
optional layer over a working portfolio app.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Default model for AI calls. Haiku 4.5 is cheap and fast - perfect for
# parsing short profile descriptions and writing 200-400 word summaries.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _load_dotenv_once() -> None:
    """Load .env from the project root, if present. Idempotent."""
    if getattr(_load_dotenv_once, "_done", False):
        return
    try:
        from dotenv import load_dotenv
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            log.debug("Loaded .env from %s", env_path)
    except ImportError:
        # python-dotenv is in requirements.txt, but be tolerant.
        log.debug("python-dotenv not installed; skipping .env load.")
    _load_dotenv_once._done = True  # type: ignore[attr-defined]


@lru_cache(maxsize=1)
def get_client():
    """Return a configured Anthropic client, or None if no API key.

    Cached so we only instantiate once per process.
    """
    _load_dotenv_once()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.info("ANTHROPIC_API_KEY not set; AI features disabled.")
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        log.warning("anthropic package not installed; AI features disabled.")
        return None
    return Anthropic(api_key=key)


def is_available() -> bool:
    """True iff the AI client is ready to make calls."""
    return get_client() is not None
