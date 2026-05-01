# CLAUDE.md — Etsy POD Automation

Self-iterating weekly pipeline for an Etsy print-on-demand shop selling gym + corporate-culture graphic tees. Runs Mon→Sun via GitHub Actions. **Two manual approval gates:** prompts on Monday, images on Wednesday. Last week's published-listing performance feeds back into Monday's theme generation, so each cycle biases toward what worked.

---

## Architecture at a glance

Two stores, two roles:

- **Notion** — the human-approval UI. One page per design. Status field drives the pipeline (`Prompt Unreviewed → Prompt Approved → Image Unreviewed → Image Approved → Copy Generated → Drafted → Published`).
- **`pod.db`** (SQLite) — the analytical brain. Eight tables capture the unbroken chain `research_runs → themes → etsy_probes → etsy_listings → concepts → design_briefs → lineage → listing_stats`. Every published listing traces from a theme, and every Sunday stats sync writes a fresh `listing_stats` row whose `favorites_delta` becomes next Monday's feedback signal.

Source of truth for deltas is `pod.db`; Notion's `Views Since Last Sync` / `Favorites Since Last Sync` columns are mirrored from `listing_stats` rows (not recomputed from stale Notion numbers).

---

## Weekly Flow

```
MON  01_research.py → 02_generate_prompts.py → 05_notify.py
     [You: approve/reject prompts in Notion]
WED  03_generate_images.py → 05_notify.py
     [You: approve/reject images in Notion]
THU  04_generate_copy.py → 06_printify_upload.py → 05_notify.py
     [You: publish drafts in Printify, paste Etsy URL into Notion]
SUN  07_track_stats.py
```

---

## Scripts

### `01_research.py` — themes → probes → mining → concepts → briefs
Six-stage Gemini + Etsy orchestrator. Reads the feedback signal from `pod.db` (cold-start sentinel on first run), generates `N_THEMES` themes split 40/40/20 across exploit/explore/underrepresented, fires Etsy probes on dual `sort_on` modes, mines saturation/volume/freshness/HHI/POD-feasibility per theme, extracts differentiated concepts, then ranks across themes into the final brief list.
- **Reads:** `pod.db` (`load_feedback_signal`), `GEMINI_API_KEY`, `ETSY_API_KEY`
- **Writes:** `design_briefs.json` (consumed by 02), `runs/<run_id>/{design_briefs.json, research_summary.md, raw/}`, rows into `research_runs / themes / etsy_probes / etsy_listings / concepts / design_briefs`
- **CLI:** `--dry-run`, `--cold-start` (force ignore stats), `--seed`, `--n-themes`, `--listings-per-probe`, `--final-count`, `--db-path`

### `02_generate_prompts.py`
Reads `design_briefs.json`, POSTs one Notion page per brief (status `Prompt Unreviewed`), then writes `lineage(brief_id, prompt_text)` keyed by the new `notion_page_id`. Carries `Brief ID / Theme ID / Run ID` rich-text columns onto every page so the chain is visible in Notion.
- **APIs:** Gemini AI, Notion

### `03_generate_images.py`
Generates images for every prompt the user approved.
- **Reads:** Notion pages where `Pipeline Status = Prompt Approved`
- **Writes:** `images/*.png` locally, uploads to ImgBB, PATCHes Notion (`Image URL`, `Pipeline Status: Image Unreviewed`), `images/results.json`, `notify_context.json`
- **APIs:** FAL.ai (Ideogram v3), ImgBB, Notion

### `04_generate_copy.py`
Generates Etsy product copy (title ≤140 chars, 3-4 sentence description, 13 tags ≤20 chars each) for every approved image. Uses the shared `gemini_client.generate_json` (JSON mode + model fallback chain). Calls `db.lineage_upsert` to backfill `image_url` if 03 missed it.
- **Reads:** Notion pages where `Pipeline Status = Image Approved`
- **Writes:** PATCHes Notion (`Etsy Title`, `Description`, `Tags`, `Pipeline Status: Copy Generated`), `notify_context.json`, `lineage` row
- **APIs:** Gemini AI, Notion

### `05_notify.py`
Sends a stage-appropriate email after each pipeline phase.
- **Reads:** `notify_context.json` (`stage`, `count`, `detail`)
- **Stage values:** `"prompts"` / `"images"` / `"drafts"`

### `06_printify_upload.py`
Creates Printify product drafts for every design with generated copy, then writes `lineage(printify_draft_url=...)`.
- **Reads:** Notion pages where `Pipeline Status = Copy Generated` and no `Printify Draft URL`
- **Writes:** Printify draft products, PATCHes Notion (`Printify Draft URL`, `Pipeline Status: Drafted`), `lineage` row, `notify_context.json`
- **APIs:** Printify, Notion

