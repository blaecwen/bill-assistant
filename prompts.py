import logging

from langfuse import Langfuse

from config import settings

logger = logging.getLogger(__name__)

_FALLBACK_PROMPT = "You are a bill-splitting assistant."


class PromptManager:
    def __init__(self) -> None:
        self._client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        self._cache_ttl_seconds = settings.prompt_cache_ttl_minutes * 60

    def get_langfuse_prompt_object(self):
        return self._client.get_prompt(
            "bill-assistant",
            cache_ttl_seconds=self._cache_ttl_seconds,
            fallback=_FALLBACK_PROMPT,
        )


prompt_manager = PromptManager()
