#!/usr/bin/env python3
"""Pipeline inspection and dry-run tool.

Reads pod.db only; the Streamlit app at scripts/approve_app.py is the
human-facing UI. This script is for terminal-side debugging.

Usage:
  python scripts/test_pipeline.py                  # full report (files + pod.db)
  python scripts/test_pipeline.py files            # checkpoint file status only
  python scripts/test_pipeline.py state            # pod.db pipeline state
  python scripts/test_pipeline.py db               # pod.db schema + counts + last 5 lineage/listing_stats
  python scripts/test_pipeline.py lineage <brief>  # full theme→concept→brief→lineage→stats chain
  python scripts/test_pipeline.py validate <N>     # check inputs for step N (01,02,03,04,06,07)
  python scripts/test_pipeline.py dry-run 01       # preview keyword generation
  python scripts/test_pipeline.py dry-run 02       # preview 1 prompt per category
  python scripts/test_pipeline.py dry-run 04       # preview copy for first image_status='approved' lineage row
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

DB_PATH_DEFAULT = str(PROJECT_ROOT / "pod.db")


# ── display helpers ─────────────────────────────────────────────────────────────

W = 64

def section(title: str):
    pad = max(W - len(title) - 6, 2)
    print(f"\n\033[1m{'-' * 3} {title} {'-' * pad}\033[0m")

def ok(msg):   print(f"  \033[32m[OK]\033[0m  {msg}")
def warn(msg): print(f"  \033[33m[!]\033[0m  {msg}")
def fail(msg): print(f"  \033[31m[X]\033[0m  {msg}")
def info(msg): print(f"     {msg}")
def blank():   print()


# ── pod.db connection ──────────────────────────────────────────────────────────

def _open_db():
    from src import db as _db
    db_path = Path(os.environ.get("POD_DB_PATH") or DB_PATH_DEFAULT)
    conn = _db.connect(db_path)
    _db.run_migrations(conn)
    return conn, _db, db_path


# ── checkpoint file inspection ─────────────────────────────────────────────────

def inspect_keywords_json():
    section("01 output → keywords.json")
    p = Path("keywords.json")
    if not p.exists():
        warn("Not found — 01 may not have run yet, or this is a fresh checkout")
        return
    data = json.loads(p.read_text())
    if not data:
        fail("File is empty")
        return
    if isinstance(data[0], dict):
        gemini_kws = [k for k in data if k.get("source") == "gemini"]
        etsy_kws   = [k for k in data if k.get("source") == "etsy"]
        ok(f"{len(data)} keywords total — {len(gemini_kws)} gemini, {len(etsy_kws)} etsy")
        for k in data[:8]:
            info(f"[{k.get('source', '?'):6}]  {k['keyword']}")
        if len(data) > 8:
            info(f"... and {len(data) - 8} more")
    else:
        ok(f"{len(data)} keywords")
        for k in data[:8]:
            info(k)


def inspect_prompts_json():
    section("02 output → prompts.json (audit log)")
    p = Path("prompts.json")
    if not p.exists():
        warn("Not found — 02 may not have run yet")
        return
    data = json.loads(p.read_text())
    by_cat: dict[str, list] = {}
    for item in data:
        by_cat.setdefault(item.get("category", "Unknown"), []).append(item)
    ok(f"{len(data)} prompts across {len(by_cat)} categories")
    for cat, prompts in by_cat.items():
        info(f"\033[1m[{cat}]\033[0m — {len(prompts)} prompt(s)")
        info(f"  {prompts[0].get('prompt','')[:95]}...")


def inspect_images_results():
    section("03 output → images/results.json (audit log)")
    p = Path("images/results.json")
    if not p.exists():
        warn("Not found — 03 may not have run yet")
        return
    data = json.loads(p.read_text())
    ok(f"{len(data)} image records")
    for r in data[:10]:
        lid = r.get("lineage_id") or r.get("page_id") or "n/a"
        info(f"lineage={lid[:8]}  imgbb={r.get('imgbb_url','n/a')}")
        info(f"  prompt: {r.get('prompt','')[:80]}...")


def inspect_notify_context():
    section("notify_context.json (latest stage written)")
    p = Path("notify_context.json")
    if not p.exists():
        warn("Not found — written at the end of each pipeline stage")
        return
    data = json.loads(p.read_text())
    ok(f"Stage: \033[1m{data.get('stage', '?')}\033[0m   Count: {data.get('count', '?')}")
    info(data.get("detail", ""))


def inspect_all_files():
    section("CHECKPOINT FILE STATUS")
    inspect_keywords_json()
    inspect_prompts_json()
    inspect_images_results()
    inspect_notify_context()


# ── pod.db pipeline state ──────────────────────────────────────────────────────

def pipeline_state():
    section("POD.DB PIPELINE STATE")
    conn, _, db_path = _open_db()
    info(f"Path: {db_path}")
    blank()

    counts = {
        "Prompts unreviewed":  conn.execute("SELECT COUNT(*) c FROM lineage WHERE prompt_status='unreviewed'").fetchone()["c"],
        "Prompts approved":    conn.execute("SELECT COUNT(*) c FROM lineage WHERE prompt_status='approved' AND image_url IS NULL").fetchone()["c"],
        "Prompts rejected":    conn.execute("SELECT COUNT(*) c FROM lineage WHERE prompt_status='rejected'").fetchone()["c"],
        "Images unreviewed":   conn.execute("SELECT COUNT(*) c FROM lineage WHERE image_status='unreviewed' AND image_url IS NOT NULL").fetchone()["c"],
        "Images approved":     conn.execute("SELECT COUNT(*) c FROM lineage WHERE image_status='approved' AND etsy_title IS NULL").fetchone()["c"],
        "Images rejected":     conn.execute("SELECT COUNT(*) c FROM lineage WHERE image_status='rejected'").fetchone()["c"],
        "Copy generated":      conn.execute("SELECT COUNT(*) c FROM lineage WHERE etsy_title IS NOT NULL AND printify_draft_url IS NULL").fetchone()["c"],
        "Drafted (no Etsy URL)": conn.execute("SELECT COUNT(*) c FROM lineage WHERE draft_status='drafted' AND etsy_listing_url IS NULL").fetchone()["c"],
        "Published":           conn.execute("SELECT COUNT(*) c FROM lineage WHERE draft_status='published'").fetchone()["c"],
    }
    total = conn.execute("SELECT COUNT(*) c FROM lineage").fetchone()["c"]

    print(f"     Total lineage rows: {total}")
    blank()
    for label, n in counts.items():
        if n == 0:
            continue
        bar = "█" * min(n, 30)
        print(f"     {label:<24} {bar} {n}")

    blank()
    if counts["Prompts unreviewed"]:
        warn(f"{counts['Prompts unreviewed']} prompts awaiting approval — open `streamlit run scripts/approve_app.py`")
    if counts["Images unreviewed"]:
        warn(f"{counts['Images unreviewed']} images awaiting approval — open the local app")
    if counts["Drafted (no Etsy URL)"]:
        warn(f"{counts['Drafted (no Etsy URL)']} drafts in Printify — publish to Etsy and let stats sync auto-detect")

    conn.close()


# ── validate step inputs ───────────────────────────────────────────────────────

def _env_check(var: str, required: bool = True):
    val = os.environ.get(var, "")
    if val:
        ok(f"{var} is set")
    elif required:
        fail(f"{var} is not set")
    else:
        warn(f"{var} is not set (optional)")


def validate_step(step: str):
    section(f"VALIDATE: inputs for step {step}")

    if step == "01":
        ok("No file inputs required — generates from hardcoded seed themes")
        blank()
        _env_check("GEMINI_API_KEY")
        _env_check("ETSY_API_KEY", required=False)
        return

    if step == "02":
        p = Path("design_briefs.json")
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            briefs = data.get("briefs", [])
            ok(f"design_briefs.json exists — {len(briefs)} briefs")
        else:
            warn("design_briefs.json not found — 02 will run without market-research context")
        blank()
        _env_check("GEMINI_API_KEY")
        return

    conn, _db, _ = _open_db()

    if step == "03":
        n = len(_db.lineage_pending_for_stage(conn, "image_gen"))
        if n: ok(f"{n} lineage rows ready for image generation")
        else: fail("0 rows with prompt_status='approved' and no image — approve prompts in the local app")
        blank()
        _env_check("FAL_KEY"); _env_check("IMGBB_API_KEY")
    elif step == "04":
        n = len(_db.lineage_pending_for_stage(conn, "copy_gen"))
        if n: ok(f"{n} lineage rows ready for copy generation")
        else: fail("0 rows with image_status='approved' and no copy — approve images in the local app")
        blank()
        _env_check("GEMINI_API_KEY")
    elif step == "06":
        n = len(_db.lineage_pending_for_stage(conn, "draft_create"))
        if n: ok(f"{n} lineage rows ready for Printify draft")
        else: fail("0 rows with copy and no Printify draft — run 04_generate_copy.py first")
        blank()
        _env_check("PRINTIFY_API_KEY"); _env_check("PRINTIFY_SHOP_ID")
    elif step == "07":
        n = len(_db.lineage_pending_for_stage(conn, "stats_sync"))
        if n: ok(f"{n} lineage rows with Etsy URLs — ready for stats sync")
        else: warn("0 published rows yet — auto-detect step needs ETSY_SHOP_ID and at least one drafted listing live on Etsy")
        blank()
        _env_check("ETSY_API_KEY")
        _env_check("ETSY_ACCESS_TOKEN", required=False)
        _env_check("ETSY_SHOP_ID", required=False)
    else:
        fail(f"Unknown step: '{step}'")
        info("Valid steps: 01  02  03  04  06  07")

    conn.close()


# ── dry-run previews ───────────────────────────────────────────────────────────

def dry_run_01():
    section("DRY RUN: 01_research.py — theme + Etsy preview (no file writes)")

    import random
    from google import genai as _genai
    from gemini_client import generate_json
    from etsy_client import EtsyClient

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail("GEMINI_API_KEY not set")
        return

    gemini = _genai.Client(api_key=api_key)

    SEED_THEMES = [
        "hybrid athlete burnout", "tech bro who CrossFits",
        "quarterly review as a PR attempt", "deload week is a meeting",
        "spreadsheets and squats", "commute as pre-workout",
    ]
    prompt = f"""You are a creative director for a gym/corporate culture apparel brand (sardonic, meme-aware).

