# Etsy POD Automation

Self-iterating weekly pipeline for an Etsy print-on-demand graphic-tee shop. Generates design themes, validates them against live Etsy data, drafts product copy, uploads to Printify, and feeds last week's `favorites_delta` back into next Monday's theme generation.

Two manual gates: **approve prompts in Notion on Monday, approve images on Wednesday.** Everything else is automated by GitHub Actions.

## What's in the loop

```
research_runs → themes → etsy_probes → etsy_listings → concepts → design_briefs
                                                                       ↓
                                                                    lineage
                                                                       ↓
                                                                 listing_stats
                                                                       ↓
                                              load_feedback_signal (next week)
```

- **Notion** — human approval UI (status field drives the pipeline)
- **`pod.db`** (SQLite) — analytical brain; eight tables capture the unbroken chain from theme → published listing → weekly stats deltas
- **Gemini 2.5 Flash** — themes, probes, concepts, synthesis, copy
- **Etsy v3 + Printify v1** — market data + draft creation
- **FAL.ai (Ideogram v3) + ImgBB** — image generation + hosting

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/Scripts/activate   # bash on Windows
pip install -r requirements.txt

# 2. Configure
cp .env.example .env           # then fill in keys
#   GEMINI_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID, FAL_KEY, IMGBB_API_KEY,
#   PRINTIFY_API_KEY, PRINTIFY_SHOP_ID, ETSY_API_KEY, ETSY_ACCESS_TOKEN,
#   GMAIL_USER, GMAIL_APP_PASSWORD

# 3. Notion DB columns — see CLAUDE.md "Notion DB Properties"
#    (Brief ID / Theme ID / Run ID rich-text columns are required for warm-start)

# 4. First run (cold-start)
python scripts/01_research.py --cold-start
python scripts/02_generate_prompts.py
# approve prompts in Notion …
python scripts/03_generate_images.py
# approve images in Notion …
python scripts/04_generate_copy.py
python scripts/06_printify_upload.py
# publish drafts in Printify, paste Etsy URL into Notion …
python scripts/07_track_stats.py

# 5. Inspect
python scripts/test_pipeline.py db
python scripts/test_pipeline.py lineage <brief_id>
```

After the first weekly cycle, `python scripts/01_research.py` (no flag) loads `listing_stats` from `pod.db`, computes the feedback signal, and biases ~40% of next week's themes toward winners.

## Tests

```bash
pytest tests/      # 36 tests including warm-start self-iteration smoke
```

## Operational notes

See [CLAUDE.md](./CLAUDE.md) for the full per-script contract, env-var matrix, GitHub Actions schedule, `pod.db` schema, code conventions, and cold-start vs. warm-start behavior.
