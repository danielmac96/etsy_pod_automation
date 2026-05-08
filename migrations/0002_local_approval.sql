-- Move human-approval state from Notion into pod.db so a local browser app
-- can drive the weekly flow with no external UI dependency.
--
-- 1. Drop the now-Notion-flavored column name.
ALTER TABLE lineage RENAME COLUMN notion_page_id TO lineage_id;
ALTER TABLE listing_stats RENAME COLUMN notion_page_id TO lineage_id;

-- 2. Approval + draft state (replaces the Notion "Pipeline Status" select).
ALTER TABLE lineage ADD COLUMN prompt_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (prompt_status IN ('unreviewed','approved','rejected'));
ALTER TABLE lineage ADD COLUMN image_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (image_status IN ('unreviewed','approved','rejected'));
ALTER TABLE lineage ADD COLUMN draft_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (draft_status IN ('pending','drafted','published'));

-- 3. Etsy copy fields (previously stored only in Notion).
ALTER TABLE lineage ADD COLUMN category TEXT;
ALTER TABLE lineage ADD COLUMN etsy_title TEXT;
ALTER TABLE lineage ADD COLUMN etsy_description TEXT;
ALTER TABLE lineage ADD COLUMN etsy_tags_json TEXT;

-- 4. Indexes for the Streamlit "what's pending" queries.
CREATE INDEX IF NOT EXISTS ix_lineage_prompt_status ON lineage(prompt_status);
CREATE INDEX IF NOT EXISTS ix_lineage_image_status  ON lineage(image_status);
CREATE INDEX IF NOT EXISTS ix_lineage_draft_status  ON lineage(draft_status);

-- 5. Backfill historical rows so feedback_signal keeps working unchanged.
--    Anything that already reached Etsy is treated as fully approved + published.
--    Anything with an image but no Etsy URL is treated as approved through image stage.
--    Anything with a prompt but no image is treated as approved through prompt stage.
UPDATE lineage SET
    prompt_status = 'approved',
    image_status  = 'approved',
    draft_status  = 'published'
WHERE etsy_listing_url IS NOT NULL;

UPDATE lineage SET
    prompt_status = 'approved',
    image_status  = 'approved',
    draft_status  = 'drafted'
WHERE printify_draft_url IS NOT NULL AND etsy_listing_url IS NULL;

UPDATE lineage SET
    prompt_status = 'approved',
    image_status  = 'approved'
WHERE image_url IS NOT NULL AND printify_draft_url IS NULL;

UPDATE lineage SET prompt_status = 'approved'
WHERE prompt_text IS NOT NULL AND image_url IS NULL AND prompt_status = 'unreviewed';
