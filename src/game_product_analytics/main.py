from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv

from game_product_analytics.pipeline import analyze_game_reviews
from game_product_analytics.schemas import GameSearchResult, ReviewAnalysisRequest, ReviewAnalysisResponse
from game_product_analytics.steam import (
    SteamAccessDeniedError,
    SteamClient,
    SteamError,
    SteamRateLimitError,
    SteamTransientError,
)


load_dotenv()

app = FastAPI(title="Game Product Analytics", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/steam/search", response_model=list[GameSearchResult])
async def search_steam_games(
    query: str = Query(min_length=2),
    limit: int = Query(default=10, ge=1, le=25),
) -> list[GameSearchResult]:
    try:
        async with SteamClient() as steam:
            return await steam.search_games(query, limit=limit)
    except (SteamRateLimitError, SteamAccessDeniedError) as exc:
        headers = {"Retry-After": str(getattr(exc, "retry_after_seconds", 60) or 60)}
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except SteamTransientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SteamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/analysis/reviews", response_model=ReviewAnalysisResponse)
async def analyze_reviews_endpoint(request: ReviewAnalysisRequest) -> ReviewAnalysisResponse:
    try:
        return await analyze_game_reviews(request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (SteamRateLimitError, SteamAccessDeniedError) as exc:
        headers = {"Retry-After": str(getattr(exc, "retry_after_seconds", 60) or 60)}
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except SteamTransientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SteamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
