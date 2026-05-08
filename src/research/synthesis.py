"""Cross-theme synthesis: rank concepts and produce final design briefs.

One Gemini call ranks ALL concepts across ALL themes (not per-theme), so
underperforming themes can lose all their concepts to a hot one. The prompt
exposes the mining numbers and the recent-winner signal; Gemini scores each
concept on volume × inv-saturation, originality, voice fit, and image
feasibility, then we slot in the final composite using RESEARCH_WEIGHTS.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from src.db import Concept, DesignBriefRow, Theme
from src.research.mining import RESEARCH_WEIGHTS

DEFAULT_FINAL_BRIEF_COUNT = 10

SYNTHESIS_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "concept_id": {"type": "STRING"},
            "rank": {"type": "INTEGER"},
            "image_prompt_seed": {"type": "STRING"},
            "scores": {
                "type": "OBJECT",
                "properties": {
                    "volume": {"type": "NUMBER"},
                    "inv_saturation": {"type": "NUMBER"},
                    "originality": {"type": "NUMBER"},
                    "voice_fit": {"type": "NUMBER"},
                    "image_feasibility": {"type": "NUMBER"},
                    "freshness": {"type": "NUMBER"},
                    "winner_similarity_bonus": {"type": "NUMBER"},
                },
                "required": ["originality", "voice_fit", "image_feasibility"],
            },
        },
        "required": ["concept_id", "rank", "image_prompt_seed", "scores"],
    },
}


def _summarize_recent_winners(feedback_signal: dict) -> str:
    winners = (feedback_signal or {}).get("top_winning_briefs") or []
    if not winners:
        return "No prior winners (cold start)."
    bits = []
    for w in winners[:5]:
        bits.append(
            f"[{w.get('category')}] {w.get('headline_text')!r} "
            f"(+{w.get('favorites_delta_total')} favs)"
        )
    return "; ".join(bits)


def build_synthesis_prompt(
    *,
    concepts_payload: list[dict],
    final_count: int,
    brand_voice: str,
    feedback_signal: dict,
) -> str:
    return (
        f"You are the chief curator. Across ALL themes below, pick the top "
        f"{final_count} concepts to ship this week. You may pick multiple "
        f"from one theme or none from another — quality over balance.\n\n"
        f"BRAND VOICE: {brand_voice}\n"
        f"RECENT WINNERS (gentle bonus for stylistic kinship, NOT clones): "
        f"{_summarize_recent_winners(feedback_signal)}\n\n"
        f"CONCEPTS: {json.dumps(concepts_payload)}\n\n"
        f"For each chosen concept return:\n"
        f"  - concept_id: copy from the input\n"
        f"  - rank: 1..{final_count}\n"
        f"  - image_prompt_seed: 1-2 sentence seed that 02_generate_prompts will "
        f"    expand into the FAL prompt. Lead with the headline_text and the "
        f"    visual direction.\n"
        f"  - scores: numbers in [0,1] for volume, inv_saturation, "
        f"    originality, voice_fit, image_feasibility, freshness; "
        f"    winner_similarity_bonus in [0, 0.1]. Use the mined values where "
        f"    given; estimate originality / voice_fit / image_feasibility "
        f"    yourself.\n"
        f"Return EXACTLY {final_count} entries (or fewer if the pool is "
        f"smaller), ordered by rank ascending."
    )


def _composite(scores: dict, theme_mining: dict) -> float:
    """Apply RESEARCH_WEIGHTS to per-concept scores. Falls back to theme-level
    mining values for volume / inv_saturation / freshness if Gemini omitted them.
    """
    def pick(name: str, theme_key: str | None = None, default: float = 0.5) -> float:
        v = scores.get(name)
        if v is not None:
            return float(v)
        if theme_key and theme_mining.get(theme_key) is not None:
            tv = theme_mining.get(theme_key)
            if name == "inv_saturation":
                # theme stores `saturation_raw` not `inv_saturation`
                return 1.0 - float(theme_mining.get("saturation_raw", 0.5))
            return float(tv)
        return default

    vol = pick("volume", "volume_raw")
    inv_sat = pick("inv_saturation")
    fresh = pick("freshness", "freshness_pct_last_90d")
    orig = pick("originality")
    vf = pick("voice_fit")
    imgf = pick("image_feasibility", "pod_feasibility")
    bonus = float(scores.get("winner_similarity_bonus") or 0.0)

    base = (
        RESEARCH_WEIGHTS["volume"] * vol
        + RESEARCH_WEIGHTS["inv_saturation"] * inv_sat
        + RESEARCH_WEIGHTS["freshness"] * fresh
        + RESEARCH_WEIGHTS["originality"] * orig
        + RESEARCH_WEIGHTS["image_feasibility"] * imgf
        + RESEARCH_WEIGHTS["voice_fit"] * vf
    )
    return round(base + min(0.1, max(0.0, bonus)), 4)


def _coerce_synth_payload(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [dict(c) for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        for key in ("ranked", "results", "items", "data"):
            if isinstance(raw.get(key), list):
                return [dict(c) for c in raw[key] if isinstance(c, dict)]
    raise ValueError(f"Synthesis payload not a list or recognized object: {type(raw).__name__}")


def synthesize_briefs(
    *,
    gen_fn: Callable[..., Any],
    run_id: str,
    themes: list[Theme],
    concepts: list[Concept],
    theme_mining: dict[str, dict],
    feedback_signal: dict,
    brand_voice: str,
    final_count: int = DEFAULT_FINAL_BRIEF_COUNT,
) -> list[DesignBriefRow]:
    """Rank concepts across themes and emit the final DesignBriefRow list.

    `theme_mining` is keyed by theme_id; values are dicts from `mining.mine_theme`.
    """
    theme_by_id = {t.theme_id: t for t in themes}
    concept_by_id = {c.concept_id: c for c in concepts}

    # Compact payload for the prompt
    payload = []
    for c in concepts:
        t = theme_by_id.get(c.theme_id)
        m = theme_mining.get(c.theme_id, {})
        payload.append({
            "concept_id": c.concept_id,
            "theme_name": t.theme_name if t else "",
            "category": t.category if t else "",
            "cultural_tension": t.cultural_tension if t else "",
            "concept_name": c.concept_name,
            "headline_text": c.headline_text,
            "visual_concept": c.visual_concept,
            "style_tags": c.style_tags,
            "differentiation_note": c.differentiation_note,
            "mining": {
                "saturation": m.get("saturation"),
                "volume_signal": m.get("volume_signal"),
                "freshness_pct_last_90d": m.get("freshness_pct_last_90d"),
                "pod_feasibility": m.get("pod_feasibility"),
                "price_p25_usd": m.get("price_p25_usd"),
                "price_p75_usd": m.get("price_p75_usd"),
                "n_listings": m.get("n_listings"),
            },
        })

    raw = gen_fn(
        build_synthesis_prompt(
            concepts_payload=payload,
            final_count=final_count,
            brand_voice=brand_voice,
            feedback_signal=feedback_signal,
        ),
        schema=SYNTHESIS_SCHEMA,
    )
    ranked = _coerce_synth_payload(raw)
    ranked.sort(key=lambda x: int(x.get("rank") or 999))

    briefs: list[DesignBriefRow] = []
    seen: set[str] = set()
    for r in ranked[:final_count]:
        cid = str(r.get("concept_id") or "")
        if cid in seen or cid not in concept_by_id:
            continue
        seen.add(cid)
        c = concept_by_id[cid]
        t = theme_by_id.get(c.theme_id)
        m = theme_mining.get(c.theme_id, {})
        composite = _composite(r.get("scores") or {}, m)
        briefs.append(DesignBriefRow(
            brief_id=str(uuid.uuid4()),
            run_id=run_id,
            concept_id=cid,
            rank=len(briefs) + 1,
            category=(t.category if t else ""),
            headline_text=c.headline_text,
            visual_concept=c.visual_concept,
            style_tags=list(c.style_tags or []),
            image_prompt_seed=str(r.get("image_prompt_seed") or "").strip(),
            saturation=str(m.get("saturation") or "unknown"),
            volume_signal=str(m.get("volume_signal") or "unknown"),
            composite_score=composite,
            color_palette_hint=c.color_palette_hint,
            target_buyer=c.target_buyer,
            price_p25_usd=m.get("price_p25_usd"),
            price_p75_usd=m.get("price_p75_usd"),
        ))
    return briefs
