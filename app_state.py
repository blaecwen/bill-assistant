"""
Shared application state — singleton instances used by all interface layers
(Telegram bot, web API server). Importing this module from multiple places
within the same process gives them access to the same objects.
"""
from config import settings
from state import PhotoStore, RateLimiter

photo_store = PhotoStore(
    ttl_minutes=settings.photo_ttl_minutes,
    retain_days=settings.photo_retain_days,
)
rate_limiter = RateLimiter(daily_limit=settings.daily_request_limit)
