"""Phase F: prove the warm-start loop closes.

Seeds a prior-week run (theme → concept → brief → lineage → listing_stats with
a winning favorites_delta), then invokes load_feedback_signal +
themes.generate_themes warm-start. Asserts:
  - feedback signal carries the winner (not cold-start)
  - the Gemini prompt actually embeds the winning brief_id
  - >=2 returned themes set seeded_from='last_week_winner' with parent_brief_id
"""
from __future__ import annotations

import uuid
from pathlib import Path

from src import db
from src.db import (
    Concept,
    DesignBriefRow,
    EtsyListingRow,
    EtsyProbe,
    Theme,
)
from src.research import themes as theme_mod
from src.research.feedback import format_feedback_for_gemini

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = PROJECT_ROOT / "migrations"


def _seed_prior_week(conn) -> tuple[str, str]:
    """Plant one full prior-week chain. Returns (winner_brief_id, loser_brief_id)."""
    prior_run = "prior-run-1"
    db.create_run(conn, prior_run, {"prior": True}, "test brand voice")

    winner_theme = Theme(
        theme_id=str(uuid.uuid4()), run_id=prior_run,
        theme_name="Vader Spotter Gym", description="movie parody at the rack",
        category="Gym Humor", cultural_tension="lifting alone with a famous spotter",
    )
    loser_theme = Theme(
        theme_id=str(uuid.uuid4()), run_id=prior_run,
        theme_name="Office Chair Cardio", description="desk job tedium",
        category="Office Humor", cultural_tension="swivel chair workouts",
    )
    db.insert_themes(conn, [winner_theme, loser_theme])

    probe = EtsyProbe(
        probe_id=str(uuid.uuid4()), theme_id=winner_theme.theme_id,
        query="vader gym tee", intent="narrow", sort_on="score",
        listings_returned=1, cache_hit=False,
    )
    db.insert_probe(conn, probe)
    db.insert_listings(conn, [EtsyListingRow(
        listing_id=1322910664, probe_id=probe.probe_id,
        title="Vader Gym Tee", tags=["gym", "parody"], price_usd=24.99,
    )])

    winner_concept = Concept(
        concept_id=str(uuid.uuid4()), theme_id=winner_theme.theme_id,
        concept_name="Spot Me Sith", headline_text="Spot Me, I Am Your Father",
        visual_concept="Vader silhouette spotting a barbell",
        style_tags=["bold typography", "movie parody"],
        evidence_listing_ids=[1322910664], selected=True,
    )
    loser_concept = Concept(
        concept_id=str(uuid.uuid4()), theme_id=loser_theme.theme_id,
        concept_name="Swivel Sets", headline_text="Swivel Sets All Day",
        visual_concept="office chair as squat rack",
        style_tags=["minimalist"],
        evidence_listing_ids=[], selected=True,
    )
    db.insert_concepts(conn, [winner_concept, loser_concept])

    winner_brief = DesignBriefRow(
        brief_id=str(uuid.uuid4()), run_id=prior_run,
        concept_id=winner_concept.concept_id, rank=1, category="Gym Humor",
        headline_text="Spot Me, I Am Your Father",
        visual_concept="Vader silhouette spotting a barbell",
        style_tags=["bold typography", "movie parody"],
        image_prompt_seed="vader spotter ready for ideogram",
        saturation="medium", volume_signal="high", composite_score=0.78,
    )
    loser_brief = DesignBriefRow(
        brief_id=str(uuid.uuid4()), run_id=prior_run,
        concept_id=loser_concept.concept_id, rank=2, category="Office Humor",
        headline_text="Swivel Sets All Day",
        visual_concept="office chair as squat rack",
        style_tags=["minimalist"],
        image_prompt_seed="swivel chair sets ready for ideogram",
        saturation="medium", volume_signal="low", composite_score=0.42,
    )
    db.insert_briefs(conn, [winner_brief, loser_brief])
    db.finish_run(conn, prior_run)

    # Lineage + stats — winner picks up high favorites_delta, loser stays flat
    winner_page = "notion-page-winner"
    loser_page = "notion-page-loser"
    db.lineage_upsert(conn, winner_page,
                      brief_id=winner_brief.brief_id,
                      etsy_listing_url="https://www.etsy.com/listing/1001")
    db.lineage_upsert(conn, loser_page,
                      brief_id=loser_brief.brief_id,
                      etsy_listing_url="https://www.etsy.com/listing/1002")
    db.record_stats(conn, winner_page, views=100, favorites=5)
    db.record_stats(conn, winner_page, views=420, favorites=58)  # +53 favs
    db.record_stats(conn, loser_page, views=50, favorites=2)
    db.record_stats(conn, loser_page, views=58, favorites=3)     # +1 fav

    return winner_brief.brief_id, loser_brief.brief_id


