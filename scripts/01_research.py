"""
Market research pipeline: Gemini themes → Etsy listing data → ranked design briefs.
Outputs design_briefs.json consumed by 02_generate_prompts.py.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from dotenv import load_dotenv
from google import genai

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from etsy_client import EtsyClient
from gemini_client import generate_json
from schemas import DesignBrief, DesignBriefContent, Evidence, ResearchRun

load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────
N_THEMES = 6
PROBES_PER_THEME = 3          # 1 broad + 2 narrow per theme
LISTINGS_PER_PROBE = 50
TOP_LISTINGS_FOR_MINING = 20  # top by favorites used for signal extraction
FINAL_BRIEF_COUNT = 10
CACHE_TTL_HOURS = 24

BRAND_VOICE = (
    "gym/corporate culture graphic tees — sardonic, meme-aware, relatable. "
    "Target buyer: someone who lifts AND has a desk job. "
    "Niches: gym before work, corporate burnout, powerlifting philosophy, "
    "endurance irony, rest-day absurdity, gym-culture bro-humor."
)

CATEGORIES = [
    "Corporate Grind",
    "Iron Discipline",
    "Cardio Confession",
    "Recovery Mode",
    "Gym Flex",
]

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true", help="Use cached Etsy data only")
parser.add_argument("--seed", type=int, default=None, help="Random seed")
args = parser.parse_args()

if args.seed is not None:
    random.seed(args.seed)

# ── setup ─────────────────────────────────────────────────────────────────────
run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
run_dir = Path("runs") / run_id
run_dir.mkdir(parents=True, exist_ok=True)

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
etsy = EtsyClient(
    api_key=os.environ.get("ETSY_API_KEY", ""),
    shared_secret=os.environ.get("ETSY_SHARED_SECRET", ""),
    cache_dir=run_dir / "etsy_cache",
)


def _log(name: str, prompt: str, response_text: str) -> None:
    (run_dir / f"{name}.txt").write_text(
        f"=== PROMPT ===\n{prompt}\n\n=== RESPONSE ===\n{response_text}\n",
        encoding="utf-8",
    )


def gemini_json(model: str, prompt: str, schema: dict, log_name: str) -> dict | list:
    result = generate_json(gemini_client, prompt, model=model, schema=schema)
    # log the prompt; response text is already parsed so re-serialize for the log
    _log(log_name, prompt, json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ── Step 1: Gemini seeds broad themes ─────────────────────────────────────────
print(f"[01] Seeding {N_THEMES} themes via Gemini…")

theme_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "theme_name": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORIES},
            "cultural_tension": {"type": "string"},
        },
        "required": ["theme_name", "description", "category", "cultural_tension"],
    },
}

theme_prompt = f"""You are a cultural strategist for a viral Etsy apparel brand.

Brand voice: {BRAND_VOICE}

Brainstorm {N_THEMES} distinct micro-trend themes for this week's t-shirt designs.
Each theme must:
- Identify a specific cultural tension or subculture (not generic gym motivation)
- Map to exactly ONE of these design categories: {json.dumps(CATEGORIES)}
- Be concrete enough to generate specific Etsy search queries and shirt slogans
- Feel fresh and meme-aware, not clichéd

Examples of good themes:
- "Hybrid athlete burnout: the person who commutes, lifts, and still does zone-2 cardio"
- "Tech bro who CrossFits: laptop stickers and chalk-covered hands"
- "Quarterly review as a PR attempt: performance review season mapped to gym PRs"

Return a JSON array of {N_THEMES} theme objects."""

themes = gemini_json("gemini-2.5-flash", theme_prompt, theme_schema, "01_themes")
print(f"    Themes: {[t['theme_name'] for t in themes]}")

# ── Step 2: Gemini generates Etsy search probes per theme ─────────────────────
print(f"[02] Generating {PROBES_PER_THEME} search probes per theme via Gemini…")

probe_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "theme_name": {"type": "string"},
            "probes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "intent": {"type": "string", "enum": ["broad", "narrow"]},
                    },
                    "required": ["query", "intent"],
                },
            },
        },
        "required": ["theme_name", "probes"],
    },
}

probe_prompt = f"""For each theme below, generate {PROBES_PER_THEME} Etsy marketplace search queries.
- 1 broad query (high volume signal, generic niche): e.g. "funny gym shirt"
- 2 narrow queries (specificity signal): target the exact cultural tension of the theme

Themes:
{json.dumps(themes, indent=2)}

Rules:
- Queries must be real phrases people would type into Etsy search
- Narrow queries should surface designs directly competing with or adjacent to the theme
- Keep queries short (2–5 words) for broad; slightly longer (3–7 words) for narrow

