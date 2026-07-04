from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from dotenv import load_dotenv

from game_product_analytics.pipeline import analyze_game_reviews
from game_product_analytics.schemas import ReviewAnalysisRequest


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Analyze Steam reviews for a game")
    parser.add_argument("game_query", nargs="?", help="Steam game name, for example: 'Dota 2'")
    parser.add_argument("--app-id", type=int, help="Steam app id, skips search if provided")
    parser.add_argument("--start-date", type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--max-reviews", type=int, default=200)
    parser.add_argument("--language", default="all")
    args = parser.parse_args()

    request = ReviewAnalysisRequest(
        game_query=args.game_query,
        app_id=args.app_id,
        start_date=args.start_date,
        end_date=args.end_date,
        max_reviews=args.max_reviews,
        language=args.language,
    )
    response = asyncio.run(analyze_game_reviews(request))
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
