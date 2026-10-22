#!/usr/bin/env python3
"""STM PRO BOT - main entry point.

Designed for the existing project layout:
  ai_strategy.py, database.py, market_data.py, random_strategy.py,
  scanner.py, stm_strategy.py, requirements.txt and env/.env.

The adapter intentionally fails closed: it never invents payout, candles,
prices or outcomes. Real Quotex access depends on the installed connector and
its available methods.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("stm_pro_bot")
PAGE_SIZE = 8
EXPIRY_SECONDS = 60
MIN_PAYOUT = 80.0
MAX_MTG = 1

# ---------------------------------------------------------------------------
# Configuration: support the user's existing `env` file as well as `.env`.
# ---------------------------------------------------------------------------
def load_env() -> None:
    for filename in ("env", ".env"):
        path = ROOT / filename
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key:
                    os.environ.setdefault(key, value)
        except OSError:
            LOG.exception("Could not read %s", filename)
        if filename == "env":
            return


load_env()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", os.getenv("JOIN_CHANNEL_URL", "")).strip()
FEEDBACK_ID = os.getenv("FEEDBACK_ID", os.getenv("FEEDBACK_USERNAME", "")).strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").replace(";", ",").split(",")
    if x.strip().lstrip("-").isdigit()
}
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL", os.getenv("QUOTEX_LOGIN", "")).strip()
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD", os.getenv("QUOTEX_PASS", "")).strip()
QUOTEX_MODE = os.getenv("QUOTEX_MODE", "demo").strip().lower()

# Exact user-specified markets (68 total). Quotex asset identifiers can differ;
# the adapter tries common OTC spellings but only accepts returned live data.
MARKETS: dict[str, list[str]] = {
    "forex": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "NZD/USD"],
    "commodities": ["UKBrent (OTC)", "Silver (OTC)", "USCrude (OTC)", "Gold (OTC)"],
    "crypto": [
        "Bitcoin (OTC)", "Ethereum Classic (OTC)", "Chainlink (OTC)", "Litecoin (OTC)",
        "Toncoin (OTC)", "Trump (OTC)", "Ripple (OTC)", "Zcash (OTC)",
        "Avalanche (OTC)", "Dash (OTC)", "Axie Infinity (OTC)", "Polkadot (OTC)",
        "Binance Coin (OTC)", "Ethereum (OTC)", "Cosmos (OTC)", "Bitcoin Cash (OTC)",
    ],
    "currency": [
        "USD/ARS (OTC)", "USD/BDT (OTC)", "USD/BRL (OTC)", "USD/CAD (OTC)",
        "USD/CHF (OTC)", "USD/COP (OTC)", "USD/DZD (OTC)", "USD/EGP (OTC)",
        "USD/IDR (OTC)", "USD/INR (OTC)", "USD/JPY (OTC)", "USD/MXN (OTC)",
        "USD/NGN (OTC)", "USD/PHP (OTC)", "USD/PKR (OTC)", "USD/ZAR (OTC)",
        "AUD/CAD (OTC)", "AUD/CHF (OTC)", "AUD/JPY (OTC)", "AUD/NZD (OTC)",
        "AUD/USD (OTC)", "CAD/CHF (OTC)", "CAD/JPY (OTC)", "CHF/JPY (OTC)",
        "EUR/AUD (OTC)", "EUR/CAD (OTC)", "EUR/CHF (OTC)", "EUR/GBP (OTC)",
        "EUR/JPY (OTC)", "EUR/NZD (OTC)", "EUR/USD (OTC)", "GBP/AUD (OTC)",
        "GBP/CAD (OTC)", "GBP/CHF (OTC)", "GBP/JPY (OTC)", "GBP/NZD (OTC)",
        "GBP/USD (OTC)", "NZD/CAD (OTC)", "NZD/CHF (OTC)", "NZD/JPY (OTC)",
        "NZD/USD (OTC)",
    ],
}
CATEGORY_NAMES = {
    "forex": "FOREX",
    "commodities": "COMMODITIES (কমোডিটি)",
    "crypto": "CRYPTO (ক্রিপ্টোকারেন্সি)",
    "currency": "CURRENCY (কারেন্সি পেয়ার)",
    "all": "SELECT ALL MARKETS",
}
STRATEGY_NAMES = {
    "ai": "AI STRATEGY",
    "stm": "STM STRATEGY",
    "random": "RANDOM STRATEGY",
    "black4x": "BLACK4X",
    "opx4o": "OPX4O",
}
STRATEGY_ORDER = ["ai", "stm", "random", "black4x", "opx4o"]
WARNINGS = [
    "MUST FOLLOW MONEY MANAGEMENT !!",
    "USE ONLY 2% OF YOU BALANCE !!",
    "TRY TO TAKE A SAFTEY MARGIN !!",
    "IF LOSS THEN USE ONLY 1 MTG !!",
    "IF MTG THEN NO NEED SAFTEY OK!!",
    "IF 2 SIGNAL LOSS IN A SEASON THEN OFF AND START 6 HOURS LATER!!",
    "ARE YOU AGREE, IF NOT ,THEN IGNORE !! STM PRO BOT SIGNAL!",
    "HAVE A SUCCESS WITH US!!",
    "WE WILL DO PROFIT INSHA ALLAH",
    "LET'S START WITH BISMILLAH!",
]

# State remains in memory for this process. The database module is optional and
# is used only if a compatible public function is found.
USERS: set[int] = set()
USER_STATE: dict[int, dict[str, Any]] = {}
AUTO_ENABLED = True
AUTO_BATCH_LIMIT = 10
AUTO_SENT = 0
LOSS_STREAK = 0
PAUSED_UNTIL = 0.0
SIGNAL_TASKS: set[asyncio.Task] = set()
AUTO_TASK: asyncio.Task | None = None


def optional_import(*names: str) -> Any | None:
    for name in names:
        try:
            return importlib.import_module(name)
        except Exception as exc:
            LOG.debug("Could not import %s: %s", name, exc)
    return None

AI_MOD = optional_import("ai_strategy", "AI_STRATEGY")
STM_MOD = optional_import("stm_strategy", "STM_STRATEGY")
RANDOM_MOD = optional_import("random_strategy", "RANDOM_STRATEGY")
SCANNER_MOD = optional_import("scanner")
MARKET_MOD = optional_import("market_data")
DATABASE_MOD = optional_import("database")
BLACK_MOD = optional_import("black4x", "BLACK4X")
OPX_MOD = optional_import("opx4o", "OPX4O")
QUOTEX_MOD = optional_import("quotex_adapter")


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])


def is_admin(user_id: int, username: str | None = None) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return bool(ADMIN_USERNAME and username and username.lstrip("@").lower() == ADMIN_USERNAME.lower())


async def maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def find_callable(modules: list[Any], names: tuple[str, ...]) -> tuple[Any, str] | tuple[None, None]:
    for module in modules:
        if module is None:
            continue
        for name in names:
            fn = getattr(module, name, None)
            if callable(fn):
                return module, name
    return None, None


async def invoke_first(
    modules: list[Any], names: tuple[str, ...], arg_sets: list[tuple[Any, ...]],
    kw_sets: list[dict[str, Any]] | None = None,
) -> Any:
    kw_sets = kw_sets or [{}]
    for module in modules:
        if module is None:
            continue
        for name in names:
            fn = getattr(module, name, None)
            if not callable(fn):
                continue
            for args in arg_sets:
                for kwargs in kw_sets:
                    try:
                        result = await maybe_await(fn(*args, **kwargs))
                        if result is not None:
                            return result
                    except TypeError:
                        continue
                    except Exception as exc:
                        LOG.debug("%s.%s failed: %s", getattr(module, "__name__", module), name, exc)
                        break
    return None


def asset_variants(pair: str) -> list[str]:
    p = pair.strip()
    base = p.replace(" (OTC)", "").strip()
    compact = base.replace("/", "").replace(" ", "")
    variants = [p, base, compact]
    if "OTC" in p.upper():
        variants.extend([f"{compact}_otc", f"{compact}-OTC", f"{base} OTC"])
    elif "/" in p:
        variants.extend([f"{compact}_otc", f"{base} (OTC)"])
    out: list[str] = []
    for item in variants:
        if item and item.lower() not in {x.lower() for x in out}:
            out.append(item)
    return out


async def get_candles(pair: str, count: int = 100) -> Any | None:
    modules = [QUOTEX_MOD, SCANNER_MOD, MARKET_MOD]
    names = ("get_candles", "fetch_candles", "get_candle_data", "candles", "get_market_data", "get_data")
    for asset in asset_variants(pair):
        result = await invoke_first(
            modules, names,
            [(asset, count), (asset, 60, count), (asset,), (asset, count, 60)],
            [{}, {"count": count}, {"period": 60, "count": count}, {"interval": 60, "count": count}],
        )
        if result is not None:
            if isinstance(result, dict):
                result = result.get("candles", result.get("data", result.get("result", result)))
            if result is not None:
                return result
    return None


async def get_payout(pair: str) -> float | None:
    modules = [QUOTEX_MOD, SCANNER_MOD, MARKET_MOD]
    names = ("get_payout", "get_asset_payout", "asset_payout", "payout", "get_profit", "get_profit_percent")
    for asset in asset_variants(pair):
        result = await invoke_first(modules, names, [(asset,), (asset, True)], [{}, {"asset": asset}])
        if isinstance(result, dict):
            result = result.get("payout", result.get("profit", result.get("percent", result.get("value"))))
        try:
            if result is not None:
                value = float(result)
                if 0 < value <= 1:
                    value *= 100
                if 0 < value <= 100:
                    return value
        except (ValueError, TypeError):
            pass
    return None


def candles_to_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("candles", raw.get("data", raw.get("result", [])))
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, (list, tuple)) and len(item) >= 5:
            # Common candle tuple: time, open, close, high, low or time, open, high, low, close.
            out.append({"time": item[0], "open": item[1], "close": item[2], "high": item[3], "low": item[4]})
    return out


def candle_close(candle: dict[str, Any]) -> float | None:
    for key in ("close", "close_price", "price", "c"):
        try:
            if candle.get(key) is not None:
                return float(candle[key])
        except (TypeError, ValueError):
            continue
    return None


def normalize_signal(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        direction = raw.strip().upper()
        if direction in {"CALL", "PUT", "BUY", "SELL", "UP", "DOWN"}:
            return {"direction": "CALL" if direction in {"CALL", "BUY", "UP"} else "PUT"}
        return None
    if not isinstance(raw, dict):
        return None
    direction = str(raw.get("direction", raw.get("signal", raw.get("action", raw.get("trend", ""))))).upper().strip()
    if direction in {"BUY", "UP", "CALL", "BULLISH", "LONG"}:
        direction = "CALL"
    elif direction in {"SELL", "DOWN", "PUT", "BEARISH", "SHORT"}:
        direction = "PUT"
    else:
        return None
    return {**raw, "direction": direction}


async def run_strategy(key: str, pair: str, candles: Any) -> dict[str, Any] | None:
    module_map = {
        "ai": [AI_MOD], "stm": [STM_MOD], "random": [RANDOM_MOD],
        "black4x": [BLACK_MOD, STM_MOD, SCANNER_MOD],
        "opx4o": [OPX_MOD, STM_MOD, SCANNER_MOD],
    }
    modules = module_map.get(key, [])
    if key == "random":
        raw = await invoke_first(modules, ("generate_signal", "get_signal", "analyze", "signal"),
                                 [(pair, candles), (candles,), (pair,), ()])
        normalized = normalize_signal(raw)
        if normalized:
            return normalized
        # RANDOM is explicitly allowed to choose direction without candle data.
        return {"direction": random.choice(("CALL", "PUT")), "strategy": "RANDOM STRATEGY"}
    names = ("generate_signal", "get_signal", "analyze", "get_strategy_signal", "signal", "run_strategy")
    kw_sets = [{"pair": pair, "candles": candles}, {"asset": pair, "candles": candles}, {"strategy": key}, {}]
    raw = await invoke_first(modules, names, [(pair, candles), (candles, pair), (candles,), (pair,), ()], kw_sets)
    result = normalize_signal(raw)
    if result:
        result.setdefault("strategy", STRATEGY_NAMES[key])
    return result


async def is_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not CHANNEL_ID:
        # Do not silently bypass the gate when channel membership is configured incorrectly.
        LOG.warning("CHANNEL_ID is empty; membership gate cannot be checked")
        return False
    user = update.effective_user
    if not user:
        return False
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user.id)
        return str(member.status).lower() in {"member", "administrator", "creator", "owner"}
    except Exception as exc:
        LOG.warning("Channel membership verification failed: %s", exc)
        return False


async def gate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows: list[list[tuple[str, str]]] = []
    if CHANNEL_URL:
        rows.append([("📢 Join Channel", "join")])
    rows.append([("✅ Verify Membership", "verify")])
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            "👋 স্বাগতম STM PRO BOT-এ।\n\nসিগন্যাল ব্যবহার করার আগে আমাদের Telegram Channel-এ Join করুন, তারপর Verify Membership চাপুন।",
            reply_markup=keyboard(rows),
        )


async def show_home(target: Any, user_id: int) -> None:
    USER_STATE.setdefault(user_id, {})
    await target.reply_text(
        "✅ Membership verified.\n\nমার্কেট ক্যাটাগরি বেছে নিন:",
        reply_markup=keyboard([
            [("📈 FOREX", "cat:forex"), ("🛢 COMMODITIES", "cat:commodities")],
            [("🪙 CRYPTO", "cat:crypto"), ("💱 CURRENCY", "cat:currency")],
            [("🌐 SELECT ALL", "cat:all")],
            [("ℹ️ HELP", "help")],
        ]),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    USERS.add(user.id)
    USER_STATE.setdefault(user.id, {"pairs": [], "strategies": []})
    if not await is_member(update, context):
        await gate_message(update, context)
        return
    await show_home(msg, user.id)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "STM PRO BOT — Help\n\n"
        "• Expiry: 1 minute (60 seconds)\n"
        "• Minimum verified payout: 80%\n"
        "• Maximum MTG: 1 step\n"
        "• Signals/results are posted to the bot and configured channel.\n"
        "• /status — connection/configuration status\n"
        "• /STMOP — admin: stop scanner/signals\n"
        "• /STMUTO — admin: choose a batch size and resume\n\n"
        "Live payout/candles and expiry prices must be returned by the installed market connector. If real data is unavailable, the bot skips the signal/result rather than fabricating it. Trading involves risk."
    )
    msg = update.effective_message
    if msg:
        await msg.reply_text(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connected = False
    if QUOTEX_EMAIL and QUOTEX_PASSWORD:
        result = await invoke_first([QUOTEX_MOD, SCANNER_MOD, MARKET_MOD],
                                    ("is_connected", "connected", "connection_status"), [(), (QUOTEX_EMAIL,)])
        connected = bool(result)
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            f"Scanner: {'ON' if AUTO_ENABLED else 'OFF'}\n"
            f"Pause timer: {'ACTIVE' if time.time() < PAUSED_UNTIL else 'inactive'}\n"
            f"Quotex credentials: {'configured' if QUOTEX_EMAIL and QUOTEX_PASSWORD else 'not configured'}\n"
            f"Connector reports connected: {'yes' if connected else 'not confirmed'}\n"
            f"Channel: {'configured' if CHANNEL_ID else 'NOT CONFIGURED'}\n"
            f"Selected mode: {QUOTEX_MODE}"
        )


async def post_channel(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not CHANNEL_ID:
        LOG.warning("Not posting to channel: CHANNEL_ID is empty")
        return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, disable_web_page_preview=True)
    except TelegramError:
        LOG.exception("Failed to post to configured channel")


async def broadcast_users(context: ContextTypes.DEFAULT_TYPE, text: str, exclude: int | None = None) -> None:
    # Users are added after /start. Membership remains required for signals.
    for uid in list(USERS):
        if uid == exclude:
            continue
        try:
            await context.bot.send_message(chat_id=uid, text=text, disable_web_page_preview=True)
        except TelegramError as exc:
            LOG.debug("Could not message user %s: %s", uid, exc)


async def send_warnings(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    for i, warning in enumerate(WARNINGS):
        try:
            msg = await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {warning}" if i < 7 else f"✨ {warning}")
            await asyncio.sleep(1.5)
            await msg.delete()
        except TelegramError:
            # Continue through all warnings even if deletion is disallowed.
            await asyncio.sleep(1.5)


def signal_message(pair: str, strategy: str, direction: str, payout: float, entry: float | None) -> str:
    now = datetime.now().astimezone().strftime("%H:%M:%S")
    entry_text = f"\n📍 Opening price: {entry}" if entry is not None else ""
    return (
        "🚨 STM PRO BOT SIGNAL 🚨\n\n"
        f"💹 Pair: {pair}\n📊 Strategy: {strategy}\n"
        f"🎯 Direction: {'🟢 CALL' if direction == 'CALL' else '🔴 PUT'}\n"
        f"⏱️ Expiry: 1 Minute\n💰 Verified payout: {payout:.0f}%"
        f"{entry_text}\n🕒 Signal time: {now}\n♻️ MTG: maximum 1 step\n\n"
        "⚠️ সিগন্যাল নিশ্চিত লাভের নিশ্চয়তা নয়।"
    )


async def result_after_expiry(context: ContextTypes.DEFAULT_TYPE, signal: dict[str, Any]) -> None:
    """Verify expiry using fresh live candles; do not guess missing results."""
    global LOSS_STREAK, PAUSED_UNTIL
    await asyncio.sleep(EXPIRY_SECONDS)
    raw = await get_candles(signal["pair"], 5)
    candles = candles_to_list(raw)
    if not candles:
        await post_channel(context, f"⚠️ RESULT PENDING\nPair: {signal['pair']}\nLive expiry candle পাওয়া যায়নি; ফলাফল ঘোষণা করা হয়নি।")
        return
    closing = candle_close(candles[-1])
    opening = signal.get("opening_price")
    if closing is None or opening is None:
        await post_channel(context, f"⚠️ RESULT PENDING\nPair: {signal['pair']}\nবাস্তব opening/closing price যাচাই করা যায়নি।")
        return
    direction = signal["direction"]
    win = closing > opening if direction == "CALL" else closing < opening
    if closing == opening:
        status = "DRAW / NO RESULT"
    else:
        status = "WIN" if win else "LOSS"
    text = (
        f"📊 STM PRO BOT RESULT\n\n💹 Pair: {signal['pair']}\n"
        f"📊 Strategy: {signal['strategy']}\n🎯 Direction: {direction}\n"
        f"📍 Opening price: {opening}\n🏁 Closing price: {closing}\n"
        f"📌 Result: {status}\n⏱️ Expiry: 1 Minute"
    )
    await post_channel(context, text)
    await broadcast_users(context, text)
    if status == "WIN":
        LOSS_STREAK = 0
    elif status == "LOSS":
        LOSS_STREAK += 1
        if LOSS_STREAK >= 2:
            PAUSED_UNTIL = time.time() + 6 * 60 * 60
            await post_channel(context, "⏸️ টানা ২টি সিগন্যাল LOSS হওয়ায় ৬ ঘণ্টার জন্য সিগন্যাল বিরতি দেওয়া হয়েছে।")


async def make_signal(context: ContextTypes.DEFAULT_TYPE, pair: str, strategy_keys: list[str], user_id: int | None = None) -> dict[str, Any] | None:
    if not AUTO_ENABLED or time.time() < PAUSED_UNTIL:
        return None
    payout = await get_payout(pair)
    if payout is None or payout < MIN_PAYOUT:
        LOG.info("Skip %s: payout unavailable or below %.0f%%", pair, MIN_PAYOUT)
        return None
    raw_candles = await get_candles(pair, 100)
    candles = candles_to_list(raw_candles)
    # No strategy other than RANDOM may signal without real candle data.
    for strategy in strategy_keys:
        if strategy != "random" and not candles:
            continue
        result = await run_strategy(strategy, pair, raw_candles)
        if not result:
            continue
        opening = candle_close(candles[-1]) if candles else None
        if opening is None and strategy != "random":
            continue
        signal = {
            "pair": pair, "strategy": STRATEGY_NAMES.get(strategy, strategy),
            "strategy_key": strategy, "direction": result["direction"],
            "payout": payout, "opening_price": opening, "created_at": time.time(),
        }
        text = signal_message(pair, signal["strategy"], signal["direction"], payout, opening)
        if user_id is not None:
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
            except TelegramError:
                LOG.exception("Could not deliver signal to requesting user")
        else:
            await broadcast_users(context, text)
        await post_channel(context, text)
        task = asyncio.create_task(result_after_expiry(context, signal))
        SIGNAL_TASKS.add(task)
        task.add_done_callback(SIGNAL_TASKS.discard)
        return signal
    return None


async def choose_strategy_screen(query: Any, user_id: int) -> None:
    state = USER_STATE.setdefault(user_id, {})
    selected = state.setdefault("strategies", [])
    rows = []
    for key in STRATEGY_ORDER:
        mark = "✅ " if key in selected else "▫️ "
        rows.append([(f"{mark}{STRATEGY_NAMES[key]}", f"stoggle:{key}")])
    rows.append([("☑️ SELECT ALL", "sall"), ("🗑 CLEAR", "sclear")])
    rows.append([("🚀 GET SIGNAL", "generate"), ("⬅️ MARKETS", "backmarkets")])
    await query.edit_message_text(
        "স্ট্র্যাটেজি নির্বাচন করুন। SELECT ALL দিলে তালিকার প্রথম যে স্ট্র্যাটেজি বৈধ সিগন্যাল দেবে সেটি ব্যবহার হবে।",
        reply_markup=keyboard(rows),
    )


async def show_pair_page(query: Any, user_id: int, page: int = 0) -> None:
    state = USER_STATE.setdefault(user_id, {})
    pairs = state.get("available_pairs", [])
    selected = set(state.setdefault("pairs", []))
    page_count = max(1, (len(pairs) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    state["pair_page"] = page
    rows: list[list[tuple[str, str]]] = []
    chunk = pairs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    for i in range(0, len(chunk), 2):
        row = []
        for pair in chunk[i:i + 2]:
            idx = pairs.index(pair)
            row.append((("✅ " if pair in selected else "▫️ ") + pair, f"pt:{idx}"))
        rows.append(row)
    rows.append([("☑️ SELECT ALL PAIRS", "pall"), ("🗑 CLEAR", "pclear")])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"pp:{page-1}"))
    nav.append((f"Page {page+1}/{page_count}", "noop"))
    if page + 1 < page_count:
        nav.append(("➡️", f"pp:{page+1}"))
    rows.append(nav)
    rows.append([("➡️ CONTINUE TO STRATEGY", "pdone")])
    await query.edit_message_text(
        f"{state.get('category_name', 'MARKET')} — pair নির্বাচন করুন।\nSelected: {len(selected)}",
        reply_markup=keyboard(rows),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()
    data = query.data or ""
    if data == "join":
        if CHANNEL_URL:
            await query.message.reply_text(f"চ্যানেলে যোগ দিতে খুলুন: {CHANNEL_URL}")
        else:
            await query.message.reply_text("CHANNEL_URL env ফাইলে সেট করা নেই।")
        return
    if data == "verify":
        if await is_member(update, context):
            await show_home(query.message, user.id)
        else:
            rows = []
            if CHANNEL_URL:
                rows.append([("📢 Join Channel", "join")])
            rows.append([("✅ Verify Membership", "verify")])
            await query.edit_message_text("❌ Membership পাওয়া যায়নি। চ্যানেলে Join করে আবার Verify করুন।", reply_markup=keyboard(rows))
        return
    if data == "help":
        await query.edit_message_text(
            "STM PRO BOT\n• 1-minute expiry\n• payout ≥80%\n• maximum 1 MTG\n• Live data না থাকলে fake signal/result নয়.\n\n/start — Main menu\n/status — Status"
        )
        return
    # All signal/menu actions require channel membership.
    if data not in {"noop"} and not await is_member(update, context):
        await gate_message(update, context)
        return
    state = USER_STATE.setdefault(user.id, {"pairs": [], "strategies": []})
    if data.startswith("cat:"):
        key = data.split(":", 1)[1]
        if key == "all":
            pairs = [pair for category in MARKETS.values() for pair in category]
            state["category_name"] = CATEGORY_NAMES["all"]
        else:
            pairs = MARKETS.get(key, [])
            state["category_name"] = CATEGORY_NAMES.get(key, key)
        state["category"] = key
        state["available_pairs"] = list(pairs)
        state["pairs"] = []
        state["strategies"] = []
        await show_pair_page(query, user.id, 0)
        return
    if data.startswith("pp:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        await show_pair_page(query, user.id, page)
        return
    if data.startswith("pt:"):
        try:
            pair = state.get("available_pairs", [])[int(data.split(":", 1)[1])]
        except (ValueError, IndexError):
            await query.answer("Pair selection invalid; /start দিয়ে আবার চেষ্টা করুন।", show_alert=True)
            return
        selected = state.setdefault("pairs", [])
        if pair in selected:
            selected.remove(pair)
        else:
            selected.append(pair)
        await show_pair_page(query, user.id, state.get("pair_page", 0))
        return
    if data == "pall":
        state["pairs"] = list(state.get("available_pairs", []))
        await show_pair_page(query, user.id, state.get("pair_page", 0))
        return
    if data == "pclear":
        state["pairs"] = []
        await show_pair_page(query, user.id, state.get("pair_page", 0))
        return
    if data == "pdone":
        if not state.get("pairs"):
            await query.answer("কমপক্ষে একটি pair নির্বাচন করুন।", show_alert=True)
            return
        await choose_strategy_screen(query, user.id)
        return
    if data.startswith("stoggle:"):
        key = data.split(":", 1)[1]
        if key in STRATEGY_ORDER:
            chosen = state.setdefault("strategies", [])
            if key in chosen:
                chosen.remove(key)
            else:
                chosen.append(key)
        await choose_strategy_screen(query, user.id)
        return
    if data == "sall":
        state["strategies"] = list(STRATEGY_ORDER)
        await choose_strategy_screen(query, user.id)
        return
    if data == "sclear":
        state["strategies"] = []
        await choose_strategy_screen(query, user.id)
        return
    if data == "backmarkets":
        await show_home(query.message, user.id)
        return
    if data == "generate":
        if not state.get("pairs") or not state.get("strategies"):
            await query.answer("Pair এবং strategy দুটোই নির্বাচন করুন।", show_alert=True)
            return
        await query.edit_message_text("⚠️ Risk warning দেখানো হচ্ছে; প্রতিটি ১.৫ সেকেন্ড পর সরানো হবে…")
        await send_warnings(context, query.message.chat_id)
        sent = 0
        for pair in state["pairs"]:
            signal = await make_signal(context, pair, state["strategies"], user_id=user.id)
            if signal:
                sent += 1
                # A manual request does not issue unlimited signals at once.
                break
        if sent:
            await context.bot.send_message(chat_id=user.id, text="✅ যোগ্যতা পূরণকারী সিগন্যাল পাঠানো হয়েছে। ফলাফল বাস্তব expiry data দিয়ে যাচাই করা হবে।")
        else:
            await context.bot.send_message(chat_id=user.id, text="⚠️ নির্বাচিত pair-গুলোর কোনোটি থেকে এখন বাস্তব payout (≥80%) ও বৈধ strategy signal নিশ্চিত করা যায়নি। পরে আবার চেষ্টা করুন।")
        return
    if data == "noop":
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg:
        await msg.reply_text("মেনু খুলতে /start, সাহায্যের জন্য /help লিখুন।")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global AUTO_ENABLED
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not is_admin(user.id, user.username):
        await msg.reply_text("⛔ এই কমান্ড শুধু অনুমোদিত অ্যাডমিন ব্যবহার করতে পারবেন।")
        return
    AUTO_ENABLED = False
    await msg.reply_text("🛑 STM PRO BOT-এর স্বয়ংক্রিয় সিগন্যাল বন্ধ করা হয়েছে।")


async def auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global AUTO_ENABLED
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not is_admin(user.id, user.username):
        await msg.reply_text("⛔ এই কমান্ড শুধু অনুমোদিত অ্যাডমিন ব্যবহার করতে পারবেন।")
        return
    await msg.reply_text("কতটি সিগন্যালের ব্যাচ চালু করবেন?", reply_markup=keyboard([
        [("10", "batch:10"), ("20", "batch:20"), ("25", "batch:25")],
        [("30", "batch:30"), ("40", "batch:40")],
    ]))


async def run_auto_batch(context: ContextTypes.DEFAULT_TYPE, limit: int) -> None:
    global AUTO_SENT, AUTO_ENABLED, AUTO_TASK
    AUTO_SENT = 0
    all_pairs = [p for category in MARKETS.values() for p in category]
    # Avoid duplicates where pairs occur in more than one requested category.
    all_pairs = list(dict.fromkeys(all_pairs))
    strategy_order = list(STRATEGY_ORDER)
    while AUTO_ENABLED and AUTO_SENT < limit and time.time() >= PAUSED_UNTIL:
        found = False
        for pair in all_pairs:
            if not AUTO_ENABLED or AUTO_SENT >= limit or time.time() < PAUSED_UNTIL:
                break
            signal = await make_signal(context, pair, strategy_order, user_id=None)
            if signal:
                AUTO_SENT += 1
                found = True
                await post_channel(context, f"📦 Auto batch progress: {AUTO_SENT}/{limit}")
                await asyncio.sleep(1)
                if AUTO_SENT >= limit:
                    break
        if not found:
            # Don't hammer an unavailable API. Recheck after 15 seconds.
            await asyncio.sleep(15)
    if AUTO_SENT >= limit:
        await post_channel(context, f"✅ Admin batch completed: {AUTO_SENT}/{limit} signals.")
    AUTO_TASK = None


async def batch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, count: int) -> None:
    global AUTO_ENABLED, AUTO_BATCH_LIMIT, AUTO_TASK
    user = update.effective_user
    query = update.callback_query
    if not user or not query:
        return
    if not is_admin(user.id, user.username):
        await query.answer("শুধু অ্যাডমিন ব্যবহার করতে পারবেন।", show_alert=True)
        return
    if AUTO_TASK and not AUTO_TASK.done():
        AUTO_TASK.cancel()
    AUTO_BATCH_LIMIT = count
    AUTO_ENABLED = True
    await query.edit_message_text(f"✅ স্বয়ংক্রিয় স্ক্যান চালু হয়েছে। ব্যাচ সীমা: {count}টি সিগন্যাল।")
    AUTO_TASK = asyncio.create_task(run_auto_batch(context, count))


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query and (query.data or "").startswith("batch:"):
        try:
            count = int(query.data.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid batch", show_alert=True)
            return
        if count not in {10, 20, 25, 30, 40}:
            await query.answer("Invalid batch", show_alert=True)
            return
        await batch_callback(update, context, count)
        return
    await callback_handler(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.error("Unhandled update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ একটি অভ্যন্তরীণ সমস্যা হয়েছে। লগে বিস্তারিত আছে।")
        except TelegramError:
            pass


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN পাওয়া যায়নি। ~/ready/env ফাইলে BOT_TOKEN=আপনার_টোকেন দিন।")
    if not CHANNEL_ID:
        LOG.warning("CHANNEL_ID is not configured; membership verification will reject signal access until configured.")
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
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)
    LOG.info("STM PRO BOT starting (mode=%s; live order execution is not performed by main.py)", QUOTEX_MODE)
    app.run_polling(drop_pending_updates=False, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    main()
