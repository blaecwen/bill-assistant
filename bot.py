import asyncio
import logging
from contextlib import asynccontextmanager

from telegram import Update, Bot
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings
from core import BillResponse, process_message
from state import PhotoStore, RateLimiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _typing(bot: Bot, chat_id: str):
    """Keep the Telegram 'typing…' indicator alive for the duration of a block."""

    async def _loop() -> None:
        try:
            while True:
                try:
                    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception:
                    pass  # don't let a transient API error kill the task
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


_HELP_TEXT = (
    "I help you split bills!\n\n"
    "1. Send me a photo of your bill\n"
    "2. Then tell me what to do, for example:\n"
    "   • Split for 3 people\n"
    "   • Alice had the pasta, Bob had the steak\n"
    "   • What's the total?\n\n"
    "Tip: Add your request as a caption to the photo to skip a step!"
)


def build_telegram_app(photo_store: PhotoStore, rate_limiter: RateLimiter) -> Application:
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(_HELP_TEXT)

    async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        msg = update.message
        logger.info(
            "Incoming photo",
            extra={"session_id": chat_id, "has_caption": bool(msg.caption)},
        )

        photo_file = await context.bot.get_file(msg.photo[-1].file_id)
        photo_bytes = bytes(await photo_file.download_as_bytearray())

        caption = msg.caption

        async with _typing(context.bot, chat_id):
            response: BillResponse = await process_message(
                session_id=chat_id,
                photo_store=photo_store,
                rate_limiter=rate_limiter,
                photo=photo_bytes,
                request=caption,
                request_type="text" if caption else None,
            )
        await update.message.reply_text(response.text, parse_mode=ParseMode.HTML)

    async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        logger.info("Incoming voice message", extra={"session_id": chat_id})

        voice_file = await context.bot.get_file(update.message.voice.file_id)
        voice_bytes = bytes(await voice_file.download_as_bytearray())

        async with _typing(context.bot, chat_id):
            response: BillResponse = await process_message(
                session_id=chat_id,
                photo_store=photo_store,
                rate_limiter=rate_limiter,
                request=voice_bytes,
                request_type="audio",
                audio_format="ogg",
            )
        await update.message.reply_text(response.text, parse_mode=ParseMode.HTML)

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        text = update.message.text
        logger.info(
            "Incoming text message: %s",
            text[:100],
            extra={"session_id": chat_id, "length": len(text)},
        )

        async with _typing(context.bot, chat_id):
            response: BillResponse = await process_message(
                session_id=chat_id,
                photo_store=photo_store,
                rate_limiter=rate_limiter,
                request=text,
                request_type="text",
            )
        await update.message.reply_text(response.text, parse_mode=ParseMode.HTML)

    async def cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        count = photo_store.cleanup_expired()
        if count > 0:
            logger.info("Cleanup job: deleted %d expired photo(s)", count)

    app: Application = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.job_queue.run_repeating(cleanup_job, interval=3600, first=10)

    return app
