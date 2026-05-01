#!/usr/bin/env python3
"""
Pipeline inspection and dry-run tool.

Usage:
  python scripts/test_pipeline.py                  # Full report: checkpoint files + Notion state
  python scripts/test_pipeline.py files            # Checkpoint file status only
  python scripts/test_pipeline.py notion           # Notion DB pipeline state only
  python scripts/test_pipeline.py validate 01      # Check env vars for step 01
  python scripts/test_pipeline.py validate 02      # Check keywords.json + env vars
  python scripts/test_pipeline.py validate 03      # Check Prompt Approved count + env vars
  python scripts/test_pipeline.py validate 04      # Check Image Approved count + env vars
  python scripts/test_pipeline.py validate 06      # Check Copy Generated count + env vars
  python scripts/test_pipeline.py validate 07      # Check Etsy listing URLs + env vars
  python scripts/test_pipeline.py dry-run 01       # Preview keywords from Gemini+Etsy (no write)
  python scripts/test_pipeline.py dry-run 02       # Preview 1 prompt per category (no Notion write)
  python scripts/test_pipeline.py dry-run 04       # Preview copy for first Image Approved page (no write)
  python scripts/test_pipeline.py db                # Inspect pod.db (counts + last 5 lineage/listing_stats rows)
  python scripts/test_pipeline.py lineage <brief_id># Print theme→concept→brief→lineage→stats chain for a brief
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
import notion_fields as nf

load_dotenv()

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DB_ID = os.environ.get("NOTION_DATABASE_ID", "")

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


# ── checkpoint file inspection ─────────────────────────────────────────────────

def inspect_keywords_json():
    section("01 output → keywords.json")
    p = Path("keywords.json")
    if not p.exists():
        fail("Not found — run 01_research.py first")
        return

    data = json.loads(p.read_text())
    if not data:
        fail("File is empty")
        return

    if isinstance(data[0], dict):
        gemini_kws = [k for k in data if k.get("source") == "gemini"]
        etsy_kws   = [k for k in data if k.get("source") == "etsy"]
        ok(f"{len(data)} keywords total — {len(gemini_kws)} gemini, {len(etsy_kws)} etsy")
        blank()
        for k in data[:8]:
            info(f"[{k.get('source', '?'):6}]  {k['keyword']}")
        if len(data) > 8:
            info(f"... and {len(data) - 8} more")
    else:
        ok(f"{len(data)} keywords (pre-annotation format — source not tracked)")
        for k in data[:8]:
            info(k)
        if len(data) > 8:
            info(f"... and {len(data) - 8} more")


def inspect_prompts_json():
    section("02 output → prompts.json  (audit log)")
    p = Path("prompts.json")
    if not p.exists():
        fail("Not found — run 02_generate_prompts.py first")
        return

    data = json.loads(p.read_text())
    by_cat: dict[str, list] = {}
    for item in data:
        cat = item.get("category", "Unknown")
        by_cat.setdefault(cat, []).append(item.get("prompt", ""))

    ok(f"{len(data)} prompts across {len(by_cat)} categories")
    blank()
    for cat, prompts in by_cat.items():
        info(f"\033[1m[{cat}]\033[0m — {len(prompts)} prompt(s)")
        info(f"  {prompts[0][:95]}...")
        blank()


def inspect_images_results():
    section("03 output → images/results.json  (audit log)")
    p = Path("images/results.json")
    if not p.exists():
        fail("Not found — run 03_generate_images.py first")
        return

    data = json.loads(p.read_text())
    ok(f"{len(data)} image records")
    blank()
    for r in data:
        pid    = r.get("page_id", "n/a")
        url    = r.get("imgbb_url", "n/a")
        prompt = r.get("prompt", "")[:80]
        info(f"page  : {pid[:8]}...")
        info(f"imgbb : {url}")
        info(f"prompt: {prompt}...")
        blank()


def inspect_notify_context():
    section("notify_context.json  (latest stage written)")
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


# ── Notion pipeline state ──────────────────────────────────────────────────────

def _query_notion_all() -> list[dict]:
    hdrs = nf.notion_headers(NOTION_TOKEN)
    pages, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{DB_ID}/query",
            headers=hdrs, json=body,
        )
        r.raise_for_status()
        result = r.json()
        pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return pages


def notion_state():
    section("NOTION DATABASE STATE")

    if not NOTION_TOKEN or not DB_ID:
        fail("NOTION_TOKEN or NOTION_DATABASE_ID not set in environment")
        return

    pages = _query_notion_all()

    counts: dict[str, int] = {}
    for p in pages:
        sel    = (p["properties"].get(nf.PIPELINE_STATUS, {}).get("select") or {})
        status = sel.get("name", "Unknown")
        counts[status] = counts.get(status, 0) + 1

    STATUS_ORDER = [
        nf.STATUS_PROMPT_UNREVIEWED,
        nf.STATUS_PROMPT_APPROVED,
        nf.STATUS_PROMPT_REJECTED,
        nf.STATUS_IMAGE_UNREVIEWED,
        nf.STATUS_IMAGE_APPROVED,
        nf.STATUS_IMAGE_REJECTED,
        nf.STATUS_COPY_GENERATED,
        nf.STATUS_DRAFTED,
        nf.STATUS_PUBLISHED,
        "Unknown",
    ]

    blank()
    print(f"     Total pages: {len(pages)}")
    blank()
    for status in STATUS_ORDER:
        n = counts.get(status, 0)
        if n == 0:
            continue
        bar = "█" * min(n, 30)
        print(f"     {status:<30} {bar} {n}")

    # Actionable prompts
    blank()
    if counts.get(nf.STATUS_PROMPT_UNREVIEWED, 0):
        warn(
            f"{counts[nf.STATUS_PROMPT_UNREVIEWED]} prompts awaiting approval — "
            "open Notion and set Prompt Approved / Rejected"
        )
    if counts.get(nf.STATUS_IMAGE_UNREVIEWED, 0):
        warn(
            f"{counts[nf.STATUS_IMAGE_UNREVIEWED]} images awaiting approval — "
            "open Notion and set Image Approved / Rejected"
        )
    if counts.get(nf.STATUS_DRAFTED, 0):
        warn(
            f"{counts[nf.STATUS_DRAFTED]} drafts in Printify — "
            "publish to Etsy and paste listing URL in Notion"
        )

    # Sample entries for actionable statuses
    SHOW_DETAIL = [
        nf.STATUS_PROMPT_APPROVED,
        nf.STATUS_IMAGE_APPROVED,
        nf.STATUS_COPY_GENERATED,
        nf.STATUS_DRAFTED,
        nf.STATUS_PUBLISHED,
    ]
    for status in SHOW_DETAIL:
        matches = [
            p for p in pages
            if (p["properties"].get(nf.PIPELINE_STATUS, {}).get("select") or {}).get("name") == status
        ]
        if not matches:
            continue
        blank()
        print(f"     \033[1m{status}\033[0m ({len(matches)}):")
        for p in matches[:3]:
            props  = p["properties"]
            prompt = nf.rich_text_plain(props.get(nf.PROMPT, {}))
            cat    = (props.get(nf.CATEGORY, {}).get("select") or {}).get("name", "")
            label  = f"[{cat}] " if cat else ""
            title  = nf.rich_text_plain(props.get(nf.ETSY_TITLE, {}))
            line   = title if title else prompt
            info(f"{label}{line[:85]}...")
        if len(matches) > 3:
            info(f"... and {len(matches) - 3} more")


# ── validate step inputs ───────────────────────────────────────────────────────

def _query_status_count(status: str) -> int:
    hdrs = nf.notion_headers(NOTION_TOKEN)
    r = requests.post(
        f"https://api.notion.com/v1/databases/{DB_ID}/query",
        headers=hdrs,
        json={
            "filter": {"property": nf.PIPELINE_STATUS, "select": {"equals": status}},
            "page_size": 100,
        },
    )
    r.raise_for_status()
    return len(r.json().get("results", []))


def _env_check(var: str, required: bool = True):
    val = os.environ.get(var, "")
    if val:
        ok(f"{var} is set")
    elif required:
        fail(f"{var} is not set")
    else:
        warn(f"{var} is not set (optional — some features may be skipped)")


def validate_step(step: str):
    section(f"VALIDATE: inputs for step {step}")

    if step == "01":
        ok("No file inputs required — generates from hardcoded seed keywords")
        blank()
        _env_check("GEMINI_API_KEY")
        _env_check("ETSY_API_KEY", required=False)
        _env_check("ETSY_SHARED_SECRET", required=False)

    elif step == "02":
        p = Path("keywords.json")
        if p.exists():
            data = json.loads(p.read_text())
            ok(f"keywords.json exists — {len(data)} keywords")
        else:
            fail("keywords.json not found — run 01_research.py first")
        blank()
        _env_check("GEMINI_API_KEY")
        _env_check("NOTION_TOKEN")
        _env_check("NOTION_DATABASE_ID")

    elif step == "03":
        if not NOTION_TOKEN or not DB_ID:
            fail("NOTION_TOKEN or NOTION_DATABASE_ID not set — cannot query Notion")
            return
        n = _query_status_count(nf.STATUS_PROMPT_APPROVED)
        if n:
            ok(f"{n} pages with status '{nf.STATUS_PROMPT_APPROVED}' — ready for image generation")
        else:
            fail(f"0 pages with status '{nf.STATUS_PROMPT_APPROVED}' — approve prompts in Notion first")
        blank()
        _env_check("FAL_KEY")
        _env_check("IMGBB_API_KEY")
        _env_check("NOTION_TOKEN")
        _env_check("NOTION_DATABASE_ID")

    elif step == "04":
        if not NOTION_TOKEN or not DB_ID:
            fail("NOTION_TOKEN or NOTION_DATABASE_ID not set — cannot query Notion")
            return
        n = _query_status_count(nf.STATUS_IMAGE_APPROVED)
        if n:
            ok(f"{n} pages with status '{nf.STATUS_IMAGE_APPROVED}' — ready for copy generation")
        else:
            fail(f"0 pages with status '{nf.STATUS_IMAGE_APPROVED}' — approve images in Notion first")
        blank()
        _env_check("GEMINI_API_KEY")
        _env_check("NOTION_TOKEN")
        _env_check("NOTION_DATABASE_ID")

    elif step == "06":
        if not NOTION_TOKEN or not DB_ID:
            fail("NOTION_TOKEN or NOTION_DATABASE_ID not set — cannot query Notion")
            return
        hdrs = nf.notion_headers(NOTION_TOKEN)
        r = requests.post(
            f"https://api.notion.com/v1/databases/{DB_ID}/query",
            headers=hdrs,
            json={
                "filter": {
                    "and": [
                        {"property": nf.PIPELINE_STATUS, "select": {"equals": nf.STATUS_COPY_GENERATED}},
                        {"property": nf.PRINTIFY_DRAFT_URL, "url": {"is_empty": True}},
                    ]
                },
                "page_size": 100,
            },
        )
        r.raise_for_status()
        n = len(r.json().get("results", []))
        if n:
            ok(f"{n} pages with status '{nf.STATUS_COPY_GENERATED}' and no draft URL — ready for Printify")
        else:
            fail(f"0 pages ready — run 04_generate_copy.py first, or all Copy Generated pages are already drafted")
        blank()
        _env_check("PRINTIFY_API_KEY")
        _env_check("PRINTIFY_SHOP_ID")
        _env_check("NOTION_TOKEN")
        _env_check("NOTION_DATABASE_ID")

    elif step == "07":
        if not NOTION_TOKEN or not DB_ID:
            fail("NOTION_TOKEN or NOTION_DATABASE_ID not set — cannot query Notion")
            return
        hdrs = nf.notion_headers(NOTION_TOKEN)
        r = requests.post(
            f"https://api.notion.com/v1/databases/{DB_ID}/query",
            headers=hdrs,
            json={"filter": {"property": nf.ETSY_LISTING_URL, "url": {"is_not_empty": True}}, "page_size": 100},
        )
        r.raise_for_status()
        n = len(r.json().get("results", []))
        if n:
            ok(f"{n} pages with Etsy listing URLs — ready for stat sync")
        else:
            fail("0 pages with Etsy listing URLs — publish products and paste the URLs into Notion")
        blank()
        _env_check("ETSY_API_KEY")
        _env_check("ETSY_ACCESS_TOKEN", required=False)
        _env_check("NOTION_TOKEN")
        _env_check("NOTION_DATABASE_ID")

    else:
        fail(f"Unknown step: '{step}'")
        info("Valid steps: 01  02  03  04  06  07")


# ── dry-run previews ───────────────────────────────────────────────────────────

def dry_run_01():
    """Preview theme + Etsy listing data — does not write design_briefs.json."""
    section("DRY RUN: 01_research.py - theme + Etsy preview  (no file writes)")

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
    ok(f"Gemini returned {len(themes)} sample themes  (not written to design_briefs.json)")
    blank()
    for t in themes:
        info(f"  [theme]  {t}")

    etsy_key    = os.environ.get("ETSY_API_KEY", "")
    etsy_secret = os.environ.get("ETSY_SHARED_SECRET", "")

    blank()
    if not etsy_key:
        warn("ETSY_API_KEY not set — Etsy listing preview skipped")
        return
    if not etsy_secret:
        warn("ETSY_SHARED_SECRET not set — Etsy listing data requires key:secret format")
        warn("Get your shared secret from etsy.com/developers/your-apps")
        return

    info("Fetching top Etsy listings for 'gym shirt funny'...")
    try:
        etsy = EtsyClient(
            api_key=etsy_key,
            shared_secret=etsy_secret,
            cache_dir=Path("runs/dry_run_cache"),
        )
        listings = etsy.search_listings("gym shirt funny", limit=5)
        blank()
        ok(f"{len(listings)} listings returned  (not cached to design_briefs.json)")
        blank()
        for l in listings:
            info(f"  favs={l.num_favorers:<5}  {l.title[:70]}")
            if l.tags:
                info(f"           tags: {', '.join(l.tags[:6])}")
    except Exception as e:
        warn(f"Etsy call failed: {e}")


def dry_run_02():
    """Preview one prompt per category from Gemini — does not write to Notion or prompts.json."""
    section("DRY RUN: 02_generate_prompts.py — 1 prompt per category  (no Notion writes)")

    from google import genai as _genai
    from gemini_client import generate_json

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail("GEMINI_API_KEY not set")
        return

    briefs_path = Path("design_briefs.json")
    kw_context = ""
    if briefs_path.exists():
        try:
            run = json.loads(briefs_path.read_text(encoding="utf-8"))
            briefs = run.get("briefs", [])
            if briefs:
                samples = [f'"{b["design_brief"]["headline_text"]}"' for b in briefs[:4]]
                kw_context = f"\nMarket-researched concepts this week: {', '.join(samples)}"
        except Exception:
            pass

    gemini = _genai.Client(api_key=api_key)

    CATEGORIES = {
        "Corporate Grind":   "Office frustration, meetings, burnout — gym as the only escape valve",
        "Iron Discipline":   "Powerlifting philosophy, 5am club, PRs, consistency as identity",
        "Cardio Confession": "The lifter doing cardio under protest — step goals, zone-2 irony",
        "Recovery Mode":     "Rest days, deload weeks, overtrained and overworked",
        "Gym Flex":          "PR celebrations, bro culture humor, gym memes, chalk and straps",
    }

    info(f"Calling Gemini for 1 sample prompt per category ({len(CATEGORIES)} calls)...")
    info("Not written to Notion.")
    blank()

    for category, description in CATEGORIES.items():
        prompt = f"""You are a witty copywriter for a viral Etsy shop selling gym + corporate culture graphic tees.

