from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class GameSearchResult(BaseModel):
    app_id: int
    name: str
    price: str | None = None
    image_url: str | None = None


class SteamReview(BaseModel):
    recommendation_id: str
    app_id: int
    author_steam_id: str | None = None
    language: str | None = None
    review: str
    voted_up: bool
    timestamp_created: datetime
    timestamp_updated: datetime | None = None
    votes_up: int = 0
    votes_funny: int = 0
    weighted_vote_score: float = 0.0
    playtime_forever_hours: float | None = None


class ReviewAnalysisRequest(BaseModel):
    game_query: str | None = Field(default=None, description="Game name to search in Steam")
    app_id: int | None = Field(default=None, description="Steam app id, if already known")
    start_date: date | None = Field(default=None, description="Inclusive review creation date")
    end_date: date | None = Field(default=None, description="Inclusive review creation date")
    max_reviews: int = Field(default=200, ge=1, le=2000)
    language: str = Field(default="all", description="Steam review language filter")

    @model_validator(mode="after")
    def validate_game_reference(self) -> "ReviewAnalysisRequest":
        if self.app_id is None and not self.game_query:
            raise ValueError("Either app_id or game_query is required")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        return self


class SentimentBreakdown(BaseModel):
    positive: int
    negative: int
    positive_share: float


class ReviewAnalysis(BaseModel):
    summary: str
    sentiment: SentimentBreakdown
    top_likes: list[str]
    top_pain_points: list[str]
    feature_requests: list[str]
    monetization_mentions: list[str]
    technical_issues: list[str]
    notable_quotes: list[str]
    raw_llm_response: dict[str, Any] | None = None


class ReviewAnalysisResponse(BaseModel):
    game: GameSearchResult | None
    app_id: int
    period: dict[str, date | None]
    reviews_count: int
    analysis: ReviewAnalysis
