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

load_dotenv()

PRINTIFY_KEY = os.environ["PRINTIFY_API_KEY"]
SHOP_ID = os.environ["PRINTIFY_SHOP_ID"]
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

pfy_headers = {"Authorization": f"Bearer {PRINTIFY_KEY}", "Content-Type": "application/json"}

conn = pod_db.connect(DB_PATH)
pod_db.run_migrations(conn)

candidates = pod_db.lineage_pending_for_stage(conn, "draft_create")

drafts_created = 0
draft_items: list[dict] = []

for row in candidates:
    lineage_id = row["lineage_id"]
    etsy_title = (row["etsy_title"] or "").strip() or "shirt-design"
    img_url = row["image_url"] or ""
    if not img_url:
        print(f"Skip {lineage_id[:8]}: missing image_url")
        continue

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in etsy_title)[:80]

    img_upload = requests.post(
        "https://api.printify.com/v1/uploads/images.json",
        headers=pfy_headers,
        json={"file_name": f"{safe_name}.png", "url": img_url},
        timeout=120,
    )
    if not img_upload.ok:
        print(f"Printify image upload failed: {img_upload.status_code} {img_upload.text}")
        continue
    upload_body = img_upload.json()
    printify_image_id = upload_body.get("id")
    if not printify_image_id:
        print(f"Printify image upload missing id: {upload_body}")
        continue

    variants_resp = requests.get(
        "https://api.printify.com/v1/catalog/blueprints/6/print_providers/99/variants.json",
        headers=pfy_headers,
        timeout=30,
    )
    variants_resp.raise_for_status()
    all_variants = variants_resp.json().get("variants", [])

    VARIANT_WHITELIST = {
        "Black / S", "Black / M", "Black / L", "Black / XL",
        "White / S", "White / M", "White / L", "White / XL",
    }

    enabled_variants = [
        {"id": v["id"], "price": 2499, "is_enabled": True}
        for v in all_variants
        if v["title"] in VARIANT_WHITELIST
    ]
    all_variant_ids = [v["id"] for v in enabled_variants]

    if not enabled_variants:
        print("No variants matched! Available titles:", [v["title"] for v in all_variants[:10]])
        continue

    product_resp = requests.post(
        f"https://api.printify.com/v1/shops/{SHOP_ID}/products.json",
        headers=pfy_headers,
        json={
            "title": etsy_title,
            "blueprint_id": 6,
            "print_provider_id": 99,
            "variants": enabled_variants,
            "print_areas": [
                {
                    "variant_ids": all_variant_ids,
                    "placeholders": [
                        {
                            "position": "front",
                            "images": [
                                {
                                    "id": printify_image_id,
                                    "x": 0.5,
                                    "y": 0.5,
                                    "scale": 0.8,
                                    "angle": 0,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        timeout=120,
    )
    if not product_resp.ok:
        print(f"Printify product create failed: {product_resp.status_code} {product_resp.text}")
        continue
    product = product_resp.json()
    pid = product.get("id")
    if not pid:
        print(f"Printify product missing id: {product}")
        continue

    draft_url = f"https://printify.com/app/shop/{SHOP_ID}/products/{pid}/edit"

    pod_db.lineage_upsert(conn, lineage_id, printify_draft_url=draft_url)
    pod_db.lineage_set_draft_status(conn, lineage_id, "drafted")
    drafts_created += 1
    draft_items.append({
        "lineage_id": lineage_id,
        "etsy_title": etsy_title,
        "printify_draft_url": draft_url,
    })
    print(f"Draft created: {draft_url}")

conn.close()

with open("notify_context.json", "w") as f:
    json.dump({
        "count": drafts_created,
        "stage": "drafts",
        "detail": (
            f"{drafts_created} Printify draft(s) created from {len(candidates)} approved design(s). "
            f"Approve them in the app's Publish tab and they'll be listed on Etsy "
            f"automatically. Sunday's stats sync will auto-detect Etsy URLs by title."
        ),
        "items": draft_items,
    }, f)

print(f"\nDone. {drafts_created}/{len(candidates)} Printify drafts created.")
