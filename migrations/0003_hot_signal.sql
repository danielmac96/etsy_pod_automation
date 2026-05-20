-- Add a hot_signal flag to listing_stats so the daily Claude Task that
-- flags spiking listings can be mirrored locally for lineage analytics.
--
-- The plan also called for `favorites_delta`, `views_delta`, and `checked_at`
-- columns — these already exist in 0001_init.sql (views_delta/favorites_delta)
-- or are served by the existing `snapshot_at` column (checked_at), so this
-- migration only adds what's actually missing.

ALTER TABLE listing_stats ADD COLUMN hot_signal INTEGER NOT NULL DEFAULT 0;
