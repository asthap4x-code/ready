from stm_strategy import analyze as base_analyze

def analyze(candles):
    """Explainable rule ensemble; this is not a trained AI model."""
    r=base_analyze(candles)
    if not r: return None
    closes=[x["close"] for x in candles]
    # Require a consistent 5-candle drift to filter weak setups.
    drift=closes[-1]-closes[-6]
    if r["direction"]=="CALL" and drift<=0: return None
    if r["direction"]=="PUT" and drift>=0: return None
    r=dict(r); r["confidence"]=min(85,r["confidence"]+3)
    r["reason"]="Rule ensemble: "+r["reason"]+"; 5-candle drift agrees"
    return r
