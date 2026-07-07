from __future__ import annotations

from game_product_analytics.cache import DiskCache, cache_key
from game_product_analytics.config import Settings, load_settings
from game_product_analytics.llm import analyze_reviews
from game_product_analytics.schemas import GameSearchResult, ReviewAnalysisRequest, ReviewAnalysisResponse
from game_product_analytics.steam import SteamClient


async def analyze_game_reviews(
    request: ReviewAnalysisRequest,
    settings: Settings | None = None,
) -> ReviewAnalysisResponse:
    settings = settings or load_settings()
    cache = DiskCache(settings.cache_dir, "analysis")
    cache_id = cache_key(
        "analysis",
        request.model_dump(mode="json"),
        settings.resolved_llm_provider,
        settings.openai_model,
        settings.ollama_model,
        settings.ollama_num_ctx,
        settings.ollama_num_predict,
    )
    cached_value = cache.get(cache_id, max_age_seconds=settings.analysis_cache_ttl_seconds)
    if cached_value is not None:
        return ReviewAnalysisResponse.model_validate(cached_value)

    async with SteamClient(settings=settings) as steam:
        game: GameSearchResult | None = None
        app_id = request.app_id

        if app_id is None:
            assert request.game_query is not None
            results = await steam.search_games(request.game_query, limit=1)
            if not results:
                raise ValueError(f"Game not found in Steam search: {request.game_query}")
            game = results[0]
            app_id = game.app_id

        reviews = await steam.fetch_reviews(
            app_id,
            start_date=request.start_date,
            end_date=request.end_date,
            max_reviews=request.max_reviews,
            language=request.language,
        )
        analysis = await analyze_reviews(reviews, settings=settings)

    response = ReviewAnalysisResponse(
        game=game,
        app_id=app_id,
        period={"start_date": request.start_date, "end_date": request.end_date},
        reviews_count=len(reviews),
        analysis=analysis,
    )
    cache.set(cache_id, response.model_dump(mode="json"))
    return response
