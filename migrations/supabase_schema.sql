-- ============================================================
-- Supabase schema for the Claude Tasks ↔ Python bridge.
--
-- This file is documentation + a manual apply target. It is NOT
-- run against pod.db (that is a separate SQLite schema). Apply it
-- in the Supabase dashboard SQL editor, via the Supabase CLI, or
-- via the Supabase MCP tool from a Claude Code session.
-- ============================================================

-- ============================================================
-- TABLE: research_seeds
-- Written by: Claude Task (Sunday night seed generator)
-- Read by:    01_research.py (Monday AM)
-- ============================================================
CREATE TABLE IF NOT EXISTS research_seeds (
    id BIGSERIAL PRIMARY KEY,
    seed_text TEXT NOT NULL,
    seed_type TEXT NOT NULL CHECK (seed_type IN ('hot_theme','trend_rising','seasonal','evergreen')),
    priority_score REAL NOT NULL DEFAULT 0.5,   -- 0..1, higher = more Etsy API budget
    trend_verdict TEXT CHECK (trend_verdict IN ('rising','plateau','declining','unknown')),
    trend_reasoning TEXT,                        -- one-sentence rationale from Claude
    source TEXT,                                 -- 'hot_signal_backprop' | 'web_trend' | 'manual'
    week_of DATE NOT NULL,                       -- Monday of the target research week
    used_in_run TEXT,                            -- run_id once 01_research.py consumes it
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_seeds_week ON research_seeds(week_of);
CREATE INDEX IF NOT EXISTS idx_seeds_unused ON research_seeds(used_in_run) WHERE used_in_run IS NULL;

-- ============================================================
-- TABLE: hot_signals
-- Written by: Claude Task (daily hot signal checker)
-- Read by:    01_research.py, 07_track_stats.py
-- ============================================================
CREATE TABLE IF NOT EXISTS hot_signals (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL,
    etsy_url TEXT,
    shop_id BIGINT,
    favorites_current INT,
    favorites_previous INT,
    favorites_delta INT,
    views_current INT,
    growth_pct REAL,            -- favorites_delta / max(favorites_previous,1)
    signal_strength REAL,       -- normalized 0..1
    theme_hint TEXT,            -- Claude's best-guess theme label for this listing
    checked_at TIMESTAMPTZ DEFAULT now(),
    run_date DATE DEFAULT CURRENT_DATE
);
CREATE INDEX IF NOT EXISTS idx_hot_signals_date ON hot_signals(run_date);
CREATE INDEX IF NOT EXISTS idx_hot_signals_listing ON hot_signals(listing_id);

-- ============================================================
-- TABLE: trend_cache
-- Written by: Claude Task (daily trend research)
-- Read by:    01_research.py (skip re-researching recently checked themes)
-- ============================================================
CREATE TABLE IF NOT EXISTS trend_cache (
    id BIGSERIAL PRIMARY KEY,
    theme_text TEXT NOT NULL UNIQUE,
    trend_score REAL,
    trend_verdict TEXT CHECK (trend_verdict IN ('rising','plateau','declining','unknown')),
    trend_reasoning TEXT,
    search_evidence TEXT,       -- bullet points Claude found via web search
    checked_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '7 days')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trend_cache_theme ON trend_cache(theme_text);

-- ============================================================
-- TABLE: pipeline_sync
-- Written by: Python scripts (so Claude Tasks can see pipeline state)
-- Read by:    Claude Tasks
--
-- UNIQUE(record_type, record_id) makes the table idempotent under
-- `Prefer: resolution=merge-duplicates` upserts from supabase_sync.py.
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_sync (
    id BIGSERIAL PRIMARY KEY,
    record_type TEXT NOT NULL CHECK (record_type IN ('live_listing','concept','brief','theme')),
    record_id TEXT NOT NULL,        -- matches pod.db id (lineage_id, concept_id, brief_id, theme_id)
    external_id TEXT,               -- etsy listing_id, printify_id, etc.
    etsy_url TEXT,
    label TEXT,                     -- human-readable (concept headline, listing title)
    status TEXT,
    favorites INT DEFAULT 0,
    views INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (record_type, record_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_type ON pipeline_sync(record_type);
CREATE INDEX IF NOT EXISTS idx_sync_live ON pipeline_sync(record_type, etsy_url) WHERE record_type = 'live_listing';
