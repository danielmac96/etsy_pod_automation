import fal_client
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
from src import db
from src.config import (
    auto_approve_images,
    image_approve_min_score,
    image_reject_max_score,
)

load_dotenv()

FAL_KEY = os.environ["FAL_KEY"]
IMGBB_API_KEY = os.environ["IMGBB_API_KEY"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

os.makedirs("images", exist_ok=True)

# Gemini vision pre-screen — optional (skipped when GEMINI_API_KEY unset).
# Scores print-readiness so the human only reviews borderline images, and
# obviously-garbled renders are auto-rejected without waiting for Wednesday.
_gemini_client = None
if GEMINI_API_KEY:
    from google import genai
    from gemini_client import generate_json
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)

_SCREEN_PROMPT = """You are quality-controlling AI-generated t-shirt graphics for an Etsy print-on-demand shop.
The intended design prompt was:

{prompt}

Score this image for print-readiness. Heavily penalize: garbled/misspelled text, text that differs from the quoted slogan in the prompt, gradients or photographic textures (must be flat screen-print style), artwork touching the canvas edges, muddy low-contrast art, or a non-white background.

Return ONLY a JSON object: {{"score": <0-10 number>, "issues": ["short issue", ...]}} — an empty issues list if the design is clean."""


def screen_image(local_path: str, prompt_text: str) -> tuple[float | None, str | None]:
    """Return (score, feedback_text) from Gemini vision, or (None, None) if unavailable."""
    if _gemini_client is None:
        return None, None
    try:
        with open(local_path, "rb") as f:
            image_bytes = f.read()
        result = generate_json(
            _gemini_client,
            _SCREEN_PROMPT.format(prompt=prompt_text),
            image_bytes=image_bytes,
            temperature=0.2,
        )
        score = float(result.get("score"))
        issues = result.get("issues") or []
        return score, "; ".join(str(i) for i in issues) if issues else "clean"
    except Exception as e:
        print(f"  AI screen failed (leaving unreviewed): {e}")
        return None, None


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


conn = db.connect(DB_PATH)
db.run_migrations(conn)

pending = db.lineage_pending_for_stage(conn, "image_gen")
if not pending:
    print("No prompts approved for image generation. Approve prompts in the local app first.")
    with open("notify_context.json", "w") as f:
        json.dump({"count": 0, "stage": "images",
                   "detail": "No approved prompts found — nothing to generate."}, f)
    conn.close()
    raise SystemExit(0)

results = []
for i, row in enumerate(pending):
    lineage_id = row["lineage_id"]
    prompt_text = row["prompt_text"] or ""
    print(f"\n[{i+1}/{len(pending)}] Generating: {prompt_text[:80]}...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"image_{i+1:03d}_{timestamp}.png"
    fal_url = generate_image(prompt_text)
    local_path = save_locally(fal_url, filename)
    imgbb_url = upload_to_imgbb(local_path)

    ai_score, ai_feedback = screen_image(local_path, prompt_text)
    status = "unreviewed"
    if ai_score is not None:
        if ai_score <= image_reject_max_score():
            status = "rejected"
        elif auto_approve_images() and ai_score >= image_approve_min_score():
            status = "approved"
        print(f"  AI screen: {ai_score}/10 ({ai_feedback}) → {status}")

    db.lineage_upsert(conn, lineage_id, image_url=imgbb_url,
                      ai_score=ai_score, ai_feedback=ai_feedback)
    db.lineage_set_image_status(conn, lineage_id, status)
    results.append({
        "lineage_id": lineage_id,
        "category": row["category"],
        "prompt": prompt_text,
        "local_path": local_path,
        "imgbb_url": imgbb_url,
        "ai_score": ai_score,
        "ai_feedback": ai_feedback,
        "image_status": status,
    })
    print(f"Updated lineage {lineage_id[:8]} → image_status={status}")

conn.close()

with open("images/results.json", "w") as f:
    json.dump(results, f, indent=2)

n_rejected = sum(1 for r in results if r["image_status"] == "rejected")
n_approved = sum(1 for r in results if r["image_status"] == "approved")
n_pending = len(results) - n_rejected - n_approved
detail = f"{len(results)} images generated; {n_pending} awaiting your approval."
if n_approved or n_rejected:
    detail += (f" AI pre-screen auto-approved {n_approved} and "
               f"auto-rejected {n_rejected}.")

with open("notify_context.json", "w") as f:
    json.dump({
        "count": n_pending,
        "stage": "images",
        "detail": detail,
        "items": [
            {"lineage_id": r["lineage_id"], "category": r["category"],
             "prompt": r["prompt"], "image_url": r["imgbb_url"],
             "ai_score": r["ai_score"], "ai_feedback": r["ai_feedback"],
             "image_status": r["image_status"]}
            for r in results
        ],
    }, f)

print(f"\nDone. {len(results)} images generated.")
