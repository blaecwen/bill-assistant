import logging

from telegram import Update
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

photo_store = PhotoStore(
    ttl_minutes=settings.photo_ttl_minutes,
    retain_days=settings.photo_retain_days,
)
rate_limiter = RateLimiter(daily_limit=settings.daily_request_limit)

_HELP_TEXT = (
    "I help you split bills!\n\n"
    "1. Send me a photo of your bill\n"
    "2. Then tell me what to do, for example:\n"
    "   • Split for 3 people\n"
    "   • Alice had the pasta, Bob had the steak\n"
    "   • What's the total?\n\n"
    "Tip: Add your request as a caption to the photo to skip a step!\n\n"
    "Your photo is remembered for 30 minutes."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_HELP_TEXT)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    msg = update.message
    logger.info(
        "Incoming photo",
        extra={"chat_id": chat_id, "has_caption": bool(msg.caption)},
    )

    photo_file = await context.bot.get_file(msg.photo[-1].file_id)
    photo_bytes = bytes(await photo_file.download_as_bytearray())

    caption = msg.caption

    response: BillResponse = await process_message(
        chat_id=chat_id,
        photo_store=photo_store,
        rate_limiter=rate_limiter,
        photo=photo_bytes,
        request=caption,
        request_type="text" if caption else None,
    )
    await update.message.reply_text(response.text)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    logger.info("Incoming voice message", extra={"chat_id": chat_id})

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    voice_bytes = bytes(await voice_file.download_as_bytearray())

    response: BillResponse = await process_message(
        chat_id=chat_id,
        photo_store=photo_store,
        rate_limiter=rate_limiter,
        request=voice_bytes,
        request_type="audio",
        audio_format="ogg",
    )
    await update.message.reply_text(response.text)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text
    logger.info(
        "Incoming text message",
        extra={"chat_id": chat_id, "length": len(text)},
    )

    response: BillResponse = await process_message(
        chat_id=chat_id,
        photo_store=photo_store,
        rate_limiter=rate_limiter,
        request=text,
        request_type="text",
    )
    await update.message.reply_text(response.text)


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    count = photo_store.cleanup_expired()
    if count > 0:
        logger.info("Cleanup job: deleted %d expired photo(s)", count)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app: Application = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.job_queue.run_repeating(cleanup_job, interval=3600, first=10)

    logger.info("Bot starting with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
