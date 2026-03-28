import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from upload_public_image import upload_public_image



with open("prompts.json") as f:
    prompts = json.load(f)

Path("images").mkdir(exist_ok=True)
results = []

HF_KEY = os.environ["HF_API_KEY"]
API_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
headers = {"Authorization": f"Bearer {HF_KEY}"}

date_suffix = datetime.now().strftime("%Y%m%d")
take = min(10, len(prompts))

for i in range(1):#range(take):
    raw_prompt = prompts[i]
    full_prompt = f"{raw_prompt}, shirt graphic design, bold illustration, no text, white background"
    print(f"\nGenerating image {i + 1}/{take}...")

    filename = f"images/design_{i + 1:02d}_{date_suffix}.png"
    image_url = ""
    generated_at = datetime.now().isoformat()

    for attempt in range(3):
        print(f"  Attempt {attempt + 1}...")
        response = requests.post(
            API_URL,
            headers=headers,
            json={"inputs": full_prompt},
            timeout=120,
        )

        if response.status_code == 200:
            with open(filename, "wb") as out:
                out.write(response.content)
            print(f"  Saved {filename}")
            try:
                image_url = upload_public_image(filename)
                print(f"  Public URL: {image_url}")
            except Exception as e:
                print(f"  ImgBB upload failed: {e}")
            break
        if response.status_code == 503:
            wait = response.json().get("estimated_time", 20)
            print(f"  Model loading, waiting {wait}s...")
            time.sleep(wait)
        else:
            print(f"  Error {response.status_code}: {response.text}")
            time.sleep(10)

    if image_url:
        results.append(
            {
                "prompt": raw_prompt,
                "filename": filename,
                "image_url": image_url,
                "generated_at": generated_at,
            }
        )
    else:
        print(f"  Skipping design {i + 1} (no image or no public URL).")

    time.sleep(2)

with open("results.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nDone. Generated {len(results)}/{take} images with public URLs.")
