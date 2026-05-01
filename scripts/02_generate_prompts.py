import json
import os
import sys
from pathlib import Path

from google import genai
import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
import notion_fields as nf
from gemini_client import generate_json
from src import db

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")
notion_headers = nf.notion_headers(NOTION_TOKEN)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# Five audience categories — each gets 5 prompts per week (25 total)
CATEGORIES = {
    "Corporate Grind": (
        "Office frustration, soul-crushing meetings, burnout, inbox overload — "
        "gym as the only escape valve from 9-to-5 misery"
    ),
    "Iron Discipline": (
        "Strength and powerlifting philosophy, 5am club, consistency over motivation, "
        "mental toughness, PRs, the grind as identity"
    ),
    "Cardio Confession": (
        "Running, LISS, endurance irony — the dedicated lifter doing cardio under protest, "
        "step goals, 10k at lunch, zone-2 suffering"
    ),
    "Recovery Mode": (
        "Rest days, foam rolling, deload weeks, overworked and overtrained, "
        "the absurdity of work-life balance when both halves destroy you"
    ),
    "Gym Flex": (
        "PR celebrations, gym culture bro humor, failed lifts, chalk and straps, "
        "loud grunters, unsolicited form advice, gym memes satirized"
    ),
}

PROMPTS_PER_CATEGORY = 5


def load_design_briefs() -> tuple[dict[str, list[dict]], dict]:
    """Return (briefs grouped by category, run-level metadata)."""
    briefs_by_category: dict[str, list[dict]] = {cat: [] for cat in CATEGORIES}
    meta = {"run_id": "", "brief_count": 0}
    try:
        with open("design_briefs.json", encoding="utf-8") as f:
            run = json.load(f)
        meta["run_id"] = run.get("run_id", "")
        meta["brief_count"] = len(run.get("briefs", []))
        for brief in run.get("briefs", []):
            cat = brief.get("category", "")
            if cat in briefs_by_category:
                briefs_by_category[cat].append(brief)
    except FileNotFoundError:
        print("design_briefs.json not found — running without market research context")
    return briefs_by_category, meta


def get_top_performers() -> list[str]:
    try:
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=notion_headers,
            json={
                "filter": {
                    "property": nf.PIPELINE_STATUS,
                    "select": {"equals": nf.STATUS_PUBLISHED},
                },
                "sorts": [{"property": nf.FAVORITES, "direction": "descending"}],
                "page_size": 8,
            },
        )
        results = resp.json().get("results", [])
        lines = []
        for p in results[:5]:
            props = p["properties"]
            prompt = nf.rich_text_plain(props.get(nf.PROMPT, {}))
            if not prompt.strip():
                continue
            cat_prop = props.get(nf.CATEGORY, {}).get("select") or {}
            cat_name = cat_prop.get("name", "")
            fav = int(nf.number_value(props.get(nf.FAVORITES, {})) or 0)
            views = int(nf.number_value(props.get(nf.VIEWS, {})) or 0)
            dv = nf.number_value(props.get(nf.VIEWS_SINCE_SYNC, {}))
            df = nf.number_value(props.get(nf.FAVORITES_SINCE_SYNC, {}))
            bit = f"- [{cat_name}] {prompt[:200]}{'...' if len(prompt) > 200 else ''} | favs={fav}, views={views}"
            if dv is not None:
                bit += f", views_delta={int(dv)}"
            if df is not None:
                bit += f", favs_delta={int(df)}"
            lines.append(bit)
        return lines
    except Exception as e:
        print(f"No top performers yet (first run): {e}")
        return []


def _build_brief_context(briefs: list[dict]) -> str:
    if not briefs:
        return ""
    lines = ["\n\nMarket research — design briefs proven by real Etsy data (use as creative fuel):"]
    for b in briefs[:3]:
        db_block = b.get("design_brief", {})
        ev = b.get("evidence", {})
        sat = ev.get("saturation", "?")
        vol = ev.get("search_volume_signal", "?")
        tags = ", ".join(ev.get("etsy_tag_overlap", [])[:6])
        lines.append(
            f'- Concept: "{b["concept_name"]}" | Headline: "{db_block.get("headline_text","")}" '
            f'| Visual: {db_block.get("visual_concept","")} '
            f'| Target: {db_block.get("target_buyer","")} '
            f'| Etsy signal: volume={vol}, saturation={sat}, tags=[{tags}]'
        )
    return "\n".join(lines)


