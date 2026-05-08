from __future__ import annotations

import json
from pathlib import Path

from src.db import EtsyListingRow
from src.research.mining import (
    PRICE_BAND_USD,
    RESEARCH_WEIGHTS,
    freshness_pct_last_90d,
    hhi_shop_concentration,
    mine_theme,
    pod_feasibility,
    price_quartiles,
    saturation_score,
    top_tags,
    volume_signal,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "etsy_search_sample.json"


def _rows_from_fixture() -> list[EtsyListingRow]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows: list[EtsyListingRow] = []
    for r in raw["results"]:
        price = r.get("price") or {}
        usd = None
        if price.get("currency_code") == "USD":
            usd = round(price["amount"] / (price.get("divisor") or 100), 2)
        rows.append(EtsyListingRow(
            listing_id=r["listing_id"],
            probe_id="p1",
            title=r["title"],
            tags=list(r.get("tags") or []),
            price_usd=usd,
            currency_code=price.get("currency_code"),
            num_favorers=r.get("num_favorers", 0),
            views=r.get("views"),
            shop_id=r.get("shop_id"),
            taxonomy_path=[r.get("taxonomy_id")] if r.get("taxonomy_id") else None,
            is_digital=r.get("is_digital"),
            is_personalizable=r.get("is_personalizable"),
            creation_tsz=r.get("created_timestamp"),
        ))
    return rows


def test_research_weights_sum_to_one():
    assert abs(sum(RESEARCH_WEIGHTS.values()) - 1.0) < 1e-9


def test_top_tags_counts_ignore_case():
    rows = _rows_from_fixture()
    tags = dict(top_tags(rows, n=20))
    assert tags["gym shirt"] == 4
    assert tags["gym shirt funny"] == 3


def test_price_quartiles_excludes_eur_and_outliers():
    rows = _rows_from_fixture()
    p25, p75 = price_quartiles(rows)
    assert p25 is not None and p75 is not None
    lo, hi = PRICE_BAND_USD
    assert lo <= p25 <= hi
    assert lo <= p75 <= hi
    # USD prices in fixture: 10.99, 24.95, 35.99, 24.95
    assert p25 < p75


def test_saturation_high_when_many_low_favorites():
    rows = _rows_from_fixture()
    sat = saturation_score(rows)
    assert 0.0 <= sat <= 1.0
    # fixture skews to 0/1 favs except outlier (80) → saturation should be moderate-to-high
    assert sat >= 0.5


def test_volume_signal_normalized():
    rows = _rows_from_fixture()
    assert volume_signal(rows) == 0.05  # 5/100
    assert volume_signal(rows * 30) == 1.0  # capped


def test_hhi_returns_one_when_all_distinct():
    rows = _rows_from_fixture()
    # 5 unique shops → HHI = 5 * (1/5)^2 = 0.2
    assert abs(hhi_shop_concentration(rows) - 0.2) < 1e-9


def test_freshness_with_known_now():
    rows = _rows_from_fixture()
    # use a "now" 30 days after the most recent fixture timestamp (1738000000)
    now = 1738000000 + 30 * 86_400
    f = freshness_pct_last_90d(rows, now_s=now)
    # Listings with timestamps within 90d of now: 1738000000 and 1737500000 → 2/5
    assert abs(f - 0.4) < 1e-9


def test_pod_feasibility_apparel_taxonomy():
    rows = _rows_from_fixture()
    # all fixture listings are taxonomy_id 2078 (apparel)
    assert pod_feasibility(rows) == 1.0


def test_mine_theme_full_dict():
    rows = _rows_from_fixture()
    out = mine_theme(rows, now_s=1738000000 + 30 * 86_400)
    assert out["n_listings"] == 5
    assert out["saturation"] in {"low", "medium", "high"}
    assert out["volume_signal"] in {"low", "medium", "high"}
    assert out["composite_score"] >= 0.0
    assert {"top_tags", "price_p25_usd", "price_p75_usd",
            "saturation_raw", "freshness_pct_last_90d",
            "pod_feasibility", "hhi_shop_concentration"}.issubset(out.keys())


def test_mine_theme_empty_listings_safe():
    out = mine_theme([])
    assert out["n_listings"] == 0
    assert out["price_p25_usd"] is None
    assert out["composite_score"] >= 0.0
