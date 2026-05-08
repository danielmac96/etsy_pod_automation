"""Etsy v3 read-only client used by the research pipeline.

Design contract is documented in docs/etsy_endpoints_used.md. The MCP server
should be the source of truth for endpoint shapes; if a field name disagrees
with what's parsed here, update both this file and the docs together.

This module is single-threaded by design — the pipeline orchestrator runs
serially, and a token bucket keeps within Etsy's per-second budget. Daily
quota is persisted to <cache_dir>/quota.json so reruns within the same UTC
day cannot blow the per-day cap.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from schemas import EtsyListing

LOG = logging.getLogger("etsy_client")

BASE_URL = "https://openapi.etsy.com/v3/application"
MAX_OFFSET = 12_000


# ── token bucket ──────────────────────────────────────────────────────────────

class TokenBucket:
    """Refills at `rate` tokens/sec, capacity `rate`. acquire() blocks."""

    def __init__(self, rate: float, time_fn=time.monotonic, sleep_fn=time.sleep):
        self.rate = float(rate)
        self.capacity = float(rate)
        self._tokens = float(rate)
        self._last = time_fn()
        self._lock = threading.Lock()
        self._time = time_fn
        self._sleep = sleep_fn

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._time()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
                self._sleep(wait)


# ── search result ─────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    listings: list[EtsyListing]
    cache_hit: bool
    raw_response_path: str
    pagination: dict = field(default_factory=dict)


class QuotaExceededError(RuntimeError):
    pass


# ── client ────────────────────────────────────────────────────────────────────

class EtsyClient:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path | str,
        *,
        shared_secret: str = "",
        rps: float = 5.0,
        daily_quota: int = 5_000,
        cache_ttl_seconds: int = 86_400,
        max_attempts: int = 4,
        session: requests.Session | None = None,
        time_fn=time.monotonic,
        sleep_fn=time.sleep,
    ):
        self.api_key = api_key
        self.shared_secret = shared_secret
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl_seconds
        self.daily_quota = daily_quota
        self.max_attempts = max_attempts
        self._bucket = TokenBucket(rps, time_fn=time_fn, sleep_fn=sleep_fn)
        self._sleep = sleep_fn
        self._session = session or requests.Session()
        self._quota_path = self.cache_dir / "quota.json"

    # ── public API ──────────────────────────────────────────────────────────

    def search_listings(
        self,
        query: str,
        *,
        limit: int = 50,
        sort_on: str = "score",
        sort_order: str = "desc",
        offset: int = 0,
        taxonomy_id: int | None = None,
    ) -> SearchResult:
        params: dict[str, Any] = {
            "keywords": query,
            "limit": limit,
            "offset": offset,
            "sort_on": sort_on,
            "sort_order": sort_order,
        }
        if taxonomy_id is not None:
            params["taxonomy_id"] = int(taxonomy_id)
        data, cache_hit, raw_path = self._get("/listings/active", params)
        listings = [EtsyListing.from_v3(L) for L in (data.get("results") or [])]
        return SearchResult(
            listings=listings,
            cache_hit=cache_hit,
            raw_response_path=str(raw_path),
            pagination=data.get("pagination") or {},
        )

    def paginate_listings(
        self,
        query: str,
        *,
        limit: int = 100,
        sort_on: str = "score",
        sort_order: str = "desc",
        taxonomy_id: int | None = None,
        max_pages: int | None = None,
    ) -> Iterator[EtsyListing]:
        offset = 0
        page = 0
        while True:
            if offset >= MAX_OFFSET:
                LOG.warning("Etsy paginate hit MAX_OFFSET=%d for query=%r; stopping.",
                            MAX_OFFSET, query)
                return
            res = self.search_listings(
                query, limit=limit, sort_on=sort_on, sort_order=sort_order,
                offset=offset, taxonomy_id=taxonomy_id,
            )
            if not res.listings:
                return
            for L in res.listings:
                yield L
            offset += limit
            page += 1
            if max_pages is not None and page >= max_pages:
                return

    def get_listing(self, listing_id: int) -> EtsyListing:
        data, _hit, _path = self._get(f"/listings/{int(listing_id)}", {})
        return EtsyListing.from_v3(data)

    def get_taxonomy_nodes(self) -> list[dict]:
        data, _hit, _path = self._get(
            "/buyer-taxonomy/nodes", {}, ttl_override=7 * 86_400,
        )
        return list(data.get("results") or [])

    # ── internals ───────────────────────────────────────────────────────────

    def _cache_key(self, endpoint: str, params: dict) -> str:
        canonical = json.dumps([endpoint, sorted(params.items())],
                               sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _check_quota(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = {"date": today, "count": 0}
        if self._quota_path.exists():
            try:
                state = json.loads(self._quota_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
            if state.get("date") != today:
                state = {"date": today, "count": 0}
        if state["count"] >= self.daily_quota:
            raise QuotaExceededError(
                f"Etsy daily quota {self.daily_quota} reached for {today}; "
                f"refusing new request. Edit {self._quota_path} to override."
            )

    def _bump_quota(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = {"date": today, "count": 0}
        if self._quota_path.exists():
            try:
                state = json.loads(self._quota_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
            if state.get("date") != today:
                state = {"date": today, "count": 0}
        state["count"] += 1
        self._quota_path.write_text(json.dumps(state), encoding="utf-8")

    def _get(
        self,
        endpoint: str,
        params: dict,
        *,
        ttl_override: int | None = None,
    ) -> tuple[dict, bool, Path]:
        ttl = ttl_override if ttl_override is not None else self.cache_ttl
        key = self._cache_key(endpoint, params)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < ttl:
                with cache_file.open("r", encoding="utf-8") as f:
                    return json.load(f), True, cache_file

        self._check_quota()
        self._bucket.acquire()

        url = BASE_URL + endpoint
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        last_exc: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                r = self._session.get(url, params=params, headers=headers, timeout=30)
            except requests.RequestException as e:
                last_exc = e
                self._sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError as e:
                    raise RuntimeError(f"Etsy returned non-JSON 200 for {endpoint}") from e
                self._bump_quota()
                cache_file.write_text(json.dumps(data), encoding="utf-8")
                return data, False, cache_file
            if r.status_code == 429:
                wait_s = float(r.headers.get("retry-after") or r.headers.get("Retry-After") or (2 ** attempt))
                LOG.warning("Etsy 429 on %s; sleeping %.1fs (attempt %d/%d)",
                            endpoint, wait_s, attempt + 1, self.max_attempts)
                self._sleep(wait_s)
                continue
            if 500 <= r.status_code < 600:
                LOG.warning("Etsy %d on %s; backoff (attempt %d/%d)",
                            r.status_code, endpoint, attempt + 1, self.max_attempts)
                self._sleep(2 ** attempt)
                continue
            r.raise_for_status()
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"Etsy {endpoint} failed after {self.max_attempts} attempts"
        )
