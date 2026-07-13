import json
import os
import sys
from pathlib import Path

from google import genai
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
from gemini_client import generate_json
from src import db
from src.config import auto_approve_prompts

load_dotenv()

DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

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


def get_top_performers(conn) -> list[str]:
    """Pull recent winners directly from pod.db's listing_stats deltas."""
    rows = conn.execute(
        """
        SELECT b.category, b.headline_text, l.prompt_text,
               COALESCE(SUM(s.favorites_delta), 0) AS fav_total,
               COALESCE(SUM(s.views_delta), 0)     AS views_total,
               (SELECT favorites FROM listing_stats s2
                  WHERE s2.lineage_id = l.lineage_id
                  ORDER BY snapshot_at DESC LIMIT 1) AS last_favs,
               (SELECT views     FROM listing_stats s2
                  WHERE s2.lineage_id = l.lineage_id
                  ORDER BY snapshot_at DESC LIMIT 1) AS last_views
        FROM listing_stats s
        JOIN lineage l       ON l.lineage_id = s.lineage_id
        JOIN design_briefs b ON b.brief_id   = l.brief_id
        WHERE s.favorites_delta IS NOT NULL
          AND s.snapshot_at >= datetime('now', '-28 days')
        GROUP BY l.lineage_id
        ORDER BY fav_total DESC
        LIMIT 8
        """
    ).fetchall()

    lines = []
    for r in rows[:5]:
        text = (r["prompt_text"] or r["headline_text"] or "").strip()
        if not text:
            continue
        snippet = text[:200] + ("..." if len(text) > 200 else "")
        bit = (
            f"- [{r['category']}] {snippet} | "
            f"favs={r['last_favs'] or 0}, views={r['last_views'] or 0}, "
            f"favs_delta={r['fav_total']}, views_delta={r['views_total']}"
        )
        lines.append(bit)
    return lines


def get_rejected_lines(conn) -> list[str]:
    """Recently rejected prompts/images (from db.load_rejection_signal) as
    negative examples — the other half of the feedback loop."""
    samples = db.load_rejection_signal(conn).get("recent_rejected_prompts") or []
    lines = []
    for s in samples:
        snippet = (s["prompt_text"] or "").strip()[:160]
        if not snippet:
            continue
        note = f" | AI note: {s['ai_feedback'][:80]}" if s.get("ai_feedback") else ""
        lines.append(f"- [{s['category']}] rejected at {s['rejected_at']} gate: {snippet}{note}")
    return lines


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
    rejected_lines: list[str],
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

    rejection_context = ""
    if rejected_lines:
        category_rejects = [l for l in rejected_lines if f"[{category}]" in l]
        relevant_rejects = (category_rejects or rejected_lines)[:5]
        rejection_context = (
            "\n\nRecently REJECTED designs (avoid these directions and repeat mistakes):\n"
            + "\n".join(relevant_rejects)
        )

    brief_context = _build_brief_context(briefs)

    prompt = f"""You are a witty copywriter for a viral Etsy shop selling gym + corporate culture graphic tees.

Category: {category}
Category theme: {description}{brief_context}{performer_context}{rejection_context}

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

    out: list[dict] = []
    for i, p in enumerate(prompts):
        src_brief = briefs[i % len(briefs)] if briefs else None
        out.append({"prompt": p, "brief": src_brief})
    return out


briefs_by_category, briefs_meta = load_design_briefs()

conn = db.connect(DB_PATH)
db.run_migrations(conn)

top_lines = get_top_performers(conn)
rejected_lines = get_rejected_lines(conn)
all_prompts: list[dict] = []
created_lineage_ids: list[str] = []

for category, description in CATEGORIES.items():
    print(f"\n--- Generating prompts for: {category} ---")
    pairs = generate_prompts_for_category(
        category, description, briefs_by_category[category], top_lines, rejected_lines
    )
    for pair in pairs:
        prompt_text = pair["prompt"]
        src_brief = pair["brief"]
        lineage_id = db.lineage_create(
            conn,
            brief_id=(src_brief or {}).get("brief_id"),
            prompt_text=prompt_text,
            category=category,
        )
        # AUTO_APPROVE_PROMPTS=1 opens the Monday gate: prompts go straight
        # to Wednesday's image gen (which spends FAL credits — default off).
        if auto_approve_prompts():
            db.lineage_set_prompt_status(conn, lineage_id, "approved")
        created_lineage_ids.append(lineage_id)
        all_prompts.append({
            "lineage_id": lineage_id,
            "category": category,
            "prompt": prompt_text,
            "brief_id": (src_brief or {}).get("brief_id"),
        })
        print(f"  Saved [{category}]: {prompt_text[:80]}...")

conn.close()

# Audit log (consumed nowhere downstream; useful for git diffs)
with open("prompts.json", "w") as f:
    json.dump(all_prompts, f, indent=2)

# Context for 05_notify.py — items list lets the email render a per-prompt summary table
_auto = auto_approve_prompts()
with open("notify_context.json", "w") as f:
    json.dump({
        "count": len(created_lineage_ids),
        "stage": "prompts",
        "detail": (
            f"{len(created_lineage_ids)} prompts generated across {len(CATEGORIES)} categories "
            f"({PROMPTS_PER_CATEGORY} per category)."
            + (" AUTO_APPROVE_PROMPTS is on — all prompts were auto-approved and "
               "will go to image generation on Wednesday." if _auto else "")
        ),
        "items": [
            {"lineage_id": p["lineage_id"], "category": p["category"],
             "prompt": p["prompt"]}
            for p in all_prompts
        ],
    }, f)

print(f"\nDone. {len(created_lineage_ids)} prompts saved to pod.db.")
