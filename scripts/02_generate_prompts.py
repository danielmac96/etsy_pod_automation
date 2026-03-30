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


def get_top_performers():
    try:
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=notion_headers,
            json={
                "sorts": [{"property": nf.FAVORITES, "direction": "descending"}],
                "page_size": 8,
            },
        )
        results = resp.json().get("results", [])
        lines = []
        for p in results[:5]:
            props = p["properties"]
            prompt = nf.rich_text_plain(props.get(nf.PROMPT, {}))
            if not prompt.strip():
                continue
            fav = int(nf.number_value(props.get(nf.FAVORITES, {})) or 0)
            views = int(nf.number_value(props.get(nf.VIEWS, {})) or 0)
            dv = nf.number_value(props.get(nf.VIEWS_SINCE_SYNC, {}))
            df = nf.number_value(props.get(nf.FAVORITES_SINCE_SYNC, {}))
            bit = f"- {prompt[:200]}{'...' if len(prompt) > 200 else ''} | favs={fav}, views={views}"
            if dv is not None:
                bit += f", views_since_last_sync={int(dv)}"
            if df is not None:
                bit += f", favs_since_last_sync={int(df)}"
            lines.append(bit)
        return lines
    except Exception as e:
        print(f"No top performers yet (first run): {e}")
        return []


top_lines = get_top_performers()

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-2.5-flash")

with open("keywords.json") as f:
    keywords = json.load(f)

system = """You are a graphic designer for 'Burnout and Barbells' — a POD brand for people who 
lift before/after their corporate job. Think relatable, witty, and wearable.

### TASK:
Generate 3 image-generation prompts as a JSON array of strings.
Use one of each format:
1. TYPOGRAPHY ONLY: 1-2 words or a short phrase. Bold, vintage lettering. No graphic.
2. ICON ONLY: One single object or silhouette. No text. No scene. Just the object.
3. COMBINED: One small icon + one short phrase. Nothing else.

### SIMPLICITY RULES (this is the most important section):
- Maximum 2 visual elements total in any design
- No scenes, no backgrounds, no multiple objects, no people
- No details like muscle definition, texture, shading, or linework complexity
- Think: what would fit on a 1-inch rubber stamp — that level of simple
- If the prompt describes more than one thing happening, it is too complex — simplify it

### POD TECHNICAL RULES:
- Pure white background, completely isolated artwork
- 2 colors max
- Flat vector only — no gradients, no shadows, no 3D, no shading
- Screen print ready

### TONE:
Dry, self-aware, relatable. The joke should be obvious in one glance.
Examples: "rest day cancelled," a lone dumbbell wearing a tie, "PRs over PPTs."

Return a JSON array of exactly 3 strings. No explanation, no markdown."""

performer_context = ""
if top_lines:
    performer_context = (
        "\n\nListings that resonated on Etsy (use as style/subject hints; "
        "favor directions similar to higher views/favorites):\n"
        + "\n".join(top_lines)
    )

resp = model.generate_content(
    f"{system}{performer_context}\n\nKeywords this week: {', '.join(keywords[:10])}"
)

raw = resp.text
match = re.search(r"\[.*\]", raw, re.DOTALL)
prompts = json.loads(match.group())

with open("prompts.json", "w") as f:
    json.dump(prompts, f, indent=2)

print("Generated prompts:", prompts)
