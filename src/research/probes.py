"""Etsy probe generation and execution.

For each theme, Gemini generates 3 probes: 1 BROAD (head term) + 2 NARROW
(more specific phrasings). Each probe is then fired TWICE against Etsy —
once with sort_on='score' (popularity) and once with sort_on='created'
(freshness). Both runs become separate `etsy_probes` rows so the analytical
layer can reason about saturation vs. novelty separately.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from src.schemas import EtsyListing
from src.db import EtsyListingRow, EtsyProbe, Theme

PROBES_PER_THEME = 3
LISTINGS_PER_PROBE_DEFAULT = 50

PROBE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING"},
            "intent": {"type": "STRING"},
        },
        "required": ["query", "intent"],
    },
}


def build_probe_prompt(theme: Theme) -> str:
    return (
        f"You are sourcing Etsy search probes for the theme below. Output a "
        f"JSON array of EXACTLY {PROBES_PER_THEME} probes: 1 BROAD head term "
        f"and 2 NARROW phrasings. Probes should be the kind of strings a real "
        f"buyer would type into Etsy.\n\n"
        f"THEME: {theme.theme_name}\n"
        f"CATEGORY: {theme.category}\n"
        f"TENSION: {theme.cultural_tension}\n"
        f"DESCRIPTION: {theme.description}\n\n"
        f"Each probe object MUST have:\n"
        f"  - query: 2-6 words, lowercase, no quotes\n"
        f"  - intent: 'broad' for the head term, 'narrow' for the others\n"
    )


def _coerce_probes_payload(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [dict(p) for p in raw if isinstance(p, dict)]
    if isinstance(raw, dict):
        for key in ("probes", "results", "items", "data"):
            if isinstance(raw.get(key), list):
                return [dict(p) for p in raw[key] if isinstance(p, dict)]
    raise ValueError(f"Probe payload not a list or recognized object: {type(raw).__name__}")


def generate_probes(
    *,
    gen_fn: Callable[..., Any],
    theme: Theme,
) -> list[dict]:
    """Return [{query, intent}, ...] of length PROBES_PER_THEME."""
    raw = gen_fn(build_probe_prompt(theme), schema=PROBE_SCHEMA)
    probes = _coerce_probes_payload(raw)
    cleaned: list[dict] = []
    for p in probes[:PROBES_PER_THEME]:
        q = str(p.get("query") or "").strip().lower()
        intent = str(p.get("intent") or "").strip().lower()
        if intent not in {"broad", "narrow"}:
            intent = "broad" if not cleaned else "narrow"
        if q:
            cleaned.append({"query": q, "intent": intent})
    return cleaned


# ── execution ─────────────────────────────────────────────────────────────────

def _to_listing_row(L: EtsyListing, probe_id: str) -> EtsyListingRow:
    return EtsyListingRow(
        listing_id=L.listing_id,
        probe_id=probe_id,
        title=L.title,
        tags=list(L.tags or []),
        price_usd=L.price_usd,
        currency_code=L.currency_code,
        num_favorers=L.num_favorers,
        views=L.views,
        shop_id=L.shop_id,
        taxonomy_path=list(L.taxonomy_path) if L.taxonomy_path else None,
        is_digital=L.is_digital,
        is_personalizable=L.is_personalizable,
        creation_tsz=L.creation_tsz,
        listing_url=L.url or None,
        primary_image_url=None,
    )


def run_probes(
    *,
    etsy_client,
    theme: Theme,
    probes: Iterable[dict],
    listings_per_probe: int = LISTINGS_PER_PROBE_DEFAULT,
    sort_modes: tuple[str, ...] = ("score", "created"),
) -> list[tuple[EtsyProbe, list[EtsyListingRow]]]:
    """Fire each probe ONCE per sort mode.

    Returns a list of `(EtsyProbe, [EtsyListingRow, ...])` tuples — one per
    (probe, sort_mode) combination. Caller is responsible for inserting these
    via `db.insert_probe` + `db.insert_listings`.
    """
    out: list[tuple[EtsyProbe, list[EtsyListingRow]]] = []
    for p in probes:
        for sort_on in sort_modes:
            probe_id = str(uuid.uuid4())
            try:
                res = etsy_client.search_listings(
                    p["query"], limit=listings_per_probe, sort_on=sort_on,
                )
                probe_row = EtsyProbe(
                    probe_id=probe_id,
                    theme_id=theme.theme_id,
                    query=p["query"],
                    intent=p.get("intent") or "broad",
                    sort_on=sort_on,
                    listings_returned=len(res.listings),
                    cache_hit=res.cache_hit,
                    raw_response_path=res.raw_response_path,
                )
                rows = [_to_listing_row(L, probe_id) for L in res.listings]
            except Exception as e:
                # Persist the failed probe row anyway so the analytical layer
                # can see we tried; downstream just sees empty listings.
                probe_row = EtsyProbe(
                    probe_id=probe_id,
                    theme_id=theme.theme_id,
                    query=p["query"],
                    intent=p.get("intent") or "broad",
                    sort_on=sort_on,
                    listings_returned=0,
                    cache_hit=False,
                    raw_response_path=None,
                )
                rows = []
                # Surface the error to stdout for the orchestrator's log
                print(f"      [etsy error] q={p['query']!r} sort={sort_on}: {e}")
            out.append((probe_row, rows))
    return out


def deduplicate_listings(
    rows_per_probe: list[tuple[EtsyProbe, list[EtsyListingRow]]],
) -> list[EtsyListingRow]:
    """Flatten and dedupe by listing_id (first probe wins) across all sort modes.

    Mining works on the listing-level set, not per-probe rows, so the orchestrator
    needs a deduplicated view. Probe rows are still persisted independently.
    """
    seen: set[int] = set()
    out: list[EtsyListingRow] = []
    for _probe, rows in rows_per_probe:
        for r in rows:
            if r.listing_id in seen:
                continue
            seen.add(r.listing_id)
            out.append(r)
    return out


def write_raw_responses(
    rows_per_probe: list[tuple[EtsyProbe, list[EtsyListingRow]]],
    raw_dir: Path,
) -> None:
    """Copy each probe's raw response (referenced by `raw_response_path`) into
    `runs/<run_id>/raw/` keyed by probe_id. The cache file is the canonical
    source; this lets `runs/<run_id>/` be self-contained for audit.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    for probe, _rows in rows_per_probe:
        if not probe.raw_response_path:
            continue
        src_path = Path(probe.raw_response_path)
        if not src_path.exists():
            continue
        target = raw_dir / f"{probe.probe_id}.json"
        try:
            data = json.loads(src_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        target.write_text(json.dumps(data), encoding="utf-8")
