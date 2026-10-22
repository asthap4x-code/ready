import os, logging, asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from database import init_db, add_user, save_signal, recent_signals, record_result, stats
from market_data import QuotexDemoFeed, MarketDataError
from scanner import scan_market

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stm")
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()
BOT_NAME = "STM PRO BOT"

STRATEGIES = {"AI": "AI Strategy", "STM": "STM Strategy", "RANDOM": "Random (demo/test only)", "ALL": "All strategies"}
MARKETS = {"FOREX": "Forex", "OTC": "OTC", "CRYPTO": "Crypto", "COMMODITIES": "Commodities"}
feed = QuotexDemoFeed()
user_prefs = {}

def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 Get Signals", callback_data="signals"), InlineKeyboardButton("🔎 Scan Market", callback_data="scan")],
        [InlineKeyboardButton("🎯 Strategy", callback_data="strategy"), InlineKeyboardButton("🌐 Market", callback_data="market")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats"), InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ])

async def member_ok(context, user_id):
    if not CHANNEL_ID:
        return True
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("Membership check failed: %s", e)
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await add_user(u.id, u.username or "", u.first_name or "")
    if CHANNEL_ID and not await member_ok(context, u.id):
        buttons = []
        if CHANNEL_URL: buttons.append([InlineKeyboardButton("📢 Join channel", url=CHANNEL_URL)])
        buttons.append([InlineKeyboardButton("✅ I joined — check", callback_data="check_member")])
        await update.effective_message.reply_text(
            f"Welcome to {BOT_NAME}!\nপ্রথমে আমাদের চ্যানেলে Join করো, তারপর Check চাপো।",
            reply_markup=InlineKeyboardMarkup(buttons))
        return
    await update.effective_message.reply_text(
        f"🚀 *{BOT_NAME}*\nDemo-market analysis & signal tracking bot.\n\n⚠️ Signals are analysis, not guaranteed outcomes. Demo mode only.",
        parse_mode="Markdown", reply_markup=home_keyboard())

async def help_cmd(update, context):
    await update.effective_message.reply_text(
        "Commands:\n/start — menu\n/status — system status\n/scan — scan selected demo assets\n/history — recent signals\n/stats — tracked results\n\nThis bot does not place trades. Quotex access is unofficial and may stop working.", reply_markup=home_keyboard())

async def status_cmd(update, context):
    await update.effective_message.reply_text(f"STM PRO BOT\nQuotex feed: {'CONNECTED' if feed.connected else 'NOT CONNECTED'}\nAccount mode: DEMO ONLY\nTrading execution: DISABLED\nSignals are not guaranteed.")

async def history_cmd(update, context):
    rows = await recent_signals(update.effective_user.id, 10)
    if not rows:
        await update.effective_message.reply_text("এখনো কোনো tracked signal নেই।")
        return
    text = "📜 Recent tracked signals\n" + "\n".join(
        f"#{r['id']} {r['asset']} {r['direction']} | {r['strategy']} | {r['outcome'] or 'PENDING'}"
        for r in rows)
    await update.effective_message.reply_text(text)

async def stats_cmd(update, context):
    s = await stats(update.effective_user.id)
    await update.effective_message.reply_text(
        f"📊 Tracked results\nTotal: {s['total']}\nWins: {s['wins']}\nLosses: {s['losses']}\nPending: {s['pending']}\nWin rate (settled only): {s['rate']:.1f}%")

async def admin_cmd(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text("Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /secret status | /secret scan")
        return
    action = context.args[0].lower()
    if action == "status":
        await status_cmd(update, context)
    elif action == "scan":
        await do_scan(update.effective_message, update.effective_user.id)
    else:
        await update.effective_message.reply_text("Available: status, scan")

async def do_scan(message, user_id):
    prefs = user_prefs.get(user_id, {"strategy":"STM", "market":"FOREX"})
    await message.reply_text("🔎 Scanning demo market data…")
    try:
        results = await scan_market(feed, prefs["market"], prefs["strategy"])
    except MarketDataError as e:
        await message.reply_text(f"❌ Market data unavailable: {e}\n\nCheck .env credentials, package installation, and Quotex connection. No fake signal was generated.")
        return
    if not results:
        await message.reply_text("No qualified setup found. Try another market later.")
        return
    out = ["📡 *STM PRO BOT — analysis candidates*"]
    for r in results[:5]:
        sid = await save_signal(user_id, r["asset"], r["direction"], prefs["strategy"], r["confidence"], r["reason"])
        out.append(f"\n*{r['asset']}* — *{r['direction']}*\nConfidence score: {r['confidence']}/100\nReason: {r['reason']}\nID: #{sid}\nExpiry: 1 minute (analysis only)")
    out.append("\n⚠️ Demo analysis only; confidence is a rule score, not a win probability.")
    await message.reply_text("\n".join(out), parse_mode="Markdown")

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "check_member":
        if await member_ok(context, uid):
            await q.edit_message_text("Membership verified. Use /start to open the menu.")
        else:
            await q.edit_message_text("Membership could not be verified. Join the channel and try again.")
    elif q.data == "signals":
        await do_scan(q.message, uid)
    elif q.data == "scan":
        await do_scan(q.message, uid)
    elif q.data == "stats":
        s = await stats(uid)
        await q.message.reply_text(f"Total: {s['total']} | Wins: {s['wins']} | Losses: {s['losses']} | Pending: {s['pending']}")
    elif q.data == "help":
        await help_cmd(update, context)
    elif q.data == "strategy":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(v, callback_data=f"setstr:{k}")] for k,v in STRATEGIES.items()])
        await q.message.reply_text("Choose strategy:", reply_markup=kb)
    elif q.data.startswith("setstr:"):
        key = q.data.split(":",1)[1]
        user_prefs.setdefault(uid, {"strategy":"STM","market":"FOREX"})["strategy"] = key
        await q.edit_message_text(f"Strategy set: {STRATEGIES[key]}")
    elif q.data == "market":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(v, callback_data=f"setmarket:{k}")] for k,v in MARKETS.items()])
        await q.message.reply_text("Choose market:", reply_markup=kb)
    elif q.data.startswith("setmarket:"):
        key = q.data.split(":",1)[1]
        user_prefs.setdefault(uid, {"strategy":"STM","market":"FOREX"})["market"] = key
        await q.edit_message_text(f"Market set: {MARKETS[key]}")

async def post_init(app):
    await init_db()
    log.info("Database initialized")

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN missing. Copy .env.example to .env and set BOT_TOKEN.")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("secret", admin_cmd))
    app.add_handler(CommandHandler("scan", lambda u,c: do_scan(u.effective_message, u.effective_user.id)))
    app.add_handler(CallbackQueryHandler(callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
