import fal_client
import requests
import os
import json
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

FAL_KEY = os.environ["FAL_KEY"]
IMGBB_API_KEY = os.environ["IMGBB_API_KEY"]

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


with open("prompts.json") as f:
    prompts = json.load(f)

results = []
for i, prompt in enumerate(prompts):
    print(f"\n[{i+1}/{len(prompts)}] Generating: {prompt}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"image_{i + 1:03d}_{timestamp}.png"
    fal_url = generate_image(prompt)
    local_path = save_locally(fal_url, filename)
    imgbb_url = upload_to_imgbb(local_path)
    results.append({"prompt": prompt, "local_path": local_path, "imgbb_url": imgbb_url})

with open("images/results.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nDone. {len(results)} images generated.")