Return a JSON array mapping each theme_name to its probes."""

probe_data = gemini_json("gemini-2.5-flash", probe_prompt, probe_schema, "02_probes")

# ── Step 3: Etsy API fetches real listing data ─────────────────────────────────
from schemas import EtsyListing  # noqa: E402

_etsy_available = bool(os.environ.get("ETSY_API_KEY") and os.environ.get("ETSY_SHARED_SECRET"))
if not _etsy_available:
    print("[03] Skipping Etsy listing fetch — ETSY_SHARED_SECRET not set. Briefs will be Gemini-only.")
else:
    print("[03] Fetching Etsy listing data…")

theme_listings: dict[str, list[EtsyListing]] = {}
for entry in probe_data:
    theme_name = entry["theme_name"]
    all_listings: list[EtsyListing] = []
    if not _etsy_available:
        theme_listings[theme_name] = []
        continue
    for probe in entry.get("probes", []):
        query = probe["query"]
        intent = probe["intent"]
        if args.dry_run:
            import hashlib
            key = hashlib.md5(f"{query}|{LISTINGS_PER_PROBE}|score".encode()).hexdigest()
            cached = etsy._load_cache(key)
            listings = [EtsyListing(**x) for x in cached] if cached else []
            print(f"    [dry-run] {intent} '{query}': {len(listings)} cached listings")
        else:
            try:
                listings = etsy.search_listings(query, limit=LISTINGS_PER_PROBE)
                print(f"    {intent} '{query}': {len(listings)} listings")
            except Exception as e:
                print(f"    {intent} '{query}': FAILED ({e}) — skipping")
                listings = []
        all_listings.extend(listings)
    theme_listings[theme_name] = all_listings

# ── Step 4: Mine the data ──────────────────────────────────────────────────────
print("[04] Mining listing data…")


def mine_theme(listings: list[EtsyListing]) -> dict:
    sorted_by_favs = sorted(listings, key=lambda x: x.num_favorers, reverse=True)
    top = sorted_by_favs[:TOP_LISTINGS_FOR_MINING]

    # tag frequency
    tag_counter: Counter = Counter()
    for listing in top:
        tag_counter.update(t.lower() for t in listing.tags)
    top_tags = [tag for tag, _ in tag_counter.most_common(15)]

    # price tier (USD only, exclude outliers)
    prices = [l.price_usd for l in listings if l.price_usd and 5 <= l.price_usd <= 80]
    if prices:
        med = median(prices)
        p25 = sorted(prices)[len(prices) // 4]
        p75 = sorted(prices)[3 * len(prices) // 4]
        price_tier = [round(p25, 2), round(p75, 2)]
        price_median = round(med, 2)
    else:
        price_tier = []
        price_median = None

    # saturation signal
    total = len(listings)
    if total >= 40:
        saturation = "high"
    elif total >= 20:
        saturation = "medium"
    else:
        saturation = "low"

    # volume signal (from top favorites count)
    top_favs = top[0].num_favorers if top else 0
    if top_favs >= 100:
        volume = "high"
    elif top_favs >= 20:
        volume = "medium"
    else:
        volume = "low"

    supporting = [
        {"title": l.title, "favorites": l.num_favorers, "url": l.url}
        for l in top[:5]
    ]

    return {
        "top_tags": top_tags,
        "price_tier": price_tier,
        "price_median": price_median,
        "saturation": saturation,
        "volume_signal": volume,
        "listing_count": total,
        "supporting_listings": supporting,
    }


mining_results: dict[str, dict] = {}
for t in themes:
    name = t["theme_name"]
    listings = theme_listings.get(name, [])
    mining_results[name] = mine_theme(listings)
    m = mining_results[name]
    print(f"    {name}: {m['listing_count']} listings, sat={m['saturation']}, vol={m['volume_signal']}, tags={m['top_tags'][:5]}")

# ── Step 5: Feed mined data back to Gemini for brief synthesis ─────────────────
print(f"[05] Synthesizing {FINAL_BRIEF_COUNT} design briefs via Gemini…")

brief_schema = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "concept_name": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORIES},
            "evidence": {
                "type": "object",
                "properties": {
                    "etsy_tag_overlap": {"type": "array", "items": {"type": "string"}},
                    "search_volume_signal": {"type": "string", "enum": ["high", "medium", "low"]},
                    "saturation": {"type": "string", "enum": ["high", "medium", "low"]},
                    "supporting_listings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "favorites": {"type": "integer"},
                                "url": {"type": "string"},
                            },
                        },
                    },
                    "price_tier_usd": {"type": "array", "items": {"type": "number"}},
                },
            },
            "design_brief": {
                "type": "object",
                "properties": {
                    "headline_text": {"type": "string"},
                    "visual_concept": {"type": "string"},
                    "style_tags": {"type": "array", "items": {"type": "string"}},
                    "color_palette_hint": {"type": "string"},
                    "target_buyer": {"type": "string"},
                },
                "required": ["headline_text", "visual_concept", "style_tags", "color_palette_hint", "target_buyer"],
            },
            "image_prompt_seed": {"type": "string"},
        },
        "required": ["concept_name", "category", "evidence", "design_brief", "image_prompt_seed"],
    },
}

# Build concise market signal summary for prompt
market_summary = []
for t in themes:
    name = t["theme_name"]
    m = mining_results[name]
    market_summary.append({
        "theme": name,
        "category": t["category"],
        "cultural_tension": t["cultural_tension"],
        "top_etsy_tags": m["top_tags"][:10],
        "saturation": m["saturation"],
        "volume_signal": m["volume_signal"],
        "price_tier_usd": m["price_tier"],
        "top_listings": m["supporting_listings"][:3],
    })

synthesis_prompt = f"""You are a creative director for a viral Etsy apparel brand.

