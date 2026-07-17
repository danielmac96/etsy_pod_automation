# Etsy POD Automation

Self-iterating weekly pipeline for an Etsy print-on-demand graphic-tee shop. Generates design themes, validates them against live Etsy data, drafts product copy, uploads to Printify, and feeds last week's `favorites_delta` back into next Monday's theme generation.

**One manual gate:** approve prompts in the local app on Monday — the cost gate before paid image generation. Everything after that runs automatically.

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

- **`pod.db` (SQLite)** — sole system of record: analytical brain + human approval state. Eight tables capture the unbroken chain from theme → published listing → weekly stats deltas. Committed to git by every workflow so state survives across runs.
- **Local Streamlit app** (`scripts/approve_app.py`) — the only human UI (Prompts + Stats tabs)
- **Gemini 2.5 Flash** — themes, probes, concepts, synthesis, copy
- **Etsy v3 (REST or MCP) + Printify v1** — market data + draft creation
- **FAL.ai (Ideogram v3) + ImgBB** — image generation + hosting

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/Scripts/activate   # bash on Windows
pip install -r requirements.txt

# 2. Configure
cp .env.example .env           # then fill in keys (see CLAUDE.md env-var table)

# 3. First run (cold-start)
python scripts/01_research.py --cold-start
python scripts/02_generate_prompts.py
# approve prompts in the local app …
streamlit run scripts/approve_app.py
# then images → copy → drafts run automatically:
python scripts/03_generate_images.py
python scripts/04_generate_copy.py
python scripts/06_printify_upload.py
# publish drafts in Printify; URLs auto-detect on Sunday:
python scripts/07_track_stats.py

# 4. Inspect anytime
python scripts/health_check.py
```

After the first weekly cycle, `01_research.py` (no `--cold-start`) loads `listing_stats`, computes the feedback signal, and biases ~40% of next week's themes toward winners.

## Weekly schedule (GitHub Actions)

| Schedule | Workflow | Action |
|----------|----------|--------|
| Mon 9am EST | `weekly.yml` | `01 → 02 → 05` (research + prompts + email) |
| Wed 9am EST | `weekly_images.yml` | `03 → 04 → 06 → 05` (images + copy + drafts, fully automatic) |
| Wed ~9:30am | `etsy_stats.yml` | `07` mid-week stats sync |
| Sun 9am EST | `weekly_stats.yml` | `07` auto-detect Etsy URLs + stats sync |

Each workflow commits `pod.db` after running, so state stays in sync with the local app. API keys live in **Settings → Secrets and variables → Actions**.

## Etsy MCP (richer market research — set `ETSY_USE_MCP=1`)

The `.mcp.json` configures a local Etsy MCP server (`mcp/etsy-mcp-server`) exposing `search_listings`, `get_trending_listings`, `get_shop` / `get_shop_listings`, `get_listing`, and `search_shops`. When `ETSY_USE_MCP=1`, `01_research.py` enriches theme generation with current Etsy trending tags. The REST `EtsyClient` is the default fallback (used by GitHub Actions, which have no local MCP server).

## Tests

```bash
pytest tests/      # no network required
```

## Operational notes

See [CLAUDE.md](./CLAUDE.md) for the full per-script contract, env-var matrix, GitHub Actions schedule, `pod.db` schema, code conventions, and cold-start vs. warm-start behavior.
