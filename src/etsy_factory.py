"""
etsy_factory.py
===============
Single entry point for getting an Etsy client. Returns the MCP-backed
client when ETSY_USE_MCP=1, otherwise the existing REST EtsyClient.

Both clients expose `search_listings(query, *, limit, sort_on, ...)` with
identical return shapes, so the pipeline never sees the difference.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# scripts/etsy_client.py lives in scripts/, not src/ — make it importable.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def get_etsy_client(
    *,
    cache_dir: Path | str | None = None,
    rps: float = 5.0,
):
    """Construct the Etsy client appropriate for the current environment.

    Args mirror EtsyClient's most-used keyword args. They are ignored by
    EtsyMcpClient (the MCP server handles caching + rate-limiting itself).
    """
    if os.environ.get("ETSY_USE_MCP", "0").strip() == "1":
        from src.etsy_mcp_client import EtsyMcpClient
        logger.info("Etsy backend: MCP (ETSY_USE_MCP=1)")
        return EtsyMcpClient()

    from etsy_client import EtsyClient  # type: ignore[import-not-found]

    api_key = os.environ.get("ETSY_API_KEY", "")
    if cache_dir is None:
        cache_dir = Path("etsy_cache")
    logger.info("Etsy backend: REST")
    return EtsyClient(api_key=api_key, cache_dir=cache_dir, rps=rps)
