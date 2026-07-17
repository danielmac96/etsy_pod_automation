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

load_dotenv()

FAL_KEY = os.environ["FAL_KEY"]
IMGBB_API_KEY = os.environ["IMGBB_API_KEY"]
DB_PATH = os.environ.get("POD_DB_PATH", "pod.db")

os.makedirs("images", exist_ok=True)


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
    db.lineage_upsert(conn, lineage_id, image_url=imgbb_url)
    db.lineage_set_image_status(conn, lineage_id, "unreviewed")
    results.append({
        "lineage_id": lineage_id,
        "category": row["category"],
        "prompt": prompt_text,
        "local_path": local_path,
        "imgbb_url": imgbb_url,
    })
    print(f"Updated lineage {lineage_id[:8]} → image_status=unreviewed")

conn.close()

with open("images/results.json", "w") as f:
    json.dump(results, f, indent=2)

with open("notify_context.json", "w") as f:
    json.dump({
        "count": len(results),
        "stage": "images",
        "detail": f"{len(results)} images generated and ready for your approval.",
        "items": [
            {"lineage_id": r["lineage_id"], "category": r["category"],
             "prompt": r["prompt"], "image_url": r["imgbb_url"]}
            for r in results
        ],
    }, f)

print(f"\nDone. {len(results)} images generated.")
