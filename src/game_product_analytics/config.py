from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str, *, default: bool = False) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _float(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    ollama_base_url: str
    ollama_model: str
    ollama_timeout_seconds: int
    ollama_keep_alive: str
    ollama_unload_after_task: bool
    ollama_num_ctx: int
    ollama_num_predict: int
    cache_dir: Path
    steam_min_request_interval_seconds: float
    steam_page_delay_seconds: float
    steam_max_retries: int
    steam_retry_base_seconds: float
    steam_retry_max_seconds: float
    steam_rate_limit_cooldown_seconds: float
    steam_forbidden_cooldown_seconds: float
    steam_search_cache_ttl_seconds: int
    steam_reviews_cache_ttl_seconds: int
    analysis_cache_ttl_seconds: int
    max_reviews_default: int
    max_reviews_limit: int
    user_agent: str

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        return "openai" if self.openai_api_key else "ollama"


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if provider not in {"auto", "openai", "ollama"}:
        raise RuntimeError("LLM_PROVIDER must be one of: auto, openai, ollama")

    return Settings(
        llm_provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:27b").strip(),
        ollama_timeout_seconds=_int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "1800"), 1800),
        ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip(),
        ollama_unload_after_task=_bool(os.getenv("OLLAMA_UNLOAD_AFTER_TASK", "true"), default=True),
        ollama_num_ctx=_int(os.getenv("OLLAMA_NUM_CTX", "4096"), 4096),
        ollama_num_predict=_int(os.getenv("OLLAMA_NUM_PREDICT", "800"), 800),
        cache_dir=Path(os.getenv("CACHE_DIR", ".cache/game-product-analytics")),
        steam_min_request_interval_seconds=_float(os.getenv("STEAM_MIN_REQUEST_INTERVAL_SECONDS", "1.2"), 1.2),
        steam_page_delay_seconds=_float(os.getenv("STEAM_PAGE_DELAY_SECONDS", "1.2"), 1.2),
        steam_max_retries=_int(os.getenv("STEAM_MAX_RETRIES", "3"), 3),
        steam_retry_base_seconds=_float(os.getenv("STEAM_RETRY_BASE_SECONDS", "1.5"), 1.5),
        steam_retry_max_seconds=_float(os.getenv("STEAM_RETRY_MAX_SECONDS", "15"), 15.0),
        steam_rate_limit_cooldown_seconds=_float(os.getenv("STEAM_RATE_LIMIT_COOLDOWN_SECONDS", "3600"), 3600.0),
        steam_forbidden_cooldown_seconds=_float(os.getenv("STEAM_FORBIDDEN_COOLDOWN_SECONDS", "86400"), 86400.0),
        steam_search_cache_ttl_seconds=_int(os.getenv("STEAM_SEARCH_CACHE_TTL_SECONDS", "2592000"), 2592000),
        steam_reviews_cache_ttl_seconds=_int(os.getenv("STEAM_REVIEWS_CACHE_TTL_SECONDS", "604800"), 604800),
        analysis_cache_ttl_seconds=_int(os.getenv("ANALYSIS_CACHE_TTL_SECONDS", "604800"), 604800),
        max_reviews_default=_int(os.getenv("MAX_REVIEWS_DEFAULT", "100"), 100),
        max_reviews_limit=_int(os.getenv("MAX_REVIEWS_LIMIT", "500"), 500),
        user_agent=os.getenv(
            "USER_AGENT",
            "game-product-analytics/0.2 (+https://github.com/Arkadiy915ggo/game-product-analytics)",
        ).strip(),
    )
