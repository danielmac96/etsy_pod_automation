# CLAUDE.md — Etsy POD Automation

Self-iterating weekly pipeline for an Etsy print-on-demand shop selling gym + corporate-culture graphic tees. Scheduled jobs run Mon→Sun via GitHub Actions; the human-in-the-loop work happens in a **local Streamlit browser app** (`scripts/approve_app.py`). Two manual approval gates: prompts on Monday, images on Wednesday. Last week's published-listing performance feeds back into Monday's theme generation, so each cycle biases toward what worked.

---

## Architecture at a glance

One store, one role:

- **`pod.db`** (SQLite) — system of record for both the analytical brain *and* human approval state. Eight tables capture the unbroken chain `research_runs → themes → etsy_probes → etsy_listings → concepts → design_briefs → lineage → listing_stats`. The `lineage` table carries `prompt_status / image_status / draft_status` columns that drive every pipeline stage, plus the Etsy copy fields (`etsy_title`, `etsy_description`, `etsy_tags_json`).

The Streamlit app at `scripts/approve_app.py` is the **only** human-facing UI. It reads and writes `pod.db` directly. There is no Notion integration anywhere in the codebase.

GitHub Actions still drives the unattended steps on a cron schedule. `pod.db` is committed to git by every workflow that mutates it; the local app pulls/pushes through the same git repository.

---

## Weekly Flow

```
MON  01_research.py → 02_generate_prompts.py → 05_notify.py
     [You: open the local app, approve/reject prompts]
WED  03_generate_images.py → 05_notify.py
     [You: open the local app, approve/reject images]
THU  04_generate_copy.py → 06_printify_upload.py → 05_notify.py
     [You: publish drafts in Printify — that's it; URLs auto-detect on Sunday]
SUN  07_track_stats.py  (auto-detects Etsy URLs by title, then syncs stats)
```

The local app: `streamlit run scripts/approve_app.py` opens `http://localhost:8501`.
Sidebar exposes "Pull latest pod.db" and "Push approvals" buttons that wrap `git pull --rebase` / `git add pod.db && git commit && git push`. Tabs: **Prompts** (Mon), **Images** (Wed gallery view), **Drafts** (Thu, with manual paste fallback), **Stats** (feedback signal dashboard). Each tab also has a "Run script now" button that invokes the next pipeline stage as a subprocess if you want to act between cron runs.

---

## Scripts

### `01_research.py` — themes → probes → mining → concepts → briefs
Six-stage Gemini + Etsy orchestrator. Reads the feedback signal from `pod.db` (cold-start sentinel on first run), generates `N_THEMES` themes split 40/40/20 across exploit/explore/underrepresented, fires Etsy probes on dual `sort_on` modes, mines saturation/volume/freshness/HHI/POD-feasibility per theme, extracts differentiated concepts, then ranks across themes into the final brief list.
- **Reads:** `pod.db` (`load_feedback_signal`), `GEMINI_API_KEY`, `ETSY_API_KEY`
- **Writes:** `design_briefs.json`, `runs/<run_id>/{design_briefs.json, research_summary.md, raw/}`, rows into `research_runs / themes / etsy_probes / etsy_listings / concepts / design_briefs`
- **CLI:** `--dry-run`, `--cold-start`, `--seed`, `--n-themes`, `--listings-per-probe`, `--final-count`, `--db-path`

### `02_generate_prompts.py`
Reads `design_briefs.json`, calls Gemini for 5 prompts × 5 categories, and inserts one `lineage` row per prompt via `db.lineage_create()` (`prompt_status='unreviewed'`). The "top performers" priming context is queried from `pod.db` listing_stats deltas.
- **Reads:** `design_briefs.json`, `pod.db`, `GEMINI_API_KEY`
- **Writes:** new `lineage` rows, `prompts.json` (audit log), `notify_context.json`
- **APIs:** Gemini AI

### `03_generate_images.py`
Generates images for every prompt the user approved.
- **Reads:** `pod.db` rows where `prompt_status='approved' AND image_url IS NULL` (via `lineage_pending_for_stage('image_gen')`)
- **Writes:** `images/*.png` locally, uploads to ImgBB, UPDATEs `lineage.image_url` + `image_status='unreviewed'`, `images/results.json`, `notify_context.json`
- **APIs:** FAL.ai (Ideogram v3), ImgBB

### `04_generate_copy.py`
Generates Etsy product copy (title ≤140 chars, 3-4 sentence description, 13 tags ≤20 chars each) for every approved image. Uses the shared `gemini_client.generate_json` (JSON mode + model fallback chain).
- **Reads:** `pod.db` rows where `image_status='approved' AND etsy_title IS NULL` (via `lineage_pending_for_stage('copy_gen')`)
- **Writes:** UPDATEs `lineage.etsy_title / etsy_description / etsy_tags_json`, `notify_context.json`
- **APIs:** Gemini AI

### `05_notify.py`
Sends a stage-appropriate **multipart HTML email** after each pipeline phase. Wednesday's email embeds inline image previews from ImgBB. All emails deep-link to the local app at `LOCAL_APP_URL` (default `http://localhost:8501`) with the relevant tab pre-selected via query param.
- **Reads:** `notify_context.json` (`stage`, `count`, `detail`, `items`)
- **Stage values:** `"prompts"` / `"images"` / `"drafts"`

