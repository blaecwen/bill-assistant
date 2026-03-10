import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    openrouter_api_key: str
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str
    llm_model: str
    photo_ttl_minutes: int
    photo_retain_days: int
    prompt_cache_ttl_minutes: int
    log_level: str
    daily_request_limit: int


def load_config() -> Config:
    def require(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise ValueError(f"Required environment variable {name!r} is not set")
        return value

    return Config(
        telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
        openrouter_api_key=require("OPENROUTER_API_KEY"),
        langfuse_public_key=require("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=require("LANGFUSE_SECRET_KEY"),
        langfuse_host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        llm_model=os.getenv("LLM_MODEL", "google/gemini-2.5-flash"),
        photo_ttl_minutes=int(os.getenv("PHOTO_TTL_MINUTES", "30")),
        photo_retain_days=int(os.getenv("PHOTO_RETAIN_DAYS", "7")),
        prompt_cache_ttl_minutes=int(os.getenv("PROMPT_CACHE_TTL_MINUTES", "10")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        daily_request_limit=int(os.getenv("DAILY_REQUEST_LIMIT", "100")),
    )


settings = load_config()
