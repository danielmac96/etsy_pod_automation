# CLAUDE.md — Etsy POD Automation

Self-iterating weekly pipeline for an Etsy print-on-demand shop selling gym + corporate-culture graphic tees. Scheduled jobs run Mon→Sun via GitHub Actions; the human-in-the-loop work happens in a **local Streamlit browser app** (`scripts/approve_app.py`). Three approval gates — prompts (Mon), images (Wed), publish (Thu) — sit exactly where money is spent, and each can be opened individually via `AUTO_*` env flags for a fully hands-off loop (see `AUTOMATION.md`). Last week's published-listing performance feeds back into Monday's theme generation, so each cycle biases toward what worked.

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
     [You: approve/reject prompts — or AUTO_APPROVE_PROMPTS=1]
WED  03_generate_images.py (+ Gemini vision pre-screen) → 05_notify.py
     [You: approve/reject images — AI auto-rejects garbled renders;
      AUTO_APPROVE_IMAGES=1 auto-approves scores ≥ 8]
THU  04_generate_copy.py → 06_printify_upload.py → (08 if AUTO_PUBLISH=1) → 05_notify.py
     [You: approve drafts in the Publish tab — or AUTO_PUBLISH=1]
FRI  08_publish_etsy.py → 05_notify.py  (publishes approved drafts via Printify API)
SUN  07_track_stats.py  (auto-detects Etsy URLs by title, then syncs stats)
```

The local app: `streamlit run scripts/approve_app.py` opens `http://localhost:8501`.
Sidebar exposes "Pull latest pod.db" and "Push approvals" buttons that wrap `git pull --rebase` / `git add pod.db && git commit && git push` (set `GIT_PUSH_TOKEN` to make these work on a cloud host). Tabs: **Prompts** (Mon), **Images** (Wed gallery view, sorted by AI score), **Publish** (Thu — the $0.20 listing-fee gate), **Listings** (manual URL paste fallback), **Stats** (feedback signal dashboard). Each tab also has a "Run script now" button that invokes the next pipeline stage as a subprocess if you want to act between cron runs.

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
Generates images for every prompt the user approved, then pre-screens each with Gemini vision (0–10 print-readiness score stored in `lineage.ai_score` / `ai_feedback`). Scores ≤ `AUTO_REJECT_IMAGE_MAX_SCORE` (3) are auto-rejected; with `AUTO_APPROVE_IMAGES=1`, scores ≥ `AUTO_APPROVE_IMAGE_MIN_SCORE` (8) are auto-approved. Screening is skipped when `GEMINI_API_KEY` is unset.
- **Reads:** `pod.db` rows where `prompt_status='approved' AND image_url IS NULL` (via `lineage_pending_for_stage('image_gen')`)
- **Writes:** `images/*.png` locally, uploads to ImgBB, UPDATEs `lineage.image_url / ai_score / ai_feedback` + `image_status`, `images/results.json`, `notify_context.json`
- **APIs:** FAL.ai (Ideogram v3), ImgBB, Gemini AI (vision pre-screen)

### `04_generate_copy.py`
Generates Etsy product copy (title ≤140 chars, 3-4 sentence description, 13 tags ≤20 chars each) for every approved image. Uses the shared `gemini_client.generate_json` (JSON mode + model fallback chain).
- **Reads:** `pod.db` rows where `image_status='approved' AND etsy_title IS NULL` (via `lineage_pending_for_stage('copy_gen')`)
- **Writes:** UPDATEs `lineage.etsy_title / etsy_description / etsy_tags_json`, `notify_context.json`
- **APIs:** Gemini AI

### `05_notify.py`
Sends a stage-appropriate **multipart HTML email** after each pipeline phase. Wednesday's email embeds inline image previews from ImgBB with AI pre-screen scores. All emails deep-link to the local app at `LOCAL_APP_URL` (default `http://localhost:8501`) with the relevant tab pre-selected via query param. Skips the email entirely for a zero-count `"published"` run.
- **Reads:** `notify_context.json` (`stage`, `count`, `detail`, `items`)
- **Stage values:** `"prompts"` / `"images"` / `"drafts"` / `"published"`

### `06_printify_upload.py`
Creates Printify product drafts for every design with generated copy.
- **Reads:** `pod.db` rows where `etsy_title IS NOT NULL AND printify_draft_url IS NULL AND image_status='approved'` (via `lineage_pending_for_stage('draft_create')`)
- **Writes:** Printify draft products, UPDATEs `lineage.printify_draft_url` + `draft_status='drafted'` (`publish_status` stays `'unreviewed'` — the Publish gate), `notify_context.json`
- **APIs:** Printify

### `08_publish_etsy.py`
Publishes approved Printify drafts to Etsy through the Printify publish API — replaces the manual "open Printify, click Publish" step. With `AUTO_PUBLISH=1` it first sweeps all `publish_review` rows through the gate. Always exits 0 so a partial failure never blocks the pod.db commit (which would cause republishes).
- **Reads:** `pod.db` rows where `publish_status='approved' AND draft_status='drafted'` (via `lineage_pending_for_stage('publish')`)
- **Writes:** Printify publish calls, UPDATEs `lineage.publish_status='published'`, `notify_context.json` (stage `"published"`)
- **APIs:** Printify (`POST /shops/{id}/products/{pid}/publish.json`)

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

