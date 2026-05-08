-- Analytical schema for the self-iterating Etsy POD pipeline.
-- schema_migrations is bootstrapped by src/db.py:run_migrations() before this file runs.

CREATE TABLE research_runs (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    config_json         TEXT NOT NULL,
    config_hash         TEXT NOT NULL,
    brand_voice         TEXT NOT NULL,
    notes               TEXT
);

CREATE TABLE themes (
    theme_id            TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES research_runs(run_id),
    theme_name          TEXT NOT NULL,
    description         TEXT NOT NULL,
    category            TEXT NOT NULL,
    cultural_tension    TEXT NOT NULL,
    seeded_from         TEXT,
    parent_brief_id     TEXT,
    created_at          TEXT NOT NULL,
    notes               TEXT
);

CREATE TABLE etsy_probes (
    probe_id            TEXT PRIMARY KEY,
    theme_id            TEXT NOT NULL REFERENCES themes(theme_id),
    query               TEXT NOT NULL,
    intent              TEXT NOT NULL,
    sort_on             TEXT NOT NULL,
    listings_returned   INTEGER NOT NULL,
    cache_hit           INTEGER NOT NULL,
    fetched_at          TEXT NOT NULL,
    raw_response_path   TEXT
);

CREATE TABLE etsy_listings (
    listing_id          INTEGER NOT NULL,
    probe_id            TEXT NOT NULL REFERENCES etsy_probes(probe_id),
    title               TEXT NOT NULL,
    tags_json           TEXT NOT NULL,
    price_usd           REAL,
    currency_code       TEXT,
    num_favorers        INTEGER,
    views               INTEGER,
    shop_id             INTEGER,
    taxonomy_path_json  TEXT,
    is_digital          INTEGER,
    is_personalizable   INTEGER,
    creation_tsz        INTEGER,
    listing_url         TEXT,
    primary_image_url   TEXT,
    PRIMARY KEY (listing_id, probe_id)
);
CREATE INDEX idx_listings_favorers ON etsy_listings(num_favorers);
CREATE INDEX idx_listings_shop ON etsy_listings(shop_id);

CREATE TABLE concepts (
    concept_id                  TEXT PRIMARY KEY,
    theme_id                    TEXT NOT NULL REFERENCES themes(theme_id),
    concept_name                TEXT NOT NULL,
    headline_text               TEXT NOT NULL,
    visual_concept              TEXT NOT NULL,
    style_tags_json             TEXT NOT NULL,
    color_palette_hint          TEXT,
    target_buyer                TEXT,
    differentiation_note        TEXT,
    evidence_listing_ids_json   TEXT NOT NULL,
    selected                    INTEGER NOT NULL DEFAULT 0,
    rejection_reason            TEXT,
    created_at                  TEXT NOT NULL
);

CREATE TABLE design_briefs (
    brief_id            TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES research_runs(run_id),
    concept_id          TEXT NOT NULL REFERENCES concepts(concept_id),
    rank                INTEGER NOT NULL,
    category            TEXT NOT NULL,
    headline_text       TEXT NOT NULL,
    visual_concept      TEXT NOT NULL,
    style_tags_json     TEXT NOT NULL,
    color_palette_hint  TEXT,
    target_buyer        TEXT,
    image_prompt_seed   TEXT NOT NULL,
    saturation          TEXT NOT NULL,
    volume_signal       TEXT NOT NULL,
    price_p25_usd       REAL,
    price_p75_usd       REAL,
    composite_score     REAL NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE lineage (
    notion_page_id      TEXT PRIMARY KEY,
    brief_id            TEXT REFERENCES design_briefs(brief_id),
    prompt_text         TEXT,
    image_url           TEXT,
    printify_draft_url  TEXT,
    etsy_listing_url    TEXT,
    created_at          TEXT NOT NULL,
    last_updated_at     TEXT NOT NULL
);

CREATE TABLE listing_stats (
    snapshot_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    notion_page_id      TEXT NOT NULL REFERENCES lineage(notion_page_id),
    snapshot_at         TEXT NOT NULL,
    views               INTEGER NOT NULL,
    favorites           INTEGER NOT NULL,
    views_delta         INTEGER,
    favorites_delta     INTEGER
);
CREATE INDEX idx_stats_page ON listing_stats(notion_page_id, snapshot_at);
