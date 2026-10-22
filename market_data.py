import os, time, asyncio, logging
from dotenv import load_dotenv
load_dotenv()
log = logging.getLogger(__name__)

class MarketDataError(RuntimeError): pass

class QuotexDemoFeed:
    """Read-only demo market-data adapter. Never calls buy()/sell()."""
    def __init__(self):
        self.email = os.getenv("QUOTEX_EMAIL", "").strip()
        self.password = os.getenv("QUOTEX_PASSWORD", "").strip()
        self.host = os.getenv("QUOTEX_HOST", "quotex.com").strip()
        self.client = None
        self.connected = False

    async def connect(self):
        if self.connected and self.client:
            return True
        if not self.email or not self.password:
            raise MarketDataError("Set QUOTEX_EMAIL and QUOTEX_PASSWORD in local .env. Never share them.")
        try:
            from pyquotex.stable_api import Quotex
        except Exception as e:
            raise MarketDataError(f"PyQuotex import failed: {e}. Install it using README Termux steps.")
        try:
            self.client = Quotex(email=self.email, password=self.password, lang="en", host=self.host)
            result = await self.client.connect()
            # Some library versions return tuple, others expose state; do not assume success.
            if isinstance(result, tuple):
                ok = bool(result[0])
            else:
                ok = bool(result)
            if not ok:
                raise MarketDataError(f"PyQuotex did not confirm connection: {result!r}")
            try:
                await self.client.change_account("PRACTICE")
            except Exception:
                try:
                    from pyquotex.utils.account_type import AccountType
                    await self.client.change_account(AccountType.DEMO)
                except Exception as e:
                    log.warning("Could not explicitly switch account to demo: %s", e)
            self.connected = True
            return True
        except MarketDataError:
            raise
        except Exception as e:
            self.client = None
            self.connected = False
            raise MarketDataError(f"Quotex demo connection failed: {type(e).__name__}: {e}")

    async def candles(self, asset, period=60, count=80):
        await self.connect()
        try:
            end_time = time.time()
            # Current PyQuotex documented interface; library API can change.
            data = await self.client.get_candles(asset, end_time, count, period)
            if not data:
                raise MarketDataError(f"No candles returned for {asset}; verify asset symbol and session.")
            normalized = []
            for c in data:
                try:
                    normalized.append({
                        "time": float(c.get("time", c.get("from", 0))),
                        "open": float(c["open"]), "high": float(c["high"]),
                        "low": float(c["low"]), "close": float(c["close"]),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
            if len(normalized) < 25:
                raise MarketDataError(f"Only {len(normalized)} valid candles for {asset}; need at least 25.")
            return sorted(normalized, key=lambda x: x["time"])
        except MarketDataError:
            raise
        except Exception as e:
            raise MarketDataError(f"Candle retrieval failed for {asset}: {type(e).__name__}: {e}")

    async def close(self):
        if self.client:
            try: await self.client.close()
            except Exception: pass
        self.client, self.connected = None, False
