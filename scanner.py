import asyncio
from market_data import MarketDataError
from ai_strategy import analyze as ai_analyze
from stm_strategy import analyze as stm_analyze
from random_strategy import analyze as random_analyze

# Symbols must match what the connected broker session actually exposes.
ASSETS = {
 "FOREX": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"],
 "OTC": ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "USDCAD_otc"],
 "CRYPTO": ["BTCUSD", "ETHUSD"],
 "COMMODITIES": ["XAUUSD", "XAGUSD", "USOIL"],
}
async def scan_market(feed, market, strategy):
    assets = ASSETS.get(market, ASSETS["FOREX"])
    methods = {"AI": ai_analyze, "STM": stm_analyze, "RANDOM": random_analyze}
    selected = list(methods.values()) if strategy == "ALL" else [methods.get(strategy, stm_analyze)]
    results = []
    # Sequential requests reduce rate-limit / connection pressure.
    for asset in assets:
        try:
            candles = await feed.candles(asset, 60, 80)
        except MarketDataError:
            continue
        for method in selected:
            result = method(candles)
            if result and result.get("direction") in ("CALL", "PUT"):
                results.append({"asset":asset, **result, "strategy":method.__module__.replace("_strategy","").upper()})
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results