Brand voice: {BRAND_VOICE}

Below is real Etsy market research data for {N_THEMES} themes. Use it to produce exactly {FINAL_BRIEF_COUNT} ranked design briefs.

Ranking criteria (in order):
1. HIGH or MEDIUM volume signal + LOW or MEDIUM saturation = best opportunity
2. Originality: concept must feel fresh, not a copy of existing titles
3. Voice fit: sardonic, meme-aware — never earnest gym-bro motivation
4. Image generation feasibility: the visual concept must work as a flat vector tee graphic

Market data:
{json.dumps(market_summary, indent=2)}

For each brief:
- concept_name: catchy 2–5 word name for the design concept
- category: one of {CATEGORIES}
- evidence: cite actual tags and listings from the data above
- design_brief.headline_text: the EXACT text that appears on the shirt (punchy, ≤8 words)
- design_brief.visual_concept: describe the graphic surrounding/supporting the text
- design_brief.style_tags: ["flat vector", "bold typography", "two-color", ...]
- design_brief.color_palette_hint: e.g. "navy + cream", "black + orange"
- design_brief.target_buyer: one sentence describing the buyer persona
- image_prompt_seed: a ready-to-paste prompt for Ideogram/SDXL. Must include the shirt text in quotes, art direction (flat vector, two colors, screen-print ready, pure white background)

Spread briefs across multiple categories. Output the {FINAL_BRIEF_COUNT} best opportunities."""

raw_briefs = gemini_json("gemini-2.5-flash", synthesis_prompt, brief_schema, "05_briefs")

# ── Step 6: Assemble and write outputs ────────────────────────────────────────
print("[06] Writing outputs…")

briefs: list[DesignBrief] = []
for rank, raw in enumerate(raw_briefs[:FINAL_BRIEF_COUNT], start=1):
    ev = raw.get("evidence", {})
    db = raw.get("design_brief", {})
    briefs.append(
        DesignBrief(
            concept_id=str(uuid.uuid4()),
            concept_name=raw["concept_name"],
            rank=rank,
            category=raw["category"],
            evidence=Evidence(
                etsy_tag_overlap=ev.get("etsy_tag_overlap", []),
                search_volume_signal=ev.get("search_volume_signal", "unknown"),
                saturation=ev.get("saturation", "unknown"),
                supporting_listings=ev.get("supporting_listings", []),
                price_tier_usd=ev.get("price_tier_usd", []),
            ),
            design_brief=DesignBriefContent(
                headline_text=db.get("headline_text", ""),
                visual_concept=db.get("visual_concept", ""),
                style_tags=db.get("style_tags", []),
                color_palette_hint=db.get("color_palette_hint", ""),
                target_buyer=db.get("target_buyer", ""),
            ),
            image_prompt_seed=raw.get("image_prompt_seed", ""),
        )
    )

run = ResearchRun(
    run_id=run_id,
    timestamp=datetime.now(timezone.utc).isoformat(),
    briefs=briefs,
)

# Primary output
with open("design_briefs.json", "w", encoding="utf-8") as f:
    json.dump(run.model_dump(), f, indent=2, ensure_ascii=False)

# Run archive
(run_dir / "design_briefs.json").write_text(
    json.dumps(run.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
)

print(f"\nDone. {len(briefs)} design briefs written to design_briefs.json")
print(f"Run logs: {run_dir}/")
for b in briefs:
    print(f"  #{b.rank} [{b.category}] {b.concept_name} — \"{b.design_brief.headline_text}\"")