Category: {category}
Theme: {description}{kw_context}

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
    """Preview AI copy for the first Image Approved page — does not write to Notion."""
    section("DRY RUN: 04_generate_copy.py — copy preview  (no Notion writes)")

    from google import genai as _genai
    from gemini_client import generate_json

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        fail("GEMINI_API_KEY not set")
        return
    if not NOTION_TOKEN or not DB_ID:
        fail("NOTION_TOKEN or NOTION_DATABASE_ID not set")
        return

    hdrs = nf.notion_headers(NOTION_TOKEN)
    r = requests.post(
        f"https://api.notion.com/v1/databases/{DB_ID}/query",
        headers=hdrs,
        json={
            "filter": {"property": nf.PIPELINE_STATUS, "select": {"equals": nf.STATUS_IMAGE_APPROVED}},
            "page_size": 1,
        },
    )
    r.raise_for_status()
    pages = r.json().get("results", [])

    if not pages:
        fail(f"No '{nf.STATUS_IMAGE_APPROVED}' pages in Notion — approve some images first")
        return

    page  = pages[0]
    props = page["properties"]
    prompt_text = nf.rich_text_plain(props.get(nf.PROMPT, {}))
    cat   = (props.get(nf.CATEGORY, {}).get("select") or {}).get("name", "")

    info(f"Using page : {page['id'][:8]}...")
    info(f"Category   : {cat or '(none)'}")
    info(f"Prompt     : {prompt_text[:100]}...")
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
    warn("Nothing written to Notion — this was a preview only")


