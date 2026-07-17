from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from src import db


@pytest.fixture
def fresh_conn(tmp_path: Path):
    conn = db.connect(tmp_path / "test.db")
    db.run_migrations(conn)
    yield conn
    conn.close()


def _make_run(conn, run_id: str | None = None) -> str:
    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
    db.create_run(conn, rid, {"k": "v"}, brand_voice="sardonic")
    return rid


def _make_theme(conn, run_id: str, theme_id: str | None = None) -> str:
    tid = theme_id or f"theme-{uuid.uuid4().hex[:8]}"
    db.insert_theme(conn, db.Theme(
        theme_id=tid, run_id=run_id, theme_name="Test Theme",
        description="desc", category="Corporate Grind", cultural_tension="t",
    ))
    return tid


def _make_concept(conn, theme_id: str) -> str:
    cid = f"concept-{uuid.uuid4().hex[:8]}"
    db.insert_concept(conn, db.Concept(
        concept_id=cid, theme_id=theme_id, concept_name="C", headline_text="H",
        visual_concept="V", style_tags=["minimalist", "vector"],
        evidence_listing_ids=[1, 2],
    ))
    return cid


def _make_brief(conn, run_id: str, concept_id: str, *, rank: int = 1,
                category: str = "Corporate Grind", style_tags=None) -> str:
    bid = f"brief-{uuid.uuid4().hex[:8]}"
    db.insert_brief(conn, db.DesignBriefRow(
        brief_id=bid, run_id=run_id, concept_id=concept_id, rank=rank,
        category=category, headline_text="H", visual_concept="V",
        style_tags=style_tags or ["minimalist"], image_prompt_seed="seed",
        saturation="medium", volume_signal="high", composite_score=0.8,
    ))
    return bid


# ── tests ──────────────────────────────────────────────────────────────────────

def test_migrations_idempotent(tmp_path: Path):
    conn = db.connect(tmp_path / "x.db")
    first = db.run_migrations(conn)
    second = db.run_migrations(conn)
    assert first == ["0001_init.sql", "0002_local_approval.sql", "0003_hot_signal.sql",
                     "0004_publish_automation.sql"]
    assert second == []
    rows = list(conn.execute("SELECT filename FROM schema_migrations"))
    assert len(rows) == len(first)


def test_fk_themes_to_runs(fresh_conn):
    rid = _make_run(fresh_conn)
    tid = _make_theme(fresh_conn, rid)
    row = fresh_conn.execute(
        "SELECT run_id FROM themes WHERE theme_id = ?", (tid,)
    ).fetchone()
    assert row["run_id"] == rid

    # FK enforcement: theme with bogus run_id must fail
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_theme(fresh_conn, db.Theme(
            theme_id="orphan", run_id="does-not-exist", theme_name="T",
            description="d", category="X", cultural_tension="t",
        ))


def test_listing_stats_deltas(fresh_conn):
    rid = _make_run(fresh_conn)
    tid = _make_theme(fresh_conn, rid)
    cid = _make_concept(fresh_conn, tid)
    bid = _make_brief(fresh_conn, rid, cid)
    page = "page-abc"
    db.lineage_upsert(fresh_conn, page, brief_id=bid)

    db.record_stats(fresh_conn, page, views=100, favorites=10)
    db.record_stats(fresh_conn, page, views=150, favorites=18)

    rows = list(fresh_conn.execute(
        "SELECT views, favorites, views_delta, favorites_delta "
        "FROM listing_stats WHERE lineage_id = ? ORDER BY snapshot_id",
        (page,),
    ))
    assert len(rows) == 2
    assert rows[0]["views_delta"] is None
    assert rows[0]["favorites_delta"] is None
    assert rows[1]["views_delta"] == 50
    assert rows[1]["favorites_delta"] == 8


def test_lineage_upsert_partial_update(fresh_conn):
    rid = _make_run(fresh_conn)
    tid = _make_theme(fresh_conn, rid)
    cid = _make_concept(fresh_conn, tid)
    bid = _make_brief(fresh_conn, rid, cid)

    db.lineage_upsert(fresh_conn, "p1", brief_id=bid, prompt_text="proof of life")
    db.lineage_upsert(fresh_conn, "p1", image_url="https://x")
    row = fresh_conn.execute("SELECT * FROM lineage WHERE lineage_id = 'p1'").fetchone()
    assert row["brief_id"] == bid
    assert row["prompt_text"] == "proof of life"
    assert row["image_url"] == "https://x"
    assert row["printify_draft_url"] is None


def test_feedback_signal_cold_start(fresh_conn):
    sig = db.load_feedback_signal(fresh_conn)
    assert sig["is_cold_start"] is True
    assert sig["top_winning_briefs"] == []
    assert sig["underrepresented_categories"] == []
    assert sig["winning_style_tags"] == []
    assert sig["recently_explored_themes"] == []
    assert sig["rejection_signal"] == {
        "rejected_by_category": [], "recent_rejected_prompts": [],
    }


def test_rejection_signal_counts_prompt_and_image_rejects(fresh_conn):
    p1 = db.lineage_create(fresh_conn, prompt_text="bad pun about mondays",
                           category="Corporate Grind")
    db.lineage_set_prompt_status(fresh_conn, p1, "rejected")

    p2 = db.lineage_create(fresh_conn, prompt_text="garbled render prompt",
                           category="Gym Flex")
    db.lineage_set_prompt_status(fresh_conn, p2, "approved")
    db.lineage_upsert(fresh_conn, p2, image_url="https://x",
                      ai_feedback="text is illegible")
    db.lineage_set_image_status(fresh_conn, p2, "rejected")

    p3 = db.lineage_create(fresh_conn, prompt_text="a keeper",
                           category="Gym Flex")
    db.lineage_set_prompt_status(fresh_conn, p3, "approved")

    sig = db.load_rejection_signal(fresh_conn)
    by_cat = {r["category"]: r for r in sig["rejected_by_category"]}
    assert by_cat["Corporate Grind"]["prompts_rejected"] == 1
    assert by_cat["Gym Flex"]["images_rejected"] == 1

    samples = sig["recent_rejected_prompts"]
    assert len(samples) == 2
    gates = {s["prompt_text"]: s["rejected_at"] for s in samples}
    assert gates["bad pun about mondays"] == "prompt"
    assert gates["garbled render prompt"] == "image"
    image_reject = next(s for s in samples if s["rejected_at"] == "image")
    assert image_reject["ai_feedback"] == "text is illegible"