Brainstorm 5 fresh micro-trend theme names for t-shirt designs — concrete cultural tensions, not generic gym motivation.
Examples: {json.dumps(random.sample(SEED_THEMES, 4))}

Return ONLY a JSON array of 5 theme name strings."""

    info("Calling Gemini for sample themes...")
    themes = generate_json(gemini, prompt)
    blank()
    ok(f"Gemini returned {len(themes)} sample themes")
    for t in themes:
        info(f"  [theme]  {t}")

    etsy_key = os.environ.get("ETSY_API_KEY", "")
    if not etsy_key:
        warn("ETSY_API_KEY not set — Etsy listing preview skipped")
        return

    info("Fetching top Etsy listings for 'gym shirt funny'...")
    try:
        etsy = EtsyClient(api_key=etsy_key, cache_dir=Path("runs/dry_run_cache"))
        result = etsy.search_listings("gym shirt funny", limit=5)
        blank()
        ok(f"{len(result.listings)} listings returned")
        for L in result.listings:
            info(f"  favs={L.num_favorers:<5}  {L.title[:70]}")
            if L.tags:
                info(f"           tags: {', '.join(L.tags[:6])}")
    except Exception as e:
        warn(f"Etsy call failed: {e}")


def dry_run_02():
    section("DRY RUN: 02_generate_prompts.py — 1 prompt per category (no DB writes)")

    from google import genai as _genai
    from gemini_client import generate_json

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail("GEMINI_API_KEY not set")
        return

    gemini = _genai.Client(api_key=api_key)

    CATEGORIES = {
        "Corporate Grind":   "Office frustration, meetings, burnout — gym as the only escape valve",
        "Iron Discipline":   "Powerlifting philosophy, 5am club, PRs, consistency as identity",
        "Cardio Confession": "The lifter doing cardio under protest — step goals, zone-2 irony",
        "Recovery Mode":     "Rest days, deload weeks, overtrained and overworked",
        "Gym Flex":          "PR celebrations, bro culture humor, gym memes, chalk and straps",
    }

    info(f"Calling Gemini for 1 sample prompt per category ({len(CATEGORIES)} calls)...")
    blank()

    for category, description in CATEGORIES.items():
        prompt = f"""You are a witty copywriter for a viral Etsy shop selling gym + corporate culture graphic tees.

