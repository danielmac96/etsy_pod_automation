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

system = """You are a shirt designer for a brand called Burnout and Barbells. 
The target customer is an athlete who works a corporate 9-5 job — they lift weights, 
run, or train seriously but spend their days in meetings and spreadsheets. 
The tone is self-aware, darkly funny, and relatable. Think meme-worthy but wearable.

Given trending keywords, generate 10 unique image prompts for print-on-demand shirt designs.
Each prompt should:
- Reflect the tension between corporate life and athletic identity
- Be optimized for a bold graphic tee (transparent PNG, works on dark or light shirts)
- Describe the art style clearly (e.g. retro 80s athletic, brutalist bold type, vintage gym poster)
- Avoid rendering text/words in the image itself
- Be 1-2 sentences max

Return a JSON array of exactly 10 strings."""

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
