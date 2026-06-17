#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动调度器 v2：定时扫描 → 输出建议 → 自动启动/关停批处理
每5分钟扫描25币种，≥60分自动起 bat，<40分自动关

安全 CLI:
  python _auto_deploy.py                     # 进入自动部署主循环
  python _auto_deploy.py --help              # 显示帮助
  python _auto_deploy.py --dry-run           # 只扫描不启动 (等价--once --no-start)
  python _auto_deploy.py --once --no-start   # 只跑一轮不启动
  python _auto_deploy.py --dry-run --symbols BTC MATIC RNDR  # 指定币种
"""
import json, sys, io, os, time, subprocess, signal, argparse
from datetime import datetime
from urllib.request import Request, urlopen
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BAT_DIR = str(Path(__file__).resolve().parent)
LOCK_FILE = os.path.join(BAT_DIR, '.auto_deploy.lock')

GATE_API = "https://api.gateio.ws/api/v4"
SCAN_INTERVAL = 300
ENTRY_THRESHOLD = 60
EXIT_THRESHOLD = 40

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


def verify_active_pids():
    """Remove stale PIDs from active_bats that no longer exist or are not Python processes."""
    import subprocess as _sp
    stale = []
    for sym, pid in list(active_bats.items()):
        try:
            result = _sp.run(
                ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=5
            )
            if f'"{pid}"' not in result.stdout or 'python' not in result.stdout.lower():
                stale.append(sym)
        except Exception:
            stale.append(sym)
    for sym in stale:
        print(f"  ⚠ {sym} PID丢失（僵尸），从活跃列表移除")
        active_bats.pop(sym, None)


def acquire_lock():
    """Prevent concurrent auto_deploy instances via lock file."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = f.read().strip()
            # Check if the old PID is still running
            result = subprocess.run(
                ["tasklist", "/fi", f"PID eq {old_pid}", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=5
            )
            if f'"{old_pid}"' in result.stdout and 'python' in result.stdout.lower():
                print(f"✗ _auto_deploy 已在运行 (pid={old_pid})，退出。")
                sys.exit(1)
        except Exception:
            pass
        # Old lock is stale — remove and proceed
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    print(f"  锁获取成功 (pid={os.getpid()})")


def release_lock():
    """Release the concurrency lock on exit."""
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


# ── CLI ─────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        description="200x Commander 自动调度器 — 扫描25币种，自动进场/离场"
    )
    p.add_argument("--dry-run", action="store_true",
                   help="只扫描不启动 bat。等价于 --once --no-start")
    p.add_argument("--once", action="store_true",
                   help="只执行一轮扫描后退出")
    p.add_argument("--no-start", action="store_true",
                   help="禁止启动 bat，只输出决策")
    p.add_argument("--symbols", nargs="+", metavar="SYM",
                   help="只评估指定币种，例如 --symbols BTC MATIC RNDR")
    return p


def _resolve_coins(args):
    """Return filtered coin list based on --symbols flag."""
    if not args.symbols:
        return COINS

    requested = set()
    for s in args.symbols:
        requested.add(s.upper() + "_USDT" if "_" not in s.upper() else s.upper())
    coins = [c for c in COINS if c in requested]
    missing = requested - set(coins)

    if coins:
        print(f"[symbols] 匹配 {len(coins)} 个: {', '.join(coins)}")
    else:
        print(f"[symbols] 无匹配币种: {', '.join(sorted(requested))}")
    if missing:
        print(f"[symbols] 以下币种不在候选列表或不受支持: {', '.join(sorted(missing))}")

    return coins


def _mode_tags(args):
    """Build a human-readable mode tag string."""
    tags = []
    if args.dry_run:
        tags.append("DRY-RUN")
    if args.once:
        tags.append("ONCE")
    if args.no_start:
        tags.append("NO-START")
    if args.symbols:
        tags.append("FILTER")
    return f"  [{', '.join(tags)}]" if tags else ""


# ── 主循环 ─────────────────────────────────────────────────────

