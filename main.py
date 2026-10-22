#!/usr/bin/env python3
"""STM PRO BOT main entry point.

Uses the existing project modules when their public interfaces are available.
Configuration is read from `env` (the filename in this project) or `.env`.
No `config.py` dependency is required.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("stm_pro_bot")


def _load_env_file() -> None:
    """Load simple KEY=VALUE pairs from `env` or `.env` without extra deps."""
    for filename in ("env", ".env"):
        path = ROOT / filename
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if value and value[0:1] in ("\"", "'") and value[-1:] == value[0:1]:
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            LOG.exception("Could not read config file %s", path.name)
        # `env` is the project's stated config file; do not override existing OS vars.
        if filename == "env":
            return


_load_env_file()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()
FEEDBACK_ID = os.getenv("FEEDBACK_ID", os.getenv("FEEDBACK_USERNAME", "")).strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_IDS = {
    int(part.strip()) for part in os.getenv("ADMIN_IDS", "").replace(";", ",").split(",")
    if part.strip().lstrip("-").isdigit()
}
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", os.getenv("QUOTEX_LOGIN", "")).strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", os.getenv("QUOTEX_PASS", "")).strip()
QUOTEX_MODE = os.getenv("QUOTEX_MODE", "demo").strip().lower()

# Existing modules are optional at import time so a missing optional integration
# produces a clear log message rather than an obscure import traceback.
def _import_optional(name: str):
    try:
        return importlib.import_module(name)
    except Exception as exc:
        LOG.warning("Optional module %s could not be imported: %s", name, exc)
        return None

AI_MODULE = _import_optional("ai_strategy")
STM_MODULE = _import_optional("stm_strategy")
RANDOM_MODULE = _import_optional("random_strategy")
MARKET_MODULE = _import_optional("market_data")
SCANNER_MODULE = _import_optional("scanner")
DB_MODULE = _import_optional("database")

# State is deliberately in-memory; database integration is used if a compatible
# API exists, but the bot can still start without guessing its schema.
USER_STATE: dict[int, dict[str, Any]] = {}
AUTO_ENABLED = True
ACTIVE_SIGNALS: dict[str, dict[str, Any]] = {}

CATEGORIES = {
    "otc": "OTC",
    "regular": "Regular market",
}
STRATEGIES = {
    "ai": "AI Strategy",
    "stm": "STM Strategy",
    "random": "Random Strategy",
    "black4x": "BLACK4X",
    "opx4o": "OPX4O",
}
WARNINGS = [
    "⚠️ ট্রেড করার আগে মার্কেটের অবস্থা যাচাই করুন।",
    "⚠️ কোনো সিগন্যালই নিশ্চিত লাভের নিশ্চয়তা দেয় না।",
    "⚠️ ঝুঁকি বুঝে নিজের সিদ্ধান্তে ট্রেড করুন।",
    "⚠️ ক্ষতি পুষিয়ে নিতে তাড়াহুড়া করে ট্রেড করবেন না।",
    "⚠️ নির্ধারিত এন্ট্রি টাইম মেনে চলুন।",
    "⚠️ পেআউট ৮০% এর কম হলে সিগন্যাল দেওয়া হবে না।",
    "⚠️ প্রতি সিগন্যালে সর্বোচ্চ একটি MTG ধাপ।",
    "⚠️ ১ মিনিটের এক্সপায়ারি ব্যবহার করা হবে।",
    "⚠️ কানেকশন বা ডেটা না পেলে সিগন্যাল স্থগিত থাকবে।",
    "⚠️ ফলাফল যাচাই না করে WIN/LOSS ঘোষণা করা হবে না।",
]


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


def _admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _is_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not CHANNEL_ID:
        return True
    user = update.effective_user
    if not user:
        return False
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user.id)
        return member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as exc:
        LOG.warning("Channel membership check failed: %s", exc)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return
    if not await _is_member(update, context):
        rows = []
        if CHANNEL_URL:
            rows.append([("📢 Join Channel", "join_channel")])
        rows.append([("✅ Check Membership", "check_member")])
        await update.message.reply_text(
            "স্বাগতম STM PRO BOT-এ। প্রথমে আমাদের চ্যানেলে যোগ দিন, তারপর Membership যাচাই করুন।",
            reply_markup=_kb(rows),
        )
        return
    USER_STATE.setdefault(user.id, {})
    await update.message.reply_text(
        "স্বাগতম STM PRO BOT-এ!\n\nপ্রথমে মার্কেট ক্যাটাগরি বেছে নিন:",
        reply_markup=_kb([
            [("OTC", "cat:otc"), ("Regular Market", "cat:regular")],
            [("ℹ️ Help", "help")],
        ]),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "STM PRO BOT\n"
        "• ট্রেডের মেয়াদ: ১ মিনিট\n"
        "• সর্বোচ্চ MTG: ১\n"
        "• পেআউট ৮০% এর কম হলে সিগন্যাল বাদ\n"
        "• /status — বটের অবস্থা\n"
        "• /STMOP — অ্যাডমিনের জন্য সিগন্যাল বন্ধ\n"
        "• /STMUTO — অ্যাডমিনের জন্য সিগন্যাল চালু\n\n"
        "সিগন্যাল নিশ্চিত লাভ নয়। বাস্তব মার্কেট ডেটা বা ফলাফল যাচাই করা না গেলে বট সিগন্যাল/রেজাল্ট অনুমান করে প্রকাশ করবে না."
    )
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global AUTO_ENABLED
    await update.effective_message.reply_text(
        f"Signal scanner: {'ON' if AUTO_ENABLED else 'OFF'}\n"
        f"Quotex credentials configured: {'yes' if QUOTEX_EMAIL and QUOTEX_PASSWORD else 'no'}\n"
        f"Channel configured: {'yes' if CHANNEL_ID else 'no'}"
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global AUTO_ENABLED
    user = update.effective_user
    if not user or not _admin(user.id):
        await update.effective_message.reply_text("⛔ এই কমান্ড ব্যবহারের অনুমতি নেই।")
        return
    AUTO_ENABLED = False
    await update.effective_message.reply_text("🛑 STM PRO BOT সিগন্যাল বন্ধ করা হয়েছে।")


async def auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global AUTO_ENABLED
    user = update.effective_user
    if not user or not _admin(user.id):
        await update.effective_message.reply_text("⛔ এই কমান্ড ব্যবহারের অনুমতি নেই।")
        return
    AUTO_ENABLED = True
    await update.effective_message.reply_text("✅ STM PRO BOT সিগন্যাল চালু করা হয়েছে।")


def _call_candidate(module: Any, names: tuple[str, ...], *args, **kwargs):
    if module is None:
        return None
    for name in names:
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        try:
            return fn(*args, **kwargs)
        except TypeError:
            try:
                return fn(*args)
            except Exception:
                continue
        except Exception:
            LOG.exception("%s.%s failed", module.__name__, name)
            continue
    return None


def _normalize_signal(result: Any) -> dict[str, Any] | None:
    if isinstance(result, str):
        direction = result.strip().upper()
        if direction in {"CALL", "PUT", "BUY", "SELL", "UP", "DOWN"}:
            return {"direction": direction, "confidence": None}
        return None
    if not isinstance(result, dict):
        return None
    direction = str(result.get("direction", result.get("signal", result.get("action", "")))).strip().upper()
    aliases = {"UP": "CALL", "BUY": "CALL", "DOWN": "PUT", "SELL": "PUT"}
    direction = aliases.get(direction, direction)
    if direction not in {"CALL", "PUT"}:
        return None
    return {**result, "direction": direction}


def _strategy_result(strategy: str, candles: Any) -> dict[str, Any] | None:
    module = {
        "ai": AI_MODULE,
        "stm": STM_MODULE,
        "random": RANDOM_MODULE,
        "black4x": STM_MODULE,
        "opx4o": STM_MODULE,
    }.get(strategy)
    if strategy == "random":
        raw = _call_candidate(module, ("generate_signal", "analyze", "get_signal"), candles)
        if raw is None:
            raw = {"direction": random.choice(("CALL", "PUT")), "strategy": "Random Strategy"}
        return _normalize_signal(raw)
    if strategy in {"black4x", "opx4o"}:
        raw = _call_candidate(module, ("analyze", "generate_signal", "get_signal"), candles, strategy=strategy)
        if raw is None:
            raw = _call_candidate(module, ("analyze", "generate_signal", "get_signal"), candles)
        return _normalize_signal(raw)
    raw = _call_candidate(module, ("generate_signal", "analyze", "get_signal"), candles, strategy="pvip" if strategy == "stm" else None)
    if raw is None:
        raw = _call_candidate(module, ("generate_signal", "analyze", "get_signal"), candles)
    return _normalize_signal(raw)


def _get_candles(pair: str) -> Any:
    # Try only known-style candle fetchers. If unavailable, return None and do not
    # fabricate market data.
    for module in (SCANNER_MODULE, MARKET_MODULE):
        raw = _call_candidate(module, ("get_candles", "fetch_candles", "candles", "get_market_data", "get_data"), pair, 100)
        if raw is None:
            raw = _call_candidate(module, ("get_candles", "fetch_candles", "candles", "get_market_data", "get_data"), pair)
        if raw is not None:
            if isinstance(raw, dict):
                raw = raw.get("candles", raw.get("data", raw.get("result")))
            if raw is not None:
                return raw
    return None


def _get_payout(pair: str) -> float | None:
    for module in (SCANNER_MODULE, MARKET_MODULE):
        raw = _call_candidate(module, ("get_payout", "payout", "get_asset_payout", "asset_payout"), pair)
        if isinstance(raw, dict):
            raw = raw.get("payout", raw.get("value", raw.get("percent")))
        try:
            if raw is not None:
                value = float(raw)
                # Support either 0.8 or 80-style payout representation.
                return value * 100 if 0 < value <= 1 else value
        except (TypeError, ValueError):
            continue
    return None


def _safe_pair(text: str) -> str:
    return text.strip().upper().replace(" ", "")[:32]


async def _post_channel(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not CHANNEL_ID:
        return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, disable_web_page_preview=True)
    except Exception:
        LOG.exception("Failed to post to channel")


async def _issue_signal(update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str, strategy: str) -> None:
    user = update.effective_user
    if not user:
        return
    if not AUTO_ENABLED:
        await update.effective_message.reply_text("⏸️ বর্তমানে সিগন্যাল বন্ধ আছে।")
        return
    payout = _get_payout(pair)
    if payout is None:
        await update.effective_message.reply_text(
            "⚠️ এই pair-এর বাস্তব payout যাচাই করা যাচ্ছে না। তাই সিগন্যাল দেওয়া হয়নি।"
        )
        return
    if payout < 80:
        await update.effective_message.reply_text(f"⚠️ {pair} payout {payout:.0f}% — ৮০% এর কম, সিগন্যাল বাদ।")
        return
    candles = _get_candles(pair)
    if candles is None and strategy != "random":
        await update.effective_message.reply_text("⚠️ বাস্তব candle data পাওয়া যায়নি। সিগন্যাল দেওয়া হয়নি।")
        return
    result = _strategy_result(strategy, candles)
    if not result:
        await update.effective_message.reply_text("⚠️ নির্বাচিত strategy থেকে বৈধ CALL/PUT সিগন্যাল পাওয়া যায়নি।")
        return
    direction = result["direction"]
    strategy_name = STRATEGIES.get(strategy, strategy)
    now = datetime.now().astimezone()
    # Never claim the result is known at signal time; results require actual expiry candles.
    signal_id = f"{user.id}-{int(time.time())}"
    ACTIVE_SIGNALS[signal_id] = {
        "user_id": user.id, "pair": pair, "direction": direction,
        "strategy": strategy, "created_at": time.time(), "payout": payout,
    }
    message = (
        "🚨 STM PRO BOT SIGNAL 🚨\n\n"
        f"💹 Pair: {pair}\n"
        f"📊 Strategy: {strategy_name}\n"
        f"🎯 Direction: {'🟢 CALL' if direction == 'CALL' else '🔴 PUT'}\n"
        "⏱️ Expiry: 1 Minute\n"
        f"💰 Payout: {payout:.0f}%\n"
        f"🕒 Time: {now.strftime('%H:%M:%S')}\n"
        "♻️ MTG: 1 step maximum\n\n"
        "⚠️ এটি স্বয়ংক্রিয় লাভের নিশ্চয়তা নয়। ফলাফল বাস্তব candle দিয়ে যাচাই না হওয়া পর্যন্ত WIN/LOSS ঘোষণা করা হবে না।"
    )
    await update.effective_message.reply_text(message)
    await _post_channel(context, message)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = update.effective_user
    if not user:
        return
    data = query.data or ""
    if data == "join_channel":
        if CHANNEL_URL:
            await query.message.reply_text(f"চ্যানেলে যোগ দিন: {CHANNEL_URL}")
        return
    if data == "check_member":
        if await _is_member(update, context):
            await query.edit_message_text("✅ Membership verified. মার্কেট ক্যাটাগরি বেছে নিন:", reply_markup=_kb([
                [("OTC", "cat:otc"), ("Regular Market", "cat:regular")],
            ]))
        else:
            await query.edit_message_text("❌ চ্যানেলে যোগ দিয়ে আবার যাচাই করুন।", reply_markup=_kb([
                [("📢 Join Channel", "join_channel")], [("✅ Check Membership", "check_member")]
            ]))
        return
    if data == "help":
        await help_command(update, context)
        return
    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        USER_STATE.setdefault(user.id, {})["category"] = category
        USER_STATE[user.id]["awaiting_pair"] = True
        await query.edit_message_text(
            f"ক্যাটাগরি: {CATEGORIES.get(category, category)}\n\nQuotex pair-এর নাম লিখুন (যেমন EURUSD_otc বা EURUSD)।\n"
            "শুধু বাস্তব ডেটা ও payout যাচাই করা গেলে সিগন্যাল দেওয়া হবে।"
        )
        return
    if data.startswith("strategy:"):
        strategy = data.split(":", 1)[1]
        state = USER_STATE.setdefault(user.id, {})
        pair = state.get("pair")
        if not pair:
            await query.edit_message_text("আগে pair নির্বাচন করুন। /start চাপুন।")
            return
        state["strategy"] = strategy
        await _issue_signal(update, context, pair, strategy)
        return
    if data.startswith("pair:"):
        pair = _safe_pair(data.split(":", 1)[1])
        USER_STATE.setdefault(user.id, {})["pair"] = pair
        await query.edit_message_text(f"Pair: {pair}\nStrategy বেছে নিন:", reply_markup=_kb([
            [("AI", "strategy:ai"), ("STM", "strategy:stm")],
            [("Random", "strategy:random")],
            [("BLACK4X", "strategy:black4x"), ("OPX4O", "strategy:opx4o")],
        ]))
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return
    state = USER_STATE.setdefault(user.id, {})
    if state.get("awaiting_pair"):
        pair = _safe_pair(message.text)
        if not pair or len(pair) < 3 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/" for ch in pair):
            await message.reply_text("সঠিক pair লিখুন, যেমন EURUSD_otc।")
            return
        state["pair"] = pair
        state["awaiting_pair"] = False
        await message.reply_text(f"Pair: {pair}\nStrategy বেছে নিন:", reply_markup=_kb([
            [("AI", "strategy:ai"), ("STM", "strategy:stm")],
            [("Random", "strategy:random")],
            [("BLACK4X", "strategy:black4x"), ("OPX4O", "strategy:opx4o")],
        ]))
        return
    await message.reply_text("মেনু খুলতে /start অথবা সাহায্যের জন্য /help লিখুন।")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.error("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ একটি অভ্যন্তরীণ সমস্যা হয়েছে। লগে বিস্তারিত আছে।")
        except Exception:
            pass


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN পাওয়া যায়নি। ~/ready/env ফাইলে BOT_TOKEN=আপনার_টোকেন দিন।"
        )
    # PTB 20+ is expected by this project. Explicit loop setup helps older PTB
    # releases that call asyncio.get_event_loop() under Python 3.14.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("STMOP", stop_command))
    app.add_handler(CommandHandler("STMUTO", auto_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)
    LOG.info("Starting STM PRO BOT polling")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    main()