# ── entry point ────────────────────────────────────────────────────────────────

USAGE = """
  python scripts/test_pipeline.py                  full report (files + Notion)
  python scripts/test_pipeline.py files            checkpoint file status only
  python scripts/test_pipeline.py notion           Notion DB state only
  python scripts/test_pipeline.py db               pod.db row counts + last runs + last 5 lineage/listing_stats
  python scripts/test_pipeline.py lineage <brief>  full theme→concept→brief→lineage→stats chain for a brief_id
  python scripts/test_pipeline.py validate <N>     check inputs for step N  (01-07)
  python scripts/test_pipeline.py dry-run  01      preview keyword generation
  python scripts/test_pipeline.py dry-run  02      preview 1 prompt per category
  python scripts/test_pipeline.py dry-run  04      preview copy for first Image Approved page
"""


def db_state():
    """Show pod.db schema status, row counts per table, last 3 runs, and feedback signal."""
    from src import db as _db

    section("POD.DB STATE")
    db_path = Path(os.environ.get("POD_DB_PATH") or (PROJECT_ROOT / "pod.db"))
    info(f"Path: {db_path}")
    conn = _db.connect(db_path)
    applied = _db.run_migrations(conn)
    if applied:
        ok(f"Applied migrations: {', '.join(applied)}")
    else:
        ok("Schema up to date (no new migrations)")
    blank()

    section("ROW COUNTS")
    tables = [
        "research_runs", "themes", "etsy_probes", "etsy_listings",
        "concepts", "design_briefs", "lineage", "listing_stats",
        "schema_migrations",
    ]
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        info(f"{t:<22} {n}")
    blank()

    section("LAST 3 research_runs")
    rows = list(conn.execute(
        "SELECT run_id, started_at, finished_at, brand_voice "
        "FROM research_runs ORDER BY started_at DESC LIMIT 3"
    ))
    if not rows:
        info("(none)")
    for r in rows:
        info(f"{r['run_id']}  started={r['started_at']}  finished={r['finished_at'] or '-'}  voice={r['brand_voice']}")
    blank()

    section("FEEDBACK SIGNAL")
    sig = _db.load_feedback_signal(conn)
    print(json.dumps(sig, indent=2, default=str))
    blank()

    section("LAST 5 lineage rows")
    rows = list(conn.execute(
        "SELECT notion_page_id, brief_id, image_url, printify_draft_url, "
        "etsy_listing_url, last_updated_at "
        "FROM lineage ORDER BY last_updated_at DESC LIMIT 5"
    ))
    if not rows:
        info("(none)")
    for r in rows:
        info(
            f"page={r['notion_page_id'][:8]}.. "
            f"brief={(r['brief_id'] or '-')[:8]}.. "
            f"img={'Y' if r['image_url'] else '-'} "
            f"draft={'Y' if r['printify_draft_url'] else '-'} "
            f"etsy={'Y' if r['etsy_listing_url'] else '-'} "
            f"updated={r['last_updated_at']}"
        )
    blank()

    section("LAST 5 listing_stats rows")
    rows = list(conn.execute(
        "SELECT notion_page_id, snapshot_at, views, favorites, "
        "views_delta, favorites_delta "
        "FROM listing_stats ORDER BY snapshot_at DESC, snapshot_id DESC LIMIT 5"
    ))
    if not rows:
        info("(none)")
    for r in rows:
        vd = "—" if r["views_delta"] is None else f"+{r['views_delta']}"
        fd = "—" if r["favorites_delta"] is None else f"+{r['favorites_delta']}"
        info(
            f"page={r['notion_page_id'][:8]}.. at={r['snapshot_at']} "
            f"views={r['views']} ({vd}) favs={r['favorites']} ({fd})"
        )

    conn.close()


