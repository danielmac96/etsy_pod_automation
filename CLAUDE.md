# CLAUDE.md — Etsy POD Automation

This file provides guidance for AI assistants working in this repository.

## Project Overview

This is a **weekly automated design-to-market pipeline** for an Etsy print-on-demand (POD) shop selling gym + corporate culture graphic tees. The target audience is corporate workers who identify with gym culture ("corporate drone who lifts heavy").

The pipeline runs across three days each week via GitHub Actions. The only user actions required are clicking a status field in Notion twice — once to approve prompts, once to approve images.

## Pipeline Architecture

```
MONDAY (auto)
  01_research.py        → keywords.json (Gemini AI + Etsy suggested searches)
  02_generate_prompts.py → 25 prompts in Notion across 5 categories (status: Prompt Unreviewed)
  05_notify.py          → Email: "25 prompts ready to approve"

  [USER: set Prompt Approved / Prompt Rejected in Notion]

WEDNESDAY (auto)
  03_generate_images.py → images for Prompt Approved pages → Notion updated (status: Image Unreviewed)
  05_notify.py          → Email: "X images ready to approve"

  [USER: set Image Approved / Image Rejected in Notion]

THURSDAY (auto)
  04_generate_copy.py   → Gemini generates title, description, tags for Image Approved → Notion updated
  06_printify_upload.py → Creates Printify drafts for Copy Generated pages → Notion updated (status: Drafted)
  05_notify.py          → Email: "X Printify drafts ready to review"

  [USER: review draft in Printify, publish to Etsy, paste Etsy URL into Notion]

SUNDAY (auto)
  07_track_stats.py     → Syncs views/favorites from Etsy API → Notion updated (feedback loop for 02)
```

## Repository Structure

```
etsy_pod_automation/
├── .github/workflows/
│   ├── weekly.yml                # Monday: research + prompt generation (renamed from original)
│   ├── weekly_images.yml         # Wednesday: image generation for approved prompts
│   ├── weekly_copy_and_draft.yml # Thursday: AI copy generation + Printify draft creation
│   └── weekly_stats.yml          # Sunday: Etsy stats sync
├── scripts/
│   ├── 01_research.py            # Gemini keywords + Etsy suggested searches → keywords.json
│   ├── 02_generate_prompts.py    # 5 categories × 5 prompts → saved to Notion + prompts.json audit log
│   ├── 03_generate_images.py     # Reads Prompt Approved from Notion → generates images → Image Unreviewed
│   ├── 04_generate_copy.py       # Reads Image Approved from Notion → Gemini copy → Copy Generated
│   ├── 04_save_to_notion.py      # SUPERSEDED — kept for reference only, not used in workflow
│   ├── 05_notify.py              # Stage-aware email notification (reads notify_context.json)
│   ├── 06_printify_upload.py     # Reads Copy Generated from Notion → Printify drafts → Drafted
│   ├── 07_track_stats.py         # Syncs Etsy listing stats → Notion (feedback loop)
│   ├── notion_fields.py          # Shared: Notion DB schema constants and helpers
│   └── upload_public_image.py    # Utility: uploads a local image to ImgBB
└── requirements.txt
```

## Data Flow Detail

Each script that completes a stage writes `notify_context.json` so `05_notify.py` knows what email to send.

```
01_research.py  →  keywords.json  (list of {keyword, source} dicts)
                        ↓
02_generate_prompts.py  →  Notion pages (status: Prompt Unreviewed)
                        →  prompts.json  (audit log)
                        →  notify_context.json  (stage: "prompts")
                        ↓  [USER APPROVES PROMPTS IN NOTION]
03_generate_images.py  →  Notion pages PATCHED (Image URL, status: Image Unreviewed)
                       →  images/results.json  (audit log)
                       →  notify_context.json  (stage: "images")
                        ↓  [USER APPROVES IMAGES IN NOTION]
04_generate_copy.py  →  Notion pages PATCHED (Etsy Title, Description, Tags, status: Copy Generated)
                     →  notify_context.json  (stage: "copy", overwritten by 06)
                        ↓
06_printify_upload.py  →  Printify draft created
                       →  Notion pages PATCHED (Printify Draft URL, status: Drafted)
                       →  notify_context.json  (stage: "drafts")
                        ↓  [USER PUBLISHES IN PRINTIFY, PASTES ETSY URL INTO NOTION]
07_track_stats.py  →  Notion PATCHED (Views, Favorites, deltas, Stats Updated)
                   →  Data feeds back into 02 top performers next Monday
```

