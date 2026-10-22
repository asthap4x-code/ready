import asyncio
import logging
import signal
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from config import settings
from storage import Store
from telegram_ui import (
    start, on_callback, stop_command, auto_command, status_command,
    help_command, error_handler
)
from engine import SignalEngine
from quotex_adapter import QuotexAdapter

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("stmpro")


async def post_to_channel(application, text: str):
    if not settings.channel_id:
        log.warning("CHANNEL_ID is not configured; channel post skipped.")
        return
    await application.bot.send_message(
        chat_id=settings.channel_id, text=text, parse_mode="HTML",
        disable_web_page_preview=True
    )


async def post_to_user(application, user_id: int, text: str):
    await application.bot.send_message(
        chat_id=user_id, text=text, parse_mode="HTML",
        disable_web_page_preview=True
    )


async def post_result(application, signal_record: dict, result: dict):
    text = result.get("text", "Result unavailable; no outcome has been assumed.")
    # Channel result is always attempted; user result is sent to the user who requested it.
    await post_to_channel(application, text)
    user_id = signal_record.get("user_id")
    if user_id:
        try:
            await post_to_user(application, int(user_id), text)
        except Exception:
            log.exception("Could not send result to user %s", user_id)


async def post_signal(application, signal_record: dict):
    text = signal_record["text"]
    await post_to_channel(application, text)
    user_id = signal_record.get("user_id")
    if user_id:
        await post_to_user(application, int(user_id), text)


async def post_status(application, text: str):
    await post_to_channel(application, text)


async def post_session_summary(application, rows: list[dict]):
    if not rows:
        return
    text = Store.format_session_summary(rows)
    await post_to_channel(application, text)


async def post_to_admins(application, text: str):
    for admin_id in settings.admin_ids:
        try:
            await application.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            log.exception("Could not notify admin %s", admin_id)


async def post_feedback_request(application, signal_record: dict):
    feedback = settings.feedback_id or settings.admin_username or "Admin"
    text = (
        "🌟 <b>আপনার মতামত আমাদের উন্নত হতে সাহায্য করে!</b>\n"
        f"Feedback: {feedback}\n"
        "অনুগ্রহ করে বাস্তব ফলাফল ও আপনার অভিজ্ঞতা জানান।"
    )
    user_id = signal_record.get("user_id")
    if user_id:
        try:
            await post_to_user(application, int(user_id), text)
        except Exception:
            pass


async def after_startup(application: Application):
    store: Store = application.bot_data["store"]
    adapter: QuotexAdapter = application.bot_data["adapter"]
    engine = SignalEngine(
        settings=settings, store=store, adapter=adapter,
        signal_callback=lambda rec: post_signal(application, rec),
        result_callback=lambda rec, res: post_result(application, rec, res),
        status_callback=lambda txt: post_status(application, txt),
        admin_callback=lambda txt: post_to_admins(application, txt),
        feedback_callback=lambda rec: post_feedback_request(application, rec),
        summary_callback=lambda rows: post_session_summary(application, rows),
    )
    application.bot_data["engine"] = engine
    await engine.start()
    log.info("STM PRO BOT started.")


async def after_shutdown(application: Application):
    engine = application.bot_data.get("engine")
    if engine:
        await engine.stop()
    adapter = application.bot_data.get("adapter")
    if adapter:
        await adapter.close()
    store = application.bot_data.get("store")
    if store:
        store.close()


def main():
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is required. Copy .env.example to .env and configure it.")
    store = Store(settings.database_path)
    adapter = QuotexAdapter(settings)
    app = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(after_startup)
        .post_shutdown(after_shutdown)
        .build()
    )
    app.bot_data["store"] = store
    app.bot_data["adapter"] = adapter

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("STMOP", stop_command))
    app.add_handler(CommandHandler("STMUTO", auto_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_callback))
    app.add_error_handler(error_handler)

    log.info("Starting Telegram polling.")
    # Python 3.14 no longer implicitly creates a current event loop.
    # python-telegram-bot's run_polling() expects one to exist.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