def lineage_chain(brief_id: str) -> None:
    """Print the full theme → concept → brief → lineage → stats chain for a brief_id."""
    from src import db as _db

    section(f"LINEAGE for brief_id={brief_id}")
    db_path = Path(os.environ.get("POD_DB_PATH") or (PROJECT_ROOT / "pod.db"))
    info(f"Path: {db_path}")
    conn = _db.connect(db_path)

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

    blank()
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
        "SELECT notion_page_id, prompt_text, image_url, printify_draft_url, "
        "etsy_listing_url, last_updated_at "
        "FROM lineage WHERE brief_id = ? ORDER BY last_updated_at DESC",
        (brief_id,),
    ))
    if not rows:
        info("(none — brief never made it to a Notion page)")
    for r in rows:
        info(f"page={r['notion_page_id']}")
        info(f"  prompt={(r['prompt_text'] or '')[:80]}")
        info(f"  image={r['image_url'] or '-'}")
        info(f"  printify={r['printify_draft_url'] or '-'}")
        info(f"  etsy={r['etsy_listing_url'] or '-'}  updated={r['last_updated_at']}")

        stats = list(conn.execute(
            "SELECT snapshot_at, views, favorites, views_delta, favorites_delta "
            "FROM listing_stats WHERE notion_page_id = ? "
            "ORDER BY snapshot_at DESC LIMIT 5",
            (r["notion_page_id"],),
        ))
        if not stats:
            info("  stats: (none yet)")
        for s in stats:
            vd = "—" if s["views_delta"] is None else f"+{s['views_delta']}"
            fd = "—" if s["favorites_delta"] is None else f"+{s['favorites_delta']}"
            info(f"  stats {s['snapshot_at']}: views={s['views']} ({vd}) favs={s['favorites']} ({fd})")

    conn.close()


def main():
    args = sys.argv[1:]

    if not args:
        inspect_all_files()
        notion_state()
        return

    cmd = args[0].lower()

    if cmd == "files":
        inspect_all_files()

    elif cmd == "notion":
        notion_state()

    elif cmd == "db":
        db_state()

    elif cmd == "lineage":
        if len(args) < 2:
            fail("Usage: test_pipeline.py lineage <brief_id>")
        else:
            lineage_chain(args[1])

    elif cmd == "validate":
        step = args[1].lstrip("0") if len(args) > 1 else ""
        step = args[1] if len(args) > 1 else ""
        validate_step(step)

    elif cmd == "dry-run":
        step = args[1] if len(args) > 1 else ""
        if step == "01":
            dry_run_01()
        elif step == "02":
            dry_run_02()
        elif step == "04":
            dry_run_04()
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