def generate_prompts_for_category(
    category: str,
    description: str,
    briefs: list[dict],
    top_lines: list[str],
) -> list[dict]:
    """Return list of {prompt, brief} — brief is the source brief or None."""
    performer_context = ""
    if top_lines:
        category_performers = [l for l in top_lines if f"[{category}]" in l]
        relevant = category_performers if category_performers else top_lines[:3]
        performer_context = (
            "\n\nTop-performing past designs (favor similar directions):\n"
            + "\n".join(relevant)
        )

    brief_context = _build_brief_context(briefs)

    prompt = f"""You are a witty copywriter for a viral Etsy shop selling gym + corporate culture graphic tees.

Category: {category}
Category theme: {description}{brief_context}{performer_context}

Generate {PROMPTS_PER_CATEGORY} image generation prompts for screen-print t-shirt graphics in the "{category}" category. Each prompt must:
- Have a funny, specific concept rooted in the category theme (not generic — think concrete moments)
- Be immediately readable as a joke or relatable moment for someone who lifts AND has a desk job
- Be Etsy-sellable: the kind of shirt someone buys for themselves or as a gift
- Include the slogan/text on the shirt in quotes inside the prompt
- Include art direction: flat vector, bold typography, two colors, screen print ready, pure white background, isolated artwork
- Where relevant, draw on or riff from the market research briefs above

Good format examples:
- "Bold retro varsity graphic with the text 'SKIPPED LEG DAY (it was a full-day offsite)', flat vector, two colors, screen print ready, pure white background"
- "Flat illustration of a protein shaker labeled 'PRE-MEETING' next to one labeled 'PRE-WORKOUT', bold sans-serif text reads 'SAME ENERGY', two colors, screen print ready, pure white background"

Return ONLY a JSON array of {PROMPTS_PER_CATEGORY} strings, no explanation."""

    prompts = generate_json(client, prompt)

    # Pair each prompt with a brief so lineage IDs flow through.
    out: list[dict] = []
    for i, p in enumerate(prompts):
        src_brief = briefs[i % len(briefs)] if briefs else None
        out.append({"prompt": p, "brief": src_brief})
    return out


def _rich(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text}}]} if text else {"rich_text": []}


def save_prompt_to_notion(prompt_text: str, category: str, brief: dict | None) -> str | None:
    properties: dict = {
        nf.NAME: {"title": [{"text": {"content": prompt_text[:100]}}]},
        nf.PROMPT: {"rich_text": [{"text": {"content": prompt_text}}]},
        nf.CATEGORY: {"select": {"name": category}},
        nf.PIPELINE_STATUS: {"select": {"name": nf.STATUS_PROMPT_UNREVIEWED}},
    }
    if brief:
        properties[nf.BRIEF_ID] = _rich(brief.get("brief_id", ""))
        properties[nf.THEME_ID] = _rich(brief.get("theme_id", ""))
        properties[nf.RUN_ID] = _rich(brief.get("run_id", ""))
    page = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers,
        json={
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": properties,
        },
    ).json()
    if "id" in page:
        return page["id"]
    print(f"Failed to save prompt to Notion: {page}")
    return None


briefs_by_category, briefs_meta = load_design_briefs()
top_lines = get_top_performers()
all_prompts = []
notion_page_ids = []

conn = db.connect(DB_PATH)
db.run_migrations(conn)

for category, description in CATEGORIES.items():
    print(f"\n--- Generating prompts for: {category} ---")
    pairs = generate_prompts_for_category(
        category, description, briefs_by_category[category], top_lines
    )
    for pair in pairs:
        prompt_text = pair["prompt"]
        src_brief = pair["brief"]
        page_id = save_prompt_to_notion(prompt_text, category, src_brief)
        if page_id:
            notion_page_ids.append(page_id)
            if src_brief:
                db.lineage_upsert(
                    conn,
                    notion_page_id=page_id,
                    brief_id=src_brief.get("brief_id"),
                    prompt_text=prompt_text,
                )
            else:
                db.lineage_upsert(conn, notion_page_id=page_id, prompt_text=prompt_text)
            print(f"  Saved [{category}]: {prompt_text[:80]}...")
        all_prompts.append({"category": category, "prompt": prompt_text,
                            "brief_id": (src_brief or {}).get("brief_id")})

conn.close()

# Audit log
with open("prompts.json", "w") as f:
    json.dump(all_prompts, f, indent=2)

# Context for 05_notify.py
with open("notify_context.json", "w") as f:
    json.dump({
        "count": len(notion_page_ids),
        "stage": "prompts",
        "detail": (
            f"{len(notion_page_ids)} prompts generated across {len(CATEGORIES)} categories "
            f"({PROMPTS_PER_CATEGORY} per category)."
        ),
    }, f)

print(f"\nDone. {len(notion_page_ids)}/{len(all_prompts)} prompts saved to Notion.")
