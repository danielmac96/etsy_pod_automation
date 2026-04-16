"""
Generates Etsy product copy (title, description, tags) using Gemini AI for all
Image Approved designs in Notion, then updates each page and marks it Copy Generated.

Run after the user has approved images in Notion.
Followed by 06_printify_upload.py which picks up Copy Generated pages.
"""

import json
import os
import re
import sys
from pathlib import Path

import google.generativeai as genai
import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import notion_fields as nf

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
notion_headers = nf.notion_headers(NOTION_TOKEN)

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-2.5-flash")


def fetch_image_approved_pages() -> list[dict]:
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=notion_headers,
        json={
            "filter": {
                "property": nf.PIPELINE_STATUS,
                "select": {"equals": nf.STATUS_IMAGE_APPROVED},
            }
        },
    )
    resp.raise_for_status()
    pages = resp.json().get("results", [])
    out = []
    for p in pages:
        props = p["properties"]
        prompt = nf.rich_text_plain(props.get(nf.PROMPT, {}))
        if not prompt:
            continue
        cat_prop = props.get(nf.CATEGORY, {}).get("select") or {}
        out.append({
            "page_id": p["id"],
            "prompt": prompt,
            "category": cat_prop.get("name", ""),
        })
    return out


def generate_copy(prompt: str, category: str) -> dict:
    gemini_prompt = f"""You are an Etsy SEO copywriter for a gym + corporate culture graphic tee shop.

Design prompt: {prompt}
Design category: {category}

Write Etsy product copy for this shirt. Return a JSON object with exactly these keys:
- "title": Etsy listing title, max 140 characters, SEO-optimized, natural language, no ALL CAPS, lead with the funniest or most searchable angle
- "description": 3-4 sentence product description — open with the joke/concept, describe the shirt (soft unisex tee, makes a great gift), name the target audience, close with a call to action
- "tags": a list of exactly 13 Etsy search tags, each max 20 characters, no duplicates, mix broad terms (gym shirt, funny tee) with niche ones (powerlifter gift, office humor)

Return ONLY valid JSON, no explanation, no markdown."""

    resp = model.generate_content(gemini_prompt)
    raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data = json.loads(match.group())
    tags = data.get("tags", [])
    return {
        "title": str(data.get("title", ""))[:140],
        "description": str(data.get("description", "")),
        "tags": ", ".join(str(t) for t in tags[:13]),
    }


def update_notion_page(page_id: str, copy: dict):
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers,
        json={
            "properties": {
                nf.ETSY_TITLE: {"rich_text": [{"text": {"content": copy["title"]}}]},
                nf.DESCRIPTION: {"rich_text": [{"text": {"content": copy["description"]}}]},
                nf.TAGS: {"rich_text": [{"text": {"content": copy["tags"]}}]},
                nf.PIPELINE_STATUS: {"select": {"name": nf.STATUS_COPY_GENERATED}},
            }
        },
    )
    resp.raise_for_status()


pages = fetch_image_approved_pages()
if not pages:
    print("No Image Approved pages found. Approve images in Notion first.")
    with open("notify_context.json", "w") as f:
        json.dump({"count": 0, "stage": "copy", "detail": "No approved images found."}, f)
    raise SystemExit(0)

processed = 0
for item in pages:
    print(f"\nGenerating copy for [{item['category']}]: {item['prompt'][:70]}...")
    try:
        copy = generate_copy(item["prompt"], item["category"])
        update_notion_page(item["page_id"], copy)
        print(f"  Title: {copy['title']}")
        print(f"  Tags:  {copy['tags'][:60]}...")
        processed += 1
    except Exception as e:
        print(f"  Failed for page {item['page_id']}: {e}")

# notify_context.json will be overwritten by 06_printify_upload.py with the final draft count
with open("notify_context.json", "w") as f:
    json.dump({
        "count": processed,
        "stage": "copy",
        "detail": f"{processed} designs have AI copy generated. Creating Printify drafts next.",
    }, f)

print(f"\nDone. {processed}/{len(pages)} pages updated with AI-generated copy.")
