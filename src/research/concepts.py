"""Per-theme concept extraction.

For each theme + its mined landscape + a sample of competitor listings, ask
Gemini to propose 3-5 differentiated concepts. Validate that any
`evidence_listing_ids` returned actually appear in the listings we passed in
(drop hallucinated ids before persisting).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Sequence

from src.db import Concept, EtsyListingRow, Theme

CONCEPTS_PER_THEME = 4
EVIDENCE_LISTING_SAMPLE = 25

CONCEPT_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "concept_name": {"type": "STRING"},
            "headline_text": {"type": "STRING"},
            "visual_concept": {"type": "STRING"},
            "style_tags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "color_palette_hint": {"type": "STRING"},
            "target_buyer": {"type": "STRING"},
            "differentiation_note": {"type": "STRING"},
            "evidence_listing_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        },
        "required": [
            "concept_name", "headline_text", "visual_concept",
            "style_tags", "differentiation_note",
        ],
    },
}


def _sample_listings_for_prompt(listings: Sequence[EtsyListingRow], n: int) -> list[dict]:
    """Order by num_favorers desc and trim to a compact prompt-friendly view."""
    ranked = sorted(
        listings,
        key=lambda L: (L.num_favorers or 0, L.views or 0),
        reverse=True,
    )[:n]
    out = []
    for L in ranked:
        out.append({
            "listing_id": L.listing_id,
            "title": L.title,
            "tags": L.tags or [],
            "num_favorers": L.num_favorers or 0,
            "price_usd": L.price_usd,
        })
    return out


def build_concept_prompt(
    *,
    theme: Theme,
    mining: dict,
    listing_sample: list[dict],
) -> str:
    return (
        f"You are designing graphic-tee CONCEPTS for the theme below. The "
        f"theme has been mined from Etsy; use the data + competitor sample "
        f"to propose {CONCEPTS_PER_THEME} differentiated concepts.\n\n"
        f"THEME: {theme.theme_name}\n"
        f"CATEGORY: {theme.category}\n"
        f"TENSION: {theme.cultural_tension}\n"
        f"DESCRIPTION: {theme.description}\n\n"
        f"MINED LANDSCAPE: {json.dumps(mining)}\n\n"
        f"COMPETITOR SAMPLE (top by favorites): {json.dumps(listing_sample)}\n\n"
        f"For each concept produce:\n"
        f"  - concept_name: 3-7 words\n"
        f"  - headline_text: the EXACT text that would appear on the tee. "
        f"Short (≤6 words) and graphic-friendly\n"
        f"  - visual_concept: 1-2 sentences describing the artwork direction\n"
        f"  - style_tags: 4-8 lowercase tags (e.g. 'bold typography', 'retro')\n"
        f"  - color_palette_hint: short phrase\n"
        f"  - target_buyer: who buys this\n"
        f"  - differentiation_note: how this beats the competitor sample\n"
        f"  - evidence_listing_ids: ids from the COMPETITOR SAMPLE above that "
        f"    inform this concept (use ONLY ids that appear in the sample)"
    )


def _coerce_concepts_payload(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [dict(c) for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        for key in ("concepts", "results", "items", "data"):
            if isinstance(raw.get(key), list):
                return [dict(c) for c in raw[key] if isinstance(c, dict)]
    raise ValueError(
        f"Concept payload not a list or recognized object: {type(raw).__name__}"
    )


def _validate_evidence_ids(
    raw_ids: Any,
    valid_ids: set[int],
) -> list[int]:
    if not isinstance(raw_ids, list):
        return []
    out: list[int] = []
    for v in raw_ids:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if i in valid_ids:
            out.append(i)
    return out


def extract_concepts(
    *,
    gen_fn: Callable[..., Any],
    theme: Theme,
    listings: Sequence[EtsyListingRow],
    mining: dict,
    sample_size: int = EVIDENCE_LISTING_SAMPLE,
) -> list[Concept]:
    sample = _sample_listings_for_prompt(listings, sample_size)
    valid_ids = {int(l["listing_id"]) for l in sample}
    raw = gen_fn(
        build_concept_prompt(theme=theme, mining=mining, listing_sample=sample),
        schema=CONCEPT_SCHEMA,
    )
    raw_concepts = _coerce_concepts_payload(raw)
    out: list[Concept] = []
    for c in raw_concepts:
        evidence = _validate_evidence_ids(c.get("evidence_listing_ids"), valid_ids)
        style_tags = c.get("style_tags") or []
        if not isinstance(style_tags, list):
            style_tags = []
        out.append(Concept(
            concept_id=str(uuid.uuid4()),
            theme_id=theme.theme_id,
            concept_name=str(c.get("concept_name") or "").strip(),
            headline_text=str(c.get("headline_text") or "").strip(),
            visual_concept=str(c.get("visual_concept") or "").strip(),
            style_tags=[str(t).strip().lower() for t in style_tags if t],
            evidence_listing_ids=evidence,
            color_palette_hint=(str(c["color_palette_hint"]).strip()
                                if c.get("color_palette_hint") else None),
            target_buyer=(str(c["target_buyer"]).strip()
                          if c.get("target_buyer") else None),
            differentiation_note=(str(c["differentiation_note"]).strip()
                                  if c.get("differentiation_note") else None),
            selected=False,
            rejection_reason=None,
        ))
    return out
