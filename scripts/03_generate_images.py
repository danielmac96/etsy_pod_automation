import fal_client
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import notion_fields as nf

load_dotenv()

FAL_KEY = os.environ["FAL_KEY"]
IMGBB_API_KEY = os.environ["IMGBB_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

notion_hdrs = nf.notion_headers(NOTION_TOKEN)

os.makedirs("images", exist_ok=True)


def fetch_approved_prompts() -> list[dict]:
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=notion_hdrs,
        json={
            "filter": {
                "property": nf.PIPELINE_STATUS,
                "select": {"equals": nf.STATUS_PROMPT_APPROVED},
            }
        },
    )
    resp.raise_for_status()
    pages = resp.json().get("results", [])
    out = []
    for p in pages:
        props = p["properties"]
        prompt_text = nf.rich_text_plain(props.get(nf.PROMPT, {}))
        if prompt_text:
            out.append({"page_id": p["id"], "prompt": prompt_text})
    return out


def generate_image(prompt: str) -> str:
    result = fal_client.run(
        "fal-ai/ideogram/v3",
        arguments={
            "prompt": (
                f"Bold screen print graphic for apparel, flat illustration, "
                f"{prompt}, "
                f"isolated on pure white background, high contrast, "
                f"no gradients, no shadows, no photography, no 3D"
            ),
            "aspect_ratio": "1:1",
            "style": "DESIGN",
            "rendering_speed": "QUALITY",
        }
    )
    return result["images"][0]["url"]


def save_locally(image_url: str, filename: str) -> str:
    response = requests.get(image_url)
    response.raise_for_status()
    local_path = os.path.join("images", filename)
    with open(local_path, "wb") as f:
        f.write(response.content)
    print(f"Saved locally: {local_path}")
    return local_path


def upload_to_imgbb(local_path: str) -> str:
    with open(local_path, "rb") as f:
        response = requests.post(
            "https://api.imgbb.com/1/upload",
            params={"key": IMGBB_API_KEY},
            files={"image": f}
        )
    response.raise_for_status()
    url = response.json()["data"]["url"]
    print(f"Uploaded to ImgBB: {url}")
    return url


def update_notion_page(page_id: str, imgbb_url: str, generated_at: str):
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_hdrs,
        json={
            "properties": {
                nf.IMAGE_URL: {"url": imgbb_url},
                nf.GENERATED_AT: {"date": {"start": generated_at[:10]}},
                nf.PIPELINE_STATUS: {"select": {"name": nf.STATUS_IMAGE_UNREVIEWED}},
            }
        },
    )
    resp.raise_for_status()


approved = fetch_approved_prompts()
if not approved:
    print("No Prompt Approved pages found in Notion. Approve prompts first.")
    with open("notify_context.json", "w") as f:
        json.dump({"count": 0, "stage": "images", "detail": "No approved prompts found — nothing to generate."}, f)
    raise SystemExit(0)

results = []
for i, item in enumerate(approved):
    print(f"\n[{i+1}/{len(approved)}] Generating: {item['prompt'][:80]}...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"image_{i+1:03d}_{timestamp}.png"
    fal_url = generate_image(item["prompt"])
    local_path = save_locally(fal_url, filename)
    imgbb_url = upload_to_imgbb(local_path)
    update_notion_page(item["page_id"], imgbb_url, timestamp)
    results.append({
        "page_id": item["page_id"],
        "prompt": item["prompt"],
        "local_path": local_path,
        "imgbb_url": imgbb_url,
    })
    print(f"Updated Notion page {item['page_id']} → {nf.STATUS_IMAGE_UNREVIEWED}")

# Audit log
with open("images/results.json", "w") as f:
    json.dump(results, f, indent=2)

# Context for 05_notify.py
with open("notify_context.json", "w") as f:
    json.dump({
        "count": len(results),
        "stage": "images",
        "detail": f"{len(results)} images generated and ready for your approval in Notion.",
    }, f)

print(f"\nDone. {len(results)} images generated.")
