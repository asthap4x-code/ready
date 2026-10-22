# STM PRO BOT — Termux / Telegram (Demo-only)

This project provides Telegram menus, user/channel gate, strategy selection, rule-based candle analysis, SQLite signal history, and a read-only PyQuotex demo market-data adapter.

## Important limitations
- PyQuotex is an unofficial third-party library and may break when Quotex changes its website/API. No connection is guaranteed.
- No buy/sell functions are called. Trading execution is intentionally absent.
- `AI` is an explainable rule ensemble, not a trained AI model.
- `Random` is disabled so the bot will not fabricate random signals.
- The displayed confidence is a heuristic score, not a win probability.
- Automatic result settlement, MTG progression, daily summary, referral attribution and true SMC/supply-demand detection are not implemented in this version.
- The code can only be considered connected after a successful local test with your own demo account.

## Termux setup
Use Termux from a trusted source. Then run:

```bash
pkg update && pkg upgrade
pkg install python git clang openssl libffi
python -m pip install --upgrade pip
git clone YOUR_PRIVATE_GITHUB_REPOSITORY_URL
cd YOUR_REPOSITORY_FOLDER
pip install -r requirements.txt
pip install git+https://github.com/cleitonleonel/pyquotex.git
cp .env.example .env
nano .env
python main.py
```

If the project is already in Termux, skip `git clone` and `cd` into its folder. PyQuotex installation may fail or require updates on Android; do not use a random shell installer without reviewing it.

## Configure `.env`
- `BOT_TOKEN`: token from Telegram `@BotFather`
- `ADMIN_IDS`: your numeric Telegram user ID
- `CHANNEL_ID`: public `@channelusername` or the appropriate private numeric chat ID
- `CHANNEL_URL`: channel invite/public URL
- `QUOTEX_EMAIL` / `QUOTEX_PASSWORD`: your Quotex demo login. Do not send these to anyone or commit `.env`.

The bot must be an administrator in the channel to check membership reliably. Test with a private repository and keep `.env` untracked.

## Commands
- `/start`, `/help`
- `/status`
- `/scan`
- `/history`
- `/stats`
- `/secret status` and `/secret scan` (admin IDs only)

## First connection check
Start the bot and run `/status`, then `/scan`. A failed connection or invalid asset will show an error/no qualified setup rather than inventing a signal. Confirm the selected account is DEMO in your own Quotex session before testing. This project never places trades.

## Security
Do not use a real account password if you can avoid it. Use a dedicated demo account, enable device security, keep `.env` out of Git, and rotate credentials if exposed.
