"""Pure analysis over a theme's deduplicated listing set.

`mine_theme(listings)` returns a dict consumed by `concepts.extract_concepts`
and `synthesis.synthesize_briefs`. No I/O, no Gemini, no DB — pure functions
so unit tests are deterministic against the fixture.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from typing import Iterable, Sequence

from src.db import EtsyListingRow

# Composite-score weights. Centralized so synthesis.py and tests reference the
# same dict.
RESEARCH_WEIGHTS = {
    "volume": 0.30,
    "inv_saturation": 0.25,
    "freshness": 0.15,
    "originality": 0.15,
    "image_feasibility": 0.10,
    "voice_fit": 0.05,
}

PRICE_BAND_USD = (5.0, 80.0)

# Apparel taxonomy ids on Etsy buyer taxonomy. 2078 = clothing/men/t-shirts;
# 2079 = clothing/women/t-shirts; 1217 = clothing root. Anything outside the
# apparel branch gets a feasibility penalty since this shop sells tees only.
APPAREL_TAXONOMY_IDS = {1217, 2078, 2079}

NINETY_DAYS_S = 90 * 86_400


# ── small numeric helpers ─────────────────────────────────────────────────────

def _percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def _band(level: float, low: float = 0.33, high: float = 0.66) -> str:
    if level >= high:
        return "high"
    if level <= low:
        return "low"
    return "medium"


# ── primitives ────────────────────────────────────────────────────────────────

def top_tags(listings: Iterable[EtsyListingRow], n: int = 15) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for L in listings:
        for tag in (L.tags or []):
            t = str(tag).strip().lower()
            if t:
                counter[t] += 1
    return counter.most_common(n)


def price_quartiles(listings: Iterable[EtsyListingRow]) -> tuple[float | None, float | None]:
    lo, hi = PRICE_BAND_USD
    prices = sorted(
        L.price_usd for L in listings
        if L.price_usd is not None and lo <= L.price_usd <= hi
    )
    if not prices:
        return None, None
    return round(_percentile(prices, 0.25), 2), round(_percentile(prices, 0.75), 2)


def saturation_score(listings: Sequence[EtsyListingRow]) -> float:
    """0..1 where higher = more saturated.

    Defined as: fraction of listings that fall under the median favorites count
    of the theme's set, capped to 1.0. With many low-favorite copycats, the
    distribution skews right and saturation rises.
    """
    favs = [L.num_favorers or 0 for L in listings]
    if not favs:
        return 0.0
    favs_sorted = sorted(favs)
    median = _percentile(favs_sorted, 0.5)
    if median <= 0:
        return 1.0
    below = sum(1 for f in favs if f <= median)
    return min(1.0, below / len(favs))


def volume_signal(listings: Sequence[EtsyListingRow]) -> float:
    """0..1 normalized by 100 listings (cap). 50 listings → 0.5; 100+ → 1.0."""
    return min(1.0, len(listings) / 100.0)


def hhi_shop_concentration(listings: Sequence[EtsyListingRow]) -> float:
    """Herfindahl-Hirschman Index on shop_id share. 0..1."""
    if not listings:
        return 0.0
    shop_counts: Counter = Counter()
    for L in listings:
        shop_counts[L.shop_id] += 1
    total = sum(shop_counts.values())
    if total == 0:
        return 0.0
    shares = [c / total for c in shop_counts.values()]
    return sum(s * s for s in shares)


def freshness_pct_last_90d(
    listings: Sequence[EtsyListingRow],
    now_s: int | None = None,
) -> float:
    """Fraction of listings created in the last 90 days. 0..1."""
    if not listings:
        return 0.0
    now_s = now_s if now_s is not None else int(time.time())
    cutoff = now_s - NINETY_DAYS_S
    fresh = sum(1 for L in listings if L.creation_tsz and L.creation_tsz >= cutoff)
    return fresh / len(listings)


def pod_feasibility(listings: Sequence[EtsyListingRow]) -> float:
    """Fraction of listings that look like apparel by taxonomy.

    1.0 = strong apparel signal (good for a tee shop). Lower = the search is
    drifting toward stickers, mugs, or non-apparel — penalty in composite.
    """
    if not listings:
        return 1.0
    apparel = 0
    counted = 0
    for L in listings:
        path = L.taxonomy_path or []
        if not path:
            continue
        counted += 1
        if any(int(node) in APPAREL_TAXONOMY_IDS for node in path):
            apparel += 1
    if counted == 0:
        return 0.5  # neutral if no taxonomy info
    return apparel / counted


# ── theme-level composite ─────────────────────────────────────────────────────

def mine_theme(
    listings: Sequence[EtsyListingRow],
    *,
    now_s: int | None = None,
) -> dict:
    """Return a dict summarizing this theme's competitive landscape.

    Keys mirror the structure consumed by concepts.extract_concepts. The
    `composite_score` here is the *theme*'s score before per-concept overlay
    in synthesis.
    """
    n = len(listings)
    tags = top_tags(listings, n=15)
    p25, p75 = price_quartiles(listings)
    sat = saturation_score(listings)
    vol = volume_signal(listings)
    hhi = hhi_shop_concentration(listings)
    fresh = freshness_pct_last_90d(listings, now_s=now_s)
    pod = pod_feasibility(listings)

    # band labels for downstream prompts
    saturation_label = _band(sat, low=0.5, high=0.8)
    volume_label = _band(vol, low=0.2, high=0.6)

    inv_sat = 1.0 - sat
    originality_proxy = 1.0 - hhi  # less concentrated = more original heads available
    feasibility = pod
    voice_fit = 1.0  # theme-level placeholder; concept-level will adjust
    image_feasibility = pod  # same proxy at theme level

    composite = (
        RESEARCH_WEIGHTS["volume"] * vol
        + RESEARCH_WEIGHTS["inv_saturation"] * inv_sat
        + RESEARCH_WEIGHTS["freshness"] * fresh
        + RESEARCH_WEIGHTS["originality"] * originality_proxy
        + RESEARCH_WEIGHTS["image_feasibility"] * image_feasibility
        + RESEARCH_WEIGHTS["voice_fit"] * voice_fit
    ) * feasibility  # apparel feasibility multiplier

    return {
        "n_listings": n,
        "top_tags": [{"tag": t, "count": c} for t, c in tags],
        "price_p25_usd": p25,
        "price_p75_usd": p75,
        "saturation": saturation_label,
        "saturation_raw": round(sat, 3),
        "volume_signal": volume_label,
        "volume_raw": round(vol, 3),
        "hhi_shop_concentration": round(hhi, 3),
        "freshness_pct_last_90d": round(fresh, 3),
        "pod_feasibility": round(pod, 3),
        "composite_score": round(composite, 4),
    }
