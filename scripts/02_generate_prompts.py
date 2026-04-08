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
prompt_count = 3

with open("keywords.json") as f:
    keywords = json.load(f)


def generate_prompts(prompt_count:int, keywords:str) -> str:
    system = f"""You are a witty copywriter for a viral Etsy shop selling gym + corporate culture graphic tees.

    Here are example keywords for inspiration:
    {keywords}
    
    Generate {prompt_count} image generation prompts for screen print t-shirt graphics. Each prompt must:
    - Have a funny, specific concept (not generic — think "I paused my macro tracker for this meeting" not "gym and work")
    - Be immediately readable as a joke or relatable moment for someone who lifts AND has a desk job
    - Be Etsy-sellable: the kind of shirt someone buys as a gift or sees and thinks "that's literally me"
    - Reference specific gym culture details (PRs, pre-workout, leg day, chalk, foam rolling, macros etc.)
    - Reference specific office culture details (standups, Slack, Jira, all-hands, OKRs, Notion etc.)
    - Include art direction: flat vector, bold typography, two colors, screen print ready, pure white background, isolated artwork
    - The text/slogan on the shirt should be included in quotes inside the prompt
    
    Good examples:
    - "Bold retro varsity graphic with the text 'SKIPPED LEG DAY (it was a full-day offsite)', flat vector, two colors, screen print ready, pure white background"
    - "Flat illustration of a protein shaker labeled 'PRE-MEETING' next to one labeled 'PRE-WORKOUT', bold sans-serif text below reads 'SAME ENERGY', two colors, screen print ready, pure white background"
    - "Vintage gym poster style illustration of a man deadlifting a giant laptop, text reads 'MY BACK HURTS FROM BOTH', flat vector, two colors, screen print ready"
    
    Return ONLY a JSON array of strings, no explanation."""

    performer_context = ""
    if top_lines:
        performer_context = (
            "\n\nListings that resonated on Etsy (use as style/subject hints; "
            "favor directions similar to higher views/favorites):\n"
            + "\n".join(top_lines)
        )

    resp = model.generate_content(
        f"{system}{performer_context}\n\nKeywords this week for inspiration of designs: {', '.join(keywords)}"
    )

    raw = resp.text
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    prompts = json.loads(match.group())
    return prompts


prompts = generate_prompts(prompt_count, keywords)

with open("prompts.json", "w") as f:
    json.dump(prompts, f, indent=2)

print("Generated prompts:", prompts)
