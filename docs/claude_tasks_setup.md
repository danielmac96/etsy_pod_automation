# Claude Tasks Setup Guide

Set up these 2 tasks in claude.ai → Tasks (Pro plan).
Each task needs the listed connectors enabled before the prompt will work.

The Python pipeline does not run any of this — Claude Tasks run on
claude.ai and write to Supabase. `01_research.py` then reads those seeds
on Monday morning. If Supabase is unreachable, the pipeline silently
falls back to its own feedback signal, so a missed Task run is safe.

---

## Task 1: Daily Hot Signal + Trend Check

**Schedule:** Every day at 9:00 AM
**Connectors required:** Supabase MCP, Etsy MCP, web search

**Prompt to paste:**

```
You are a daily market research assistant for a print-on-demand Etsy shop
selling gym + corporate-culture graphic tees.

STEP 1 — CHECK LISTING STATS
Using the Etsy MCP, fetch current favorites and views for each live
listing in the pipeline_sync table in Supabase
(record_type = 'live_listing'). For each listing, compare current
favorites to the stored value. Flag any listing where favorites grew more
than 20% since last check as a HOT SIGNAL.

STEP 2 — WRITE HOT SIGNALS
For each hot signal listing, insert a row into the hot_signals table in
Supabase:
- listing_id, etsy_url, favorites_current, favorites_previous,
  favorites_delta, growth_pct (delta / max(previous,1)),
  signal_strength (min(1.0, growth_pct)), theme_hint (your best guess at
  the design theme from the listing title), checked_at (now),
  run_date (today's date).
After writing, update the corresponding pipeline_sync row's `favorites`
and `views` so the next run sees the new baseline.

STEP 3 — TREND RESEARCH
Look at theme_hint values from hot signals and the recent research_seeds
in Supabase. Using web search, check the current cultural momentum for
up to 10 active or candidate themes. For each:
- Search for recent news, social media trends, seasonal signals
- Score: rising (0.7–1.0), plateau (0.4–0.6), declining (0.0–0.3)
- One sentence of evidence

STEP 4 — WRITE TREND CACHE
Upsert each result into the trend_cache table in Supabase:
(theme_text, trend_score, trend_verdict, trend_reasoning,
 search_evidence, expires_at = now + 7 days)

STEP 5 — SUMMARIZE
Output a brief summary:
- N listings checked, M hot signals found
- Top hot signal: [listing title] (+X favorites, Y% growth)
- Trend highlights: 2–3 sentences on what is rising this week
```

---

## Task 2: Weekly Seed Generator

**Schedule:** Every Sunday at 8:00 PM
**Connectors required:** Supabase MCP, web search

**Prompt to paste:**

```
You are a weekly research seed generator for a print-on-demand Etsy shop
that sells gym + corporate-culture graphic t-shirts. Your output feeds
directly into Monday's automated research pipeline (01_research.py).

STEP 1 — READ HOT SIGNALS
Query the hot_signals table in Supabase for the last 7 days. Group by
theme_hint. Sum signal_strength per theme. Identify the top 5 themes by
total signal strength — these are proven sellers worth doubling down on.

STEP 2 — READ TREND CACHE
Query the trend_cache table in Supabase for entries where:
  trend_verdict = 'rising' AND expires_at > now
These are trending themes Claude has already researched this week.

STEP 3 — DISCOVER NEW TRENDS
Using web search, research:
- Niche humor/lifestyle topics gaining traction this week
  (TikTok, Reddit, news cycles, upcoming holidays/events in next 30 days)
- Print-on-demand niches emerging on Etsy right now
- Any seasonal or event-driven themes that peak in the next 2–4 weeks

Focus on themes that work as graphic t-shirt designs: short phrases, bold
visuals, relatable humor, specific identity/hobby niches. Lean toward the
shop's lanes: gym before work, corporate burnout, powerlifting philosophy,
endurance irony, rest-day absurdity, gym-culture bro-humor.

STEP 4 — GENERATE SEED LIST
Produce a prioritized list of 15–20 seed themes for next week's run:
- 5 from hot signals (type = 'hot_theme', score = signal_strength normalized 0..1)
- 5 from trend_cache rising themes (type = 'trend_rising', score = trend_score)
- 5–10 new discoveries from web search
  (type = 'seasonal' or 'evergreen', score = your confidence 0..1)

STEP 5 — WRITE TO SUPABASE
Insert each seed into the research_seeds table:
- seed_text, seed_type, priority_score, trend_verdict, trend_reasoning
- source: 'hot_signal_backprop' | 'web_trend' | 'manual'
- week_of = NEXT Monday's date (the Monday that 01_research.py will run)
- used_in_run = NULL

STEP 6 — SUMMARIZE
Output:
- Total seeds generated: N
- Top 3 seeds by priority score with one-line reasoning each
- Any themes you're dropping from last week (declining or plateau with
  no signal)
- One sentence on the biggest trend opportunity you found this week
```

---

## Manual first-run checklist

Once the code is deployed and Supabase is provisioned:

1. Apply `migrations/supabase_schema.sql` in the Supabase dashboard SQL
   editor (or via the Supabase CLI / Supabase MCP).
2. Set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in your local `.env`
   and in the GitHub Actions secrets used by `weekly_stats.yml` /
   `etsy_stats.yml` so `07_track_stats.py` can push live-listing state
   to Supabase.
3. Create Task 1 and Task 2 in claude.ai with the prompts above.
4. **Trigger Task 2 manually once** so `research_seeds` has rows before
   the first Monday — `01_research.py` will quietly fall back to the
   feedback signal alone if the table is empty, but the whole point of
   the bridge is to feed it priority seeds.
5. Optional: set `ETSY_USE_MCP=1` and install `etsy-mcp` locally to
   route 01_research.py's Etsy probes through the MCP server instead
   of the REST client.
