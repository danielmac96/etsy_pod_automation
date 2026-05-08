from __future__ import annotations

from src.db import EtsyListingRow, Theme
from src.research.concepts import extract_concepts


def _theme() -> Theme:
    return Theme(
        theme_id="t-1", run_id="r-1", theme_name="Vader Spotter Gym",
        description="movie parody", category="Gym Humor",
        cultural_tension="lifting alone with a famous spotter",
    )


def _listings() -> list[EtsyListingRow]:
    return [
        EtsyListingRow(listing_id=111, probe_id="p", title="Real listing A",
                       tags=["gym shirt"], num_favorers=80),
        EtsyListingRow(listing_id=222, probe_id="p", title="Real listing B",
                       tags=["funny gym shirt"], num_favorers=14),
        EtsyListingRow(listing_id=333, probe_id="p", title="Real listing C",
                       tags=["gym tee"], num_favorers=1),
    ]


def test_extract_concepts_drops_hallucinated_listing_ids():
    def fake_gen(prompt, schema=None):
        return [
            {
                "concept_name": "Spot Me Sith",
                "headline_text": "Spot Me, I Am Your Father",
                "visual_concept": "Vader silhouette spotting a barbell",
                "style_tags": ["bold typography", "movie parody"],
                "color_palette_hint": "black + chrome",
                "target_buyer": "gym bros who like Star Wars",
                "differentiation_note": "first parody pairing",
                # 999 is hallucinated; 111 + 222 are real
                "evidence_listing_ids": [111, 999, 222],
            },
        ]

    concepts = extract_concepts(
        gen_fn=fake_gen, theme=_theme(), listings=_listings(), mining={"n_listings": 3},
    )
    assert len(concepts) == 1
    c = concepts[0]
    assert c.theme_id == "t-1"
    assert c.evidence_listing_ids == [111, 222]  # 999 removed
    assert "movie parody" in c.style_tags
    assert len(c.concept_id) == 36


def test_extract_concepts_handles_missing_optional_fields():
    def fake_gen(prompt, schema=None):
        return [{
            "concept_name": "X",
            "headline_text": "Y",
            "visual_concept": "Z",
            "style_tags": [],
            "differentiation_note": "diff",
        }]

    concepts = extract_concepts(
        gen_fn=fake_gen, theme=_theme(), listings=_listings(), mining={},
    )
    assert concepts[0].evidence_listing_ids == []
    assert concepts[0].color_palette_hint is None
    assert concepts[0].target_buyer is None


def test_extract_concepts_object_payload():
    def fake_gen(prompt, schema=None):
        return {"concepts": [{
            "concept_name": "X", "headline_text": "Y", "visual_concept": "Z",
            "style_tags": ["a"], "differentiation_note": "d",
            "evidence_listing_ids": [111],
        }]}

    concepts = extract_concepts(
        gen_fn=fake_gen, theme=_theme(), listings=_listings(), mining={},
    )
    assert len(concepts) == 1
    assert concepts[0].evidence_listing_ids == [111]