Category: {category}
Theme: {description}

Generate 1 image generation prompt for a screen-print t-shirt in the "{category}" category.
Must include: a specific joke or relatable moment, the shirt slogan in quotes, and art direction
(flat vector, bold typography, two colors, screen print ready, pure white background).

Return ONLY a JSON string (a single string, not an array)."""
        result = generate_json(gemini, prompt)
        if isinstance(result, list):
            result = result[0]
        print(f"     \033[1m[{category}]\033[0m")
        info(result)
        blank()


def dry_run_04():
    section("DRY RUN: 04_generate_copy.py — copy preview (no DB writes)")

    from google import genai as _genai
    from gemini_client import generate_json

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail("GEMINI_API_KEY not set")
        return

    conn, _db, _ = _open_db()
    pending = _db.lineage_pending_for_stage(conn, "copy_gen")
    if not pending:
        # Fall back to any image-approved row (even if copy already done)
        pending = list(conn.execute(
            "SELECT * FROM lineage WHERE image_status='approved' "
            "ORDER BY last_updated_at DESC LIMIT 1"
        ))
    if not pending:
        fail("No image-approved lineage rows found — approve some images in the local app first")
        conn.close()
        return

    row = pending[0]
    cat = row["category"] or ""
    prompt_text = row["prompt_text"] or ""
    info(f"Using lineage : {row['lineage_id'][:8]}...")
    info(f"Category      : {cat or '(none)'}")
    info(f"Prompt        : {prompt_text[:100]}...")
    blank()

    gemini = _genai.Client(api_key=api_key)
    gemini_prompt = f"""You are an Etsy SEO copywriter for a gym + corporate culture graphic tee shop.

