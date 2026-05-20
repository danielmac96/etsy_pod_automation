"""Sunday stats sync — and Thursday-paste killer.

Two passes against pod.db:

  1. Auto-detect Etsy URLs for any row in draft_status='drafted' with no
     etsy_listing_url yet. Matches by etsy_title against the user's active
     Etsy shop listings. When matched, marks draft_status='published'.

  2. Stats sync. For every row with an etsy_listing_url, fetch the live
     views + favorites and append a listing_stats snapshot. Deltas are
     computed automatically by db.record_stats().

Requires OAuth access token with scope listings_r — set ETSY_API_KEY (the
keystring), ETSY_ACCESS_TOKEN (Bearer token), and ETSY_SHOP_ID (your numeric
shop id, not the URL slug).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
from src import db as pod_db

load_dotenv()

ETSY_KEY = os.environ.get("ETSY_API_KEY", "")
ETSY_TOKEN = os.environ.get("ETSY_ACCESS_TOKEN", "")
ETSY_SHOP_ID = os.environ.get("ETSY_SHOP_ID", "")
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

LISTING_ID_RE = re.compile(r"/listing/(\d+)")
ETSY_BASE = "https://openapi.etsy.com/v3/application"


def _headers() -> dict:
    h = {"x-api-key": ETSY_KEY}
    if ETSY_TOKEN:
        h["Authorization"] = f"Bearer {ETSY_TOKEN}"
    return h


def _normalize(title: str) -> str:
    """Loose match — Etsy may add suffixes/whitespace differences."""
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def fetch_active_shop_listings(shop_id: str) -> list[dict]:
    """Page through every active listing for the shop. Returns Etsy v3 dicts."""
    out: list[dict] = []
    offset = 0
    limit = 100
    while True:
        r = requests.get(
            f"{ETSY_BASE}/shops/{shop_id}/listings/active",
            headers=_headers(),
            params={"limit": limit, "offset": offset},
            timeout=60,
        )
        r.raise_for_status()
        body = r.json() or {}
        results = body.get("results") or []
        if not results:
            break
        out.extend(results)
        if len(results) < limit:
            break
        offset += limit
        if offset >= 5000:
            break
    return out


def fetch_listing_stats(listing_id: str) -> dict:
    r = requests.get(
        f"{ETSY_BASE}/listings/{listing_id}",
        headers=_headers(),
        params={"includes": "Shop"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def extract_listing_id(url: str) -> str | None:
    if not url:
        return None
    m = LISTING_ID_RE.search(url)
    return m.group(1) if m else None


def _build_listing_url(listing: dict) -> str:
    url = listing.get("url")
    if url:
        return url
    lid = listing.get("listing_id")
    return f"https://www.etsy.com/listing/{lid}" if lid else ""


def auto_detect_etsy_urls(conn, shop_id: str) -> int:
    """Match Drafted rows missing an Etsy URL to live shop listings by title."""
    pending = pod_db.lineage_pending_for_stage(conn, "etsy_publish")
    if not pending:
        return 0
    print(f"\n--- Auto-detect: {len(pending)} draft(s) awaiting an Etsy URL ---")
    try:
        active = fetch_active_shop_listings(shop_id)
    except requests.HTTPError as e:
        print(f"Etsy shop listings query failed: {e.response.status_code} {e.response.text}")
        return 0
    print(f"  Etsy reports {len(active)} active listings in shop {shop_id}")

    by_title: dict[str, dict] = {}
    for L in active:
        key = _normalize(L.get("title", ""))
        if key and key not in by_title:
            by_title[key] = L

    matched = 0
    for row in pending:
        target = _normalize(row["etsy_title"] or "")
        if not target:
            continue
        L = by_title.get(target)
        if not L:
            continue
        url = _build_listing_url(L)
        if not url:
            continue
        pod_db.lineage_upsert(conn, row["lineage_id"], etsy_listing_url=url)
        pod_db.lineage_set_draft_status(conn, row["lineage_id"], "published")
        matched += 1
        print(f"  Matched {row['lineage_id'][:8]} → {url}")
    print(f"  {matched}/{len(pending)} draft(s) auto-published")
    return matched


def sync_stats(conn) -> int:
    """Snapshot views/favorites for every published lineage row."""
    rows = pod_db.lineage_pending_for_stage(conn, "stats_sync")
    if not rows:
        print("\nNo published listings to sync.")
        return 0
    print(f"\n--- Stats sync: {len(rows)} listing(s) ---")
    synced = 0
    for row in rows:
        lid = row["lineage_id"]
        listing_url = row["etsy_listing_url"]
        listing_id = extract_listing_id(listing_url or "")
        if not listing_id:
            print(f"Skip {lid[:8]}: could not parse listing id from {listing_url!r}")
            continue
        try:
            stats = fetch_listing_stats(listing_id)
        except requests.HTTPError as e:
            print(f"Etsy API error for {listing_id}: {e.response.status_code} {e.response.text}")
            continue
        new_views = int(stats.get("views") or 0)
        new_favs = int(stats.get("num_favorers") or 0)
        snapshot_id = pod_db.record_stats(conn, lid, new_views, new_favs)
        delta_row = conn.execute(
            "SELECT views_delta, favorites_delta FROM listing_stats WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        vd = delta_row["views_delta"] if delta_row["views_delta"] is not None else "n/a"
        fd = delta_row["favorites_delta"] if delta_row["favorites_delta"] is not None else "n/a"
        print(f"Listing {listing_id}: views={new_views} (delta {vd}), favs={new_favs} (delta {fd})")
        synced += 1
    return synced


def main() -> None:
    if not ETSY_KEY:
        raise SystemExit("Set ETSY_API_KEY")
    if not ETSY_TOKEN:
        print("Warning: ETSY_ACCESS_TOKEN empty — Etsy v3 usually requires OAuth; requests may fail.")

    conn = pod_db.connect(DB_PATH)
    pod_db.run_migrations(conn)

    if ETSY_SHOP_ID:
        auto_detect_etsy_urls(conn, ETSY_SHOP_ID)
    else:
        print("ETSY_SHOP_ID not set — skipping Etsy URL auto-detect. Set it to "
              "have new listings discovered automatically by title.")

    sync_stats(conn)

    # Mirror live listing + concept state into Supabase so Claude Tasks
    # can read it. No-op when SUPABASE_URL is unset.
    try:
        from supabase_sync import sync_approved_concepts, sync_live_listings
        n_live = sync_live_listings(conn)
        n_concepts = sync_approved_concepts(conn)
        print(f"Supabase sync: {n_live} live listings, {n_concepts} approved concepts")
    except Exception as e:
        print(f"Supabase sync skipped: {e}")

    conn.close()


if __name__ == "__main__":
    main()
