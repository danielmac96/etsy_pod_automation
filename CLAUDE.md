# CLAUDE.md — Etsy POD Automation

This file provides guidance for AI assistants working in this repository.

## Project Overview

This is a **weekly automated design-to-market pipeline** for an Etsy print-on-demand (POD) shop selling gym + corporate culture graphic tees. The target audience is corporate workers who identify with gym culture ("corporate drone who lifts heavy").

The pipeline runs every Monday at 9am EST via GitHub Actions and covers:
1. Trend research via Gemini AI
2. Image prompt generation via Gemini AI
3. Image generation via FAL.ai (Ideogram v3 model)
4. Saving designs to a Notion database
5. Email notification to the owner for manual review
6. (Manual trigger) Uploading approved designs to Printify POD
7. (Separate cron) Syncing Etsy listing stats back to Notion

## Repository Structure

```
etsy_pod_automation/
├── .github/workflows/
│   └── weekly.yml              # GitHub Actions: runs scripts 01-05 on Monday 9am EST
├── scripts/
│   ├── 01_research.py          # Generates 20 trending keywords via Gemini → keywords.json
│   ├── 02_generate_prompts.py  # Creates 3 image prompts from keywords + Notion history → prompts.json
│   ├── 03_generate_images.py   # Generates images via FAL/Ideogram, uploads to ImgBB → images/results.json
│   ├── 04_save_to_notion.py    # Saves image metadata to Notion database (status: Unreviewed)
│   ├── 05_notify.py            # Sends Gmail alert with design count + Notion link
│   ├── 06_printify_upload.py   # Uploads Approved designs to Printify, creates drafts, updates Notion
│   ├── 07_track_stats.py       # Syncs Etsy listing views/favorites into Notion with delta tracking
│   ├── notion_fields.py        # Shared: Notion DB schema constants and helper functions
│   └── upload_public_image.py  # Utility: Uploads a local image to ImgBB, returns public HTTPS URL
└── requirements.txt            # Python dependencies
```

## Data Flow

Scripts are numbered for execution order. Data is passed between steps via JSON files written into the `scripts/` directory:

```
01_research.py  →  keywords.json
                       ↓
02_generate_prompts.py  →  prompts.json
                               ↓
03_generate_images.py  →  images/results.json  +  images/image_NNN_TIMESTAMP.png
                               ↓
04_save_to_notion.py  →  Notion database entries (status: Unreviewed)
                               ↓
05_notify.py  →  Email to owner
                               ↓
        [MANUAL: Owner reviews in Notion, sets status to Approved, adds Etsy Title]
                               ↓
06_printify_upload.py  →  Printify draft product  →  Notion updated (status: Drafted)
                               ↓
        [MANUAL: Owner finishes product in Printify, publishes to Etsy, pastes URL in Notion]
                               ↓
07_track_stats.py  →  Notion updated with views/favorites deltas
```

## Notion Database Schema

The Notion database (`NOTION_DATABASE_ID`) is the single source of truth for all design workflow state. Properties are defined in `scripts/notion_fields.py` as constants — **always use those constants, never hardcode strings**.

| Property | Type | Description |
|---|---|---|
| `Name` | Title | Auto-generated from the image prompt |
| `Prompt` | Rich Text | Full image generation prompt |
| `Pipeline Status` | Select | Workflow stage (see values below) |
| `Etsy Title` | Rich Text | Product title for Etsy (owner fills manually) |
| `Tags` | Rich Text | Etsy tags (optional, owner fills manually) |
| `Image URL` | URL | ImgBB CDN link to generated image |
| `Generated At` | Date | When image was created |
| `Printify Draft URL` | URL | Link to the draft in Printify editor |
| `Etsy Listing URL` | URL | Live Etsy listing URL (owner fills manually) |
| `Views` | Number | Total Etsy listing views |
| `Favorites` | Number | Total Etsy listing favorites |
| `Views Since Last Sync` | Number | Delta views since last stat sync |
| `Favorites Since Last Sync` | Number | Delta favorites since last stat sync |
| `Stats Updated` | Date | Timestamp of last 07_track_stats.py run |

**Pipeline Status values:** `Unreviewed` → `Approved` / `Rejected` → `Drafted` → `Published`

## Environment Variables

All secrets are sourced from environment variables. For local development, use a `.env` file (loaded via `python-dotenv`). For CI, set these as GitHub Actions secrets.

