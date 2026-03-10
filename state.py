import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

logger = logging.getLogger(__name__)


_HISTORY_LIMIT = 10


@dataclass
class HistoryEntry:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class StoredPhoto:
    data: bytes
    stored_at: datetime  # UTC
    size_bytes: int
    chat_id: str


@dataclass
class ChatState:
    photo: Optional[StoredPhoto] = None
    pending_request: Optional[str] = None
    awaiting_stale_confirmation: bool = False
    history: list[HistoryEntry] = field(default_factory=list)


class PhotoStore:
    def __init__(self, ttl_minutes: int, retain_days: int) -> None:
        self._ttl_minutes = ttl_minutes
        self._retain_days = retain_days
        self._states: dict[str, ChatState] = {}

    def _get_state(self, chat_id: str) -> ChatState:
        if chat_id not in self._states:
            self._states[chat_id] = ChatState()
        return self._states[chat_id]

    def store_photo(self, chat_id: str, data: bytes) -> None:
        now = datetime.now(timezone.utc)
        size = len(data)
        state = self._get_state(chat_id)
        state.photo = StoredPhoto(
            data=data,
            stored_at=now,
            size_bytes=size,
            chat_id=chat_id,
        )
        state.pending_request = None
        state.awaiting_stale_confirmation = False
        logger.info(
            "Photo stored",
            extra={"chat_id": chat_id, "timestamp": now.isoformat(), "size_bytes": size},
        )

    def get_photo(self, chat_id: str) -> Optional[StoredPhoto]:
        return self._get_state(chat_id).photo

    def _photo_age_minutes(self, chat_id: str) -> Optional[float]:
        photo = self._get_state(chat_id).photo
        if photo is None:
            return None
        delta = datetime.now(timezone.utc) - photo.stored_at
        return delta.total_seconds() / 60

    def is_photo_fresh(self, chat_id: str) -> bool:
        age = self._photo_age_minutes(chat_id)
        return age is not None and age < self._ttl_minutes

    def is_photo_stale(self, chat_id: str) -> bool:
        age = self._photo_age_minutes(chat_id)
        if age is None:
            return False
        return age >= self._ttl_minutes and age < self._retain_days * 24 * 60

    def reset_photo_ttl(self, chat_id: str) -> None:
        state = self._get_state(chat_id)
        if state.photo is not None:
            logger.warning(
                "Stale photo reused",
                extra={"chat_id": chat_id},
            )
            state.photo.stored_at = datetime.now(timezone.utc)

    def delete_photo(self, chat_id: str) -> None:
        state = self._get_state(chat_id)
        state.photo = None

    def set_pending_request(self, chat_id: str, request: str) -> None:
        self._get_state(chat_id).pending_request = request

    def get_pending_request(self, chat_id: str) -> Optional[str]:
        return self._get_state(chat_id).pending_request

    def set_awaiting_stale_confirmation(self, chat_id: str, val: bool) -> None:
        self._get_state(chat_id).awaiting_stale_confirmation = val

    def is_awaiting_stale_confirmation(self, chat_id: str) -> bool:
        return self._get_state(chat_id).awaiting_stale_confirmation

    def clear_state(self, chat_id: str) -> None:
        state = self._get_state(chat_id)
        state.pending_request = None
        state.awaiting_stale_confirmation = False

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    def add_to_history(
        self, chat_id: str, role: Literal["user", "assistant"], content: str
    ) -> None:
        state = self._get_state(chat_id)
        state.history.append(HistoryEntry(role=role, content=content))
        if len(state.history) > _HISTORY_LIMIT:
            state.history = state.history[-_HISTORY_LIMIT:]

    def get_history(self, chat_id: str) -> list[HistoryEntry]:
        return list(self._get_state(chat_id).history)

    def cleanup_expired(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retain_days)
        to_delete = [
            chat_id
            for chat_id, state in self._states.items()
            if state.photo is not None and state.photo.stored_at < cutoff
        ]
        for chat_id in to_delete:
            state = self._states[chat_id]
            age_days = (
                datetime.now(timezone.utc) - state.photo.stored_at
            ).days if state.photo else 0
            logger.info(
                "Hard-deleting expired photo",
                extra={"chat_id": chat_id, "age_days": age_days},
            )
            self.delete_photo(chat_id)
        return len(to_delete)


class RateLimiter:
    def __init__(self, daily_limit: int) -> None:
        self._limit = daily_limit
        self._count = 0
        self._reset_date = datetime.now(timezone.utc).date()
        self._lock = asyncio.Lock()

    async def check_and_increment(self) -> bool:
        async with self._lock:
            today = datetime.now(timezone.utc).date()
            if today > self._reset_date:
                logger.info("Daily rate limit counter reset at midnight UTC")
                self._count = 0
                self._reset_date = today
            if self._count >= self._limit:
                return False
            self._count += 1
            return True

    @property
    def current_count(self) -> int:
        return self._count
