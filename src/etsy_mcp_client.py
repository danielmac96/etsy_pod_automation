"""
etsy_mcp_client.py
==================
Drop-in replacement for EtsyClient that routes calls through the local Etsy
MCP server over JSON-RPC on stdio.

Activated via the factory `get_etsy_client()` when env `ETSY_USE_MCP=1`.
The REST EtsyClient stays as the default — no caller needs to know which
backend is active.

MCP server command is read from ETSY_MCP_CMD env var (default: the local
build at ./mcp/etsy-mcp-server/build/index.js run via node).
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import uuid
from typing import Any

from src.etsy_client import SearchResult
from src.schemas import EtsyListing

logger = logging.getLogger(__name__)

_MCP_CMD_DEFAULT = "node ./mcp/etsy-mcp-server/build/index.js"
MCP_SERVER_CMD: list[str] = shlex.split(
    os.environ.get("ETSY_MCP_CMD", _MCP_CMD_DEFAULT)
)
MCP_TIMEOUT = int(os.environ.get("ETSY_MCP_TIMEOUT", "30"))


def _call_mcp_tool(tool_name: str, arguments: dict) -> Any:
    """One-shot JSON-RPC call against the MCP server over stdio."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })
    env = {
        **os.environ,
        "ETSY_API_KEY": os.environ.get("ETSY_API_KEY", ""),
    }
    result = subprocess.run(
        MCP_SERVER_CMD,
        input=payload,
        capture_output=True,
        text=True,
        timeout=MCP_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"MCP server returned {result.returncode}: {result.stderr[:500]}")
    parsed = json.loads(result.stdout)
    if "error" in parsed:
        raise RuntimeError(f"MCP tool error: {parsed['error']}")
    return parsed.get("result", {})


def _extract_payload(result: Any) -> dict:
    """Unwrap MCP content envelope → plain dict."""
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

    Signatures and return types are identical to src/etsy_client.py so the
    factory can swap backends transparently.
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
        data = _extract_payload(_call_mcp_tool("search_listings", args))
        listings = [EtsyListing.from_v3(L) for L in (data.get("results") or [])]
        return SearchResult(
            listings=listings,
            cache_hit=False,
            raw_response_path="",
            pagination=data.get("pagination") or {},
        )

    def get_trending_listings(self, limit: int = 25) -> list[dict]:
        """Return trending Etsy listings. Uses MCP tool: get_trending_listings."""
        data = _extract_payload(_call_mcp_tool("get_trending_listings", {"limit": min(limit, 100)}))
        return list(data.get("results") or data if isinstance(data, list) else [])

    def get_shop(self, shop_id: int) -> dict:
        """Fetch shop details by shop ID."""
        return _extract_payload(_call_mcp_tool("get_shop", {"shop_id": int(shop_id)}))

    def get_shop_listings(self, shop_id: int, limit: int = 25) -> list[dict]:
        """Return active listings for a specific shop."""
        data = _extract_payload(
            _call_mcp_tool("get_shop_listings", {"shop_id": int(shop_id), "limit": min(limit, 100)})
        )
        return list(data.get("results") or data if isinstance(data, list) else [])

    def get_listing(self, listing_id: int) -> dict:
        """Deep-dive on a specific listing."""
        return _extract_payload(_call_mcp_tool("get_listing", {"listing_id": int(listing_id)}))

    def search_shops(self, shop_name: str, limit: int = 10) -> list[dict]:
        """Find shops by keyword/name."""
        data = _extract_payload(
            _call_mcp_tool("search_shops", {"shop_name": shop_name, "limit": limit})
        )
        return list(data.get("results") or data if isinstance(data, list) else [])
