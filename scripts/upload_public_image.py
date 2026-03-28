"""Upload a local image to ImgBB and return a direct HTTPS URL (Printify-friendly)."""

import base64
import os

import requests
from dotenv import load_dotenv
load_dotenv()  # ← add this

def upload_imgbb(image_path: str, api_key: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": b64},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"ImgBB error: {data}")
    url = data["data"]["url"]
    if not url:
        raise RuntimeError("ImgBB returned no URL")
    return url


def upload_public_image(image_path: str) -> str:
    key = os.environ.get("IMGBB_API_KEY")
    if not key:
        raise RuntimeError("Set IMGBB_API_KEY (free at https://api.imgbb.com/)")
    return upload_imgbb(image_path, key)
