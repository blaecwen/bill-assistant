import base64
import logging
import time
from typing import Optional

from langfuse import get_client, observe
from langfuse.openai import AsyncOpenAI

from config import settings
from prompts import prompt_manager
from state import HistoryEntry

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


_client = AsyncOpenAI(
    api_key=settings.openrouter_api_key,
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://split-my-bill.zorz.io/",
        "X-Title": "Split My Bill",
    },
)


def _build_user_content(
    request_text: Optional[str],
    photo_bytes: bytes,
    audio_bytes: Optional[bytes],
    audio_format: str,
) -> list[dict]:
    content: list[dict] = []

    content.append({"type": "text", "text": request_text or "Please analyze this bill."})

    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"},
    })

    if audio_bytes is not None:
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        content.append({
            "type": "input_audio",
            "input_audio": {"data": audio_b64, "format": audio_format},
        })

    return content


@observe(name="bill-split")
async def call_llm(
    photo_bytes: bytes,
    request_text: Optional[str] = None,
    audio_bytes: Optional[bytes] = None,
    audio_format: str = "ogg",
    history: Optional[list[HistoryEntry]] = None,
    session_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    user_id: Optional[str] = None,
) -> str:
    get_client().update_current_trace(
        session_id=session_id,
        user_id=user_id,
        tags=tags or [],
    )

    langfuse_prompt = prompt_manager.get_langfuse_prompt_object()
    system_prompt_text = langfuse_prompt.compile()

    messages: list[dict] = [{"role": "system", "content": system_prompt_text}]

    for entry in (history or []):
        messages.append({"role": entry.role, "content": entry.content})

    messages.append({
        "role": "user",
        "content": _build_user_content(
            request_text, photo_bytes, audio_bytes, audio_format
        ),
    })

    logger.debug(
        "LLM request model=%s has_audio=%s: %s",
        settings.llm_model,
        audio_bytes is not None,
        (request_text or "")[:100],
    )

    start = time.monotonic()
    try:
        response = await _client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            langfuse_prompt=langfuse_prompt,
        )
    except Exception as exc:
        logger.error("LLM API call failed: %s", exc)
        raise LLMError(str(exc)) from exc

    latency_ms = (time.monotonic() - start) * 1000
    result = response.choices[0].message.content
    if not result:
        raise LLMError("Model returned empty response")
    logger.info(
        "LLM call complete model=%s latency_ms=%d: %s",
        settings.llm_model,
        round(latency_ms),
        (request_text or "")[:100],
    )
    logger.debug("LLM response: %s", result)
    return result