Design prompt: {prompt_text}
Design category: {cat}

Write Etsy product copy. Return a JSON object with exactly these keys:
- "title": max 140 chars, SEO-optimized, no ALL CAPS
- "description": 3-4 sentences — hook with the humor, describe the shirt, name the audience, CTA
- "tags": list of exactly 13 Etsy search tags, each max 20 chars

Return ONLY valid JSON, no explanation, no markdown."""
    info("Calling Gemini...")
    data = generate_json(gemini, gemini_prompt)

    blank()
    ok(f"Title  ({len(data.get('title', ''))} chars):")
    info(data.get("title", ""))
    blank()
    ok("Description:")
    info(data.get("description", ""))
    blank()
    tags = data.get("tags", [])
    ok(f"Tags  ({len(tags)} provided):")
    info(", ".join(str(t) for t in tags))
    blank()
    warn("Nothing written to pod.db — preview only")
    conn.close()


# ── pod.db state dump ──────────────────────────────────────────────────────────

def db_state():
    conn, _db, db_path = _open_db()

    section("POD.DB STATE")
    info(f"Path: {db_path}")

    section("ROW COUNTS")
    tables = [
        "research_runs", "themes", "etsy_probes", "etsy_listings",
        "concepts", "design_briefs", "lineage", "listing_stats",
        "schema_migrations",
    ]
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        info(f"{t:<22} {n}")

    section("LAST 3 research_runs")
    rows = list(conn.execute(
        "SELECT run_id, started_at, finished_at, brand_voice "
        "FROM research_runs ORDER BY started_at DESC LIMIT 3"
    ))
    if not rows: info("(none)")
    for r in rows:
        info(f"{r['run_id']}  started={r['started_at']}  finished={r['finished_at'] or '-'}  voice={r['brand_voice']}")

    section("FEEDBACK SIGNAL")
    sig = _db.load_feedback_signal(conn)
    print(json.dumps(sig, indent=2, default=str))

    section("LAST 5 lineage rows")
    rows = list(conn.execute(
        "SELECT lineage_id, brief_id, prompt_status, image_status, draft_status, "
        "image_url, printify_draft_url, etsy_listing_url, last_updated_at "
        "FROM lineage ORDER BY last_updated_at DESC LIMIT 5"
    ))
    if not rows: info("(none)")
    for r in rows:
        info(
            f"id={r['lineage_id'][:8]}.. "
            f"brief={(r['brief_id'] or '-')[:8]}.. "
            f"P={r['prompt_status'][0].upper()} I={r['image_status'][0].upper()} D={r['draft_status'][0].upper()} "
            f"img={'Y' if r['image_url'] else '-'} "
            f"draft={'Y' if r['printify_draft_url'] else '-'} "
            f"etsy={'Y' if r['etsy_listing_url'] else '-'} "
            f"updated={r['last_updated_at']}"
        )

    section("LAST 5 listing_stats rows")
    rows = list(conn.execute(
        "SELECT lineage_id, snapshot_at, views, favorites, "
        "views_delta, favorites_delta "
        "FROM listing_stats ORDER BY snapshot_at DESC, snapshot_id DESC LIMIT 5"
    ))
    if not rows: info("(none)")
    for r in rows:
        vd = "—" if r["views_delta"] is None else f"+{r['views_delta']}"
        fd = "—" if r["favorites_delta"] is None else f"+{r['favorites_delta']}"
        info(f"id={r['lineage_id'][:8]}.. at={r['snapshot_at']} "
             f"views={r['views']} ({vd}) favs={r['favorites']} ({fd})")
    conn.close()


def lineage_chain(brief_id: str) -> None:
    conn, _db, db_path = _open_db()

    section(f"LINEAGE for brief_id={brief_id}")
    info(f"Path: {db_path}")

    brief = conn.execute(
        "SELECT b.brief_id, b.run_id, b.concept_id, b.rank, b.category, "
        "b.headline_text, b.composite_score, b.created_at "
        "FROM design_briefs b WHERE b.brief_id = ?",
        (brief_id,),
    ).fetchone()
    if not brief:
        fail(f"No design_briefs row with brief_id={brief_id}")
        conn.close()
        return

    concept = conn.execute(
        "SELECT concept_id, theme_id, concept_name, headline_text "
        "FROM concepts WHERE concept_id = ?",
        (brief["concept_id"],),
    ).fetchone()
    theme = conn.execute(
        "SELECT theme_id, run_id, theme_name, category, cultural_tension, "
        "seeded_from, parent_brief_id "
        "FROM themes WHERE theme_id = ?",
        (concept["theme_id"] if concept else None,),
    ).fetchone() if concept else None
    run = conn.execute(
        "SELECT run_id, started_at, finished_at FROM research_runs WHERE run_id = ?",
        (brief["run_id"],),
    ).fetchone()

    section("RUN")
    if run:
        info(f"run_id={run['run_id']}  started={run['started_at']}  finished={run['finished_at'] or '-'}")
    section("THEME")
    if theme:
        info(f"theme_id={theme['theme_id']}")
        info(f"name=[{theme['category']}] {theme['theme_name']}")
        info(f"tension={theme['cultural_tension']}")
        info(f"seeded_from={theme['seeded_from'] or '-'}  parent_brief_id={theme['parent_brief_id'] or '-'}")
    section("CONCEPT")
    if concept:
        info(f"concept_id={concept['concept_id']}")
        info(f"name={concept['concept_name']}")
        info(f"headline={concept['headline_text']}")
    section("BRIEF")
    info(f"brief_id={brief['brief_id']}  rank={brief['rank']}  composite={brief['composite_score']}")
    info(f"category={brief['category']}  headline={brief['headline_text']}")
    info(f"created_at={brief['created_at']}")

    section("LINEAGE rows referencing this brief")
    rows = list(conn.execute(
        "SELECT lineage_id, prompt_status, image_status, draft_status, "
        "prompt_text, image_url, printify_draft_url, etsy_listing_url, last_updated_at "
        "FROM lineage WHERE brief_id = ? ORDER BY last_updated_at DESC",
        (brief_id,),
    ))
    if not rows:
        info("(none — brief never made it to a lineage row)")
    for r in rows:
        info(f"lineage_id={r['lineage_id']}")
        info(f"  status: prompt={r['prompt_status']} image={r['image_status']} draft={r['draft_status']}")
        info(f"  prompt={(r['prompt_text'] or '')[:80]}")
        info(f"  image={r['image_url'] or '-'}")
        info(f"  printify={r['printify_draft_url'] or '-'}")
        info(f"  etsy={r['etsy_listing_url'] or '-'}  updated={r['last_updated_at']}")

        stats = list(conn.execute(
            "SELECT snapshot_at, views, favorites, views_delta, favorites_delta "
            "FROM listing_stats WHERE lineage_id = ? "
            "ORDER BY snapshot_at DESC LIMIT 5",
            (r["lineage_id"],),
        ))
        if not stats:
            info("  stats: (none yet)")
        for s in stats:
            vd = "—" if s["views_delta"] is None else f"+{s['views_delta']}"
            fd = "—" if s["favorites_delta"] is None else f"+{s['favorites_delta']}"
            info(f"  stats {s['snapshot_at']}: views={s['views']} ({vd}) favs={s['favorites']} ({fd})")
    conn.close()


# ── entry point ────────────────────────────────────────────────────────────────

USAGE = """
  python scripts/test_pipeline.py                  full report (files + pod.db)
  python scripts/test_pipeline.py files            checkpoint file status only
  python scripts/test_pipeline.py state            pod.db pipeline state
  python scripts/test_pipeline.py db               pod.db row counts + last 5 lineage/listing_stats
  python scripts/test_pipeline.py lineage <brief>  full theme→concept→brief→lineage→stats chain
  python scripts/test_pipeline.py validate <N>     check inputs for step N (01,02,03,04,06,07)
  python scripts/test_pipeline.py dry-run  01      preview keyword generation
  python scripts/test_pipeline.py dry-run  02      preview 1 prompt per category
  python scripts/test_pipeline.py dry-run  04      preview copy for first image-approved lineage row
"""


def main():
    args = sys.argv[1:]
    if not args:
        inspect_all_files()
        pipeline_state()
        return

    cmd = args[0].lower()

    if cmd == "files":
        inspect_all_files()
    elif cmd in ("state", "notion"):  # 'notion' kept as a soft alias for muscle-memory
        pipeline_state()
    elif cmd == "db":
        db_state()
    elif cmd == "lineage":
        if len(args) < 2:
            fail("Usage: test_pipeline.py lineage <brief_id>")
        else:
            lineage_chain(args[1])
    elif cmd == "validate":
        validate_step(args[1] if len(args) > 1 else "")
    elif cmd == "dry-run":
        step = args[1] if len(args) > 1 else ""
        if step == "01":   dry_run_01()
        elif step == "02": dry_run_02()
        elif step == "04": dry_run_04()
        else:
            fail(f"dry-run not available for step '{step}'")
            info("Available: 01  02  04")
    elif cmd in ("help", "--help", "-h"):
        print(USAGE)
    else:
        fail(f"Unknown command: '{cmd}'")
        print(USAGE)


if __name__ == "__main__":
    main()
