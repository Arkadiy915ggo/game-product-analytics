from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Any

import httpx

from game_product_analytics.schemas import GameSearchResult, SteamReview


STEAM_STORE_BASE_URL = "https://store.steampowered.com"


class SteamClient:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=STEAM_STORE_BASE_URL,
            timeout=timeout_seconds,
            headers={"User-Agent": "game-product-analytics/0.1"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SteamClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def search_games(self, query: str, limit: int = 10) -> list[GameSearchResult]:
        response = await self._client.get(
            "/api/storesearch/",
            params={"term": query, "l": "english", "cc": "us"},
        )
        response.raise_for_status()
        data = response.json()

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
        start_ts = _start_of_day_ts(start_date) if start_date else None
        end_ts = _end_of_day_ts(end_date) if end_date else None
        cursor = "*"
        reviews: list[SteamReview] = []

        while len(reviews) < max_reviews:
            response = await self._client.get(
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
            response.raise_for_status()
            payload = response.json()
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
            await asyncio.sleep(0.2)

        return reviews


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
    return int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp())


def _end_of_day_ts(value: date) -> int:
    return int(datetime.combine(value, time.max, tzinfo=timezone.utc).timestamp())
