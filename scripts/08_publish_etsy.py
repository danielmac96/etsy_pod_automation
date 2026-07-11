"""Publish approved Printify drafts to Etsy through the Printify API.

This replaces the manual "open Printify, click Publish" step. The cost
gate stays: only rows the user approved in the local app's Publish tab
(publish_status='approved') are published — unless AUTO_PUBLISH=1, in
which case every freshly drafted row is swept through the gate first.

After a successful publish, Printify creates the Etsy listing
asynchronously; 07_track_stats.py auto-detects the listing URL by title
on its next run and flips draft_status to 'published'.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
from src import db as pod_db
from src import printify
from src.config import auto_publish

load_dotenv()

PRINTIFY_KEY = os.environ["PRINTIFY_API_KEY"]
SHOP_ID = os.environ["PRINTIFY_SHOP_ID"]
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")


def main() -> None:
    conn = pod_db.connect(DB_PATH)
    pod_db.run_migrations(conn)

    if auto_publish():
        swept = pod_db.lineage_pending_for_stage(conn, "publish_review")
        for row in swept:
            pod_db.lineage_set_publish_status(conn, row["lineage_id"], "approved")
        if swept:
            print(f"AUTO_PUBLISH=1 — auto-approved {len(swept)} drafted design(s) for publishing.")

    candidates = pod_db.lineage_pending_for_stage(conn, "publish")
    published = 0
    items: list[dict] = []
    failures: list[str] = []

    for row in candidates:
        lineage_id = row["lineage_id"]
        title = row["etsy_title"] or "(no title)"
        product_id = printify.extract_product_id(row["printify_draft_url"])
        if not product_id:
            print(f"Skip {lineage_id[:8]}: cannot parse product id from "
                  f"{row['printify_draft_url']!r}")
            failures.append(title)
            continue
        try:
            printify.publish_product(PRINTIFY_KEY, SHOP_ID, product_id)
        except requests.HTTPError as e:
            body = e.response.text[:300] if e.response is not None else ""
            print(f"Publish failed for {lineage_id[:8]} ({title}): {e} {body}")
            failures.append(title)
            continue
        pod_db.lineage_set_publish_status(conn, lineage_id, "published")
        published += 1
        items.append({
            "lineage_id": lineage_id,
            "etsy_title": title,
            "printify_draft_url": row["printify_draft_url"],
        })
        print(f"Published to Etsy channel: {title} (product {product_id})")

    conn.close()

    detail = (
        f"{published} design(s) sent to Etsy via Printify. "
        f"Etsy listing URLs will be auto-detected by the next stats sync."
    )
    if failures:
        detail += f" {len(failures)} failed: {', '.join(failures[:5])}."

    with open("notify_context.json", "w") as f:
        json.dump({
            "count": published,
            "stage": "published",
            "detail": detail,
            "items": items,
        }, f)

    # Always exit 0: a partial failure must not block the pod.db commit in CI,
    # or already-published rows would be republished on the next run. Failures
    # are surfaced in the notification email instead.
    print(f"\nDone. {published}/{len(candidates)} published, {len(failures)} failed.")


if __name__ == "__main__":
    main()