### `06_printify_upload.py`
Creates Printify product drafts for every design with generated copy.
- **Reads:** `pod.db` rows where `etsy_title IS NOT NULL AND printify_draft_url IS NULL AND image_status='approved'` (via `lineage_pending_for_stage('draft_create')`)
- **Writes:** Printify draft products, UPDATEs `lineage.printify_draft_url` + `draft_status='drafted'`, `notify_context.json`
- **APIs:** Printify

### `07_track_stats.py`
Two passes against `pod.db`:
1. **Auto-detect Etsy URLs** — for every row in `draft_status='drafted'` with no `etsy_listing_url`, query the user's active Etsy shop listings (`/shops/{shop_id}/listings/active`), match by `etsy_title`, and UPDATE `etsy_listing_url` + `draft_status='published'`. **This eliminates the manual paste step.**
2. **Stats sync** — for every row with an `etsy_listing_url`, fetch live views/favorites and append a `listing_stats` snapshot. `db.record_stats()` computes `views_delta / favorites_delta` against the previous snapshot for the same `lineage_id`.
- **Reads:** `pod.db`, `ETSY_API_KEY`, `ETSY_ACCESS_TOKEN`, `ETSY_SHOP_ID`
- **Writes:** UPDATE on `lineage`, INSERT into `listing_stats`
- **APIs:** Etsy v3

### `approve_app.py` — local browser approval app
Streamlit single-file UI. Run with `streamlit run scripts/approve_app.py`.
- Reads pending work via `db.lineage_pending_for_stage(stage)` for each tab.
- Writes via `db.lineage_set_prompt_status / set_image_status / set_draft_status` and `db.lineage_upsert`.
- Sidebar exposes git pull/push buttons. Each tab has a "Run script now" button that invokes the next pipeline stage as a subprocess so you can fast-forward between cron runs.

### Shared modules
- `gemini_client.py` — `generate_json(client, prompt, *, schema=..., model=..., temperature=...)` with model fallback chain. Use everywhere; do not import `google.generativeai` directly.
- `etsy_client.py` — token-bucket rate limiter (5 rps default), persistent daily quota in `<cache_dir>/quota.json`, sha256 cache keys, 24h TTL, 4-attempt backoff honoring `Retry-After`, MAX_OFFSET=12000, dual `sort_on` exposed to callers.
- `schemas.py` — Pydantic v2 models: `EtsyListing`, `Evidence`, `DesignBrief` (carries `brief_id / concept_id / theme_id / run_id` UUIDs), `DesignBriefContent`, `ResearchRun`.
- `src/db.py` — typed `connect`, `run_migrations`, dataclass insert helpers (`Theme`, `EtsyProbe`, `EtsyListingRow`, `Concept`, `DesignBriefRow`), **lineage helpers** (`lineage_create`, `lineage_upsert`, `lineage_set_prompt_status / set_image_status / set_draft_status`, `lineage_pending_for_stage`), `record_stats`, `load_feedback_signal`.
- `src/research/{feedback,themes,probes,mining,concepts,synthesis}.py` — pure stage modules, each takes a `gen_fn` callable so they're trivially mockable.

### `test_pipeline.py`
Inspection + validation + dry-run tool. Pure pod.db reads (with one exception: `dry-run` calls Gemini for previews). Does not modify any pipeline state.

---

## Approval state

The `lineage` table carries three orthogonal status columns:

| Column | Values | Set by |
|---|---|---|
| `prompt_status` | `unreviewed` / `approved` / `rejected` | local app (Mon) |
| `image_status`  | `unreviewed` / `approved` / `rejected` | 03 sets `unreviewed`; local app (Wed) |
| `draft_status`  | `pending` / `drafted` / `published`    | 06 sets `drafted`; 07 auto-detect sets `published` |

A row "moves through the pipeline" by combinations of these three plus the presence of `image_url`, `etsy_title`, `printify_draft_url`, `etsy_listing_url`. `db.lineage_pending_for_stage(stage)` is the single helper that returns "what's ready for stage X" — every script and the local app go through it.

---

## Testing & Debugging

All commands run from the repo root. Requires `.env` (see Environment Variables).

### Inspect pipeline state
```bash
# Full report: checkpoint files + pod.db pipeline state
python scripts/test_pipeline.py
python scripts/test_pipeline.py files
python scripts/test_pipeline.py state         # pod.db pipeline state (replaces the old `notion` subcommand)
python scripts/test_pipeline.py db            # honors POD_DB_PATH

# Walk a single brief: RUN → THEME → CONCEPT → BRIEF → LINEAGE → stats
python scripts/test_pipeline.py lineage <brief_id>
```

### Validate before running a step
```bash
python scripts/test_pipeline.py validate 01   # env vars only
python scripts/test_pipeline.py validate 02   # design_briefs.json + env vars
python scripts/test_pipeline.py validate 03   # pod.db rows ready + env vars
python scripts/test_pipeline.py validate 04   # pod.db rows ready + env vars
python scripts/test_pipeline.py validate 06   # pod.db rows ready + env vars
python scripts/test_pipeline.py validate 07   # pod.db rows ready + env vars
```

