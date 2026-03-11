import asyncio
import logging

import uvicorn
from langfuse import get_client

from api import build_fastapi_app
from bot import build_telegram_app
from config import settings
from state import PhotoStore, RateLimiter

logger = logging.getLogger(__name__)


class _SuppressHealthCheck(logging.Filter):
    """Drop GET /health access log records from uvicorn — too noisy at INFO."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()


async def _run(telegram_app, fastapi_app) -> None:
    uvicorn_config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(uvicorn_config)

    async with telegram_app:
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot and API server ready")

        await server.serve()

        logger.info("Shutting down bot...")
        await telegram_app.updater.stop()
        await telegram_app.stop()


def main() -> None:
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("uvicorn.access").addFilter(_SuppressHealthCheck())
    logging.getLogger("langfuse").setLevel(log_level)

    photo_store = PhotoStore(
        ttl_minutes=settings.photo_ttl_minutes,
        retain_days=settings.photo_retain_days,
    )
    rate_limiter = RateLimiter(daily_limit=settings.daily_request_limit)

    logger.info(
        "Starting bill-assistant | model=%s port=8000 "
        "photo_ttl=%dm retain=%dd rate_limit=%d/day prompt_cache=%dm log_level=%s",
        settings.llm_model,
        settings.photo_ttl_minutes,
        settings.photo_retain_days,
        settings.daily_request_limit,
        settings.prompt_cache_ttl_minutes,
        settings.log_level,
    )

    langfuse = get_client()
    if langfuse.auth_check():
        logger.info("Langfuse connected ok")
    else:
        logger.warning("Langfuse auth failed — tracing will be disabled")

    telegram_app = build_telegram_app(photo_store, rate_limiter)
    fastapi_app = build_fastapi_app(photo_store, rate_limiter)

    try:
        asyncio.run(_run(telegram_app, fastapi_app))
    finally:
        logger.info("Flushing Langfuse traces...")
        get_client().flush()


if __name__ == "__main__":
    main()
