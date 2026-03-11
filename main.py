import asyncio
import logging

import uvicorn

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
        logger.info("Bot polling started")

        await server.serve()

        logger.info("Shutting down bot...")
        await telegram_app.updater.stop()
        await telegram_app.stop()


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("uvicorn.access").addFilter(_SuppressHealthCheck())

    photo_store = PhotoStore(
        ttl_minutes=settings.photo_ttl_minutes,
        retain_days=settings.photo_retain_days,
    )
    rate_limiter = RateLimiter(daily_limit=settings.daily_request_limit)

    telegram_app = build_telegram_app(photo_store, rate_limiter)
    fastapi_app = build_fastapi_app(photo_store, rate_limiter)

    logger.info("Starting bot and API server...")
    asyncio.run(_run(telegram_app, fastapi_app))


if __name__ == "__main__":
    main()
