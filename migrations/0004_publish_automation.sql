-- Hands-off publishing + AI image pre-screening.
--
-- 1. publish_status — the cost gate for Etsy's $0.20 listing fee. Script 08
--    publishes every 'approved' row through the Printify publish API, which
--    removes the manual "open Printify and click Publish" Thursday step.
--    'unreviewed' rows wait in the local app's Publish tab (or are swept up
--    automatically when AUTO_PUBLISH=1).
ALTER TABLE lineage ADD COLUMN publish_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (publish_status IN ('unreviewed','approved','rejected','published'));

-- 2. AI pre-screen results written by 03_generate_images.py. score is 0-10
--    (print-readiness + text fidelity); feedback is the model's issue list.
ALTER TABLE lineage ADD COLUMN ai_score REAL;
ALTER TABLE lineage ADD COLUMN ai_feedback TEXT;

CREATE INDEX IF NOT EXISTS ix_lineage_publish_status ON lineage(publish_status);

-- 3. Backfill: anything already live on Etsy has, by definition, been published.
UPDATE lineage SET publish_status = 'published'
WHERE etsy_listing_url IS NOT NULL OR draft_status = 'published';
