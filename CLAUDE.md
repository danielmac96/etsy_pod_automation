# CLAUDE.md — Etsy POD Automation

Automated weekly pipeline for an Etsy print-on-demand shop selling gym + corporate culture graphic tees. Runs Monday–Sunday via GitHub Actions. **Only two manual steps:** approve prompts in Notion on Monday, approve images on Wednesday.

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

Notion is the single source of truth. JSON files (`keywords.json`, `prompts.json`, etc.) are local audit logs only.

---

## Scripts

### `01_research.py`
Generates keywords for this week's designs.
- **Reads:** hardcoded seed keywords + `ETSY_API_KEY` (optional, for Etsy suggested searches)
- **Writes:** `keywords.json` — list of `{keyword, source}` dicts (`source`: `"gemini"` or `"etsy"`)
- **APIs:** Gemini AI, Etsy v3 suggested-searches

### `02_generate_prompts.py`
Generates 25 prompts (5 per category) and saves them to Notion for approval.
- **Reads:** `keywords.json`, top-performing Published pages from Notion (feedback loop)
- **Writes:** 25 Notion pages (`Pipeline Status: Prompt Unreviewed`), `prompts.json` (audit log), `notify_context.json`
- **APIs:** Gemini AI, Notion

### `03_generate_images.py`
Generates images for every prompt the user approved in Notion.
- **Reads:** Notion pages where `Pipeline Status = Prompt Approved`
- **Writes:** `images/*.png` locally, uploads to ImgBB, PATCHes Notion pages (`Image URL`, `Pipeline Status: Image Unreviewed`), `images/results.json`, `notify_context.json`
- **APIs:** FAL.ai (Ideogram v3), ImgBB, Notion

### `04_generate_copy.py`
Generates Etsy product copy (title, description, 13 tags) for every image the user approved.
- **Reads:** Notion pages where `Pipeline Status = Image Approved`
- **Writes:** PATCHes Notion (`Etsy Title`, `Description`, `Tags`, `Pipeline Status: Copy Generated`), `notify_context.json`
- **APIs:** Gemini AI, Notion

### `05_notify.py`
Sends a stage-appropriate email after each pipeline phase.
- **Reads:** `notify_context.json` (`stage`, `count`, `detail`)
- **Writes:** Email via Gmail SMTP
- **Stage values:** `"prompts"` / `"images"` / `"drafts"`

### `06_printify_upload.py`
Creates Printify product drafts for every design with generated copy.
- **Reads:** Notion pages where `Pipeline Status = Copy Generated` and no `Printify Draft URL`
- **Writes:** Printify draft products, PATCHes Notion (`Printify Draft URL`, `Pipeline Status: Drafted`), `notify_context.json`
- **APIs:** Printify, Notion

### `07_track_stats.py`
Syncs Etsy listing stats (views, favorites) back into Notion. Feeds into 02 the following Monday.
- **Reads:** Notion pages where `Etsy Listing URL` is set
- **Writes:** PATCHes Notion (`Views`, `Favorites`, `Views Since Last Sync`, `Favorites Since Last Sync`, `Stats Updated`)
- **APIs:** Etsy v3, Notion

### `notion_fields.py`
Shared constants and helpers. All Notion property names and pipeline status strings live here. **Always use these constants — never hardcode strings.**

### `test_pipeline.py`
Inspection and dry-run tool. Does not modify any pipeline state.

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

All commands run from the repo root. Requires a `.env` file with credentials (see Environment Variables below).

### Check current state
```bash
# Full report: all checkpoint files + Notion status breakdown
python scripts/test_pipeline.py

# Checkpoint files only (no Notion query needed)
python scripts/test_pipeline.py files

# Notion DB counts grouped by pipeline status
python scripts/test_pipeline.py notion
```

### Validate before running a step
Checks that required inputs exist and env vars are set — run this before each step.
```bash
python scripts/test_pipeline.py validate 01   # env vars only
python scripts/test_pipeline.py validate 02   # keywords.json + env vars
python scripts/test_pipeline.py validate 03   # Prompt Approved count + env vars
python scripts/test_pipeline.py validate 04   # Image Approved count + env vars
python scripts/test_pipeline.py validate 06   # Copy Generated count + env vars
python scripts/test_pipeline.py validate 07   # Etsy listing URLs + env vars
```

### Preview AI output without saving (tune prompts here)
These make real API calls but write nothing to disk or Notion.
```bash
# Preview keyword generation from Gemini + Etsy
python scripts/test_pipeline.py dry-run 01

# Preview one prompt per category (uses your actual keywords.json)
python scripts/test_pipeline.py dry-run 02

# Preview title/description/tags for the first Image Approved page
python scripts/test_pipeline.py dry-run 04
```

### Run a step manually
```bash
python scripts/01_research.py
python scripts/02_generate_prompts.py
python scripts/03_generate_images.py
python scripts/04_generate_copy.py
python scripts/06_printify_upload.py
python scripts/07_track_stats.py
```

---

## Environment Variables

Create a `.env` file in the repo root for local development.

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
| `ETSY_API_KEY` | 01, 07 | Etsy REST API |
| `ETSY_ACCESS_TOKEN` | 07 | Etsy OAuth2 bearer token |

---

## GitHub Actions Workflows

| File | Schedule | Steps |
|---|---|---|
| `weekly.yml` | Mon 9am EST | 01 → 02 → 05 |
| `weekly_images.yml` | Wed 9am EST | 03 → 05 |
| `weekly_copy_and_draft.yml` | Thu 9am EST | 04 → 06 → 05 |
| `weekly_stats.yml` | Sun 9am EST | 07 |

All workflows support `workflow_dispatch` for manual triggering. Secrets are set in **Settings → Secrets and variables → Actions**.

---

## Notion DB Properties

| Property | Type | Set By |
|---|---|---|
| Name | Title | 02 |
| Prompt | Rich Text | 02 |
| Category | Select | 02 |
| Pipeline Status | Select | all scripts |
| Etsy Title | Rich Text | 04 |
| Description | Rich Text | 04 |
| Tags | Rich Text | 04 |
| Image URL | URL | 03 |
| Generated At | Date | 03 |
| Printify Draft URL | URL | 06 |
| Etsy Listing URL | URL | you (manual) |
| Views | Number | 07 |
| Favorites | Number | 07 |
| Views Since Last Sync | Number | 07 |
| Favorites Since Last Sync | Number | 07 |
| Stats Updated | Date | 07 |

**First-time setup:** add `Category` (Select) and `Description` (Rich Text) if migrating from old schema. Update Pipeline Status options to match all values in the status flow above.

---

## Code Conventions

- Python 3.11, procedural scripts, no classes
- All Notion property names via constants in `notion_fields.py`
- HTTP calls use `requests` directly; always call `.raise_for_status()`
- Script 02 POSTs new Notion pages; all other scripts PATCH existing ones
- Secrets via `os.environ.get(...)` only — never hardcoded

## Known Issues

- `04_save_to_notion.py` is superseded — kept for reference, not used in any workflow
- `notion-client` in `requirements.txt` is unused; scripts use `requests` directly
