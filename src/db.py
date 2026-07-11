"""SQLite persistence for the self-iterating Etsy POD pipeline.

pod.db is the system of record: it holds the analytical brain (themes,
probes, listings, concepts, briefs, stats) and the human-approval state
(lineage.prompt_status / image_status / draft_status). The Streamlit
local app at scripts/approve_app.py is the human UI.

Migration files live under migrations/ and are applied in lexical order.
schema_migrations is bootstrapped here in code to avoid a chicken-and-egg
with the first migration file.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── connection ────────────────────────────────────────────────────────────────

def connect(path: str | Path = "pod.db", *, check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    applied = {r["filename"] for r in conn.execute("SELECT filename FROM schema_migrations")}
    newly: list[str] = []
    # Only pod.db (SQLite) migration files are digit-prefixed (e.g. "0001_init.sql").
    # Other *.sql files in this dir (like supabase_schema.sql) target other engines
    # and are applied manually — never load them into SQLite.
    for f in sorted(migrations_dir.glob("[0-9]*.sql")):
        if f.name in applied:
            continue
        sql = f.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(filename, applied_at) VALUES (?, ?)",
            (f.name, _now()),
        )
        conn.commit()
        newly.append(f.name)
    return newly


# ── dataclasses for typed inputs ──────────────────────────────────────────────

@dataclass
class Theme:
    theme_id: str
    run_id: str
    theme_name: str
    description: str
    category: str
    cultural_tension: str
    seeded_from: Optional[str] = None
    parent_brief_id: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class EtsyProbe:
    probe_id: str
    theme_id: str
    query: str
    intent: str
    sort_on: str
    listings_returned: int
    cache_hit: bool
    raw_response_path: Optional[str] = None


@dataclass
class EtsyListingRow:
    listing_id: int
    probe_id: str
    title: str
    tags: list
    price_usd: Optional[float] = None
    currency_code: Optional[str] = None
    num_favorers: Optional[int] = None
    views: Optional[int] = None
    shop_id: Optional[int] = None
    taxonomy_path: Optional[list] = None
    is_digital: Optional[bool] = None
    is_personalizable: Optional[bool] = None
    creation_tsz: Optional[int] = None
    listing_url: Optional[str] = None
    primary_image_url: Optional[str] = None


@dataclass
class Concept:
    concept_id: str
    theme_id: str
    concept_name: str
    headline_text: str
    visual_concept: str
    style_tags: list
    evidence_listing_ids: list
    color_palette_hint: Optional[str] = None
    target_buyer: Optional[str] = None
    differentiation_note: Optional[str] = None
    selected: bool = False
    rejection_reason: Optional[str] = None


@dataclass
class DesignBriefRow:
    brief_id: str
    run_id: str
    concept_id: str
    rank: int
    category: str
    headline_text: str
    visual_concept: str
    style_tags: list
    image_prompt_seed: str
    saturation: str
    volume_signal: str
    composite_score: float
    color_palette_hint: Optional[str] = None
    target_buyer: Optional[str] = None
    price_p25_usd: Optional[float] = None
    price_p75_usd: Optional[float] = None


# ── research_runs ─────────────────────────────────────────────────────────────

def create_run(conn, run_id: str, config: dict, brand_voice: str, notes: Optional[str] = None) -> str:
    cfg_json = json.dumps(config, sort_keys=True, default=str)
    cfg_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO research_runs(run_id, started_at, finished_at, config_json, "
        "config_hash, brand_voice, notes) VALUES (?, ?, NULL, ?, ?, ?, ?)",
        (run_id, _now(), cfg_json, cfg_hash, brand_voice, notes),
    )
    conn.commit()
    return run_id


def finish_run(conn, run_id: str) -> None:
    conn.execute("UPDATE research_runs SET finished_at = ? WHERE run_id = ?", (_now(), run_id))
    conn.commit()


# ── themes ────────────────────────────────────────────────────────────────────

def insert_theme(conn, t: Theme) -> str:
    conn.execute(
        "INSERT INTO themes(theme_id, run_id, theme_name, description, category, "
        "cultural_tension, seeded_from, parent_brief_id, created_at, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (t.theme_id, t.run_id, t.theme_name, t.description, t.category,
         t.cultural_tension, t.seeded_from, t.parent_brief_id, _now(), t.notes),
    )
    conn.commit()
    return t.theme_id


def insert_themes(conn, themes: list[Theme]) -> list[str]:
    return [insert_theme(conn, t) for t in themes]


# ── etsy_probes ───────────────────────────────────────────────────────────────

def insert_probe(conn, p: EtsyProbe) -> str:
    conn.execute(
        "INSERT INTO etsy_probes(probe_id, theme_id, query, intent, sort_on, "
        "listings_returned, cache_hit, fetched_at, raw_response_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (p.probe_id, p.theme_id, p.query, p.intent, p.sort_on,
         p.listings_returned, int(p.cache_hit), _now(), p.raw_response_path),
    )
    conn.commit()
    return p.probe_id


# ── etsy_listings ─────────────────────────────────────────────────────────────

def insert_listings(conn, listings: list[EtsyListingRow]) -> int:
    rows = [
        (
            L.listing_id, L.probe_id, L.title, json.dumps(L.tags),
            L.price_usd, L.currency_code, L.num_favorers, L.views, L.shop_id,
            json.dumps(L.taxonomy_path) if L.taxonomy_path is not None else None,
            int(L.is_digital) if L.is_digital is not None else None,
            int(L.is_personalizable) if L.is_personalizable is not None else None,
            L.creation_tsz, L.listing_url, L.primary_image_url,
        )
        for L in listings
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO etsy_listings(listing_id, probe_id, title, tags_json, "
        "price_usd, currency_code, num_favorers, views, shop_id, taxonomy_path_json, "
        "is_digital, is_personalizable, creation_tsz, listing_url, primary_image_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


# ── concepts ──────────────────────────────────────────────────────────────────

def insert_concept(conn, c: Concept) -> str:
    conn.execute(
        "INSERT INTO concepts(concept_id, theme_id, concept_name, headline_text, "
        "visual_concept, style_tags_json, color_palette_hint, target_buyer, "
        "differentiation_note, evidence_listing_ids_json, selected, rejection_reason, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (c.concept_id, c.theme_id, c.concept_name, c.headline_text, c.visual_concept,
         json.dumps(c.style_tags), c.color_palette_hint, c.target_buyer,
         c.differentiation_note, json.dumps(c.evidence_listing_ids),
         int(c.selected), c.rejection_reason, _now()),
    )
    conn.commit()
    return c.concept_id


def insert_concepts(conn, concepts: list[Concept]) -> list[str]:
    return [insert_concept(conn, c) for c in concepts]


def mark_concepts_selected(conn, concept_ids: list[str]) -> None:
    conn.executemany(
        "UPDATE concepts SET selected = 1 WHERE concept_id = ?",
        [(cid,) for cid in concept_ids],
    )
    conn.commit()


# ── design_briefs ─────────────────────────────────────────────────────────────

def insert_brief(conn, b: DesignBriefRow) -> str:
    conn.execute(
        "INSERT INTO design_briefs(brief_id, run_id, concept_id, rank, category, "
        "headline_text, visual_concept, style_tags_json, color_palette_hint, "
        "target_buyer, image_prompt_seed, saturation, volume_signal, price_p25_usd, "
        "price_p75_usd, composite_score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (b.brief_id, b.run_id, b.concept_id, b.rank, b.category, b.headline_text,
         b.visual_concept, json.dumps(b.style_tags), b.color_palette_hint, b.target_buyer,
         b.image_prompt_seed, b.saturation, b.volume_signal, b.price_p25_usd,
         b.price_p75_usd, b.composite_score, _now()),
    )
    conn.commit()
    return b.brief_id


def insert_briefs(conn, briefs: list[DesignBriefRow]) -> list[str]:
    return [insert_brief(conn, b) for b in briefs]


# ── lineage ───────────────────────────────────────────────────────────────────

LINEAGE_FIELDS = (
    "brief_id", "prompt_text", "image_url", "printify_draft_url", "etsy_listing_url",
    "category", "etsy_title", "etsy_description", "etsy_tags_json",
    "prompt_status", "image_status", "draft_status", "publish_status",
    "ai_score", "ai_feedback",
)

_PROMPT_STATUSES  = {"unreviewed", "approved", "rejected"}
_IMAGE_STATUSES   = {"unreviewed", "approved", "rejected"}
_DRAFT_STATUSES   = {"pending", "drafted", "published"}
_PUBLISH_STATUSES = {"unreviewed", "approved", "rejected", "published"}


def lineage_create(
    conn,
    *,
    brief_id: Optional[str] = None,
    prompt_text: Optional[str] = None,
    category: Optional[str] = None,
    lineage_id: Optional[str] = None,
) -> str:
    """Create a fresh lineage row for a new design and return its lineage_id.

    Use this in 02_generate_prompts when a brief becomes a candidate. All
    status columns get their schema defaults (prompt_status='unreviewed').
    """
    lid = lineage_id or uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO lineage(lineage_id, brief_id, prompt_text, category, "
        "created_at, last_updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (lid, brief_id, prompt_text, category, now, now),
    )
    conn.commit()
    return lid


def lineage_upsert(conn, lineage_id: str, **fields: Any) -> None:
    valid = {k: v for k, v in fields.items() if k in LINEAGE_FIELDS}
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM lineage WHERE lineage_id = ?", (lineage_id,)
    ).fetchone()
    if not existing:
        cols = ["lineage_id", "created_at", "last_updated_at", *valid.keys()]
        placeholders = ", ".join("?" * len(cols))
        vals = [lineage_id, now, now, *valid.values()]
        conn.execute(
            f"INSERT INTO lineage({', '.join(cols)}) VALUES ({placeholders})", vals
        )
    elif valid:
        sets = ", ".join(f"{k} = ?" for k in valid) + ", last_updated_at = ?"
        vals = [*valid.values(), now, lineage_id]
        conn.execute(f"UPDATE lineage SET {sets} WHERE lineage_id = ?", vals)
    else:
        conn.execute(
            "UPDATE lineage SET last_updated_at = ? WHERE lineage_id = ?",
            (now, lineage_id),
        )
    conn.commit()


def lineage_set_prompt_status(conn, lineage_id: str, status: str) -> None:
    if status not in _PROMPT_STATUSES:
        raise ValueError(f"prompt_status must be one of {_PROMPT_STATUSES}, got {status!r}")
    conn.execute(
        "UPDATE lineage SET prompt_status = ?, last_updated_at = ? WHERE lineage_id = ?",
        (status, _now(), lineage_id),
    )
    conn.commit()


def lineage_set_image_status(conn, lineage_id: str, status: str) -> None:
    if status not in _IMAGE_STATUSES:
        raise ValueError(f"image_status must be one of {_IMAGE_STATUSES}, got {status!r}")
    conn.execute(
        "UPDATE lineage SET image_status = ?, last_updated_at = ? WHERE lineage_id = ?",
        (status, _now(), lineage_id),
    )
    conn.commit()


def lineage_set_draft_status(conn, lineage_id: str, status: str) -> None:
    if status not in _DRAFT_STATUSES:
        raise ValueError(f"draft_status must be one of {_DRAFT_STATUSES}, got {status!r}")
    conn.execute(
        "UPDATE lineage SET draft_status = ?, last_updated_at = ? WHERE lineage_id = ?",
        (status, _now(), lineage_id),
    )
    conn.commit()


def lineage_set_publish_status(conn, lineage_id: str, status: str) -> None:
    if status not in _PUBLISH_STATUSES:
        raise ValueError(f"publish_status must be one of {_PUBLISH_STATUSES}, got {status!r}")
    conn.execute(
        "UPDATE lineage SET publish_status = ?, last_updated_at = ? WHERE lineage_id = ?",
        (status, _now(), lineage_id),
    )
    conn.commit()


def lineage_pending_for_stage(conn, stage: str) -> list[sqlite3.Row]:
    """Return lineage rows that are ready for the next pipeline stage.

    stage values:
      'prompt_review'  — prompt_status='unreviewed' (Streamlit Mon tab)
      'image_gen'      — prompt_status='approved' AND image_url IS NULL (script 03)
      'image_review'   — image_status='unreviewed' AND image_url IS NOT NULL (Wed tab)
      'copy_gen'       — image_status='approved' AND etsy_title IS NULL (script 04)
      'draft_create'   — etsy_title IS NOT NULL AND printify_draft_url IS NULL
                         AND image_status='approved' (script 06)
      'publish_review' — draft_status='drafted' AND publish_status='unreviewed'
                         (Streamlit Publish tab — the Etsy-listing-fee cost gate)
      'publish'        — publish_status='approved' AND draft_status='drafted'
                         AND printify_draft_url IS NOT NULL (script 08)
      'etsy_publish'   — draft_status='drafted' AND etsy_listing_url IS NULL
                         (Streamlit Listings tab + script 07 auto-detect)
      'stats_sync'     — etsy_listing_url IS NOT NULL (script 07)
    """
    where = {
        "prompt_review":  "prompt_status = 'unreviewed'",
        "image_gen":      "prompt_status = 'approved' AND image_url IS NULL",
        "image_review":   "image_status = 'unreviewed' AND image_url IS NOT NULL",
        "copy_gen":       "image_status = 'approved' AND etsy_title IS NULL",
        "draft_create":   "etsy_title IS NOT NULL AND printify_draft_url IS NULL "
                          "AND image_status = 'approved'",
        "publish_review": "draft_status = 'drafted' AND publish_status = 'unreviewed'",
        "publish":        "publish_status = 'approved' AND draft_status = 'drafted' "
                          "AND printify_draft_url IS NOT NULL",
        "etsy_publish":   "draft_status = 'drafted' AND etsy_listing_url IS NULL",
        "stats_sync":     "etsy_listing_url IS NOT NULL",
    }.get(stage)
    if where is None:
        raise ValueError(f"unknown stage {stage!r}")
    return list(conn.execute(
        f"SELECT * FROM lineage WHERE {where} ORDER BY created_at"
    ))


# ── listing_stats ─────────────────────────────────────────────────────────────

def record_stats(conn, lineage_id: str, views: int, favorites: int) -> int:
    prev = conn.execute(
        "SELECT views, favorites FROM listing_stats WHERE lineage_id = ? "
        "ORDER BY snapshot_at DESC, snapshot_id DESC LIMIT 1",
        (lineage_id,),
    ).fetchone()
    views_delta = (views - prev["views"]) if prev else None
    favorites_delta = (favorites - prev["favorites"]) if prev else None
    cur = conn.execute(
        "INSERT INTO listing_stats(lineage_id, snapshot_at, views, favorites, "
        "views_delta, favorites_delta) VALUES (?, ?, ?, ?, ?, ?)",
        (lineage_id, _now(), views, favorites, views_delta, favorites_delta),
    )
    conn.commit()
    return cur.lastrowid


# ── feedback signal ───────────────────────────────────────────────────────────

def load_feedback_signal(conn, weeks: int = 4) -> dict:
    """Return the feedback dict consumed by themes.generate_themes.

    Cold-start sentinel when listing_stats is empty.
    """
    has_stats = conn.execute("SELECT 1 FROM listing_stats LIMIT 1").fetchone()
    if not has_stats:
        return {
            "is_cold_start": True,
            "weeks_analyzed": 0,
            "top_winning_briefs": [],
            "underrepresented_categories": [],
            "winning_style_tags": [],
            "recently_explored_themes": [],
        }

    cutoff_days = weeks * 7
    cutoff = f"-{cutoff_days} days"

    top_rows = conn.execute(
        """
        SELECT b.brief_id, b.category, b.headline_text,
               COALESCE(SUM(s.favorites_delta), 0) AS fav_total
        FROM listing_stats s
        JOIN lineage l ON l.lineage_id = s.lineage_id
        JOIN design_briefs b ON b.brief_id = l.brief_id
        WHERE s.favorites_delta IS NOT NULL
          AND s.snapshot_at >= datetime('now', ?)
        GROUP BY b.brief_id
        ORDER BY fav_total DESC
        LIMIT 5
        """,
        (cutoff,),
    ).fetchall()

    top_winning_briefs = []
    for r in top_rows:
        themes = conn.execute(
            """
            SELECT DISTINCT t.theme_name FROM themes t
            JOIN concepts c ON c.theme_id = t.theme_id
            JOIN design_briefs b ON b.concept_id = c.concept_id
            WHERE b.brief_id = ?
            """,
            (r["brief_id"],),
        ).fetchall()
        top_winning_briefs.append({
            "brief_id": r["brief_id"],
            "category": r["category"],
            "headline_text": r["headline_text"],
            "favorites_delta_total": r["fav_total"],
            "themes": [t["theme_name"] for t in themes],
        })

    underrep = conn.execute(
        """
        SELECT b.category, COUNT(DISTINCT l.lineage_id) AS published_count
        FROM design_briefs b
        JOIN lineage l ON l.brief_id = b.brief_id
        WHERE l.etsy_listing_url IS NOT NULL
        GROUP BY b.category
        ORDER BY published_count ASC
        LIMIT 3
        """
    ).fetchall()

    tag_rows = conn.execute(
        """
        WITH ranked AS (
            SELECT b.brief_id, b.style_tags_json,
                   COALESCE(SUM(s.favorites_delta), 0) AS fav_total
            FROM listing_stats s
            JOIN lineage l ON l.lineage_id = s.lineage_id
            JOIN design_briefs b ON b.brief_id = l.brief_id
            WHERE s.favorites_delta IS NOT NULL
            GROUP BY b.brief_id
        )
        SELECT j.value AS tag,
               COUNT(*) AS frequency,
               AVG(r.fav_total) AS avg_favorites_delta
        FROM ranked r, json_each(r.style_tags_json) j
        WHERE r.fav_total > 0
        GROUP BY tag
        ORDER BY avg_favorites_delta DESC, frequency DESC
        LIMIT 10
        """
    ).fetchall()

    recent_themes = conn.execute(
        """
        SELECT DISTINCT theme_name, cultural_tension, run_id
        FROM themes
        WHERE created_at >= datetime('now', '-56 days')
        ORDER BY created_at DESC
        """
    ).fetchall()

    return {
        "is_cold_start": False,
        "weeks_analyzed": weeks,
        "top_winning_briefs": top_winning_briefs,
        "underrepresented_categories": [
            {"category": r["category"], "published_count": r["published_count"]} for r in underrep
        ],
        "winning_style_tags": [
            {"tag": r["tag"], "frequency": r["frequency"],
             "avg_favorites_delta": float(r["avg_favorites_delta"])}
            for r in tag_rows
        ],
        "recently_explored_themes": [
            {"theme_name": r["theme_name"], "cultural_tension": r["cultural_tension"],
             "run_id": r["run_id"]}
            for r in recent_themes
        ],
    }