| Variable | Used By | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | 01, 02 | Google Gemini API key |
| `NOTION_TOKEN` | 02, 04, 06, 07 | Notion integration Bearer token |
| `NOTION_DATABASE_ID` | 02, 04, 05, 06, 07 | Notion database UUID |
| `FAL_KEY` | 03 | FAL.ai API key (for Ideogram image generation) |
| `IMGBB_API_KEY` | 03 | ImgBB API key for image hosting |
| `GMAIL_USER` | 05 | Gmail address to send notifications from |
| `GMAIL_APP_PASSWORD` | 05 | Gmail app-specific password (not the account password) |
| `PRINTIFY_API_KEY` | 06 | Printify API key |
| `PRINTIFY_SHOP_ID` | 06 | Printify shop ID |
| `ETSY_API_KEY` | 07 | Etsy REST API key |
| `ETSY_ACCESS_TOKEN` | 07 | Etsy OAuth2 Bearer token |

## Development Conventions

### Language & Runtime
- **Python 3.11** (enforced in GitHub Actions)
- No build system — scripts run directly with `python scripts/NN_script_name.py`
- Dependencies managed via `requirements.txt`

### Code Style
- **Functional/procedural** scripts — no classes, minimal abstraction
- Use `pathlib.Path` for all file paths (not string concatenation)
- All secrets must come from environment variables (`os.environ.get(...)` or `os.getenv(...)`)
- Use `.raise_for_status()` on every HTTP response from external APIs
- Minimal error handling — only catch exceptions at natural recovery points (e.g., 02_generate_prompts.py gracefully handles an empty Notion DB on first run)

### Notion API
- Use `requests` directly (not the `notion-client` package, despite it being in requirements.txt)
- Use the shared headers/helpers in `notion_fields.py` — do not duplicate Notion request boilerplate
- Property names in API payloads must exactly match the constants in `notion_fields.py`

### JSON Data Files
- Scripts write their output to JSON files in the `scripts/` directory
- The next script in the pipeline reads from the previous script's output file
- Do not change output file names without updating all downstream scripts that read them

### Image Handling
- Images are generated locally to `scripts/images/image_NNN_TIMESTAMP.png`
- `upload_public_image.py` is called to upload local images to ImgBB and return a public URL
- The ImgBB URL is what gets stored in Notion and used for Printify uploads

### Adding a New Script
1. Number it appropriately (e.g., `08_new_step.py`)
2. Read inputs from the previous step's JSON output
3. Write outputs to a new JSON file in `scripts/`
4. Add any new required environment variables to this CLAUDE.md and to `.github/workflows/weekly.yml`
5. Add the script to the GitHub Actions workflow if it should run automatically

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Create a .env file with all required variables (see Environment Variables table above)
# Then run individual scripts:
python scripts/01_research.py
python scripts/02_generate_prompts.py
python scripts/03_generate_images.py
python scripts/04_save_to_notion.py
python scripts/05_notify.py

# Manually triggered (run after reviewing in Notion):
python scripts/06_printify_upload.py

# Stat tracking (run periodically):
python scripts/07_track_stats.py
```

## CI/CD — GitHub Actions

**File:** `.github/workflows/weekly.yml`

- **Automatic trigger:** Monday at 1:13 PM UTC (9:13am EST), via cron: `0 13 * * 1`
- **Manual trigger:** `workflow_dispatch` (can be triggered from the GitHub Actions UI)
- **Python version:** 3.11
- **Steps run automatically:** 01 through 05 (research → notify)
- **Steps NOT in automated workflow:** 06 (Printify upload) and 07 (stat tracking) — these run separately

All secrets in the workflow must be set in the repository's **Settings → Secrets and variables → Actions**.

## Known Issues & Quirks

- **`HF_API_KEY` in workflow:** This secret is listed in the workflow env block but is not actively used in any script (legacy from a previous Hugging Face integration attempt)
- **`notion-client` in requirements:** Imported in `notion_fields.py` but scripts use `requests` directly for all Notion API calls
- **No automated tests:** There are no test files in this repository. When making changes, manually run the relevant script and verify its JSON output and Notion database state
- **First run edge case:** `02_generate_prompts.py` handles an empty Notion database (no top-performing listings yet) by catching exceptions and continuing with just the keyword context

## External Services Summary

| Service | Used For | Docs |
|---|---|---|
| Google Gemini AI | Keyword research + prompt generation | ai.google.dev |
| FAL.ai (Ideogram v3) | AI image generation | fal.ai |
| ImgBB | Public image hosting (CDN for generated images) | api.imgbb.com |
| Notion API | Design database + workflow state management | developers.notion.com |
| Gmail SMTP | Owner notification emails | (app password required) |
| Printify API | Print-on-demand product creation | developers.printify.com |
| Etsy API v3 | Listing stats retrieval | developers.etsy.com |