## Notion Database Schema

All property names are defined as constants in `scripts/notion_fields.py` — **always use those constants, never hardcode strings**.

### Properties

| Property | Type | Description |
|---|---|---|
| `Name` | Title | First 100 chars of the prompt (auto-set by script) |
| `Prompt` | Rich Text | Full image generation prompt |
| `Category` | Select | One of the 5 audience categories |
| `Pipeline Status` | Select | Current workflow stage (see status values below) |
| `Etsy Title` | Rich Text | AI-generated Etsy listing title (set by 04) |
| `Description` | Rich Text | AI-generated product description (set by 04) |
| `Tags` | Rich Text | AI-generated Etsy tags, comma-separated (set by 04) |
| `Image URL` | URL | ImgBB CDN link to generated image (set by 03) |
| `Generated At` | Date | When the image was created (set by 03) |
| `Printify Draft URL` | URL | Link to the draft in Printify editor (set by 06) |
| `Etsy Listing URL` | URL | Live Etsy listing URL (set manually by owner) |
| `Views` | Number | Total Etsy listing views (set by 07) |
| `Favorites` | Number | Total Etsy listing favorites (set by 07) |
| `Views Since Last Sync` | Number | Delta views since last stat sync (set by 07) |
| `Favorites Since Last Sync` | Number | Delta favorites since last stat sync (set by 07) |
| `Stats Updated` | Date | Timestamp of last 07_track_stats.py run |

### Pipeline Status Values (in order)

```
Prompt Unreviewed  →  Prompt Approved / Prompt Rejected
                            ↓
                       Image Unreviewed  →  Image Approved / Image Rejected
                                                  ↓
                                             Copy Generated
                                                  ↓
                                               Drafted
                                                  ↓
                                             Published
```

### Notion DB Setup Required

Add these two new properties if migrating from the old schema:
- **Category** — Select type with options: `Corporate Grind`, `Iron Discipline`, `Cardio Confession`, `Recovery Mode`, `Gym Flex`
- **Description** — Rich Text type

Update the **Pipeline Status** Select options to include all values listed above.

## The Five Categories

Defined in `02_generate_prompts.py` as `CATEGORIES`. Each gets 5 prompts per week (25 total).

| Category | Theme |
|---|---|
| `Corporate Grind` | Office frustration, meetings, burnout — gym as the escape valve |
| `Iron Discipline` | Powerlifting philosophy, 5am club, consistency, PRs as identity |
| `Cardio Confession` | The lifter doing cardio under protest, step goals, zone-2 irony |
| `Recovery Mode` | Rest days, deload weeks, overtrained and overworked |
| `Gym Flex` | PR celebrations, bro culture humor, gym memes, chalk and straps |

## Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | 01, 02, 04 | Google Gemini API key |
| `NOTION_TOKEN` | 02, 03, 04, 06, 07 | Notion integration Bearer token |
| `NOTION_DATABASE_ID` | 02, 03, 04, 05, 06, 07 | Notion database UUID |
| `FAL_KEY` | 03 | FAL.ai API key (Ideogram image generation) |
| `IMGBB_API_KEY` | 03 | ImgBB API key for image hosting |
| `GMAIL_USER` | 05 | Gmail address to send notifications from |
| `GMAIL_APP_PASSWORD` | 05 | Gmail app-specific password |
| `PRINTIFY_API_KEY` | 06 | Printify API key |
| `PRINTIFY_SHOP_ID` | 06 | Printify shop ID |
| `ETSY_API_KEY` | 01, 07 | Etsy REST API key (01: keyword suggestions; 07: listing stats) |
| `ETSY_ACCESS_TOKEN` | 07 | Etsy OAuth2 Bearer token |

## Development Conventions

### Language & Runtime
- **Python 3.11** (enforced in GitHub Actions)
- No build system — scripts run directly with `python scripts/NN_script_name.py`
- Dependencies managed via `requirements.txt`

