"""Theme generation with 40/40/20 exploit/explore/underrepresented split.

`generate_themes` calls Gemini once with a prompt embedding the feedback signal
and the requested split intent, then post-processes the result:
  - validates schema-like shape
  - dedups near-duplicates against last-8-week themes via TF-IDF cosine
    (threshold = 0.85). Near-dups are KEPT in the returned list but tagged in
    `notes` as `near_duplicate_of:<theme_id>` so the orchestrator can filter
    them and the dedup decision is auditable.

A simple character-token TF-IDF (no sklearn dep) is used to keep this module
self-contained.
"""
from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from typing import Any, Callable, Iterable, Optional

from src.db import Theme

DEDUP_THRESHOLD = 0.85

THEME_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "theme_name": {"type": "STRING"},
            "description": {"type": "STRING"},
            "category": {"type": "STRING"},
            "cultural_tension": {"type": "STRING"},
            "seeded_from": {"type": "STRING"},
            "parent_brief_id": {"type": "STRING"},
        },
        "required": ["theme_name", "description", "category", "cultural_tension"],
    },
}


# ── prompt construction ───────────────────────────────────────────────────────

def build_theme_prompt(
    *,
    n_themes: int,
    brand_voice: str,
    categories: list[str],
    feedback_block: str,
    is_cold_start: bool,
) -> str:
    cats = ", ".join(categories)
    if is_cold_start:
        split_block = (
            "STRATEGY: Cold start. Allocate ALL themes to EXPLORE — surface "
            "fresh cultural tensions across the categories. Do not set "
            "`seeded_from` or `parent_brief_id`."
        )
    else:
        n_exploit = round(n_themes * 0.4)
        n_explore = round(n_themes * 0.4)
        n_under = max(0, n_themes - n_exploit - n_explore)
        split_block = (
            f"STRATEGY: Allocate the {n_themes} themes across three buckets:\n"
            f"  - EXPLOIT (~{n_exploit}): build on the top winning briefs above. "
            f"For each exploit theme, set `seeded_from='last_week_winner'` and "
            f"`parent_brief_id` to the brief_id you are riffing on.\n"
            f"  - EXPLORE (~{n_explore}): novel cultural tensions not in the "
            f"recently explored list. Leave `seeded_from`/`parent_brief_id` empty.\n"
            f"  - UNDERREPRESENTED (~{n_under}): target the underrepresented "
            f"categories. Set `seeded_from='underrepresented_category'` and "
            f"leave `parent_brief_id` empty.\n"
            f"Exact counts can shift by ±1 if the signal genuinely warrants it."
        )
    return (
        f"You generate weekly DESIGN THEMES for a print-on-demand graphic-tee "
        f"shop. Themes drive Etsy probes and downstream image briefs.\n\n"
        f"BRAND VOICE: {brand_voice}\n\n"
        f"CATEGORIES (each theme MUST set `category` to one of these): {cats}\n\n"
        f"{feedback_block}\n\n"
        f"{split_block}\n\n"
        f"Return EXACTLY {n_themes} themes as a JSON array. Each theme:\n"
        f"  - theme_name: 3-7 words, evocative, specific\n"
        f"  - description: 1-2 sentences on what unifies this theme\n"
        f"  - category: one of the categories listed above\n"
        f"  - cultural_tension: the friction or in-joke this taps (1 sentence)\n"
        f"  - seeded_from / parent_brief_id: per the strategy above"
    )


# ── parsing ───────────────────────────────────────────────────────────────────

