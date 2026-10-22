def analyze(c):
    if len(c) < 25: return None
    closes=[x["close"] for x in c]
    last=c[-1]; prev=c[-2]
    recent=c[-20:-1]
    support=min(x["low"] for x in recent); resistance=max(x["high"] for x in recent)
    body=last["close"]-last["open"]
    rng=max(last["high"]-last["low"], 1e-9)
    # Simple trend + level rejection; heuristic, not a validated predictive model.
    up=sum(closes[i]>closes[i-1] for i in range(-6,-1))
    down=sum(closes[i]<closes[i-1] for i in range(-6,-1))
    if last["low"] <= support + (resistance-support)*0.08 and last["close"]>last["open"] and up>=2:
        return {"direction":"CALL","confidence":min(80,55+up*4),"reason":"Support-zone rejection + short-term bullish momentum"}
    if last["high"] >= resistance - (resistance-support)*0.08 and last["close"]<last["open"] and down>=2:
        return {"direction":"PUT","confidence":min(80,55+down*4),"reason":"Resistance-zone rejection + short-term bearish momentum"}
    if up>=4 and body>0 and body/rng>0.55:
        return {"direction":"CALL","confidence":60,"reason":"Bullish momentum candle (rule-based)"}
    if down>=4 and body<0 and abs(body)/rng>0.55:
        return {"direction":"PUT","confidence":60,"reason":"Bearish momentum candle (rule-based)"}
    return None
