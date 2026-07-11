"""Etsy OAuth2 token management.

Etsy access tokens expire after one hour, so a static ETSY_ACCESS_TOKEN
secret silently breaks the Sunday stats sync within an hour of being
minted. With ETSY_REFRESH_TOKEN set (valid 90 days), every run exchanges
it for a fresh access token instead — no weekly manual re-auth.

Refreshing rotates the refresh token. The newest one is cached in a
gitignored file so back-to-back local runs keep working even if Etsy
ever invalidates the previous token; in GitHub Actions the cache is
ephemeral and the ETSY_REFRESH_TOKEN secret is used as-is (Etsy keeps
issued refresh tokens valid until their 90-day expiry, so this is safe —
just re-mint the secret quarterly, see AUTOMATION.md).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
_CACHE_PATH = Path(os.environ.get(
    "ETSY_TOKEN_CACHE",
    Path(__file__).resolve().parent.parent / ".etsy_token_cache.json",
))


def _load_cached_refresh_token() -> str | None:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return data.get("refresh_token") or None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_cache(payload: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps({
            "refresh_token": payload.get("refresh_token"),
        }), encoding="utf-8")
    except OSError:
        pass  # read-only filesystem (CI) — env token still works next run


def refresh_access_token(api_key: str, refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token. Returns the raw
    token payload ({access_token, refresh_token, expires_in, ...})."""
    r = requests.post(
        TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "client_id": api_key,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_access_token() -> str:
    """Best available Etsy bearer token for this run.

    Priority: fresh token via ETSY_REFRESH_TOKEN (cached rotation wins over
    the env value) → static ETSY_ACCESS_TOKEN → empty string.
    """
    api_key = os.environ.get("ETSY_API_KEY", "")
    refresh = _load_cached_refresh_token() or os.environ.get("ETSY_REFRESH_TOKEN", "")
    if api_key and refresh:
        try:
            payload = refresh_access_token(api_key, refresh)
            _save_cache(payload)
            print("Etsy OAuth: refreshed access token via refresh token.")
            return payload["access_token"]
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            print(f"Etsy OAuth refresh failed ({code}) — falling back to ETSY_ACCESS_TOKEN.")
    return os.environ.get("ETSY_ACCESS_TOKEN", "")
