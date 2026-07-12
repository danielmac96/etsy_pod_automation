"""Tests for the publish gate (migration 0004, db helpers, printify helpers)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src import db
from src import printify
from src.config import env_flag, env_float


@pytest.fixture
def fresh_conn(tmp_path: Path):
    conn = db.connect(tmp_path / "test.db")
    db.run_migrations(conn)
    yield conn
    conn.close()


def _drafted_row(conn, **extra) -> str:
    lid = db.lineage_create(conn, prompt_text="p", category="Gym Flex")
    db.lineage_upsert(
        conn, lid,
        image_url="https://i.ibb.co/x/img.png",
        etsy_title="Test Tee",
        printify_draft_url="https://printify.com/app/shop/123/products/abc123def456/edit",
        **extra,
    )
    db.lineage_set_prompt_status(conn, lid, "approved")
    db.lineage_set_image_status(conn, lid, "approved")
    db.lineage_set_draft_status(conn, lid, "drafted")
    return lid


# ── publish_status column + helper ────────────────────────────────────────────

def test_publish_status_defaults_unreviewed(fresh_conn):
    lid = _drafted_row(fresh_conn)
    row = fresh_conn.execute(
        "SELECT publish_status FROM lineage WHERE lineage_id = ?", (lid,)
    ).fetchone()
    assert row["publish_status"] == "unreviewed"


def test_set_publish_status_rejects_typos(fresh_conn):
    lid = _drafted_row(fresh_conn)
    with pytest.raises(ValueError):
        db.lineage_set_publish_status(fresh_conn, lid, "publishedd")
    db.lineage_set_publish_status(fresh_conn, lid, "approved")


# ── stage queries ─────────────────────────────────────────────────────────────

def test_publish_review_stage_returns_drafted_unreviewed(fresh_conn):
    lid = _drafted_row(fresh_conn)
    pending = db.lineage_pending_for_stage(fresh_conn, "publish_review")
    assert [r["lineage_id"] for r in pending] == [lid]

    db.lineage_set_publish_status(fresh_conn, lid, "rejected")
    assert db.lineage_pending_for_stage(fresh_conn, "publish_review") == []


def test_publish_stage_returns_only_approved(fresh_conn):
    lid_a = _drafted_row(fresh_conn)
    _drafted_row(fresh_conn)  # stays unreviewed
    db.lineage_set_publish_status(fresh_conn, lid_a, "approved")

    ready = db.lineage_pending_for_stage(fresh_conn, "publish")
    assert [r["lineage_id"] for r in ready] == [lid_a]

    db.lineage_set_publish_status(fresh_conn, lid_a, "published")
    assert db.lineage_pending_for_stage(fresh_conn, "publish") == []


def test_ai_score_roundtrips_through_upsert(fresh_conn):
    lid = db.lineage_create(fresh_conn, prompt_text="p", category="Gym Flex")
    db.lineage_upsert(fresh_conn, lid, ai_score=7.5, ai_feedback="clean")
    row = fresh_conn.execute(
        "SELECT ai_score, ai_feedback FROM lineage WHERE lineage_id = ?", (lid,)
    ).fetchone()
    assert row["ai_score"] == 7.5
    assert row["ai_feedback"] == "clean"


def test_migration_backfills_published_rows(tmp_path: Path):
    # Simulate a pre-0004 db by applying only the first three migrations,
    # inserting a live listing, then applying 0004.
    import shutil
    partial_dir = tmp_path / "migrations"
    partial_dir.mkdir()
    for name in ("0001_init.sql", "0002_local_approval.sql", "0003_hot_signal.sql"):
        shutil.copy(db.MIGRATIONS_DIR / name, partial_dir / name)

    conn = db.connect(tmp_path / "old.db")
    db.run_migrations(conn, partial_dir)
    lid = db.lineage_create(conn, prompt_text="p", category="Gym Flex")
    db.lineage_upsert(conn, lid, etsy_listing_url="https://www.etsy.com/listing/1")

    applied = db.run_migrations(conn)  # full dir now includes 0004
    assert "0004_publish_automation.sql" in applied
    row = conn.execute(
        "SELECT publish_status FROM lineage WHERE lineage_id = ?", (lid,)
    ).fetchone()
    assert row["publish_status"] == "published"
    conn.close()


# ── printify helpers ──────────────────────────────────────────────────────────

def test_extract_product_id_from_draft_url():
    url = "https://printify.com/app/shop/123/products/64f1c0ffee/edit"
    assert printify.extract_product_id(url) == "64f1c0ffee"
    assert printify.extract_product_id(None) is None
    assert printify.extract_product_id("https://printify.com/app/shop/123") is None


def test_sanitize_etsy_tags_enforces_constraints():
    tags = printify.sanitize_etsy_tags(
        ["gym shirt", "GYM SHIRT", "  ", "a tag that is far too long for etsy"]
        + [f"tag{i}" for i in range(20)]
    )
    assert len(tags) == 13
    assert tags[0] == "gym shirt"
    assert "GYM SHIRT" not in tags  # case-insensitive dedup
    assert all(len(t) <= 20 for t in tags)


def test_select_variants_filters_color_size_grid():
    catalog = [
        {"id": 1, "title": "Black / S"},
        {"id": 2, "title": "Black / 3XL"},
        {"id": 3, "title": "White / M"},
        {"id": 4, "title": "Heather Navy / S"},
    ]
    picked = printify.select_variants(
        catalog, colors=["Black", "White"], sizes=["S", "M"], price_cents=2499
    )
    assert [v["id"] for v in picked] == [1, 3]
    assert all(v["price"] == 2499 and v["is_enabled"] for v in picked)


def test_build_product_payload_is_etsy_ready():
    variants = [{"id": 1, "price": 2499, "is_enabled": True}]
    payload = printify.build_product_payload(
        title="Test Tee",
        description="A great shirt.",
        tags=["gym", "office humor"],
        blueprint_id=6,
        print_provider_id=99,
        variants=variants,
        image_id="img-1",
        print_scale=0.75,
    )
    assert payload["title"] == "Test Tee"
    assert payload["description"] == "A great shirt."
    assert payload["tags"] == ["gym", "office humor"]
    assert payload["blueprint_id"] == 6
    assert payload["print_areas"][0]["variant_ids"] == [1]
    img = payload["print_areas"][0]["placeholders"][0]["images"][0]
    assert img["id"] == "img-1" and img["scale"] == 0.75


# ── config flags ──────────────────────────────────────────────────────────────

def test_env_flag_parsing(monkeypatch):
    monkeypatch.setenv("X_FLAG", "1")
    assert env_flag("X_FLAG") is True
    monkeypatch.setenv("X_FLAG", "true")
    assert env_flag("X_FLAG") is True
    monkeypatch.setenv("X_FLAG", "0")
    assert env_flag("X_FLAG") is False
    monkeypatch.delenv("X_FLAG")
    assert env_flag("X_FLAG") is False
    assert env_flag("X_FLAG", default=True) is True


def test_env_float_parsing(monkeypatch):
    monkeypatch.setenv("X_SCORE", "7.5")
    assert env_float("X_SCORE", 8.0) == 7.5
    monkeypatch.setenv("X_SCORE", "garbage")
    assert env_float("X_SCORE", 8.0) == 8.0
    monkeypatch.delenv("X_SCORE")
    assert env_float("X_SCORE", 8.0) == 8.0
