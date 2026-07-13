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


def sanitize_etsy_tags(tags: list) -> list[str]:
    """Enforce Etsy tag constraints: max 13 tags, ≤20 chars each, no dupes/blanks."""
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        tag = str(t).strip()[:20]
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            out.append(tag)
        if len(out) == 13:
            break
    return out


def select_variants(
    all_variants: list[dict],
    *,
    colors: list[str],
    sizes: list[str],
    price_cents: int,
) -> list[dict]:
    """Filter a blueprint's variant catalog down to the configured
    color × size grid (Printify titles them '{Color} / {Size}')."""
    wanted = {f"{c.strip()} / {s.strip()}".lower() for c in colors for s in sizes}
    return [
        {"id": v["id"], "price": price_cents, "is_enabled": True}
        for v in all_variants
        if v.get("title", "").lower() in wanted
    ]


def build_product_payload(
    *,
    title: str,
    description: str,
    tags: list[str],
    blueprint_id: int,
    print_provider_id: int,
    variants: list[dict],
    image_id: str,
    print_scale: float = 0.8,
) -> dict:
    """Full Printify product-create payload. description + tags are what
    make the eventual Etsy listing publish complete — Printify forwards
    both to Etsy on publish."""
    variant_ids = [v["id"] for v in variants]
    return {
        "title": title,
        "description": description,
        "tags": sanitize_etsy_tags(tags),
        "blueprint_id": blueprint_id,
        "print_provider_id": print_provider_id,
        "variants": variants,
        "print_areas": [
            {
                "variant_ids": variant_ids,
                "placeholders": [
                    {
                        "position": "front",
                        "images": [
                            {"id": image_id, "x": 0.5, "y": 0.5,
                             "scale": print_scale, "angle": 0}
                        ],
                    }
                ],
            }
        ],
    }


def get_product(api_key: str, shop_id: str, product_id: str, *, timeout: int = 60) -> dict:
    r = requests.get(
        f"{API_BASE}/shops/{shop_id}/products/{product_id}.json",
        headers=headers(api_key),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()