def test_warm_start_seeds_themes_from_last_week_winners(tmp_path):
    db_path = tmp_path / "pod_phase_f.db"
    conn = db.connect(db_path)
    db.run_migrations(conn, MIGRATIONS)

    winner_brief_id, loser_brief_id = _seed_prior_week(conn)

    signal = db.load_feedback_signal(conn)
    assert signal["is_cold_start"] is False
    winners = signal["top_winning_briefs"]
    assert winners, "expected at least one winning brief"
    assert winners[0]["brief_id"] == winner_brief_id
    assert winners[0]["favorites_delta_total"] >= 50

    block = format_feedback_for_gemini(signal)
    assert winner_brief_id in block, "feedback block must embed the winning brief_id"
    assert "Vader Spotter Gym" in block

    captured: dict = {}

    def gen_fn(prompt, schema=None):
        captured["prompt"] = prompt
        # Two exploit themes seeded off the winner, two explore, one underrep.
        return [
            {"theme_name": "Sith Lord Spotting Sequel",
             "description": "lean into the Vader-spotter gag with new angles",
             "category": "Gym Humor",
             "cultural_tension": "famous fictional villains as gym partners",
             "seeded_from": "last_week_winner",
             "parent_brief_id": winner_brief_id},
            {"theme_name": "Galactic PR Day",
             "description": "more star-wars gym riffs",
             "category": "Gym Humor",
             "cultural_tension": "lifting in a galaxy far far away",
             "seeded_from": "last_week_winner",
             "parent_brief_id": winner_brief_id},
            {"theme_name": "Stand-up Desk Discontent",
             "description": "pure exploration, no parent",
             "category": "Office Humor",
             "cultural_tension": "ergonomic theater"},
            {"theme_name": "Quiet Quitting Cardio",
             "description": "novel office tension",
             "category": "Office Humor",
             "cultural_tension": "doing the bare minimum, beautifully"},
            {"theme_name": "Underrated Underrep",
             "description": "fills the underrepresented category bucket",
             "category": "Office Humor",
             "cultural_tension": "beige cubicle elegies",
             "seeded_from": "underrepresented_category"},
        ]

    new_run_id = "run-warm-1"
    db.create_run(conn, new_run_id, {"warm": True}, "test brand voice")

    themes = theme_mod.generate_themes(
        gen_fn=gen_fn, n_themes=5, brand_voice="test",
        categories=["Gym Humor", "Office Humor"],
        feedback_signal=signal, run_id=new_run_id, feedback_block=block,
    )

    # The prompt must reflect warm-start strategy and embed the winner's brief_id.
    assert "EXPLOIT" in captured["prompt"]
    assert winner_brief_id in captured["prompt"]
    assert "cold start" not in captured["prompt"].lower()

    seeded = [t for t in themes if t.seeded_from == "last_week_winner"]
    assert len(seeded) >= 2, f"expected >=2 last_week_winner themes, got {len(seeded)}"
    for t in seeded:
        assert t.parent_brief_id == winner_brief_id

    # Loser brief must NOT have been promoted as a parent.
    assert all(t.parent_brief_id != loser_brief_id for t in themes)

    conn.close()
