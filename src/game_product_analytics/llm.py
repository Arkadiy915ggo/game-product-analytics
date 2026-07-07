from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

import httpx

from game_product_analytics.config import Settings, load_settings
from game_product_analytics.schemas import ReviewAnalysis, SentimentBreakdown, SteamReview


SYSTEM_PROMPT = """You are a senior game product analyst.
Analyze Steam reviews as product feedback.
Return strict JSON only.
Do not wrap JSON in markdown.
"""


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, *, system: str, user: str) -> str:
        raise NotImplementedError

    async def unload(self) -> None:
        return None


class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for LLM_PROVIDER=openai")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(self, *, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return str(content or "").strip()


class OllamaClient(LLMClient):
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int,
        keep_alive: str,
        unload_after_task: bool,
        num_ctx: int,
        num_predict: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self.unload_after_task = unload_after_task
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    async def complete(self, *, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "keep_alive": self.keep_alive,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "options": {
                        "temperature": 0.2,
                        "num_ctx": self.num_ctx,
                        "num_predict": self.num_predict,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
        total_duration = (data.get("total_duration") or 0) / 1_000_000_000
        prompt_eval_count = data.get("prompt_eval_count") or 0
        eval_count = data.get("eval_count") or 0
        eval_duration = (data.get("eval_duration") or 0) / 1_000_000_000
        tokens_per_second = eval_count / eval_duration if eval_duration else 0
        logging.info(
            "Ollama response model=%s total_s=%.1f prompt_tokens=%s eval_tokens=%s eval_tps=%.2f",
            self.model,
            total_duration,
            prompt_eval_count,
            eval_count,
            tokens_per_second,
        )
        return str(data.get("message", {}).get("content", "")).strip()

    async def unload(self) -> None:
        if not self.unload_after_task:
            return
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model, "prompt": "", "keep_alive": 0},
                )
                response.raise_for_status()
            except Exception:  # noqa: BLE001
                logging.exception("Failed to unload Ollama model %s", self.model)
                return
        logging.info("Ollama model unloaded model=%s", self.model)


def build_llm_client(
    settings: Settings,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> LLMClient:
    resolved = provider or settings.resolved_llm_provider
    if resolved == "openai":
        return OpenAICompatibleClient(
            settings.openai_api_key,
            settings.openai_base_url,
            model or settings.openai_model,
        )
    if resolved == "ollama":
        return OllamaClient(
            settings.ollama_base_url,
            model or settings.ollama_model,
            settings.ollama_timeout_seconds,
            settings.ollama_keep_alive,
            settings.ollama_unload_after_task,
            settings.ollama_num_ctx,
            settings.ollama_num_predict,
        )
    raise RuntimeError(f"Unsupported LLM provider: {resolved}")


async def analyze_reviews(reviews: list[SteamReview], settings: Settings | None = None) -> ReviewAnalysis:
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

    settings = settings or load_settings()
    try:
        client = build_llm_client(settings)
        llm_text = await client.complete(system=SYSTEM_PROMPT, user=_build_user_prompt(reviews))
        try:
            await client.unload()
        except Exception:  # noqa: BLE001
            logging.debug("LLM unload failed; ignoring")
        parsed = _parse_json_object(llm_text)
        if parsed is None:
            raise ValueError("LLM response was not valid JSON")
        return _analysis_from_llm(parsed, reviews)
    except Exception as exc:  # noqa: BLE001
        logging.warning("LLM analysis failed, falling back to keyword analysis: %s", exc)
        return fallback_analysis(reviews)


def _build_user_prompt(reviews: list[SteamReview]) -> str:
    payload = {
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
    return json.dumps(payload, ensure_ascii=False)


def _analysis_from_llm(parsed: dict[str, Any], reviews: list[SteamReview]) -> ReviewAnalysis:
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
            f"{sentiment.positive_share:.1%}. Fallback analysis is keyword-based; use Ollama or OpenAI for deeper theme extraction."
        ),
        sentiment=sentiment,
        top_likes=[f"Frequent positive terms: {', '.join(top_positive_terms)}"] if top_positive_terms else [],
        top_pain_points=[f"Frequent negative terms: {', '.join(top_negative_terms)}"] if top_negative_terms else [],
        feature_requests=requests,
        monetization_mentions=monetization,
        technical_issues=technical_issues,
        notable_quotes=_notable_quotes(reviews),
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fenced = re.sub(r"^```(?:json)?|```$", "", stripped.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        parsed = json.loads(fenced)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


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
