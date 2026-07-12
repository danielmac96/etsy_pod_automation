"""Feedback signal helpers for the research stage.

Re-exports `load_feedback_signal` from `src.db` so research modules can import
from a single namespace. Also formats the signal as a compact text block for
injection into the Gemini theme-generation prompt.
"""
from __future__ import annotations

from src.db import load_feedback_signal  # re-export

__all__ = ["load_feedback_signal", "format_feedback_for_gemini"]


def _format_rejections(signal: dict) -> list[str]:
    rejections = signal.get("rejection_signal") or {}
    lines: list[str] = []

    by_cat = rejections.get("rejected_by_category") or []
    if by_cat:
        lines.append("")
        lines.append("Rejection pressure by category (approval-gate rejects, recent weeks):")
        for r in by_cat:
            lines.append(
                f"  - {r['category']}: {r['prompts_rejected']} prompt(s), "
                f"{r['images_rejected']} image(s) rejected"
            )

    samples = rejections.get("recent_rejected_prompts") or []
    if samples:
        lines.append("")
        lines.append("Recently REJECTED designs — steer AWAY from these directions/mistakes:")
        for s in samples[:8]:
            snippet = (s["prompt_text"] or "")[:140]
            note = f" (AI note: {s['ai_feedback'][:80]})" if s.get("ai_feedback") else ""
            lines.append(f"  - [{s['category']}] rejected at {s['rejected_at']} gate: "
                         f"{snippet!r}{note}")
    return lines


def format_feedback_for_gemini(signal: dict) -> str:
    if signal.get("is_cold_start"):
        cold = (
            "FEEDBACK SIGNAL: cold start — no listing performance history yet. "
            "Generate themes from first principles using the brand voice and "
            "category set; bias toward exploration, not exploitation."
        )
        rejection_lines = _format_rejections(signal)
        return cold + ("\n" + "\n".join(rejection_lines) if rejection_lines else "")

    lines: list[str] = []
    weeks = signal.get("weeks_analyzed", 4)
    lines.append(f"FEEDBACK SIGNAL (last {weeks} weeks of published listings):")

    winners = signal.get("top_winning_briefs") or []
    if winners:
        lines.append("")
        lines.append("Top winning briefs (favorites delta):")
        for w in winners:
            themes = ", ".join(w.get("themes") or []) or "—"
            lines.append(
                f"  - [{w['category']}] {w['headline_text']!r} "
                f"(+{w['favorites_delta_total']} favs; themes: {themes}; "
                f"brief_id={w['brief_id']})"
            )

    tags = signal.get("winning_style_tags") or []
    if tags:
        lines.append("")
        lines.append("Style tags correlated with favorites:")
        for t in tags:
            lines.append(
                f"  - {t['tag']} (freq={t['frequency']}, "
                f"avg_fav_delta={t['avg_favorites_delta']:.1f})"
            )

    underrep = signal.get("underrepresented_categories") or []
    if underrep:
        lines.append("")
        lines.append("Underrepresented categories (low publish count):")
        for u in underrep:
            lines.append(f"  - {u['category']} ({u['published_count']} published)")

    recent = signal.get("recently_explored_themes") or []
    if recent:
        lines.append("")
        lines.append(f"Recently explored themes ({len(recent)} in last 8 weeks) — "
                     "AVOID near-duplicates:")
        for r in recent[:20]:
            tension = r.get("cultural_tension") or ""
            lines.append(f"  - {r['theme_name']} :: {tension}")
        if len(recent) > 20:
            lines.append(f"  ... and {len(recent) - 20} more")

    lines.extend(_format_rejections(signal))

    return "\n".join(lines)
