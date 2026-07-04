from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any

import httpx

from game_product_analytics.schemas import ReviewAnalysis, SentimentBreakdown, SteamReview


SYSTEM_PROMPT = """You are a senior game product analyst.
Analyze Steam reviews as product feedback. Return strict JSON only.
Do not wrap JSON in markdown.
"""


def build_review_payload(reviews: list[SteamReview], limit: int = 80) -> list[dict[str, Any]]:
    return [
        {
            "voted_up": review.voted_up,
            "language": review.language,
            "created_at": review.timestamp_created.isoformat(),
            "playtime_hours": review.playtime_forever_hours,
            "review": _compact_text(review.review, 1200),
        }
        for review in reviews[:limit]
        if review.review.strip()
    ]


async def analyze_reviews(reviews: list[SteamReview]) -> ReviewAnalysis:
    if not reviews:
        return ReviewAnalysis(
            summary="No reviews found for the selected period.",
            sentiment=SentimentBreakdown(positive=0, negative=0, positive_share=0.0),
            top_likes=[],
            top_pain_points=[],
            feature_requests=[],
            monetization_mentions=[],
            technical_issues=[],
            notable_quotes=[],
        )

    llm_result = await _try_llm_analysis(reviews)
    if llm_result is not None:
        return llm_result
    return fallback_analysis(reviews)


async def _try_llm_analysis(reviews: list[SteamReview]) -> ReviewAnalysis | None:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    user_prompt = {
        "task": "Analyze Steam reviews for game product analytics.",
        "required_json_schema": {
            "summary": "string: concise executive summary",
            "top_likes": ["string"],
            "top_pain_points": ["string"],
            "feature_requests": ["string"],
            "monetization_mentions": ["string"],
            "technical_issues": ["string"],
            "notable_quotes": ["string"],
        },
        "reviews": build_review_payload(reviews),
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return None

    sentiment = _sentiment_from_reviews(reviews)
    return ReviewAnalysis(
        summary=str(parsed.get("summary") or "LLM analysis completed."),
        sentiment=sentiment,
        top_likes=_string_list(parsed.get("top_likes")),
        top_pain_points=_string_list(parsed.get("top_pain_points")),
        feature_requests=_string_list(parsed.get("feature_requests")),
        monetization_mentions=_string_list(parsed.get("monetization_mentions")),
        technical_issues=_string_list(parsed.get("technical_issues")),
        notable_quotes=_string_list(parsed.get("notable_quotes")),
        raw_llm_response=parsed,
    )


def fallback_analysis(reviews: list[SteamReview]) -> ReviewAnalysis:
    sentiment = _sentiment_from_reviews(reviews)
    negative_reviews = [review.review for review in reviews if not review.voted_up]
    positive_reviews = [review.review for review in reviews if review.voted_up]

    top_negative_terms = _top_terms(negative_reviews)
    top_positive_terms = _top_terms(positive_reviews)
    technical_issues = _find_reviews_by_terms(reviews, ["bug", "crash", "fps", "lag", "server", "stutter", "freeze"])
    monetization = _find_reviews_by_terms(reviews, ["dlc", "price", "pay", "microtransaction", "battle pass", "skin"])
    requests = _find_reviews_by_terms(reviews, ["please", "add", "need", "wish", "should", "feature"])

    return ReviewAnalysis(
        summary=(
            f"Analyzed {len(reviews)} reviews. Positive share is "
            f"{sentiment.positive_share:.1%}. Fallback analysis is keyword-based; set LLM_API_KEY "
            "for deeper theme extraction."
        ),
        sentiment=sentiment,
        top_likes=[f"Frequent positive terms: {', '.join(top_positive_terms)}"] if top_positive_terms else [],
        top_pain_points=[f"Frequent negative terms: {', '.join(top_negative_terms)}"] if top_negative_terms else [],
        feature_requests=requests,
        monetization_mentions=monetization,
        technical_issues=technical_issues,
        notable_quotes=_notable_quotes(reviews),
    )


def _sentiment_from_reviews(reviews: list[SteamReview]) -> SentimentBreakdown:
    positive = sum(1 for review in reviews if review.voted_up)
    negative = len(reviews) - positive
    return SentimentBreakdown(
        positive=positive,
        negative=negative,
        positive_share=round(positive / len(reviews), 4) if reviews else 0.0,
    )


def _top_terms(texts: list[str], limit: int = 8) -> list[str]:
    stop_words = {
        "the",
        "and",
        "that",
        "this",
        "with",
        "for",
        "you",
        "are",
        "but",
        "not",
        "game",
        "have",
        "was",
        "just",
        "like",
        "all",
        "can",
        "get",
        "from",
        "play",
    }
    words: list[str] = []
    for text in texts:
        words.extend(
            word
            for word in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower())
            if word not in stop_words
        )
    return [word for word, _ in Counter(words).most_common(limit)]


def _find_reviews_by_terms(reviews: list[SteamReview], terms: list[str], limit: int = 5) -> list[str]:
    matches: list[str] = []
    for review in reviews:
        text = review.review.strip()
        lower = text.lower()
        if text and any(term in lower for term in terms):
            matches.append(_compact_text(text, 220))
        if len(matches) >= limit:
            break
    return matches


def _notable_quotes(reviews: list[SteamReview], limit: int = 5) -> list[str]:
    ranked = sorted(reviews, key=lambda item: (item.votes_up, item.weighted_vote_score), reverse=True)
    return [_compact_text(review.review, 220) for review in ranked[:limit] if review.review.strip()]


def _compact_text(value: str, max_length: int) -> str:
    compacted = re.sub(r"\s+", " ", value).strip()
    if len(compacted) <= max_length:
        return compacted
    return compacted[: max_length - 1].rstrip() + "..."


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
