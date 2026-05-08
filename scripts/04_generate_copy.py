"""Generate Etsy product copy (title, description, tags) for every approved image.

Reads from pod.db (lineage rows where image_status='approved' and no etsy_title yet),
calls Gemini, writes etsy_title / etsy_description / etsy_tags_json back to lineage.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
from gemini_client import generate_json
from src import db

load_dotenv()

DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

COPY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "description", "tags"],
}


def generate_copy(prompt: str, category: str) -> dict:
    gemini_prompt = f"""You are an Etsy SEO copywriter for a gym + corporate culture graphic tee shop.

Design prompt: {prompt}
Design category: {category}

Write Etsy product copy for this shirt. Return JSON with EXACTLY these keys:
- title: Etsy listing title, ≤140 chars, SEO-aware, natural language, no ALL CAPS
- description: 3-4 sentence product description — open with the joke, describe the shirt (soft unisex tee, makes a great gift), name the audience, close with a call to action
- tags: array of EXACTLY 13 Etsy search tags, each ≤20 chars, no duplicates, mix broad and niche."""

    data = generate_json(client, gemini_prompt, schema=COPY_SCHEMA)
    raw_tags = data.get("tags", []) or []
    tags = [str(t) for t in raw_tags[:13]]
    return {
        "title": str(data.get("title", ""))[:140],
        "description": str(data.get("description", "")),
        "tags": tags,
    }


def main() -> None:
    conn = db.connect(DB_PATH)
    db.run_migrations(conn)

    pending = db.lineage_pending_for_stage(conn, "copy_gen")
    if not pending:
        print("No images approved for copy generation. Approve images in the local app first.")
        Path("notify_context.json").write_text(
            json.dumps({"count": 0, "stage": "copy", "detail": "No approved images found."}),
            encoding="utf-8",
        )
        conn.close()
        return

    processed = 0
    for row in pending:
        lineage_id = row["lineage_id"]
        category = row["category"] or ""
        prompt = row["prompt_text"] or ""
        print(f"\nGenerating copy for [{category}]: {prompt[:70]}...")
        try:
            copy = generate_copy(prompt, category)
            db.lineage_upsert(
                conn, lineage_id,
                etsy_title=copy["title"],
                etsy_description=copy["description"],
                etsy_tags_json=json.dumps(copy["tags"]),
            )
            print(f"  Title: {copy['title']}")
            print(f"  Tags:  {', '.join(copy['tags'])[:60]}...")
            processed += 1
        except Exception as e:
            print(f"  Failed for lineage {lineage_id[:8]}: {e}")

    conn.close()

    Path("notify_context.json").write_text(
        json.dumps({
            "count": processed,
            "stage": "copy",
            "detail": f"{processed} designs have AI copy generated. Creating Printify drafts next.",
        }),
        encoding="utf-8",
    )
    print(f"\nDone. {processed}/{len(pending)} rows updated with AI-generated copy.")


if __name__ == "__main__":
    main()
