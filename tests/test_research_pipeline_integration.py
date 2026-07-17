"""End-to-end Phase-D smoke test with mocked Etsy + Gemini.

Exercises feedback → themes → probes → mining → concepts → synthesis →
db.insert_briefs path with the same Pydantic+dataclass plumbing the
orchestrator script uses. Asserts non-zero rows in every analytical table.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from src import db
from src.db import EtsyListingRow
from src.research import concepts as concept_mod
from src.research import probes as probe_mod
from src.research import synthesis as synth_mod
from src.research import themes as theme_mod
from src.research.feedback import format_feedback_for_gemini, load_feedback_signal
from src.research.mining import mine_theme

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "etsy_search_sample.json"
MIGRATIONS = PROJECT_ROOT / "migrations"


class _FakeSearchResult:
    def __init__(self, listings, cache_hit=False, raw_response_path=None):
        self.listings = listings
        self.cache_hit = cache_hit
        self.raw_response_path = raw_response_path
        self.pagination = {}


class _FakeEtsy:
    """Returns parsed EtsyListings from the fixture for every search."""
    def __init__(self):
        from src.schemas import EtsyListing
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self._listings = [EtsyListing.from_v3(r) for r in raw["results"]]
        self.calls = []

    def search_listings(self, query, *, limit=50, sort_on="score", **kw):
        self.calls.append((query, sort_on))
        return _FakeSearchResult(list(self._listings))


def _make_gen_fn():
    """Closure-based fake gen_fn that responds based on prompt content."""
    state = {"theme_count": 0}

    def gen(prompt, schema=None):
        # Order matters: check synthesis (which embeds concept JSON) first.
        if "chief curator" in prompt:
            import re
            ids = re.findall(r'"concept_id"\s*:\s*"([0-9a-f-]+)"', prompt)
            ranked = []
            for i, cid in enumerate(ids[:3]):
                ranked.append({
                    "concept_id": cid,
                    "rank": i + 1,
                    "image_prompt_seed": f"{cid[:8]} :: ready for ideogram",
                    "scores": {
                        "originality": 0.7, "voice_fit": 0.8,
                        "image_feasibility": 0.9, "winner_similarity_bonus": 0.0,
                    },
                })
            return ranked
        if "weekly DESIGN THEMES" in prompt:
            return [
                {"theme_name": "Vader Spotter Gym", "description": "movie parody",
                 "category": "Gym Humor",
                 "cultural_tension": "lifting alone with a famous spotter"},
                {"theme_name": "Office Chair Cardio", "description": "desk job tedium",
                 "category": "Office Humor",
                 "cultural_tension": "swivel chair workouts"},
            ]
        if "Etsy search probes" in prompt:
            return [
                {"query": "gym shirt funny", "intent": "broad"},
                {"query": "vader gym tee", "intent": "narrow"},
                {"query": "spotter shirt parody", "intent": "narrow"},
            ]
        if "designing graphic-tee CONCEPTS" in prompt:
            return [
                {
                    "concept_name": "Spot Me Sith",
                    "headline_text": "Spot Me, I Am Your Father",
                    "visual_concept": "Vader silhouette spotting a barbell",
                    "style_tags": ["bold typography", "movie parody"],
                    "color_palette_hint": "black + chrome",
                    "target_buyer": "lifters who like Star Wars",
                    "differentiation_note": "first parody pairing",
                    "evidence_listing_ids": [1322910664, 1280922413],
                },
                {
                    "concept_name": "Solo Lift Saga",
                    "headline_text": "Lifting Alone, Watching Reps Roll By",
                    "visual_concept": "starfield + barbell silhouette",
                    "style_tags": ["retro", "minimalist"],
                    "color_palette_hint": "navy + cream",
                    "target_buyer": "introverted gym goers",
                    "differentiation_note": "no parody crowding",
                    "evidence_listing_ids": [1322910664],
                },
            ]
        raise AssertionError(f"unexpected prompt: {prompt[:150]}")

    return gen


def test_full_pipeline_writes_all_six_analytical_tables(tmp_path):
    db_path = tmp_path / "pod_smoke.db"
    conn = db.connect(db_path)
    db.run_migrations(conn, MIGRATIONS)

    run_id = "run-smoke-1"
    db.create_run(conn, run_id, {"smoke": True}, "test brand voice")

    signal = load_feedback_signal(conn)  # cold start
    block = format_feedback_for_gemini(signal)
    assert "cold start" in block

    gen = _make_gen_fn()
    themes = theme_mod.generate_themes(
        gen_fn=gen, n_themes=2, brand_voice="test",
        categories=["Gym Humor", "Office Humor"],
        feedback_signal=signal, run_id=run_id, feedback_block=block,
    )
    db.insert_themes(conn, themes)
    assert len(themes) == 2

    fake_etsy = _FakeEtsy()
    theme_listings: dict[str, list[EtsyListingRow]] = {}
    theme_mining: dict[str, dict] = {}
    for t in themes:
        probes = probe_mod.generate_probes(gen_fn=gen, theme=t)
        rows_per_probe = probe_mod.run_probes(
            etsy_client=fake_etsy, theme=t, probes=probes, listings_per_probe=5,
        )
        for probe, rows in rows_per_probe:
            db.insert_probe(conn, probe)
            if rows:
                db.insert_listings(conn, rows)
        theme_listings[t.theme_id] = probe_mod.deduplicate_listings(rows_per_probe)
        theme_mining[t.theme_id] = mine_theme(theme_listings[t.theme_id])

    all_concepts = []
    for t in themes:
        cs = concept_mod.extract_concepts(
            gen_fn=gen, theme=t, listings=theme_listings[t.theme_id],
            mining=theme_mining[t.theme_id],
        )
        all_concepts.extend(cs)
        db.insert_concepts(conn, cs)
    assert len(all_concepts) >= 2

    briefs = synth_mod.synthesize_briefs(
        gen_fn=gen, run_id=run_id, themes=themes, concepts=all_concepts,
        theme_mining=theme_mining, feedback_signal=signal,
        brand_voice="test", final_count=2,
    )
    db.mark_concepts_selected(conn, [b.concept_id for b in briefs])
    db.insert_briefs(conn, briefs)
    db.finish_run(conn, run_id)
    assert len(briefs) >= 1

    # Lineage upsert (mimics 02_generate_prompts.py)
    page_id = "notion-page-smoke-1"
    db.lineage_upsert(conn, page_id, brief_id=briefs[0].brief_id,
                      prompt_text="ideogram prompt: ...")

    counts = {
        t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        for t in ("research_runs", "themes", "etsy_probes", "etsy_listings",
                  "concepts", "design_briefs", "lineage")
    }
    for table, c in counts.items():
        assert c >= 1, f"{table} has 0 rows"

    # Selected concepts mirror the briefs
    selected = conn.execute("SELECT COUNT(*) AS c FROM concepts WHERE selected=1").fetchone()["c"]
    assert selected == len(briefs)

    # Lineage row carries the brief_id we just upserted
    row = conn.execute(
        "SELECT brief_id, prompt_text FROM lineage WHERE lineage_id = ?",
        (page_id,),
    ).fetchone()
    assert row["brief_id"] == briefs[0].brief_id
    assert row["prompt_text"].startswith("ideogram")

    conn.close()