### Code Style
- **Functional/procedural** scripts — no classes, minimal abstraction
- Use `pathlib.Path` for all file paths
- All secrets must come from environment variables (`os.environ.get(...)` or `os.getenv(...)`)
- Use `.raise_for_status()` on every HTTP response from external APIs

### Notion API
- Use `requests` directly (not the `notion-client` package, despite it being in requirements.txt)
- Use the shared headers/helpers in `notion_fields.py` — do not duplicate Notion request boilerplate
- Property names in API payloads must exactly match the constants in `notion_fields.py`
- Scripts that own a pipeline stage **PATCH** existing Notion pages; only script 02 **POSTs** new pages

### JSON Data Files
- `keywords.json` — list of `{keyword, source}` dicts (source: `"gemini"` or `"etsy"`)
- `prompts.json` — list of `{category, prompt}` dicts (audit log only; Notion is the source of truth)
- `images/results.json` — list of `{page_id, prompt, local_path, imgbb_url}` (audit log only)
- `notify_context.json` — `{count, stage, detail}` consumed by `05_notify.py`; overwritten each run

### Feedback Loop
Script `02_generate_prompts.py` calls `get_top_performers()` to fetch published designs sorted by favorites. Their prompts and categories are included as positive examples in the Gemini prompt for each category, so high-performing designs influence future prompt generation.

### Adding a New Script
1. Number it appropriately (e.g., `08_new_step.py`)
2. Read its gate condition from Notion (query for a specific Pipeline Status)
3. PATCH Notion pages (not POST) — the page already exists from step 02
4. Write `notify_context.json` if followed by `05_notify.py`
5. Add any new env vars to this file and to the relevant workflow file

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Create a .env file with all required variables (see Environment Variables table above)

# Run the Monday pipeline:
python scripts/01_research.py
python scripts/02_generate_prompts.py

# Then approve some prompts in Notion, then run Wednesday:
python scripts/03_generate_images.py

# Then approve some images in Notion, then run Thursday:
python scripts/04_generate_copy.py
python scripts/06_printify_upload.py

# Weekly stats (run anytime against published listings):
python scripts/07_track_stats.py
```

## CI/CD — GitHub Actions

| Workflow File | Schedule | What Runs |
|---|---|---|
| `weekly.yml` | Monday 9am EST (`0 14 * * 1`) | 01 → 02 → 05 (notify: prompts) |
| `weekly_images.yml` | Wednesday 9am EST (`0 14 * * 3`) | 03 → 05 (notify: images) |
| `weekly_copy_and_draft.yml` | Thursday 9am EST (`0 14 * * 4`) | 04 → 06 → 05 (notify: drafts) |
| `weekly_stats.yml` | Sunday 9am EST (`0 14 * * 0`) | 07 |

All workflows also support `workflow_dispatch` for manual triggering from the GitHub Actions UI.

All secrets must be set in **Settings → Secrets and variables → Actions**.

## Known Issues & Quirks

- **`notion-client` in requirements:** Imported in `notion_fields.py` but scripts use `requests` directly for all Notion API calls
- **`04_save_to_notion.py`:** Superseded by `04_generate_copy.py` + the Notion upload now built into `02_generate_prompts.py`. Kept for reference; not called by any workflow.
- **`HF_API_KEY`:** Legacy env var from a previous Hugging Face integration. No longer referenced anywhere.
- **No automated tests:** When making changes, manually run the relevant script and verify its `notify_context.json` output and Notion database state
- **First run edge case:** `02_generate_prompts.py` handles an empty Notion database gracefully (no top performers yet) by catching the exception in `get_top_performers()` and continuing with keyword context only

## External Services Summary

| Service | Used For |
|---|---|
| Google Gemini AI | Keyword research (01), prompt generation (02), copy writing (04) |
| Etsy API v3 | Keyword suggestions (01), listing stats (07) |
| FAL.ai (Ideogram v3) | AI image generation (03) |
| ImgBB | Public image hosting/CDN (03) |
| Notion API | Central state store for all design workflow data |
| Gmail SMTP | Owner notification emails (05) |
| Printify API | Print-on-demand product draft creation (06) |
