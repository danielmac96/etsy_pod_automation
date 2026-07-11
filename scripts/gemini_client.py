"""Shared Gemini wrapper with model fallback and 503/429 retry logic."""
from __future__ import annotations

import json
import time
from typing import Any

from google import genai
from google.genai import types as genai_types

_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
]


def generate_json(
    client: genai.Client,
    prompt: str,
    *,
    model: str = "gemini-2.5-flash",
    schema: dict | None = None,
    temperature: float = 1.0,
    image_bytes: bytes | None = None,
    image_mime_type: str = "image/png",
) -> Any:
    """Call Gemini with JSON output mode, retrying on 503 and falling back on 429.

    Pass image_bytes for multimodal calls (e.g. the 03 image pre-screen).
    """
    models_to_try = [model] + [m for m in _FALLBACK_MODELS if m != model]
    last_err: Exception | None = None

    contents: Any = prompt
    if image_bytes is not None:
        contents = [
            genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type),
            prompt,
        ]

    for m in models_to_try:
        for retry in range(3):
            try:
                config = genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                )
                if schema is not None:
                    config.response_schema = schema
                response = client.models.generate_content(
                    model=m,
                    contents=contents,
                    config=config,
                )
                if m != model:
                    print(f"    (gemini fallback: {m})")
                return json.loads(response.text)
            except Exception as e:
                last_err = e
                err = str(e)
                if "503" in err or "UNAVAILABLE" in err:
                    wait = 15 * (retry + 1)
                    print(f"    Gemini 503 ({m}, attempt {retry+1}), retrying in {wait}s…")
                    time.sleep(wait)
                elif "429" in err or "RESOURCE_EXHAUSTED" in err:
                    print(f"    Gemini 429 quota exhausted on {m}, trying next model…")
                    break  # move to next model
                else:
                    raise
        else:
            continue  # all retries for this model exhausted, try next

    raise RuntimeError(f"All Gemini models failed. Last error: {last_err}")