def main(args=None):
    if args is None:
        parser = build_parser()
        args = parser.parse_args()

    # --dry-run implies --once + --no-start
    if args.dry_run:
        args.once = True
        args.no_start = True

    coins = _resolve_coins(args)
    if not coins:
        sys.exit(1)

    # Only acquire lock if we might modify state (full loop without --no-start)
    needs_lock = not args.once or not args.no_start
    if needs_lock:
        acquire_lock()

    mode_str = _mode_tags(args)

    print("=" * 75)
    print("  自动调度器 v2 — 扫描 → 自动起/停批处理")
    print(f"  进场 ≥{ENTRY_THRESHOLD}分 | 离场 <{EXIT_THRESHOLD}分 | 间隔 {SCAN_INTERVAL}s{mode_str}")
    print("=" * 75)
    sys.stdout.flush()

    try:
        while True:
            verify_active_pids()  # 先清理僵尸PID
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{now}] 扫描中...")
            sys.stdout.flush()

            results = []
            for sym in coins:
                klines = fetch_klines(sym)
                if not klines or len(klines) < 50:
                    if args.symbols:
                        print(f"  ✗ {sym}: klines 获取失败或无数据（symbol 可能不存在于 Gate.io）")
                    continue
                for k in klines:
                    k["_symbol"] = sym
                r = analyze(klines)
                if r is None:
                    if args.symbols:
                        print(f"  ✗ {sym}: 分析失败（数据量不足）")
                    continue
                r["score"] = score(r)
                results.append(r)

            if not results:
                print("  (无可用数据)")
                if args.once:
                    break
                print(f"  ▸ 下次扫描: {SCAN_INTERVAL}s 后")
                time.sleep(SCAN_INTERVAL)
                continue

            results.sort(key=lambda x: x["score"], reverse=True)

            print(f"  {'币种':<14} {'价格':>10} {'ADX':>5} {'ATR%':>6} {'量比':>5}  {'得分':>4}  {'状态'}")
            print("  " + "-" * 65)
            for r in results:
                sym = r["symbol"]
                active = sym in active_bats
                would_start = r["score"] >= ENTRY_THRESHOLD and not active
                if args.no_start:
                    status = "→进场" if would_start else ("运行中(禁)" if active else "")
                else:
                    status = "运行中" if active else ("→进场" if would_start else "")
                marker = "★" if would_start else " "
                print(f"  {marker}{sym:<13} {r['price']:>10.4f} {r['adx']:>5.1f} "
                      f"{r['atr_pct']:>5.3f}% {r['vol_ratio']:>5.2f}  {r['score']:>4.0f}  {status}")
            sys.stdout.flush()

            # 自动操作 — API 降级/安全模式时跳过
            if api_is_degraded():
                if not args.no_start:
                    print(f"  ⚠ API 持续失败 ({API_ERRORS}次)，跳过进场/离场决策")
            elif not args.no_start:
                for r in results:
                    sym = r["symbol"]
                    if r["score"] >= ENTRY_THRESHOLD and sym not in active_bats:
                        start_bat(sym)
                    elif r["score"] < EXIT_THRESHOLD and sym in active_bats:
                        stop_bat(sym)
            else:
                # --no-start: 只输出决策，不执行
                would_start = [r["symbol"] for r in results
                               if r["score"] >= ENTRY_THRESHOLD and r["symbol"] not in active_bats]
                would_stop = [s for s in active_bats
                              if any(r["symbol"] == s and r["score"] < EXIT_THRESHOLD for r in results)]
                if would_start:
                    print(f"\n  [NO-START] 将进场(未启动): {', '.join(would_start)}")
                if would_stop:
                    print(f"  [NO-START] 将离场(未执行): {', '.join(would_stop)}")
                if not would_start and not would_stop:
                    print(f"\n  [NO-START] 无操作")

            if active_bats:
                print(f"\n  ▸ 运行中: {', '.join(active_bats.keys())}  ({len(active_bats)}个)")
            else:
                print(f"\n  ▸ 无运行中的策略")
            sys.stdout.flush()

            if args.once:
                print(f"\n  ▸ --once 模式，退出。")
                break

            print(f"  ▸ 下次扫描: {SCAN_INTERVAL}s 后")
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n  关停所有...")
        stop_all()
        release_lock()
        print("  退出。")

    if not needs_lock:
        pass  # lock was never acquired, nothing to release
    elif not (args.once and not args.no_start):
        release_lock()


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    main(args)