def _coerce_themes_payload(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [dict(t) for t in raw if isinstance(t, dict)]
    if isinstance(raw, dict):
        for key in ("themes", "results", "items", "data"):
            if isinstance(raw.get(key), list):
                return [dict(t) for t in raw[key] if isinstance(t, dict)]
    raise ValueError(f"Theme payload not a list or recognized object: {type(raw).__name__}")


def _build_themes(
    raw_themes: list[dict],
    *,
    run_id: str,
    valid_categories: set[str],
) -> list[Theme]:
    out: list[Theme] = []
    for t in raw_themes:
        cat = t.get("category") or ""
        if valid_categories and cat not in valid_categories:
            cat = next(iter(valid_categories))
        out.append(Theme(
            theme_id=str(uuid.uuid4()),
            run_id=run_id,
            theme_name=str(t.get("theme_name") or "").strip(),
            description=str(t.get("description") or "").strip(),
            category=cat,
            cultural_tension=str(t.get("cultural_tension") or "").strip(),
            seeded_from=(t.get("seeded_from") or None) or None,
            parent_brief_id=(t.get("parent_brief_id") or None) or None,
            notes=None,
        ))
    return out


# ── TF-IDF cosine dedup ───────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    if not tokens:
        return {}
    c = Counter(tokens)
    n = len(tokens)
    return {tok: cnt / n for tok, cnt in c.items()}


def _idf(corpus: list[list[str]]) -> dict[str, float]:
    n = len(corpus)
    df: Counter[str] = Counter()
    for doc in corpus:
        df.update(set(doc))
    return {tok: math.log((1 + n) / (1 + d)) + 1.0 for tok, d in df.items()}


def _vectorize(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = _tf(tokens)
    return {tok: w * idf.get(tok, 1.0) for tok, w in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _theme_text(name: str, tension: str) -> str:
    return f"{name} {tension}".strip()


def annotate_duplicates(
    themes: list[Theme],
    recent_themes: Iterable[dict],
    *,
    threshold: float = DEDUP_THRESHOLD,
) -> list[Theme]:
    """Mark themes whose cosine similarity to any recent theme exceeds threshold.

    `recent_themes` items must have keys `theme_name`, `cultural_tension`, and
    optionally `theme_id` (used in the notes annotation when present).
    """
    recent = list(recent_themes)
    if not recent or not themes:
        return themes

    new_docs = [_tokenize(_theme_text(t.theme_name, t.cultural_tension)) for t in themes]
    old_docs = [_tokenize(_theme_text(r.get("theme_name", ""), r.get("cultural_tension", "")))
                for r in recent]

    idf = _idf(new_docs + old_docs)
    new_vecs = [_vectorize(d, idf) for d in new_docs]
    old_vecs = [_vectorize(d, idf) for d in old_docs]

    out: list[Theme] = []
    for i, t in enumerate(themes):
        best_sim = 0.0
        best_id: Optional[str] = None
        for j, ov in enumerate(old_vecs):
            sim = _cosine(new_vecs[i], ov)
            if sim > best_sim:
                best_sim = sim
                best_id = recent[j].get("theme_id") or recent[j].get("theme_name")
        if best_sim >= threshold and best_id:
            note = f"near_duplicate_of:{best_id} (sim={best_sim:.2f})"
            existing = (t.notes or "").strip()
            t.notes = f"{existing} | {note}".strip(" |") if existing else note
        out.append(t)
    return out


def filter_unique(themes: list[Theme]) -> list[Theme]:
    return [t for t in themes if not (t.notes or "").startswith("near_duplicate_of:")]


# ── public entry point ────────────────────────────────────────────────────────

def generate_themes(
    *,
    gen_fn: Callable[..., Any],
    n_themes: int,
    brand_voice: str,
    categories: list[str],
    feedback_signal: dict,
    run_id: str,
    feedback_block: Optional[str] = None,
) -> list[Theme]:
    """Generate themes via `gen_fn` and tag near-duplicates against recent themes.

    `gen_fn` is called as `gen_fn(prompt, schema=THEME_SCHEMA)` and must return
    a list-of-dicts (or an object containing one). The returned `Theme` objects
    have fresh UUID `theme_id`s and run_id set. Near-duplicates are tagged via
    `notes`; orchestrator decides whether to keep or drop.
    """
    if feedback_block is None:
        from src.research.feedback import format_feedback_for_gemini
        feedback_block = format_feedback_for_gemini(feedback_signal)

    prompt = build_theme_prompt(
        n_themes=n_themes,
        brand_voice=brand_voice,
        categories=categories,
        feedback_block=feedback_block,
        is_cold_start=bool(feedback_signal.get("is_cold_start")),
    )
    raw = gen_fn(prompt, schema=THEME_SCHEMA)
    raw_themes = _coerce_themes_payload(raw)
    themes = _build_themes(raw_themes, run_id=run_id, valid_categories=set(categories))
    themes = annotate_duplicates(themes, feedback_signal.get("recently_explored_themes") or [])
    return themes