### Preview AI output without saving (tune prompts here)
```bash
python scripts/test_pipeline.py dry-run 01    # one cold-start theme + concept round-trip
python scripts/test_pipeline.py dry-run 02    # one prompt per category
python scripts/test_pipeline.py dry-run 04    # title/description/tags for first image-approved row
```

### Run the unit + integration suite
```bash
pytest tests/                                  # 39 tests including phase-D smoke + warm-start loop
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
streamlit run scripts/approve_app.py           # the human approval UI
```

### Cold-start vs. warm-start
- **Cold start** — `listing_stats` is empty (or `--cold-start` is passed). All themes go to EXPLORE; no `seeded_from`.
- **Warm start** — populated stats. Themes split 40/40/20: EXPLOIT (riffs on top winning briefs), EXPLORE (novel cultural tensions, deduped against last 8 weeks via TF-IDF cosine ≥0.85), UNDERREPRESENTED (categories with low publish count).

---

## Environment Variables

| Variable | Required By | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | 01, 02, 04 | Google Gemini AI |
| `FAL_KEY` | 03 | FAL.ai (Ideogram image gen) |
| `IMGBB_API_KEY` | 03 | ImgBB image hosting |
| `GMAIL_USER` | 05 | Gmail send address |
| `GMAIL_APP_PASSWORD` | 05 | Gmail app password |
| `LOCAL_APP_URL` | 05 (optional) | Override the deep-link in emails (default `http://localhost:8501`) |
| `PRINTIFY_API_KEY` | 06 | Printify API |
| `PRINTIFY_SHOP_ID` | 06 | Printify shop ID |
| `ETSY_API_KEY` | 01, 07 | Etsy keystring (`x-api-key`) |
| `ETSY_ACCESS_TOKEN` | 07 | Etsy OAuth2 bearer token (scope `listings_r`) |
| `ETSY_SHOP_ID` | 07 (optional) | Numeric Etsy shop ID — required for the auto-detect step that eliminates the Thursday URL paste |
| `POD_DB_PATH` | every script + local app | Override default `pod.db` location (optional) |

---

## GitHub Actions Workflows

| File | Schedule | Steps |
|---|---|---|
| `weekly.yml` | Mon 9am EST | 01 → 02 → 05 → commit pod.db |
| `weekly_images.yml` | Wed 9am EST | 03 → 05 → commit pod.db |
| `weekly_copy_and_draft.yml` | Thu 9am EST | 04 → 06 → 05 → commit pod.db |
| `weekly_stats.yml` | Sun 9am EST | 07 (auto-detect + stats sync) → commit pod.db |
| `etsy_stats.yml` | Wed ~9:30am EST | 07 (mid-week stats sync) → commit pod.db |

All support `workflow_dispatch`. Secrets live in **Settings → Secrets and variables → Actions**. `pod.db` is committed by every workflow so analytical state and approval state both survive across runs and stay in sync with your local app.

---

## `pod.db` schema (migrations/)

Eight tables, one analytical brain. `schema_migrations` is bootstrapped in code; `.sql` files in `migrations/` apply in lexical order on every `db.run_migrations(conn)`.

- `research_runs` — one row per `01_research.py` invocation (config_json, brand_voice, started_at/finished_at)
- `themes` — Gemini-generated themes per run (`seeded_from`, `parent_brief_id`, dedup notes)
- `etsy_probes` — one row per (theme, query, sort_on); raw response dumped under `runs/<run_id>/raw/`
- `etsy_listings` — all listings ever seen, keyed by `(listing_id, probe_id)`
- `concepts` — Gemini-extracted per-theme concepts; `selected=1` once promoted to a brief
- `design_briefs` — final ranked briefs; mirrors `design_briefs.json` row-for-row
- `lineage` — one row per design candidate; carries `lineage_id` (synthetic UUID, primary key), `brief_id`, `prompt_text / image_url / printify_draft_url / etsy_listing_url`, the three status columns, and the Etsy copy fields. **System of record for human approval state.**
- `listing_stats` — append-only stats snapshots; `views_delta` / `favorites_delta` computed against the previous snapshot for the same `lineage_id`

Migration `0002_local_approval.sql` renamed the historical `notion_page_id` column to `lineage_id`, added the three status columns + Etsy copy fields, added indexes, and backfilled historical rows so the feedback signal keeps working.

---

## Code Conventions

- Python 3.11+, procedural scripts; classes only inside `src/` modules where state warrants it
- HTTP calls via `requests`; always call `.raise_for_status()`
- Secrets via `os.environ.get(...)` only — never hardcoded
- Gemini calls go through `gemini_client.generate_json` (JSON mode + fallback chain)
- All pipeline scripts read pending work via `db.lineage_pending_for_stage(stage)` — never write a custom `WHERE prompt_status = ...` query in script code
- Status writes go through `db.lineage_set_*` helpers (CHECK constraints reject typos)
