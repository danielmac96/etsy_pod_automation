"""
supabase_sync.py
================
Syncs pod.db state to Supabase so Claude Tasks can read it, and reads
Claude Task-generated research seeds back into the Python pipeline.

Direction:
  pod.db → Supabase (pipeline_sync)     one-way write, Tasks read
  Supabase → Python (research_seeds)    one-way read, Tasks write

If SUPABASE_URL is unset (or the network is down), all functions log a
warning and return cleanly so the rest of the pipeline keeps running.

Adapted to the actual pod.db schema:
  - lineage.lineage_id (TEXT UUID), lineage.etsy_listing_url
  - listing_stats keyed by lineage_id, snapshots ordered by snapshot_id
  - concepts.selected (1 = selected/approved), concepts.headline_text
  - design_briefs.headline_text
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}


def _enabled() -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY not set — Supabase sync skipped")
        return False
    return True


def _upsert(table: str, rows: list[dict], on_conflict: str | None = None) -> None:
    if not rows:
        return
    if not _enabled():
        return
    params = {}
    if on_conflict:
        params["on_conflict"] = on_conflict
    try:
        r = httpx.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=params,
            json=rows,
            timeout=15,
        )
        r.raise_for_status()
        logger.info(f"Supabase upsert: {len(rows)} rows → {table}")
    except Exception as e:
        logger.warning(f"Supabase upsert to {table} failed: {e}")


def sync_live_listings(conn: sqlite3.Connection) -> int:
    """Push all live listings (those with an etsy_listing_url) to pipeline_sync.

    Joins the latest listing_stats snapshot per lineage_id so Claude Tasks
    can see the most recent favorites/views without re-fetching.
    """
    rows = conn.execute("""
        SELECT
            'live_listing'                            AS record_type,
            l.lineage_id                              AS record_id,
            NULL                                      AS external_id,
            l.etsy_listing_url                        AS etsy_url,
            COALESCE(l.etsy_title, b.headline_text)   AS label,
            'published'                               AS status,
            COALESCE(latest.favorites, 0)             AS favorites,
            COALESCE(latest.views, 0)                 AS views
        FROM lineage l
        LEFT JOIN design_briefs b ON b.brief_id = l.brief_id
        LEFT JOIN (
            SELECT ls.lineage_id, ls.favorites, ls.views
            FROM listing_stats ls
            WHERE ls.snapshot_id = (
                SELECT MAX(snapshot_id) FROM listing_stats WHERE lineage_id = ls.lineage_id
            )
        ) latest ON latest.lineage_id = l.lineage_id
        WHERE l.etsy_listing_url IS NOT NULL
    """).fetchall()
    payload = [dict(r) for r in rows]
    _upsert("pipeline_sync", payload, on_conflict="record_type,record_id")
    return len(payload)


def sync_approved_concepts(conn: sqlite3.Connection) -> int:
    """Push selected (approved) concepts so Tasks see recent design directions."""
    rows = conn.execute("""
        SELECT
            'concept'           AS record_type,
            c.concept_id        AS record_id,
            NULL                AS external_id,
            NULL                AS etsy_url,
            c.headline_text     AS label,
            'approved'          AS status,
            0                   AS favorites,
            0                   AS views
        FROM concepts c
        WHERE c.selected = 1
        ORDER BY c.created_at DESC
        LIMIT 50
    """).fetchall()
    payload = [dict(r) for r in rows]
    _upsert("pipeline_sync", payload, on_conflict="record_type,record_id")
    return len(payload)


def read_research_seeds_for_run(run_week: date) -> list[dict]:
    """Fetch seeds Claude Tasks generated for this week.

    Returns a list of dicts with keys (at minimum):
      seed_text, seed_type, priority_score, trend_verdict, trend_reasoning, source

    Returns [] on any failure so 01_research.py keeps running on its own
    feedback signal.
    """
    if not _enabled():
        return []
    try:
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/research_seeds",
            headers=HEADERS,
            params={
                "week_of": f"eq.{run_week.isoformat()}",
                "used_in_run": "is.null",
                "order": "priority_score.desc",
            },
            timeout=10,
        )
        r.raise_for_status()
        seeds = r.json()
        logger.info(f"Loaded {len(seeds)} research seeds from Supabase for week {run_week}")
        return seeds
    except Exception as e:
        logger.warning(f"Could not read Supabase seeds: {e} — falling back to feedback signal only")
        return []


def mark_seeds_used(run_id: str, seed_texts: list[str]) -> None:
    """Mark seeds as consumed by this run so they aren't re-used next week."""
    if not _enabled() or not seed_texts:
        return
    in_clause = ",".join(f'"{s}"' for s in seed_texts)
    try:
        r = httpx.patch(
            f"{SUPABASE_URL}/rest/v1/research_seeds",
            headers=HEADERS,
            params={"seed_text": f"in.({in_clause})", "used_in_run": "is.null"},
            json={"used_in_run": run_id},
            timeout=15,
        )
        r.raise_for_status()
        logger.info(f"Marked {len(seed_texts)} seeds as used by run {run_id}")
    except Exception as e:
        logger.warning(f"Could not mark seeds used: {e}")