### `07_track_stats.py`
Syncs Etsy stats. **`pod.db` is the source of truth for deltas.** Each run inserts a `listing_stats` row and computes `views_delta` / `favorites_delta` against the previous snapshot for the same `notion_page_id`. Notion's "Since Last Sync" columns mirror those deltas.
- **Reads:** Notion pages where `Etsy Listing URL` is set
- **Writes:** `listing_stats` row, lineage backfill (`etsy_listing_url`, `brief_id`), PATCHes Notion (`Views`, `Favorites`, `Views Since Last Sync`, `Favorites Since Last Sync`, `Stats Updated`)
- **APIs:** Etsy v3, Notion

### Shared modules
- `notion_fields.py` — Notion property name constants + helpers (`notion_headers`, `rich_text_plain`, etc.). **Always use these constants — never hardcode strings.**
- `gemini_client.py` — `generate_json(client, prompt, *, schema=..., model=..., temperature=...)` with model fallback chain. Use this everywhere; do not import `google.generativeai` directly.
- `etsy_client.py` — token-bucket rate limiter (5 rps default), persistent daily quota in `<cache_dir>/quota.json`, sha256 cache keys, 24h TTL, 4-attempt backoff honoring `Retry-After`, MAX_OFFSET=12000, dual `sort_on` exposed to callers.
- `schemas.py` — Pydantic v2 models: `EtsyListing`, `Evidence`, `DesignBrief` (carries `brief_id / concept_id / theme_id / run_id` UUIDs), `DesignBriefContent`, `ResearchRun`.
- `src/db.py` — typed `connect`, `run_migrations`, dataclass insert helpers (`Theme`, `EtsyProbe`, `EtsyListingRow`, `Concept`, `DesignBriefRow`), `lineage_upsert`, `record_stats`, `load_feedback_signal`.
- `src/research/{feedback,themes,probes,mining,concepts,synthesis}.py` — pure stage modules. Each takes a `gen_fn` callable so they're trivially mockable in tests.

### `test_pipeline.py`
Inspection + validation + dry-run tool. Does not modify any pipeline state.

---

## Pipeline Status Values

```
Prompt Unreviewed → Prompt Approved / Prompt Rejected
                          ↓
                    Image Unreviewed → Image Approved / Image Rejected
                                             ↓
                                       Copy Generated → Drafted → Published
```

---

## Testing & Debugging

All commands run from the repo root. Requires `.env` (see Environment Variables).

### Inspect pipeline state
```bash
# Full report: checkpoint files, Notion counts, pod.db row counts + last 5 lineage / listing_stats
python scripts/test_pipeline.py
python scripts/test_pipeline.py files
python scripts/test_pipeline.py notion
python scripts/test_pipeline.py db                      # honors POD_DB_PATH

# Walk a single brief: RUN → THEME → CONCEPT → BRIEF → LINEAGE → stats
python scripts/test_pipeline.py lineage <brief_id>
```

### Validate before running a step
```bash
python scripts/test_pipeline.py validate 01   # env vars only
python scripts/test_pipeline.py validate 02   # design_briefs.json + env vars
python scripts/test_pipeline.py validate 03   # Prompt Approved count + env vars
python scripts/test_pipeline.py validate 04   # Image Approved count + env vars
python scripts/test_pipeline.py validate 06   # Copy Generated count + env vars
python scripts/test_pipeline.py validate 07   # Etsy listing URLs + env vars
```

### Preview AI output without saving (tune prompts here)
```bash
python scripts/test_pipeline.py dry-run 01    # one cold-start theme + concept round-trip
python scripts/test_pipeline.py dry-run 02    # one prompt per brief in design_briefs.json
python scripts/test_pipeline.py dry-run 04    # title/description/tags for first Image Approved page
```

### Run the unit + integration suite
```bash
pytest tests/                                  # 36 tests including phase-D smoke + warm-start loop
```

### Run a step manually
```bash
python scripts/01_research.py                  # warm-start (uses listing_stats deltas)
python scripts/01_research.py --cold-start     # ignore stats, all-explore generation
python scripts/02_generate_prompts.py
python scripts/03_generate_images.py
python scripts/04_generate_copy.py
python scripts/06_printify_upload.py
python scripts/07_track_stats.py
```

### Cold-start vs. warm-start
- **Cold start** — `listing_stats` is empty (or `--cold-start` is passed). All themes go to EXPLORE; no `seeded_from`. Use this on day-1 or after wiping `pod.db`.
- **Warm start** — populated stats. Themes split 40/40/20: EXPLOIT (riffs on top winning briefs, `seeded_from='last_week_winner'`, `parent_brief_id` set), EXPLORE (novel cultural tensions, deduped against last 8 weeks via TF-IDF cosine ≥0.85), UNDERREPRESENTED (categories with low publish count, `seeded_from='underrepresented_category'`).

