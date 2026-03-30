import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import notion_fields as nf

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ["NOTION_DATABASE_ID"]
headers = nf.notion_headers(NOTION_TOKEN)

with open("results.json") as f:
    results = json.load(f)

notion_page_ids = []


def short_name(prompt: str, index: int) -> str:
    line = prompt.strip().split("\n")[0]
    if len(line) > 100:
        line = line[:97] + "..."
    return line or f"Design {index + 1}"


for i, r in enumerate(results):
    prompt_text = r["prompt"]
    name = short_name(prompt_text, i)
    props = {
        nf.NAME: {"title": [{"text": {"content": ""}}]},
        nf.PROMPT: {"rich_text": [{"text": {"content": prompt_text}}]},
        nf.PIPELINE_STATUS: {"select": {"name": nf.STATUS_UNREVIEWED}},
        nf.ETSY_TITLE: {"rich_text": [{"text": {"content": ""}}]},
        nf.TAGS: {"rich_text": [{"text": {"content": ""}}]},
        nf.IMAGE_URL: {"url": r["image_url"]},
        nf.GENERATED_AT: {"date": {"start": r["generated_at"][:10]}},
    }

    page = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json={"parent": {"database_id": DB_ID}, "properties": props},
    ).json()

    print("Notion response:", json.dumps(page, indent=2))

    if "id" in page:
        notion_page_ids.append(page["id"])
        print(f"Saved to Notion: {page['id']}")
    else:
        print(f"Failed to save design {i + 1}")

with open("notion_ids.json", "w") as f:
    json.dump(notion_page_ids, f)

print(f"\nDone. Saved {len(notion_page_ids)}/{len(results)} rows to Notion.")
