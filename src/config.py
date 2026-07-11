"""Automation switches for the weekly pipeline.

Every human gate can be opened individually via env vars (GitHub Actions
repository variables → step env). The two gates that spend money default
to OFF so nothing is bought or listed without explicit human approval:

  AUTO_APPROVE_PROMPTS  prompt gate  → approving triggers FAL image gen (paid)
  AUTO_APPROVE_IMAGES   image gate   → auto-approve images whose Gemini
                                       pre-screen score ≥ AUTO_APPROVE_IMAGE_MIN_SCORE;
                                       score < AUTO_REJECT_IMAGE_MAX_SCORE is
                                       auto-rejected even when the flag is off
  AUTO_PUBLISH          publish gate → publishing incurs Etsy's $0.20 listing fee

Set a flag to 1/true/yes/on to enable.
"""
from __future__ import annotations

import os


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def auto_approve_prompts() -> bool:
    return env_flag("AUTO_APPROVE_PROMPTS")


def auto_approve_images() -> bool:
    return env_flag("AUTO_APPROVE_IMAGES")


def auto_publish() -> bool:
    return env_flag("AUTO_PUBLISH")


def image_approve_min_score() -> float:
    """AI score at/above which AUTO_APPROVE_IMAGES approves an image."""
    return env_float("AUTO_APPROVE_IMAGE_MIN_SCORE", 8.0)


def image_reject_max_score() -> float:
    """AI score at/below which an image is auto-rejected (always on when
    scoring runs — a 2/10 garbled render never needs human eyes)."""
    return env_float("AUTO_REJECT_IMAGE_MAX_SCORE", 3.0)
