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
import notion_fields as nf
from src import db as pod_db

load_dotenv()

PRINTIFY_KEY = os.environ["PRINTIFY_API_KEY"]
SHOP_ID = os.environ["PRINTIFY_SHOP_ID"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ["NOTION_DATABASE_ID"]
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

pfy_headers = {"Authorization": f"Bearer {PRINTIFY_KEY}", "Content-Type": "application/json"}
notion_headers = nf.notion_headers(NOTION_TOKEN)

conn = pod_db.connect(DB_PATH)
pod_db.run_migrations(conn)

resp = requests.post(
    f"https://api.notion.com/v1/databases/{DB_ID}/query",
    headers=notion_headers,
    json={
        "filter": {
            "and": [
                {"property": nf.PIPELINE_STATUS, "select": {"equals": nf.STATUS_COPY_GENERATED}},
                {"property": nf.PRINTIFY_DRAFT_URL, "url": {"is_empty": True}},
            ]
        }
    },
)
resp.raise_for_status()
candidates = resp.json().get("results", [])

drafts_created = 0

for page in candidates:
    props = page["properties"]
    etsy_title = nf.rich_text_plain(props.get(nf.ETSY_TITLE, {})).strip()
    if not etsy_title:
        etsy_title = nf.title_plain(props.get(nf.NAME, {})).strip() or "shirt-design"
    img_url = nf.url_value(props.get(nf.IMAGE_URL, {})) or ""
    if not img_url:
        print(f"Skip page {page['id']}: missing Image URL")
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

    # Fetch valid variants for this blueprint + provider
    variants_resp = requests.get(
        "https://api.printify.com/v1/catalog/blueprints/6/print_providers/99/variants.json",
        headers=pfy_headers,
        timeout=30,
    )
    variants_resp.raise_for_status()
    all_variants = variants_resp.json().get("variants", [])

    # Only create a small set of color + size combos
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

    patch = requests.patch(
        f"https://api.notion.com/v1/pages/{page['id']}",
        headers=notion_headers,
        json={
            "properties": {
                nf.PIPELINE_STATUS: {"select": {"name": nf.STATUS_DRAFTED}},
                nf.PRINTIFY_DRAFT_URL: {"url": draft_url},
            }
        },
    )
    patch.raise_for_status()
    brief_id = nf.rich_text_plain(props.get(nf.BRIEF_ID, {})) or None
    lineage_kwargs = {"printify_draft_url": draft_url}
    if brief_id:
        lineage_kwargs["brief_id"] = brief_id
    if img_url:
        lineage_kwargs["image_url"] = img_url
    pod_db.lineage_upsert(conn, page["id"], **lineage_kwargs)
    drafts_created += 1
    print(f"Draft created: {draft_url}")

conn.close()

# Write final notify context for 05_notify.py
with open("notify_context.json", "w") as f:
    json.dump({
        "count": drafts_created,
        "stage": "drafts",
        "detail": (
            f"{drafts_created} Printify draft(s) created from {len(candidates)} approved design(s). "
            f"Review drafts in Printify, then publish to Etsy."
        ),
    }, f)

print(f"\nDone. {drafts_created}/{len(candidates)} Printify drafts created.")
