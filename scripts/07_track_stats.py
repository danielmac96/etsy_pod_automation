"""
Sync Etsy listing totals into Notion and compute deltas since the previous sync.

Requires OAuth access token with scope that can read the shop's listings, e.g. listings_r.
Set ETSY_API_KEY (string from Etsy app) and ETSY_ACCESS_TOKEN (Bearer token).
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import notion_fields as nf

load_dotenv()

ETSY_KEY = os.environ.get("ETSY_API_KEY", "")
ETSY_TOKEN = os.environ.get("ETSY_ACCESS_TOKEN", "")
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ["NOTION_DATABASE_ID"]

notion_headers = nf.notion_headers(NOTION_TOKEN)

LISTING_ID_RE = re.compile(r"/listing/(\d+)")


def extract_listing_id(url: str) -> str | None:
    if not url:
        return None
    m = LISTING_ID_RE.search(url)
    return m.group(1) if m else None


def fetch_listing_stats(listing_id: str) -> dict:
    url = f"https://openapi.etsy.com/v3/application/listings/{listing_id}"
    headers = {"x-api-key": ETSY_KEY}
    if ETSY_TOKEN:
        headers["Authorization"] = f"Bearer {ETSY_TOKEN}"
    r = requests.get(
        url,
        headers=headers,
        params={"includes": "Shop"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def main():
    if not ETSY_KEY:
        raise SystemExit("Set ETSY_API_KEY")
    if not ETSY_TOKEN:
        print("Warning: ETSY_ACCESS_TOKEN empty — Etsy v3 usually requires OAuth; requests may fail.")

    resp = requests.post(
        f"https://api.notion.com/v1/databases/{DB_ID}/query",
        headers=notion_headers,
        json={"filter": {"property": nf.ETSY_LISTING_URL, "url": {"is_not_empty": True}}},
    )
    resp.raise_for_status()
    pages = resp.json().get("results", [])
    now = datetime.utcnow().isoformat() + "Z"

    for page in pages:
        props = page["properties"]
        listing_url = nf.url_value(props.get(nf.ETSY_LISTING_URL, {}))
        listing_id = extract_listing_id(listing_url or "")
        if not listing_id:
            print(f"Skip {page['id']}: could not parse listing id from URL")
            continue

        try:
            stats = fetch_listing_stats(listing_id)
        except requests.HTTPError as e:
            print(f"Etsy API error for {listing_id}: {e.response.status_code} {e.response.text}")
            continue

        new_views = int(stats.get("views") or 0)
        new_favs = int(stats.get("num_favorers") or 0)

        old_views = nf.number_value(props.get(nf.VIEWS, {}))
        old_favs = nf.number_value(props.get(nf.FAVORITES, {}))

        views_delta = None
        favs_delta = None
        if old_views is not None:
            views_delta = new_views - int(old_views)
        if old_favs is not None:
            favs_delta = new_favs - int(old_favs)

        patch_props = {
            nf.VIEWS: {"number": new_views},
            nf.FAVORITES: {"number": new_favs},
            nf.STATS_UPDATED: {"date": {"start": now[:10]}},
        }
        if views_delta is not None:
            patch_props[nf.VIEWS_SINCE_SYNC] = {"number": views_delta}
        if favs_delta is not None:
            patch_props[nf.FAVORITES_SINCE_SYNC] = {"number": favs_delta}

        pr = requests.patch(
            f"https://api.notion.com/v1/pages/{page['id']}",
            headers=notion_headers,
            json={"properties": patch_props},
        )
        pr.raise_for_status()
        vd = views_delta if views_delta is not None else "n/a"
        fd = favs_delta if favs_delta is not None else "n/a"
        print(f"Listing {listing_id}: views={new_views} (delta {vd}), favs={new_favs} (delta {fd})")


if __name__ == "__main__":
    main()