def test_rejection_signal_included_even_on_cold_start(fresh_conn):
    lid = db.lineage_create(fresh_conn, prompt_text="rejected on day one",
                            category="Iron Discipline")
    db.lineage_set_prompt_status(fresh_conn, lid, "rejected")

    sig = db.load_feedback_signal(fresh_conn)
    assert sig["is_cold_start"] is True
    assert sig["rejection_signal"]["rejected_by_category"][0]["category"] == "Iron Discipline"


def test_lineage_create_defaults(fresh_conn):
    rid = _make_run(fresh_conn)
    tid = _make_theme(fresh_conn, rid)
    cid = _make_concept(fresh_conn, tid)
    bid = _make_brief(fresh_conn, rid, cid)
    lid = db.lineage_create(fresh_conn, brief_id=bid, prompt_text="t",
                            category="Gym Flex")
    row = fresh_conn.execute(
        "SELECT * FROM lineage WHERE lineage_id = ?", (lid,)
    ).fetchone()
    assert row["prompt_status"] == "unreviewed"
    assert row["image_status"] == "unreviewed"
    assert row["draft_status"] == "pending"
    assert row["category"] == "Gym Flex"


def test_lineage_set_status_validates(fresh_conn):
    lid = db.lineage_create(fresh_conn, prompt_text="x")
    db.lineage_set_prompt_status(fresh_conn, lid, "approved")
    db.lineage_set_image_status(fresh_conn, lid, "rejected")
    db.lineage_set_draft_status(fresh_conn, lid, "drafted")
    row = fresh_conn.execute(
        "SELECT prompt_status, image_status, draft_status FROM lineage WHERE lineage_id = ?",
        (lid,),
    ).fetchone()
    assert (row["prompt_status"], row["image_status"], row["draft_status"]) == \
           ("approved", "rejected", "drafted")
    with pytest.raises(ValueError):
        db.lineage_set_prompt_status(fresh_conn, lid, "bogus")


def test_lineage_pending_for_stage(fresh_conn):
    a = db.lineage_create(fresh_conn, prompt_text="A")
    b = db.lineage_create(fresh_conn, prompt_text="B")
    db.lineage_set_prompt_status(fresh_conn, a, "approved")  # ready for image_gen
    # b stays unreviewed → ready for prompt_review

    pending_review = db.lineage_pending_for_stage(fresh_conn, "prompt_review")
    assert {r["lineage_id"] for r in pending_review} == {b}

    pending_image_gen = db.lineage_pending_for_stage(fresh_conn, "image_gen")
    assert {r["lineage_id"] for r in pending_image_gen} == {a}

    # Single approval gate: once a prompt is approved and has an image, it is
    # immediately ready for copy generation — no separate image-review gate.
    db.lineage_upsert(fresh_conn, a, image_url="https://x")
    pending_copy = db.lineage_pending_for_stage(fresh_conn, "copy_gen")
    assert {r["lineage_id"] for r in pending_copy} == {a}

    # The image_review stage was removed — it must raise like any unknown stage.
    with pytest.raises(ValueError):
        db.lineage_pending_for_stage(fresh_conn, "image_review")
    with pytest.raises(ValueError):
        db.lineage_pending_for_stage(fresh_conn, "nope")


def test_feedback_signal_with_data(fresh_conn):
    rid = _make_run(fresh_conn)
    tid = _make_theme(fresh_conn, rid)
    cid_winner = _make_concept(fresh_conn, tid)
    cid_loser = _make_concept(fresh_conn, tid)
    winner = _make_brief(fresh_conn, rid, cid_winner, rank=1,
                         style_tags=["minimalist", "screen-print"])
    loser = _make_brief(fresh_conn, rid, cid_loser, rank=2,
                        style_tags=["watercolor"])

    db.lineage_upsert(fresh_conn, "winner-page", brief_id=winner,
                      etsy_listing_url="https://etsy/winner")
    db.lineage_upsert(fresh_conn, "loser-page", brief_id=loser,
                      etsy_listing_url="https://etsy/loser")

    db.record_stats(fresh_conn, "winner-page", views=100, favorites=10)
    db.record_stats(fresh_conn, "winner-page", views=300, favorites=80)
    db.record_stats(fresh_conn, "loser-page", views=50, favorites=5)
    db.record_stats(fresh_conn, "loser-page", views=55, favorites=6)

    sig = db.load_feedback_signal(fresh_conn)
    assert sig["is_cold_start"] is False
    assert sig["weeks_analyzed"] == 4
    assert len(sig["top_winning_briefs"]) >= 1
    assert sig["top_winning_briefs"][0]["brief_id"] == winner
    assert sig["top_winning_briefs"][0]["favorites_delta_total"] == 70
    assert "Test Theme" in sig["top_winning_briefs"][0]["themes"]

    tag_names = {t["tag"] for t in sig["winning_style_tags"]}
    assert "minimalist" in tag_names or "screen-print" in tag_names

    assert any(t["theme_name"] == "Test Theme" for t in sig["recently_explored_themes"])