---

## Environment Variables

| Variable | Required By | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | 01, 02, 04 | Google Gemini AI |
| `NOTION_TOKEN` | 02, 03, 04, 06, 07 | Notion integration token |
| `NOTION_DATABASE_ID` | 02–07 | Notion database UUID |
| `FAL_KEY` | 03 | FAL.ai (Ideogram image gen) |
| `IMGBB_API_KEY` | 03 | ImgBB image hosting |
| `GMAIL_USER` | 05 | Gmail send address |
| `GMAIL_APP_PASSWORD` | 05 | Gmail app password |
| `PRINTIFY_API_KEY` | 06 | Printify API |
| `PRINTIFY_SHOP_ID` | 06 | Printify shop ID |
| `ETSY_API_KEY` | 01, 07 | Etsy keystring (`x-api-key`) |
| `ETSY_ACCESS_TOKEN` | 07 | Etsy OAuth2 bearer token (scope `listings_r`) |
| `POD_DB_PATH` | 04, 06, 07, test_pipeline | Override default `pod.db` location (optional) |

---

## GitHub Actions Workflows

| File | Schedule | Steps |
|---|---|---|
| `weekly.yml` | Mon 9am EST | 01 → 02 → 05 |
| `weekly_images.yml` | Wed 9am EST | 03 → 05 |
| `weekly_copy_and_draft.yml` | Thu 9am EST | 04 → 06 → 05 |
| `weekly_stats.yml` | Sun 9am EST | 07 |

All support `workflow_dispatch`. Secrets in **Settings → Secrets and variables → Actions**. `pod.db` is committed and updated by the workflows so the analytical state is preserved across runs.

---

## Notion DB Properties

| Property | Type | Set By |
|---|---|---|
| Name | Title | 02 |
| Prompt | Rich Text | 02 |
| Category | Select | 02 |
| Pipeline Status | Select | all scripts |
| Brief ID | Rich Text | 02 |
| Theme ID | Rich Text | 02 |
| Run ID | Rich Text | 02 |
| Etsy Title | Rich Text | 04 |
| Description | Rich Text | 04 |
| Tags | Rich Text | 04 |
| Image URL | URL | 03 |
| Generated At | Date | 03 |
| Printify Draft URL | URL | 06 |
| Etsy Listing URL | URL | you (manual) |
| Views | Number | 07 |
| Favorites | Number | 07 |
| Views Since Last Sync | Number | 07 (mirrored from `listing_stats`) |
| Favorites Since Last Sync | Number | 07 (mirrored from `listing_stats`) |
| Stats Updated | Date | 07 |

**First-time setup:** add `Brief ID`, `Theme ID`, `Run ID` (Rich Text) if migrating from the pre-`pod.db` schema. Update `Pipeline Status` options to match the status flow above.

---

## `pod.db` schema (migrations/)

Eight tables, one analytical brain. `schema_migrations` is bootstrapped in code; `.sql` files in `migrations/` apply in lexical order on every `db.run_migrations(conn)`.

- `research_runs` — one row per `01_research.py` invocation (config_json, brand_voice, started_at/finished_at)
- `themes` — Gemini-generated themes per run (`seeded_from`, `parent_brief_id`, dedup notes)
- `etsy_probes` — one row per (theme, query, sort_on); raw response dumped under `runs/<run_id>/raw/`
- `etsy_listings` — all listings ever seen, keyed by `(listing_id, probe_id)`
- `concepts` — Gemini-extracted per-theme concepts; `selected=1` once promoted to a brief
- `design_briefs` — final ranked briefs; mirrors `design_briefs.json` row-for-row
- `lineage` — one row per Notion page; carries `brief_id / prompt_text / image_url / printify_draft_url / etsy_listing_url`
- `listing_stats` — append-only stats snapshots; `views_delta` / `favorites_delta` computed against the previous snapshot for the same `notion_page_id`

---

## Code Conventions

- Python 3.11+, procedural scripts; classes only inside `src/` modules where state warrants it
- All Notion property names via constants in `notion_fields.py`
- HTTP calls via `requests`; always call `.raise_for_status()`
- Script 02 POSTs new Notion pages; all other scripts PATCH existing ones
- Secrets via `os.environ.get(...)` only — never hardcoded
- Gemini calls go through `gemini_client.generate_json` (JSON mode + fallback chain)
- Every Notion-page-mutating script must also call `db.lineage_upsert(conn, page_id, ...)` so the analytical chain stays intact
