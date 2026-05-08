from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from etsy_client import EtsyClient, MAX_OFFSET, QuotaExceededError, TokenBucket  # noqa: E402
from schemas import EtsyListing  # noqa: E402

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "etsy_search_sample.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _mock_session(payload: dict, status: int = 200, headers: dict | None = None):
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    sess.get.return_value = resp
    return sess


# ── EtsyListing parsing ───────────────────────────────────────────────────────

def test_listing_parses_usd_price():
    raw = _fixture()["results"][0]
    L = EtsyListing.from_v3(raw)
    assert L.listing_id == 1322910664
    assert L.price_usd == 10.99
    assert L.currency_code == "USD"
    assert L.num_favorers == 80
    assert L.views == 1240
    assert L.shop_id == 19237001
    assert L.taxonomy_path == [2078]
    assert L.creation_tsz == 1714000000


def test_non_usd_price_returns_none():
    raw = _fixture()["results"][-1]  # the EUR listing
    L = EtsyListing.from_v3(raw)
    assert L.currency_code == "EUR"
    assert L.price_usd is None
    assert L.title.startswith("Camiseta")


def test_legacy_creation_tsz_field():
    raw = {"listing_id": 1, "title": "x", "tags": [], "price": {"amount": 0, "divisor": 100, "currency_code": "USD"},
           "creation_tsz": 1700000000}  # legacy v2 name
    L = EtsyListing.from_v3(raw)
    assert L.creation_tsz == 1700000000


# ── cache + HTTP avoidance ────────────────────────────────────────────────────

def test_cache_hit_avoids_http(tmp_path):
    payload = _fixture()
    sess = _mock_session(payload)
    client = EtsyClient(api_key="k", cache_dir=tmp_path, session=sess,
                        rps=1000.0, sleep_fn=lambda s: None)

    r1 = client.search_listings("gym shirt funny")
    r2 = client.search_listings("gym shirt funny")
    assert r1.cache_hit is False
    assert r2.cache_hit is True
    assert sess.get.call_count == 1
    assert len(r2.listings) == 5


# ── rate limiting ─────────────────────────────────────────────────────────────

def test_token_bucket_blocks_under_burst():
    """With rps=2, firing 5 calls must invoke sleep at least 3 times."""
    sleeps: list[float] = []
    fake_now = [0.0]
    def time_fn(): return fake_now[0]
    def sleep_fn(s):
        sleeps.append(s)
        fake_now[0] += s
    bucket = TokenBucket(rate=2.0, time_fn=time_fn, sleep_fn=sleep_fn)
    for _ in range(5):
        bucket.acquire()
    # 2 free (initial capacity), then 3 must wait
    assert len(sleeps) >= 3
    assert all(s > 0 for s in sleeps)


# ── quota ─────────────────────────────────────────────────────────────────────

def test_daily_quota_refuses_after_cap(tmp_path):
    sess = _mock_session(_fixture())
    client = EtsyClient(api_key="k", cache_dir=tmp_path, session=sess,
                        rps=1000.0, daily_quota=2, sleep_fn=lambda s: None)
    client.search_listings("a")  # 1
    client.search_listings("b")  # 2
    with pytest.raises(QuotaExceededError):
        client.search_listings("c")  # blocked
    # third request never made it to HTTP
    assert sess.get.call_count == 2


# ── retries ───────────────────────────────────────────────────────────────────

def test_429_honors_retry_after(tmp_path):
    sess = MagicMock()
    bad = MagicMock(); bad.status_code = 429; bad.headers = {"retry-after": "0.01"}
    good = MagicMock(); good.status_code = 200; good.headers = {}
    good.json.return_value = _fixture()
    sess.get.side_effect = [bad, good]
    sleeps: list[float] = []
    client = EtsyClient(api_key="k", cache_dir=tmp_path, session=sess,
                        rps=1000.0, sleep_fn=lambda s: sleeps.append(s))
    res = client.search_listings("retry-me")
    assert sess.get.call_count == 2
    assert any(abs(s - 0.01) < 1e-6 for s in sleeps)
    assert len(res.listings) == 5


# ── pagination cap ────────────────────────────────────────────────────────────

def test_paginate_stops_at_max_offset(tmp_path):
    payload = _fixture()
    # always returns 5 listings — without the offset cap this would loop forever
    sess = _mock_session(payload)
    client = EtsyClient(api_key="k", cache_dir=tmp_path, session=sess,
                        rps=1000.0, sleep_fn=lambda s: None)
    count = 0
    for _ in client.paginate_listings("nonsense", limit=2000):
        count += 1
        if count > 100_000:  # safety
            pytest.fail("paginator did not terminate")
    # ceil(MAX_OFFSET / 2000) = 6 pages * 5 listings each = 30
    assert count == 6 * 5