The `lineage` table carries four orthogonal status columns:

| Column | Values | Set by |
|---|---|---|
| `prompt_status`  | `unreviewed` / `approved` / `rejected` | local app (Mon); 02 auto-approves with `AUTO_APPROVE_PROMPTS=1` |
| `image_status`   | `unreviewed` / `approved` / `rejected` | 03 sets from the AI pre-screen (auto-reject ≤3, auto-approve ≥8 when `AUTO_APPROVE_IMAGES=1`, else `unreviewed`); local app (Wed) |
| `publish_status` | `unreviewed` / `approved` / `rejected` / `published` | local app Publish tab (Thu) or `AUTO_PUBLISH=1`; 08 sets `published` |
| `draft_status`   | `pending` / `drafted` / `published`    | 06 sets `drafted`; 07 auto-detect sets `published` |

A row "moves through the pipeline" by combinations of these plus the presence of `image_url`, `etsy_title`, `printify_draft_url`, `etsy_listing_url`. `db.lineage_pending_for_stage(stage)` is the single helper that returns "what's ready for stage X" — every script and the local app go through it. `lineage.ai_score` / `ai_feedback` hold the Gemini vision pre-screen result.

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
python scripts/08_publish_etsy.py              # publish approved drafts to Etsy
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
| `PRINTIFY_API_KEY` | 06, 08 | Printify API |
| `PRINTIFY_SHOP_ID` | 06, 08 | Printify shop ID |
| `ETSY_API_KEY` | 01, 07 | Etsy keystring (`x-api-key`) |
| `ETSY_ACCESS_TOKEN` | 07 (fallback) | Static Etsy OAuth2 bearer token (expires in 1h — prefer refresh token) |
| `ETSY_REFRESH_TOKEN` | 07 | 90-day refresh token; `src/etsy_auth.py` mints a fresh access token per run |
| `ETSY_SHOP_ID` | 07 (optional) | Numeric Etsy shop ID — required for the auto-detect step that eliminates the Thursday URL paste |
| `AUTO_APPROVE_PROMPTS` | 02 (optional) | `1` opens the Monday prompt gate |
| `AUTO_APPROVE_IMAGES` / `AUTO_APPROVE_IMAGE_MIN_SCORE` / `AUTO_REJECT_IMAGE_MAX_SCORE` | 03 (optional) | AI-screen-driven image gate behavior |
| `AUTO_PUBLISH` | 08 (optional) | `1` opens the publish gate ($0.20/listing) |
| `GIT_PUSH_TOKEN` | local app (optional) | Fine-grained PAT so git pull/push works on a cloud-hosted app |
| `POD_DB_PATH` | every script + local app | Override default `pod.db` location (optional) |

---

## GitHub Actions Workflows

| File | Schedule | Steps |
|---|---|---|
| `weekly.yml` | Mon 9am EST | 01 → 02 → 05 → commit pod.db |
| `weekly_images.yml` | Wed 9am EST | 03 → 05 → commit pod.db |
| `weekly_copy_and_draft.yml` | Thu 9am EST | 04 → 06 → (08 when `AUTO_PUBLISH=1`) → 05 → commit pod.db |
| `weekly_publish.yml` | Fri 9am EST | 08 (publish approved drafts) → 05 → commit pod.db |
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
- `lineage` — one row per design candidate; carries `lineage_id` (synthetic UUID, primary key), `brief_id`, `prompt_text / image_url / printify_draft_url / etsy_listing_url`, the four status columns, `ai_score`/`ai_feedback`, and the Etsy copy fields. **System of record for human approval state.**
- `listing_stats` — append-only stats snapshots; `views_delta` / `favorites_delta` computed against the previous snapshot for the same `lineage_id`

Migration `0002_local_approval.sql` renamed the historical `notion_page_id` column to `lineage_id`, added the three status columns + Etsy copy fields, added indexes, and backfilled historical rows so the feedback signal keeps working. Migration `0004_publish_automation.sql` added `publish_status` (the Etsy-listing-fee cost gate consumed by script 08) plus the `ai_score`/`ai_feedback` pre-screen columns.

---

## Code Conventions

- Python 3.11+, procedural scripts; classes only inside `src/` modules where state warrants it
- HTTP calls via `requests`; always call `.raise_for_status()`
- Secrets via `os.environ.get(...)` only — never hardcoded
- Gemini calls go through `gemini_client.generate_json` (JSON mode + fallback chain)
- All pipeline scripts read pending work via `db.lineage_pending_for_stage(stage)` — never write a custom `WHERE prompt_status = ...` query in script code
- Status writes go through `db.lineage_set_*` helpers (CHECK constraints reject typos)
