#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
市场扫描器：检测主流币是否适合策略运行
适合条件：有趋势 + 有波动 + 有量
"""
import json, sys, io, time
from urllib.request import Request, urlopen

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

GATE_API = "https://api.gateio.ws/api/v4"
INTERVAL = "5m"
LIMIT = 200

COINS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "AVAX_USDT", "DOT_USDT", "LINK_USDT",
    "MATIC_USDT", "UNI_USDT", "ATOM_USDT", "LTC_USDT", "FIL_USDT",
    "APT_USDT", "ARB_USDT", "OP_USDT", "SUI_USDT", "SEI_USDT",
    "TIA_USDT", "INJ_USDT", "RUNE_USDT", "FET_USDT", "RNDR_USDT",
]


def fetch_klines(symbol: str, limit: int = LIMIT) -> list:
    endpoint = f"/futures/usdt/candlesticks?contract={symbol}&interval={INTERVAL}&limit={limit}"
    url = f"{GATE_API}{endpoint}"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return []
    result = []
    for c in data:
        result.append({
            "t": int(c.get("t", 0)),
            "o": float(c.get("o", 0)),
            "h": float(c.get("h", 0)),
            "l": float(c.get("l", 0)),
            "c": float(c.get("c", 0)),
            "v": float(c.get("v", 0)),
        })
    return result


def calc_tr(klines: list, i: int) -> float:
    h, l, c = klines[i]["h"], klines[i]["l"], klines[i]["c"]
    if i == 0:
        return h - l
    pc = klines[i - 1]["c"]
    return max(h - l, abs(h - pc), abs(l - pc))


def calc_ema(values: list, period: int) -> list:
    ema = []
    k = 2 / (period + 1)
    for i, v in enumerate(values):
        if i == 0:
            ema.append(v)
        else:
            ema.append(v * k + ema[-1] * (1 - k))
    return ema


def calc_rma(values: list, period: int) -> list:
    rma = [sum(values[:period]) / period]
    alpha = 1 / period
    for i in range(period, len(values)):
        rma.append(values[i] * alpha + rma[-1] * (1 - alpha))
    return [0] * (period - 1) + rma


def analyze(klines: list) -> dict:
    if len(klines) < 50:
        return None

    closes = [k["c"] for k in klines]
    highs = [k["h"] for k in klines]
    lows = [k["l"] for k in klines]
    volumes = [k["v"] for k in klines]

    n = len(closes)

    # --- ATR% ---
    tr_list = [calc_tr(klines, i) for i in range(n)]
    atr14 = calc_rma(tr_list, 14)
    atr_val = atr14[-1]
    atr_pct = atr_val / closes[-1] * 100

    # --- ADX ---
    dm_plus, dm_minus = [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        dm_plus.append(up if up > dn and up > 0 else 0)
        dm_minus.append(dn if dn > up and dn > 0 else 0)

    tr14 = calc_rma(tr_list, 14)
    dmp14 = calc_rma([0] + dm_plus, 14)
    dmn14 = calc_rma([0] + dm_minus, 14)

    di_plus = [dmp14[i] / tr14[i] * 100 if tr14[i] > 0 else 0 for i in range(len(tr14))]
    di_minus = [dmn14[i] / tr14[i] * 100 if tr14[i] > 0 else 0 for i in range(len(tr14))]

    dx_list = []
    for i in range(len(di_plus)):
        s = di_plus[i] + di_minus[i]
        dx_list.append(abs(di_plus[i] - di_minus[i]) / s * 100 if s > 0 else 0)

    adx14 = calc_rma(dx_list, 14)
    adx_val = adx14[-1]

    # --- 趋势方向 ---
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    trend_up = ema20[-1] > ema50[-1] and closes[-1] > ema20[-1]

    # --- 成交量比 ---
    vol_short = sum(volumes[-20:]) / 20
    vol_long = sum(volumes[-50:]) / 50 if n >= 50 else vol_short
    vol_ratio = vol_short / vol_long if vol_long > 0 else 1

    # --- 24h 涨跌 ---
    change_24h = (closes[-1] / closes[-min(288, n - 1)] - 1) * 100

    # --- Chop 震荡指数 ---
    hh = max(highs[-14:])
    ll = min(lows[-14:])
    chop = (hh - ll) / (atr_val * 14) if atr_val > 0 else 1

    return {
        "symbol": klines[0].get("_symbol", ""),
        "price": closes[-1],
        "atr_pct": round(atr_pct, 3),
        "adx": round(adx_val, 1),
        "trend_up": trend_up,
        "vol_ratio": round(vol_ratio, 2),
        "change_24h": round(change_24h, 1),
        "chop": round(chop, 2),
        "n_candles": n,
    }


def calc_raw_score(r: dict) -> float:
    s = 0
    if r["adx"] >= 30:     s += 30
    elif r["adx"] >= 20:   s += 15
    elif r["adx"] >= 15:   s += 5
    if r["atr_pct"] >= 0.5: s += 25
    elif r["atr_pct"] >= 0.3: s += 15
    elif r["atr_pct"] >= 0.2: s += 5
    if r["vol_ratio"] >= 1.3: s += 15
    elif r["vol_ratio"] >= 1.0: s += 10
    elif r["vol_ratio"] >= 0.8: s += 5
    if r["trend_up"]:       s += 15
    if r["chop"] >= 1.5:    s += 15
    elif r["chop"] >= 1.0:  s += 8
    elif r["chop"] >= 0.7:  s += 3
    return s


def verdict(score: float) -> str:
    if score >= 80:   return "强烈推荐"
    elif score >= 60: return "推荐"
    elif score >= 40: return "可观察"
    else:             return "不推荐"


def main():
    print("=" * 95)
    print("  市场扫描器 — 检测主流币是否适合策略运行 （5m K线）")
    print("=" * 95)
    print(f"  {'币种':<14} {'价格':>10}  {'ADX':>5}  {'ATR%':>6}  {'量比':>5}  {'24h%':>7}  {'Chop':>5}   {'得分':>4}  {'判定'}")
    print("  " + "-" * 93)

    results = []
    for symbol in COINS:
        klines = fetch_klines(symbol)
        if not klines or len(klines) < 50:
            print(f"  {symbol:<14} {'数据不足':>10}")
            continue
        for k in klines:
            k["_symbol"] = symbol
        r = analyze(klines)
        if r is None:
            continue
        score = calc_raw_score(r)
        r["score"] = score
        results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)

    for r in results:
        marker = "→" if r["score"] >= 60 else " "
        trend_icon = "↗" if r["trend_up"] else "↘"
        print(f"  {marker} {r['symbol']:<13} {r['price']:>10.4f} {r['adx']:>5.1f} {r['atr_pct']:>5.3f}% {r['vol_ratio']:>5.2f} {r['change_24h']:>6.1f}% {r['chop']:>5.2f}  {r['score']:>4.0f}  {trend_icon} {verdict(r['score'])}")

    print("  " + "-" * 93)
    print(f"  共扫描 {len(results)} 个币种")

    top = [r for r in results if r["score"] >= 60][:3]
    if top:
        print()
        print("  ★ 推荐部署:")
        for r in top:
            print(f"    {r['symbol']}  得分 {r['score']}  ADX={r['adx']}  ATR%={r['atr_pct']}%  {verdict(r['score'])}")
    else:
        print()
        print("  ⚠ 当前无币种达到推荐标准，市场整体清淡。")
    print()
    print("  评分: ADX(0-30) + 波动(0-25) + 量比(0-15) + 趋势(0-15) + Chop(0-15)")
    print()


if __name__ == "__main__":
    main()
