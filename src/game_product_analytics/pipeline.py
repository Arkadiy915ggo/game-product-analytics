from __future__ import annotations

from game_product_analytics.llm import analyze_reviews
from game_product_analytics.schemas import GameSearchResult, ReviewAnalysisRequest, ReviewAnalysisResponse
from game_product_analytics.steam import SteamClient


async def analyze_game_reviews(request: ReviewAnalysisRequest) -> ReviewAnalysisResponse:
    async with SteamClient() as steam:
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
        analysis = await analyze_reviews(reviews)

    return ReviewAnalysisResponse(
        game=game,
        app_id=app_id,
        period={"start_date": request.start_date, "end_date": request.end_date},
        reviews_count=len(reviews),
        analysis=analysis,
    )
