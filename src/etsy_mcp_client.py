"""
etsy_mcp_client.py
==================
Drop-in replacement for EtsyClient that routes calls through a community
Etsy MCP server (defaults to `etsy-mcp`) over JSON-RPC on stdio.

Activated via the factory `get_etsy_client()` when env `ETSY_USE_MCP=1`.
The REST EtsyClient stays as the default — no caller needs to know which
backend is active.

The single method 01_research.py uses is `search_listings(query, limit,
sort_on, ...)`. We return a SearchResult shaped exactly like the REST
client's return value so callers see no difference.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

# Reuse the existing SearchResult dataclass + parser so callers get identical
# objects no matter which backend served the request.
import sys as _sys
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))

from etsy_client import SearchResult  # type: ignore[import-not-found]
from schemas import EtsyListing  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

MCP_SERVER_CMD = os.environ.get("ETSY_MCP_CMD", "etsy-mcp")
MCP_TIMEOUT = int(os.environ.get("ETSY_MCP_TIMEOUT", "30"))


def _call_mcp_tool(tool_name: str, arguments: dict) -> Any:
    """One-shot JSON-RPC call against the MCP server over stdio.

    Spawns the server fresh per call. That's slower than a long-running
    subprocess but keeps this client stateless and matches how community
    Etsy MCP servers are typically invoked.
    """
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })
    env = {
        **os.environ,
        "ETSY_API_KEY": os.environ.get("ETSY_API_KEY", ""),
        "ETSY_REFRESH_TOKEN": os.environ.get("ETSY_REFRESH_TOKEN", ""),
        "ETSY_DEFAULT_SHOP_ID": os.environ.get("ETSY_DEFAULT_SHOP_ID", ""),
    }
    result = subprocess.run(
        [MCP_SERVER_CMD],
        input=payload,
        capture_output=True,
        text=True,
        timeout=MCP_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{MCP_SERVER_CMD} returned {result.returncode}: {result.stderr[:500]}")
    parsed = json.loads(result.stdout)
    if "error" in parsed:
        raise RuntimeError(f"{MCP_SERVER_CMD} tool error: {parsed['error']}")
    return parsed.get("result", {})


def _extract_listings_payload(result: Any) -> dict:
    """MCP servers wrap tool output in {"content": [{"type":"text","text":"..."}]}.

    Unwrap into the v3-shaped dict that EtsyListing.from_v3 expects.
    """
    if isinstance(result, dict) and "content" in result:
        for block in result.get("content") or []:
            if block.get("type") == "text":
                try:
                    return json.loads(block.get("text") or "{}")
                except json.JSONDecodeError:
                    pass
    if isinstance(result, dict):
        return result
    return {}


class EtsyMcpClient:
    """Mirrors the EtsyClient surface used by the research pipeline.

    Only methods that 01_research.py / src/research/probes.py actually call
    are implemented. Add more here as callers grow — keep signatures + return
    types identical to scripts/etsy_client.py.
    """

    def search_listings(
        self,
        query: str,
        *,
        limit: int = 50,
        sort_on: str = "score",
        sort_order: str = "desc",
        offset: int = 0,
        taxonomy_id: int | None = None,
    ) -> SearchResult:
        args: dict[str, Any] = {
            "keywords": query,
            "limit": min(limit, 100),
            "offset": offset,
            "sort_on": sort_on,
            "sort_order": sort_order,
        }
        if taxonomy_id is not None:
            args["taxonomy_id"] = int(taxonomy_id)
        raw = _call_mcp_tool("findAllListingsActive", args)
        data = _extract_listings_payload(raw)
        listings = [EtsyListing.from_v3(L) for L in (data.get("results") or [])]
        return SearchResult(
            listings=listings,
            cache_hit=False,  # MCP server may have its own cache; we don't know
            raw_response_path="",
            pagination=data.get("pagination") or {},
        )

    # ── extra capabilities the REST client lacks ────────────────────────────
    # These are not called by 01_research.py today but are useful next steps
    # for HHI scoring and concept extraction. Keep them here so callers can
    # opt in without another factory swap.

    def get_listing(self, listing_id: int) -> dict:
        return _extract_listings_payload(_call_mcp_tool("getListing", {"listing_id": int(listing_id)}))

    def get_shop(self, shop_id: int) -> dict:
        return _extract_listings_payload(_call_mcp_tool("getShop", {"shop_id": int(shop_id)}))

    def get_listing_reviews(self, listing_id: int, limit: int = 25) -> dict:
        return _extract_listings_payload(
            _call_mcp_tool("getListingReviews", {"listing_id": int(listing_id), "limit": int(limit)})
        )

    def get_taxonomy_nodes(self) -> dict:
        return _extract_listings_payload(_call_mcp_tool("getSellerTaxonomyNodes", {}))
