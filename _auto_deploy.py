#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动调度器 v2：定时扫描 → 输出建议 → 自动启动/关停批处理
每5分钟扫描25币种，≥60分自动起 bat，<40分自动关
"""
import json, sys, io, os, time, subprocess, signal
from datetime import datetime
from urllib.request import Request, urlopen

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

GATE_API = "https://api.gateio.ws/api/v4"
SCAN_INTERVAL = 300
ENTRY_THRESHOLD = 60
EXIT_THRESHOLD = 40

BAT_DIR = r"C:\Users\Administrator\200x_commander"

COINS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "AVAX_USDT", "DOT_USDT", "LINK_USDT",
    "MATIC_USDT", "UNI_USDT", "ATOM_USDT", "LTC_USDT", "FIL_USDT",
    "APT_USDT", "ARB_USDT", "OP_USDT", "SUI_USDT", "SEI_USDT",
    "TIA_USDT", "INJ_USDT", "RUNE_USDT", "FET_USDT", "RNDR_USDT",
]

# Bat 文件映射 (自动生成路径)
def bat_path(symbol):
    prefix = symbol.split("_")[0].lower()
    # SOL 特殊情况：实际文件名是 run_rt_paper.bat
    if prefix == "sol":
        return os.path.join(BAT_DIR, "run_rt_paper.bat")
    return os.path.join(BAT_DIR, f"run_rt_paper_{prefix}.bat")


# ── 扫描（同前）────────────────────────────────────────────────

API_ERRORS = 0
MAX_CONSECUTIVE_API_ERRORS = 3


def fetch_klines(symbol, limit=200, retries=2):
    global API_ERRORS
    endpoint = f"/futures/usdt/candlesticks?contract={symbol}&interval=5m&limit={limit}"
    req = Request(f"{GATE_API}{endpoint}", headers={"Accept": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            API_ERRORS = 0
            return [{"t": int(c.get("t",0)), "o": float(c.get("o",0)),
                     "h": float(c.get("h",0)), "l": float(c.get("l",0)),
                     "c": float(c.get("c",0)), "v": float(c.get("v",0))} for c in data]
        except Exception as e:
            if attempt < retries:
                print(f"  ⚠ {symbol} API error (attempt {attempt+1}/{retries+1}): {e}")
                time.sleep(2)
            else:
                API_ERRORS += 1
                print(f"  ✗ {symbol} API failed after {retries+1} attempts: {e}")
    return []


def api_is_degraded():
    """If too many consecutive API failures, skip start/stop cycle."""
    return API_ERRORS >= MAX_CONSECUTIVE_API_ERRORS


def calc_tr(klines, i):
    h, l, c = klines[i]["h"], klines[i]["l"], klines[i]["c"]
    if i == 0: return h - l
    return max(h - l, abs(h - klines[i-1]["c"]), abs(l - klines[i-1]["c"]))


def calc_ema(values, period):
    ema, k = [], 2/(period+1)
    for i, v in enumerate(values):
        ema.append(v if i == 0 else v*k + ema[-1]*(1-k))
    return ema


def calc_rma(values, period):
    rma, a = [sum(values[:period])/period], 1/period
    for i in range(period, len(values)):
        rma.append(values[i]*a + rma[-1]*(1-a))
    return [0]*(period-1) + rma


def analyze(klines):
    if len(klines) < 50: return None
    close = [k["c"] for k in klines]
    high = [k["h"] for k in klines]
    low = [k["l"] for k in klines]
    vol = [k["v"] for k in klines]
    n = len(close)
    tr = [calc_tr(klines, i) for i in range(n)]
    atr14 = calc_rma(tr, 14)
    atr_pct = atr14[-1]/close[-1]*100
    dm_p, dm_m = [], []
    for i in range(1, n):
        up, dn = high[i]-high[i-1], low[i-1]-low[i]
        dm_p.append(up if up>dn and up>0 else 0)
        dm_m.append(dn if dn>up and dn>0 else 0)
    tr14 = calc_rma(tr, 14)
    dmp14 = calc_rma([0]+dm_p, 14)
    dmn14 = calc_rma([0]+dm_m, 14)
    dip = [dmp14[i]/tr14[i]*100 if tr14[i]>0 else 0 for i in range(len(tr14))]
    dim = [dmn14[i]/tr14[i]*100 if tr14[i]>0 else 0 for i in range(len(tr14))]
    dx = [abs(dip[i]-dim[i])/(dip[i]+dim[i])*100 if dip[i]+dim[i]>0 else 0 for i in range(len(dip))]
    adx = calc_rma(dx, 14)[-1]
    ema20, ema50 = calc_ema(close, 20), calc_ema(close, 50)
    trend_up = ema20[-1] > ema50[-1] and close[-1] > ema20[-1]
    vs = sum(vol[-20:])/20
    vl = sum(vol[-50:])/50 if n>=50 else vs
    vol_ratio = vs/vl if vl>0 else 1
    return {"symbol": klines[0].get("_symbol",""), "price": close[-1],
            "atr_pct": round(atr_pct,3), "adx": round(adx,1),
            "trend_up": trend_up, "vol_ratio": round(vol_ratio,2)}


def score(r):
    s = 0
    if r["adx"]>=30: s+=30
    elif r["adx"]>=20: s+=15
    elif r["adx"]>=15: s+=5
    if r["atr_pct"]>=0.5: s+=25
    elif r["atr_pct"]>=0.3: s+=15
    elif r["atr_pct"]>=0.2: s+=5
    if r["vol_ratio"]>=1.3: s+=15
    elif r["vol_ratio"]>=1.0: s+=10
    elif r["vol_ratio"]>=0.8: s+=5
    if r["trend_up"]: s+=15
    return s


# ── 进程管理 ───────────────────────────────────────────────────

active_bats: dict = {}  # symbol -> subprocess.Popen (cmd进程,不会被Python杀)


def start_bat(symbol):
    bp = bat_path(symbol)
    if not os.path.exists(bp):
        print(f"  ⚠ {symbol}: bat 文件不存在 {bp}")
        return False
    p = subprocess.Popen(
        ["cmd.exe", "/c", bp], cwd=BAT_DIR,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    active_bats[symbol] = p.pid
    print(f"  ▶ 启动 {symbol}")
    return True


def stop_bat(symbol):
    pid = active_bats.pop(symbol, None)
    if pid:
        try:
            subprocess.run(["taskkill", "/f", "/t", "/pid", str(pid)],
                           capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            print(f"  ⚠ {symbol} taskkill timeout (pid={pid})")
        except Exception as e:
            print(f"  ⚠ {symbol} taskkill error: {e}")
        print(f"  ■ 关停 {symbol}")


def stop_all():
    for sym in list(active_bats):
        stop_bat(sym)


# ── 主循环 ─────────────────────────────────────────────────────

def main():
    print("=" * 75)
    print("  自动调度器 v2 — 扫描 → 自动起/停批处理")
    print(f"  进场 ≥{ENTRY_THRESHOLD}分 | 离场 <{EXIT_THRESHOLD}分 | 间隔 {SCAN_INTERVAL}s")
    print("=" * 75)
    sys.stdout.flush()

    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{now}] 扫描中...")
            sys.stdout.flush()

            results = []
            for sym in COINS:
                klines = fetch_klines(sym)
                if not klines or len(klines) < 50:
                    continue
                for k in klines:
                    k["_symbol"] = sym
                r = analyze(klines)
                if r is None:
                    continue
                r["score"] = score(r)
                results.append(r)

            results.sort(key=lambda x: x["score"], reverse=True)

            print(f"  {'币种':<14} {'价格':>10} {'ADX':>5} {'ATR%':>6} {'量比':>5}  {'得分':>4}  {'状态'}")
            print("  " + "-" * 65)
            for r in results:
                sym = r["symbol"]
                active = sym in active_bats
                status = "运行中" if active else ("→进场" if r["score"] >= ENTRY_THRESHOLD else "")
                marker = "★" if r["score"] >= ENTRY_THRESHOLD else " "
                print(f"  {marker}{sym:<13} {r['price']:>10.4f} {r['adx']:>5.1f} {r['atr_pct']:>5.3f}% {r['vol_ratio']:>5.2f}  {r['score']:>4.0f}  {status}")
            sys.stdout.flush()

            # 自动操作 — API 降级时跳过，防止误杀
            if api_is_degraded():
                print(f"  ⚠ API 持续失败 ({API_ERRORS}次)，跳过进场/离场决策")
            else:
                for r in results:
                    sym = r["symbol"]
                    if r["score"] >= ENTRY_THRESHOLD and sym not in active_bats:
                        start_bat(sym)
                    elif r["score"] < EXIT_THRESHOLD and sym in active_bats:
                        stop_bat(sym)

            if active_bats:
                print(f"\n  ▸ 运行中: {', '.join(active_bats.keys())}  ({len(active_bats)}个)")
            else:
                print(f"\n  ▸ 无运行中的策略")
            sys.stdout.flush()

            print(f"  ▸ 下次扫描: {SCAN_INTERVAL}s 后")
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n  关停所有...")
        stop_all()
        print("  退出。")


if __name__ == "__main__":
    main()
