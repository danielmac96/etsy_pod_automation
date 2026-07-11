"""Thin Printify v1 API helpers shared by 06 (draft) and 08 (publish).

Publishing through the API is what removes the manual "open Printify and
click Publish" step: Printify pushes the product to the connected Etsy
sales channel using the shop's publish defaults (listing state, shipping
profile, etc. are configured once in Printify → Manage My Stores).
"""
from __future__ import annotations

import re
from typing import Optional

import requests

API_BASE = "https://api.printify.com/v1"

# 06 stores printify_draft_url as .../shop/{shop_id}/products/{product_id}/edit
_PRODUCT_ID_RE = re.compile(r"/products/([0-9a-fA-F]+)")


def headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def extract_product_id(draft_url: str | None) -> Optional[str]:
    """Pull the Printify product id back out of a stored draft URL."""
    if not draft_url:
        return None
    m = _PRODUCT_ID_RE.search(draft_url)
    return m.group(1) if m else None


def publish_product(api_key: str, shop_id: str, product_id: str, *, timeout: int = 120) -> dict:
    """Publish a draft product to the shop's connected sales channel (Etsy).

    Raises requests.HTTPError on failure. Printify handles the Etsy listing
    creation asynchronously; 07_track_stats.py picks up the resulting listing
    URL by title match on its next run.
    """
    r = requests.post(
        f"{API_BASE}/shops/{shop_id}/products/{product_id}/publish.json",
        headers=headers(api_key),
        json={
            "title": True,
            "description": True,
            "images": True,
            "variants": True,
            "tags": True,
            "keyFeatures": True,
            "shipping_template": True,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json() if r.text else {}


def get_product(api_key: str, shop_id: str, product_id: str, *, timeout: int = 60) -> dict:
    r = requests.get(
        f"{API_BASE}/shops/{shop_id}/products/{product_id}.json",
        headers=headers(api_key),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()
