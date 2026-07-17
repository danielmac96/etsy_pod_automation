"""Research orchestrator: themes → probes → mining → concepts → briefs.

Reads feedback from pod.db (cold-start sentinel on first run), generates
themes via Gemini, probes Etsy on dual sort modes, mines saturation/volume
signals, asks Gemini to extract differentiated concepts per theme, then ranks
across themes into the final brief list. Persists every stage into pod.db so
the chain themes → concepts → design_briefs → lineage → listing_stats stays
unbroken.

Outputs `design_briefs.json` (consumed by 02_generate_prompts.py) plus
`runs/<run_id>/{design_briefs.json, research_summary.md, raw/}` for audit.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from dotenv import load_dotenv
from google import genai

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.gemini_client import generate_json
from src.schemas import (
    DesignBrief, DesignBriefContent, Evidence, ResearchRun,
)
from src import db
from src.research import concepts as concept_mod
from src.research import probes as probe_mod
from src.research import synthesis as synth_mod
from src.research import themes as theme_mod
from src.etsy_factory import get_etsy_client
from src.research.feedback import format_feedback_for_gemini
from src.research.mining import mine_theme

load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────
N_THEMES = 6
LISTINGS_PER_PROBE_DEFAULT = 50
FINAL_BRIEF_COUNT = 10
DEDUP_THRESHOLD = theme_mod.DEDUP_THRESHOLD

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
parser.add_argument("--dry-run", action="store_true",
                    help="No HTTP — Gemini still runs but Etsy results come from cache or are empty.")
parser.add_argument("--cold-start", action="store_true",
                    help="Force cold-start (ignore existing listing_stats)")
parser.add_argument("--seed", type=int, default=None)
parser.add_argument("--n-themes", type=int, default=N_THEMES)
parser.add_argument("--listings-per-probe", type=int, default=LISTINGS_PER_PROBE_DEFAULT)
parser.add_argument("--final-count", type=int, default=FINAL_BRIEF_COUNT)
parser.add_argument("--db-path", type=str, default="pod.db")
args = parser.parse_args()

if args.seed is not None:
    random.seed(args.seed)

# ── setup ─────────────────────────────────────────────────────────────────────
run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
run_dir = Path("runs") / run_id
run_dir.mkdir(parents=True, exist_ok=True)
raw_dir = run_dir / "raw"

conn = db.connect(args.db_path)
db.run_migrations(conn)

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
gen_fn = partial(generate_json, gemini_client, model="gemini-2.5-flash")

etsy_available = bool(os.environ.get("ETSY_API_KEY"))
etsy = get_etsy_client(cache_dir=run_dir / "etsy_cache", rps=5.0)


def _log(name: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
    (run_dir / f"{name}.txt").write_text(text, encoding="utf-8")


# ── 1. Feedback signal ────────────────────────────────────────────────────────
print(f"[01] Loading feedback signal (db={args.db_path})…")
signal = db.load_feedback_signal(conn) if not args.cold_start else {
    "is_cold_start": True, "weeks_analyzed": 0, "top_winning_briefs": [],
    "underrepresented_categories": [], "winning_style_tags": [],
    "recently_explored_themes": [],
}
feedback_block = format_feedback_for_gemini(signal)
print(f"    cold_start={signal.get('is_cold_start')}, "
      f"winners={len(signal.get('top_winning_briefs') or [])}, "
      f"recent_themes={len(signal.get('recently_explored_themes') or [])}")
_log("01_feedback_signal", signal)

# ── 1b. Etsy trending enrichment (MCP only) ───────────────────────────────────
# Pulls get_trending_listings to weight theme generation toward what's hot now.
# Silently skipped when ETSY_USE_MCP is not set (REST client has no trending endpoint).
if os.environ.get("ETSY_USE_MCP") == "1":
    try:
        from collections import Counter
        trending_raw = etsy.get_trending_listings(limit=50)
        tag_counter: Counter = Counter()
        for item in trending_raw:
            for tag in (item.get("tags") or []):
                tag_counter[str(tag).lower()] += 1
        top_trending_tags = [t for t, _ in tag_counter.most_common(15)]
        if top_trending_tags:
            feedback_block += (
                "\n\n## Etsy trending tags (weight toward these in theme generation):\n"
                + "\n".join(f"- {t}" for t in top_trending_tags)
            )
            _log("01c_trending_tags", {"tags": top_trending_tags, "total_items": len(trending_raw)})
        print(f"    Trending enrichment: {len(top_trending_tags)} tags from {len(trending_raw)} listings")
    except Exception as exc:
        print(f"    Trending enrichment skipped (non-fatal): {exc}")

# ── 2. Create research_run row ────────────────────────────────────────────────
config = {
    "n_themes": args.n_themes,
    "listings_per_probe": args.listings_per_probe,
    "final_brief_count": args.final_count,
    "categories": CATEGORIES,
    "dry_run": args.dry_run,
    "cold_start": bool(args.cold_start or signal.get("is_cold_start")),
}
db.create_run(conn, run_id, config, BRAND_VOICE,
              notes=f"cli_args={vars(args)}")

# ── 3. Themes ─────────────────────────────────────────────────────────────────
print(f"[02] Generating {args.n_themes} themes…")
themes = theme_mod.generate_themes(
    gen_fn=gen_fn,
    n_themes=args.n_themes,
    brand_voice=BRAND_VOICE,
    categories=CATEGORIES,
    feedback_signal=signal,
    run_id=run_id,
    feedback_block=feedback_block,
)
themes_kept = theme_mod.filter_unique(themes)
dropped = [t for t in themes if t not in themes_kept]
db.insert_themes(conn, themes)  # persist ALL incl. near-dups (notes column tells the story)
for t in themes:
    print(f"    [{t.category}] {t.theme_name} "
          f"(seeded_from={t.seeded_from or '-'}; "
          f"{'NEAR-DUP' if (t.notes or '').startswith('near_duplicate_of:') else 'unique'})")
_log("02_themes", [vars(t) for t in themes])

# ── 4. Probes ─────────────────────────────────────────────────────────────────
print(f"[03] Generating + running probes for {len(themes_kept)} unique themes…")
theme_listings: dict[str, list] = {}
probe_count = 0
for theme in themes_kept:
    probes = probe_mod.generate_probes(gen_fn=gen_fn, theme=theme)
    if not probes:
        theme_listings[theme.theme_id] = []
        continue
    if not etsy_available and not args.dry_run:
        print(f"    [skip] no ETSY_API_KEY — theme {theme.theme_name!r} has no listings")
        for p in probes:
            for sort_on in ("score", "created"):
                pid = str(uuid.uuid4())
                db.insert_probe(conn, db.EtsyProbe(
                    probe_id=pid, theme_id=theme.theme_id, query=p["query"],
                    intent=p.get("intent") or "broad", sort_on=sort_on,
                    listings_returned=0, cache_hit=False,
                ))
        theme_listings[theme.theme_id] = []
        continue
    rows_per_probe = probe_mod.run_probes(
        etsy_client=etsy, theme=theme, probes=probes,
        listings_per_probe=args.listings_per_probe,
    )
    for probe_row, rows in rows_per_probe:
        db.insert_probe(conn, probe_row)
        if rows:
            db.insert_listings(conn, rows)
        probe_count += 1
        print(f"    [{theme.theme_name[:28]:28s}] "
              f"{probe_row.intent:6s} sort={probe_row.sort_on:7s} "
              f"q={probe_row.query!r:30s} → {probe_row.listings_returned} "
              f"{'(cache)' if probe_row.cache_hit else ''}")
    probe_mod.write_raw_responses(rows_per_probe, raw_dir)
    theme_listings[theme.theme_id] = probe_mod.deduplicate_listings(rows_per_probe)

print(f"    fired {probe_count} probes total")

# ── 5. Mining ─────────────────────────────────────────────────────────────────
print("[04] Mining theme landscapes…")
theme_mining: dict[str, dict] = {}
for theme in themes_kept:
    listings = theme_listings.get(theme.theme_id, [])
    m = mine_theme(listings)
    theme_mining[theme.theme_id] = m
    print(f"    [{theme.theme_name[:32]:32s}] n={m['n_listings']:3d} "
          f"sat={m['saturation']:6s} vol={m['volume_signal']:6s} "
          f"score={m['composite_score']:.3f}")
_log("04_mining", {tid: theme_mining[tid] for tid in theme_mining})

# ── 6. Concepts (per theme) ───────────────────────────────────────────────────
print("[05] Extracting concepts per theme…")
all_concepts: list = []
for theme in themes_kept:
    listings = theme_listings.get(theme.theme_id, [])
    if not listings:
        print(f"    [{theme.theme_name[:40]}] skipped (no listings)")
        continue
    cs = concept_mod.extract_concepts(
        gen_fn=gen_fn, theme=theme, listings=listings,
        mining=theme_mining[theme.theme_id],
    )
    all_concepts.extend(cs)
    db.insert_concepts(conn, cs)
    print(f"    [{theme.theme_name[:32]:32s}] +{len(cs)} concepts")

if not all_concepts:
    print("[!] No concepts extracted (likely no Etsy data). Aborting before synthesis.")
    db.finish_run(conn, run_id)
    sys.exit(2)

# ── 7. Synthesis (cross-theme ranking) ────────────────────────────────────────
print(f"[06] Synthesizing top {args.final_count} briefs across themes…")
brief_rows = synth_mod.synthesize_briefs(
    gen_fn=gen_fn,
    run_id=run_id,
    themes=themes_kept,
    concepts=all_concepts,
    theme_mining=theme_mining,
    feedback_signal=signal,
    brand_voice=BRAND_VOICE,
    final_count=args.final_count,
)
db.mark_concepts_selected(conn, [b.concept_id for b in brief_rows])
db.insert_briefs(conn, brief_rows)
print(f"    {len(brief_rows)} briefs ranked")

# ── 8. Build the JSON contract for downstream scripts ─────────────────────────
print("[07] Writing design_briefs.json…")
concept_by_id = {c.concept_id: c for c in all_concepts}
theme_by_id = {t.theme_id: t for t in themes}

briefs_out: list[DesignBrief] = []
for b in brief_rows:
    c = concept_by_id[b.concept_id]
    t = theme_by_id[c.theme_id]
    m = theme_mining.get(c.theme_id, {})
    supporting = []
    for lid in (c.evidence_listing_ids or [])[:5]:
        # find the listing in the deduped pool
        for L in theme_listings.get(c.theme_id, []):
            if L.listing_id == lid:
                supporting.append({
                    "title": L.title, "favorites": L.num_favorers or 0,
                    "url": L.listing_url or f"https://www.etsy.com/listing/{lid}",
                })
                break
    briefs_out.append(DesignBrief(
        brief_id=b.brief_id, concept_id=c.concept_id, theme_id=t.theme_id, run_id=run_id,
        concept_name=c.concept_name, rank=b.rank, category=b.category,
        evidence=Evidence(
            etsy_tag_overlap=[tag["tag"] for tag in (m.get("top_tags") or [])[:8]],
            search_volume_signal=str(m.get("volume_signal") or "unknown"),
            saturation=str(m.get("saturation") or "unknown"),
            supporting_listings=supporting,
            price_tier_usd=[v for v in (m.get("price_p25_usd"), m.get("price_p75_usd")) if v is not None],
        ),
        design_brief=DesignBriefContent(
            headline_text=b.headline_text,
            visual_concept=b.visual_concept,
            style_tags=list(b.style_tags or []),
            color_palette_hint=c.color_palette_hint or "",
            target_buyer=c.target_buyer or "",
        ),
        image_prompt_seed=b.image_prompt_seed,
    ))

run = ResearchRun(
    run_id=run_id,
    timestamp=datetime.now(timezone.utc).isoformat(),
    briefs=briefs_out,
)
out_payload = run.model_dump()
Path("design_briefs.json").write_text(
    json.dumps(out_payload, indent=2, ensure_ascii=False), encoding="utf-8"
)
(run_dir / "design_briefs.json").write_text(
    json.dumps(out_payload, indent=2, ensure_ascii=False), encoding="utf-8"
)

# ── 9. Research summary (markdown) ────────────────────────────────────────────
summary_lines = [
    f"# Research run {run_id}",
    "",
    f"- cold_start: **{signal.get('is_cold_start')}**",
    f"- themes generated: {len(themes)} (kept unique: {len(themes_kept)}, "
    f"dropped near-dups: {len(dropped)})",
    f"- probes fired: {probe_count}",
    f"- concepts extracted: {len(all_concepts)}",
    f"- briefs ranked: {len(brief_rows)}",
    "",
    "## Feedback signal in",
    "```",
    feedback_block,
    "```",
    "",
    "## Themes",
]
for t in themes:
    flag = " (NEAR-DUP)" if (t.notes or "").startswith("near_duplicate_of:") else ""
    summary_lines.append(f"- **[{t.category}] {t.theme_name}**{flag}  ")
    summary_lines.append(f"  tension: {t.cultural_tension}  ")
    summary_lines.append(f"  seeded_from: `{t.seeded_from or '-'}`  parent: `{t.parent_brief_id or '-'}`")

summary_lines += ["", "## Briefs (ranked)"]
for b in brief_rows:
    c = concept_by_id[b.concept_id]
    summary_lines.append(
        f"{b.rank}. **[{b.category}] {c.concept_name}** — "
        f"\"{b.headline_text}\" (composite={b.composite_score})"
    )
(run_dir / "research_summary.md").write_text(
    "\n".join(summary_lines), encoding="utf-8"
)

db.finish_run(conn, run_id)

conn.close()

print(f"\nDone. {len(briefs_out)} briefs → design_briefs.json")
print(f"Run artifacts: {run_dir}/")
for b in briefs_out:
    print(f"  #{b.rank} [{b.category}] {b.concept_name} — \"{b.design_brief.headline_text}\"")
