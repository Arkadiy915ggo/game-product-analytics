from __future__ import annotations

import asyncio
import random
import time
from datetime import date, datetime, time as dt_time, timezone
from typing import Any

import httpx

from game_product_analytics.cache import DiskCache, cache_key
from game_product_analytics.config import Settings, load_settings
from game_product_analytics.schemas import GameSearchResult, SteamReview


STEAM_STORE_BASE_URL = "https://store.steampowered.com"


class SteamError(RuntimeError):
    pass


class SteamCooldownActiveError(SteamError):
    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SteamRateLimitError(SteamCooldownActiveError):
    pass


class SteamAccessDeniedError(SteamCooldownActiveError):
    pass


class SteamTransientError(SteamError):
    pass


class SteamClient:
    def __init__(self, settings: Settings | None = None, timeout_seconds: float = 30.0) -> None:
        self.settings = settings or load_settings()
        self._client = httpx.AsyncClient(
            base_url=STEAM_STORE_BASE_URL,
            timeout=timeout_seconds,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            headers={"User-Agent": self.settings.user_agent},
        )
        self._last_request_at = 0.0
        self._search_cache = DiskCache(self.settings.cache_dir, "steam-search")
        self._reviews_cache = DiskCache(self.settings.cache_dir, "steam-reviews")
        self._cooldown_cache = DiskCache(self.settings.cache_dir, "steam-cooldown")

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SteamClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def search_games(self, query: str, limit: int = 10) -> list[GameSearchResult]:
        cache_value = self._search_cache.get(
            cache_key("search_games", query, limit),
            max_age_seconds=self.settings.steam_search_cache_ttl_seconds,
        )
        if cache_value is not None:
            return [GameSearchResult.model_validate(item) for item in cache_value]

        data = await self._request_json(
            "/api/storesearch/",
            params={"term": query, "l": "english", "cc": "us"},
        )

        results: list[GameSearchResult] = []
        for item in data.get("items", [])[:limit]:
            app_id = item.get("id")
            name = item.get("name")
            if not app_id or not name:
                continue
            results.append(
                GameSearchResult(
                    app_id=int(app_id),
                    name=name,
                    price=_extract_price(item),
                    image_url=item.get("tiny_image"),
                )
            )

        self._search_cache.set(cache_key("search_games", query, limit), [item.model_dump(mode="json") for item in results])
        return results

    async def fetch_reviews(
        self,
        app_id: int,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        max_reviews: int = 200,
        language: str = "all",
    ) -> list[SteamReview]:
        cache_value = self._reviews_cache.get(
            cache_key("fetch_reviews", app_id, start_date, end_date, max_reviews, language),
            max_age_seconds=self.settings.steam_reviews_cache_ttl_seconds,
        )
        if cache_value is not None:
            return [SteamReview.model_validate(item) for item in cache_value]

        start_ts = _start_of_day_ts(start_date) if start_date else None
        end_ts = _end_of_day_ts(end_date) if end_date else None
        cursor = "*"
        reviews: list[SteamReview] = []

        while len(reviews) < max_reviews:
            payload = await self._request_json(
                f"/appreviews/{app_id}",
                params={
                    "json": 1,
                    "filter": "recent",
                    "language": language,
                    "review_type": "all",
                    "purchase_type": "all",
                    "num_per_page": min(100, max_reviews - len(reviews)),
                    "cursor": cursor,
                },
            )
            page_reviews = payload.get("reviews", [])
            if not page_reviews:
                break

            oldest_ts_on_page: int | None = None
            for item in page_reviews:
                created_ts = item.get("timestamp_created")
                if not isinstance(created_ts, int):
                    continue
                oldest_ts_on_page = created_ts if oldest_ts_on_page is None else min(oldest_ts_on_page, created_ts)
                if end_ts is not None and created_ts > end_ts:
                    continue
                if start_ts is not None and created_ts < start_ts:
                    continue
                reviews.append(_parse_review(app_id, item))
                if len(reviews) >= max_reviews:
                    break

            next_cursor = payload.get("cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

            if start_ts is not None and oldest_ts_on_page is not None and oldest_ts_on_page < start_ts:
                break
            await asyncio.sleep(self.settings.steam_page_delay_seconds)

        self._reviews_cache.set(
            cache_key("fetch_reviews", app_id, start_date, end_date, max_reviews, language),
            [item.model_dump(mode="json") for item in reviews],
        )
        return reviews

    async def _request_json(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        await self._respect_cooldown()

        for attempt in range(self.settings.steam_max_retries + 1):
            await self._enforce_min_interval()
            try:
                response = await self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.settings.steam_max_retries:
                    raise SteamTransientError(f"Steam request failed after retries: {exc}") from exc
                await self._sleep_backoff(attempt)
                continue

            if response.status_code == 429:
                retry_after = int(self.settings.steam_rate_limit_cooldown_seconds)
                self._record_cooldown("rate_limited", retry_after)
                raise SteamRateLimitError(
                    "Steam returned 429 rate limit. Requests are paused to avoid further limiting.",
                    retry_after_seconds=retry_after,
                )

            if response.status_code == 403:
                retry_after = int(self.settings.steam_forbidden_cooldown_seconds)
                self._record_cooldown("forbidden", retry_after)
                raise SteamAccessDeniedError(
                    "Steam returned 403 forbidden. Requests are paused to avoid further limiting.",
                    retry_after_seconds=retry_after,
                )

            if response.status_code >= 500:
                if attempt >= self.settings.steam_max_retries:
                    raise SteamTransientError(f"Steam returned {response.status_code}: {response.text[:300]}")
                await self._sleep_backoff(attempt)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise SteamTransientError(
                    f"Steam returned {response.status_code}: {response.text[:300]}"
                ) from exc

            self._last_request_at = time.monotonic()
            return response.json()

        raise SteamTransientError("Steam request exhausted retries")

    async def _respect_cooldown(self) -> None:
        cooldown = self._cooldown_cache.get("steam-global", max_age_seconds=7 * 24 * 3600)
        if not cooldown:
            return
        retry_at = float(cooldown.get("retry_at", 0))
        if retry_at <= time.time():
            return
        retry_after = max(1, int(retry_at - time.time()))
        reason = str(cooldown.get("reason", "cooldown active"))
        if reason == "forbidden":
            raise SteamAccessDeniedError(
                f"Steam cooldown is active for about {retry_after} seconds after a 403 response.",
                retry_after_seconds=retry_after,
            )
        raise SteamRateLimitError(
            f"Steam cooldown is active for about {retry_after} seconds after a rate limit response.",
            retry_after_seconds=retry_after,
        )

    async def _enforce_min_interval(self) -> None:
        min_interval = max(0.0, float(self.settings.steam_min_request_interval_seconds))
        if min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

    async def _sleep_backoff(self, attempt: int) -> None:
        delay = min(
            self.settings.steam_retry_max_seconds,
            self.settings.steam_retry_base_seconds * (2**attempt),
        )
        delay += random.uniform(0, min(1.0, delay / 4 if delay else 0.25))
        await asyncio.sleep(delay)

    def _record_cooldown(self, reason: str, retry_after_seconds: int) -> None:
        self._cooldown_cache.set(
            "steam-global",
            {
                "reason": reason,
                "retry_at": time.time() + retry_after_seconds,
            },
        )


def _extract_price(item: dict[str, Any]) -> str | None:
    price = item.get("price")
    if price:
        return str(price)
    if item.get("is_free"):
        return "Free"
    return None


def _parse_review(app_id: int, item: dict[str, Any]) -> SteamReview:
    author = item.get("author") or {}
    playtime_minutes = author.get("playtime_forever")
    weighted_vote_score = item.get("weighted_vote_score") or 0
    return SteamReview(
        recommendation_id=str(item.get("recommendationid", "")),
        app_id=app_id,
        author_steam_id=str(author.get("steamid")) if author.get("steamid") else None,
        language=item.get("language"),
        review=item.get("review") or "",
        voted_up=bool(item.get("voted_up")),
        timestamp_created=datetime.fromtimestamp(item["timestamp_created"], tz=timezone.utc),
        timestamp_updated=datetime.fromtimestamp(item["timestamp_updated"], tz=timezone.utc)
        if item.get("timestamp_updated")
        else None,
        votes_up=int(item.get("votes_up") or 0),
        votes_funny=int(item.get("votes_funny") or 0),
        weighted_vote_score=float(weighted_vote_score),
        playtime_forever_hours=round(playtime_minutes / 60, 2) if isinstance(playtime_minutes, int) else None,
    )


def _start_of_day_ts(value: date) -> int:
    return int(datetime.combine(value, dt_time.min, tzinfo=timezone.utc).timestamp())


def _end_of_day_ts(value: date) -> int:
    return int(datetime.combine(value, dt_time.max, tzinfo=timezone.utc).timestamp())
