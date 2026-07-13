"""Create Printify product drafts — complete Etsy-ready listings.

Each draft carries the full AI-generated Etsy copy (title, description,
13 SEO tags) plus the configured variant grid, so approving in the Publish
tab is the only step left before the listing goes live on Etsy.

Product economics are user-defined via env (see .env.example):
  PRINTIFY_BLUEPRINT_ID / PRINTIFY_PRINT_PROVIDER_ID — catalog choice
  POD_PRICE_CENTS                                    — retail price per variant
  POD_VARIANT_COLORS / POD_VARIANT_SIZES             — the enabled color × size grid
  POD_PRINT_SCALE                                    — front print-area scale (0–1)
"""
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

load_dotenv()

PRINTIFY_KEY = os.environ["PRINTIFY_API_KEY"]
SHOP_ID = os.environ["PRINTIFY_SHOP_ID"]
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

BLUEPRINT_ID = int(os.environ.get("PRINTIFY_BLUEPRINT_ID", "6"))            # Gildan 5000
PRINT_PROVIDER_ID = int(os.environ.get("PRINTIFY_PRINT_PROVIDER_ID", "99")) # Printify Choice
PRICE_CENTS = int(os.environ.get("POD_PRICE_CENTS", "2499"))
VARIANT_COLORS = [c for c in os.environ.get("POD_VARIANT_COLORS", "Black,White").split(",") if c.strip()]
VARIANT_SIZES = [s for s in os.environ.get("POD_VARIANT_SIZES", "S,M,L,XL").split(",") if s.strip()]
PRINT_SCALE = float(os.environ.get("POD_PRINT_SCALE", "0.8"))

pfy_headers = printify.headers(PRINTIFY_KEY)

conn = pod_db.connect(DB_PATH)
pod_db.run_migrations(conn)

candidates = pod_db.lineage_pending_for_stage(conn, "draft_create")

drafts_created = 0
draft_items: list[dict] = []
enabled_variants: list[dict] = []

if candidates:
    # One catalog lookup covers every product this run (same blueprint/provider).
    variants_resp = requests.get(
        f"{printify.API_BASE}/catalog/blueprints/{BLUEPRINT_ID}"
        f"/print_providers/{PRINT_PROVIDER_ID}/variants.json",
        headers=pfy_headers,
        timeout=30,
    )
    variants_resp.raise_for_status()
    all_variants = variants_resp.json().get("variants", [])
    enabled_variants = printify.select_variants(
        all_variants,
        colors=VARIANT_COLORS,
        sizes=VARIANT_SIZES,
        price_cents=PRICE_CENTS,
    )
    if not enabled_variants:
        print(f"No variants matched colors={VARIANT_COLORS} sizes={VARIANT_SIZES}! "
              f"Available titles: {[v.get('title') for v in all_variants[:10]]}")

for row in candidates:
    if not enabled_variants:
        break
    lineage_id = row["lineage_id"]
    etsy_title = (row["etsy_title"] or "").strip() or "shirt-design"
    etsy_description = (row["etsy_description"] or "").strip()
    try:
        etsy_tags = json.loads(row["etsy_tags_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        etsy_tags = []
    img_url = row["image_url"] or ""
    if not img_url:
        print(f"Skip {lineage_id[:8]}: missing image_url")
        continue

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in etsy_title)[:80]

    img_upload = requests.post(
        f"{printify.API_BASE}/uploads/images.json",
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

    payload = printify.build_product_payload(
        title=etsy_title,
        description=etsy_description,
        tags=etsy_tags,
        blueprint_id=BLUEPRINT_ID,
        print_provider_id=PRINT_PROVIDER_ID,
        variants=enabled_variants,
        image_id=printify_image_id,
        print_scale=PRINT_SCALE,
    )
    product_resp = requests.post(
        f"{printify.API_BASE}/shops/{SHOP_ID}/products.json",
        headers=pfy_headers,
        json=payload,
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
            f"{drafts_created} Etsy-ready Printify draft(s) created from {len(candidates)} "
            f"approved design(s) — title, description, and tags all set. "
            f"Approve them in the app's Publish tab and they'll be listed on Etsy "
            f"automatically. Sunday's stats sync will auto-detect Etsy URLs by title."
        ),
        "items": draft_items,
    }, f)

print(f"\nDone. {drafts_created}/{len(candidates)} Printify drafts created.")
