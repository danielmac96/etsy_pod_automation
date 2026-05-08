from __future__ import annotations

from src.db import Theme
from src.research.themes import (
    DEDUP_THRESHOLD,
    annotate_duplicates,
    build_theme_prompt,
    filter_unique,
    generate_themes,
)


def test_cold_start_prompt_has_explore_only_strategy():
    prompt = build_theme_prompt(
        n_themes=6,
        brand_voice="dry humor for gym bros",
        categories=["Gym Humor", "Office Humor"],
        feedback_block="FEEDBACK SIGNAL: cold start",
        is_cold_start=True,
    )
    assert "Cold start" in prompt
    assert "EXPLORE" in prompt
    assert "EXPLOIT" not in prompt


def test_warm_start_prompt_has_three_buckets():
    prompt = build_theme_prompt(
        n_themes=6,
        brand_voice="dry humor",
        categories=["Gym Humor"],
        feedback_block="FEEDBACK SIGNAL: ...",
        is_cold_start=False,
    )
    assert "EXPLOIT" in prompt
    assert "EXPLORE" in prompt
    assert "UNDERREPRESENTED" in prompt


def test_generate_themes_calls_gen_fn_and_assigns_ids():
    captured = {}

    def fake_gen(prompt, schema=None):
        captured["prompt"] = prompt
        captured["schema"] = schema
        return [
            {"theme_name": "Vader Spotter", "description": "movie parody",
             "category": "Gym Humor", "cultural_tension": "lifting alone"},
            {"theme_name": "Pre-Workout Madness", "description": "the jitters",
             "category": "Gym Humor", "cultural_tension": "caffeine joke"},
        ]

    themes = generate_themes(
        gen_fn=fake_gen,
        n_themes=2,
        brand_voice="dry humor",
        categories=["Gym Humor", "Office Humor"],
        feedback_signal={"is_cold_start": True, "recently_explored_themes": []},
        run_id="run-1",
    )
    assert len(themes) == 2
    assert all(isinstance(t, Theme) for t in themes)
    assert all(t.run_id == "run-1" for t in themes)
    assert all(len(t.theme_id) == 36 for t in themes)  # uuid4
    assert "schema" in captured and captured["schema"] is not None


def test_dedup_marks_near_duplicates_in_notes():
    new = [
        Theme(theme_id="n1", run_id="r", theme_name="Vader Spotter Gym",
              description="", category="Gym Humor",
              cultural_tension="lifting alone movie parody"),
        Theme(theme_id="n2", run_id="r", theme_name="Coffee Office Survival",
              description="", category="Office Humor",
              cultural_tension="meetings and caffeine"),
    ]
    recent = [
        {"theme_id": "old-1", "theme_name": "Vader Spotter Gym",
         "cultural_tension": "lifting alone movie parody"},
    ]
    out = annotate_duplicates(new, recent, threshold=DEDUP_THRESHOLD)
    assert out[0].notes is not None
    assert out[0].notes.startswith("near_duplicate_of:old-1")
    assert out[1].notes is None


def test_filter_unique_drops_marked_themes():
    a = Theme(theme_id="a", run_id="r", theme_name="A", description="",
              category="X", cultural_tension="", notes="near_duplicate_of:old")
    b = Theme(theme_id="b", run_id="r", theme_name="B", description="",
              category="X", cultural_tension="")
    assert filter_unique([a, b]) == [b]


def test_dedup_no_recent_returns_unchanged():
    new = [Theme(theme_id="n1", run_id="r", theme_name="X", description="",
                 category="Y", cultural_tension="z")]
    out = annotate_duplicates(new, [])
    assert out[0].notes is None


def test_generate_themes_normalizes_invalid_category():
    def fake_gen(prompt, schema=None):
        return [{"theme_name": "T", "description": "d",
                 "category": "Nonsense Category",
                 "cultural_tension": "tension"}]

    themes = generate_themes(
        gen_fn=fake_gen,
        n_themes=1,
        brand_voice="v",
        categories=["Gym Humor", "Office Humor"],
        feedback_signal={"is_cold_start": True, "recently_explored_themes": []},
        run_id="r",
    )
    assert themes[0].category in {"Gym Humor", "Office Humor"}
