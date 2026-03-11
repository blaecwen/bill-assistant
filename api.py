import logging
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from core import process_message
from state import PhotoStore, RateLimiter

logger = logging.getLogger(__name__)

# Maps MIME types to format strings passed to the LLM layer.
# Chrome/Firefox/Android send audio/webm; Safari/iOS send audio/mp4.
_MIME_TO_FORMAT: dict[str, str] = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}


def _infer_audio_format(content_type: Optional[str]) -> str:
    if not content_type:
        return "wav"
    mime = content_type.split(";")[0].strip().lower()
    return _MIME_TO_FORMAT.get(mime) or mime.split("/")[-1] or "wav"


def build_fastapi_app(photo_store: PhotoStore, rate_limiter: RateLimiter) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return Response(status_code=200)

    @app.post("/api/process")
    async def api_process(
        session_id: Optional[str] = Form(None),
        user_id: Optional[str] = Form(None),
        photo: Optional[UploadFile] = File(None),
        audio: Optional[UploadFile] = File(None),
    ):
        if not session_id:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "session_id is required"},
            )

        if audio is None:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "audio is required"},
            )

        photo_bytes = await photo.read() if photo else None

        audio_bytes: Optional[bytes] = None
        audio_format: Optional[str] = None
        if audio is not None:
            audio_bytes = await audio.read()
            audio_format = _infer_audio_format(audio.content_type)

        try:
            response = await process_message(
                session_id=session_id,
                photo_store=photo_store,
                rate_limiter=rate_limiter,
                photo=photo_bytes,
                request=audio_bytes,
                request_type="audio" if audio_bytes is not None else None,
                audio_format=audio_format,
                skip_stale_check=True,
                source="web",
                user_id=user_id,
            )
        except Exception as exc:
            logger.error("Unexpected error in /api/process: %s", exc)
            return JSONResponse(status_code=500, content={"error": "server_error"})

        if response.rate_limited:
            return JSONResponse(status_code=429, content={"error": "daily_limit_reached"})

        if response.llm_error:
            return JSONResponse(status_code=500, content={"error": "server_error"})

        return JSONResponse(content={
            "text": response.text,
            "request_summary": response.request_summary,
        })

    return app
