# AUTOMATION.md — how hands-off is the pipeline now?

Goal: run the Etsy shop as automatically as possible, keeping human approval
only where money is spent. This document maps every stage to its automation
state, explains the toggles, and lists the manual tasks that remain **your**
responsibility (marked TODO).

## Automation map (research → live Etsy listing)

| Stage | Script | Automated? | Human gate | Cost at gate |
|---|---|---|---|---|
| Market research (themes, probes, mining, briefs) | 01 | ✅ full (Mon cron) | none | Etsy API free tier |
| Prompt generation | 02 | ✅ full (Mon cron) | **Prompt approval** — openable via `AUTO_APPROVE_PROMPTS` | approving spends FAL image credits |
| Image generation | 03 | ✅ full (Wed cron) | — | ~cents/image (FAL Ideogram) |
| **AI image pre-screen** (new) | 03 | ✅ Gemini vision scores every image 0–10; garbled renders (≤3) auto-rejected | **Image approval** — `AUTO_APPROVE_IMAGES` approves scores ≥8 automatically | none directly (drafting is free) |
| Product title/description/tags | 04 | ✅ full (Thu cron) | none | Gemini free tier |
| Printify draft creation | 06 | ✅ full (Thu cron) | none | free |
| **Etsy publishing** (new — was manual clicking in Printify) | 08 | ✅ via Printify publish API (Thu if `AUTO_PUBLISH=1`, else Fri cron) | **Publish approval** in the app's Publish tab — openable via `AUTO_PUBLISH` | $0.20 Etsy listing fee per design |
| Etsy URL detection | 07 | ✅ full (matches by title, Sun + Wed crons) | none | — |
| Stats sync → next week's research bias | 07 → 01 | ✅ full | none | — |
| Notifications | 05 | ✅ email after every phase, deep-links to the app | — | — |
| Etsy OAuth token refresh (new) | src/etsy_auth.py | ✅ auto-refreshes the 1-hour access token from `ETSY_REFRESH_TOKEN` | — | — |

**Default posture:** the three gates stay closed (human approves), because each
one is the last stop before money is spent. Everything between the gates runs
itself. Flip repository **variables** (Settings → Secrets and variables →
Actions → Variables) to open gates one at a time:

| Variable | Effect when `1` |
|---|---|
| `AUTO_APPROVE_PROMPTS` | Monday prompts skip review → Wednesday images generate unattended |
| `AUTO_APPROVE_IMAGES` | Images scoring ≥ `AUTO_APPROVE_IMAGE_MIN_SCORE` (default 8) skip review |
| `AUTO_REJECT_IMAGE_MAX_SCORE` | Score at/below which images are auto-rejected (default 3; always active when Gemini scoring runs) |
| `AUTO_PUBLISH` | Thursday drafts are published to Etsy immediately — the full loop runs with zero clicks |

With all three on, the entire Monday→Sunday cycle is autonomous; the emails
become confirmations rather than requests.

## The web app

`streamlit run scripts/approve_app.py` — tabs: **Prompts · Images · Publish ·
Listings · Stats**. The new **Publish** tab is the $0.20 cost gate: approving a
draft there queues it for script 08, which lists it on Etsy through the
Printify API. The old "open Printify and click publish" step is gone; the
Listings tab's manual URL paste remains only as a fallback (07 auto-detects
URLs by title).

The app can run from anywhere, not just localhost: it now supports a
`GIT_PUSH_TOKEN` secret (GitHub fine-grained PAT, contents read/write on this
repo) so the Pull/Push buttons work on a cloud host. See TODO below.

## MCP findings

- **No Etsy or Printify MCP server exists in the Claude connector registry** —
  the pipeline's direct REST integrations (scripts/etsy_client.py,
  src/printify.py) remain the right approach for unattended CI runs.
- A community `etsy-mcp` stdio server is already optionally wired in
  (`ETSY_USE_MCP=1`, src/etsy_mcp_client.py) as a richer research data layer.
- A **Shopify MCP** exists in the registry if the shop ever expands to
  Shopify; today nothing here uses Shopify (drafting is Printify → Etsy).

## TODO — manual/user tasks (cannot be done from code)

Things that require your accounts, browsers, or one-time setup:

- [ ] **Mint an Etsy OAuth refresh token** and add it as the `ETSY_REFRESH_TOKEN`
      repo secret (plus `ETSY_ACCESS_TOKEN` if you want a static fallback).
      Etsy access tokens die after 1 hour — without the refresh token the
      Sunday/Wednesday stats syncs fail silently. Refresh tokens last 90 days:
      **re-mint quarterly** (calendar reminder recommended). Scope needed:
      `listings_r`.
- [ ] **Add the `GEMINI_API_KEY` secret to the Wednesday image workflow's
      scope** (already referenced in `weekly_images.yml`; the secret itself
      exists if Monday's workflow runs — nothing to do unless you scoped
      secrets per-environment).
- [ ] **Check Printify → Manage My Stores → Etsy connection settings** once:
      publish defaults (listing state, shipping profile, return policy) are
      taken from there when script 08 publishes via API.
- [ ] **Decide your gate posture**: set the `AUTO_*` repository variables
      (Settings → Secrets and variables → Actions → Variables). Recommended
      first step: `AUTO_APPROVE_IMAGES=1` (drafting costs nothing, the AI
      screen filters garbage) while keeping `AUTO_PUBLISH=0`.
- [ ] **(Optional) Deploy the approval app** to Streamlit Community Cloud
      (free) so approvals work from your phone: point it at this repo /
      `scripts/approve_app.py`, add `GIT_PUSH_TOKEN` to the app secrets, and
      set the `LOCAL_APP_URL` repo variable to the deployed URL so the email
      deep-links open it. Add Streamlit's built-in password protection or keep
      the app URL private — it can spend money (Publish tab).
- [ ] **(Optional) Install the community `etsy-mcp` server** (`npm i -g etsy-mcp`
      or equivalent) and set `ETSY_USE_MCP=1` if you want the MCP-backed
      research data layer locally.
- [ ] **Printify account funding / card on file** — fulfillment charges hit
      when orders come in; the API cannot manage billing.
