from __future__ import annotations

from pathlib import Path

from game_product_analytics.schemas import ReviewAnalysisResponse


def save_markdown_report(response: ReviewAnalysisResponse, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = (response.game.name if response.game else str(response.app_id)).replace("/", "_")
    period_start = response.period.get("start_date") or "any"
    period_end = response.period.get("end_date") or "any"
    filename = f"{name}__{period_start}__{period_end}.md"
    safe = _safe_filename(filename)
    path = out_dir / safe

    a = response.analysis

    lines = [
        f"# {name} — Game Product Analysis",
        "",
        f"- **App ID:** {response.app_id}",
        f"- **Period:** {period_start} — {period_end}",
        f"- **Reviews:** {response.reviews_count}",
        "",
        "## Sentiment",
        "",
        f"- Positive share: **{a.sentiment.positive_share:.1%}**",
        f"- Positive: {a.sentiment.positive} · Negative: {a.sentiment.negative}",
        "",
    ]

    if a.summary:
        lines += ["## Summary", "", a.summary, ""]

    if a.top_likes:
        lines += ["## What Players Like", ""]
        lines += [f"- {item}" for item in a.top_likes]
        lines += [""]

    if a.top_pain_points:
        lines += ["## Pain Points", ""]
        lines += [f"- {item}" for item in a.top_pain_points]
        lines += [""]

    if a.feature_requests:
        lines += ["## Feature Requests", ""]
        lines += [f"- {item}" for item in a.feature_requests]
        lines += [""]

    if a.monetization_mentions:
        lines += ["## Monetization Mentions", ""]
        lines += [f"- {item}" for item in a.monetization_mentions]
        lines += [""]

    if a.technical_issues:
        lines += ["## Technical Issues", ""]
        lines += [f"- {item}" for item in a.technical_issues]
        lines += [""]

    if a.notable_quotes:
        lines += ["## Notable Quotes", ""]
        lines += [f"- {item}" for item in a.notable_quotes]
        lines += [""]

    if a.raw_llm_response:
        lines += ["---", "", f"_Analysis by LLM_"]
    else:
        lines += ["---", "", f"_Keyword-based fallback analysis_"]

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _safe_filename(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_", " ", ".") else "_" for c in name)
    return safe.strip().strip(".")
