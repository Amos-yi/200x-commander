"""
自主优化器 — HT_DCPHASE 入场 × 固定盈亏比出场 × 网格搜索
"""
import sys, os, json, statistics, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from gate_data import fetch_klines
from backtest import _ht_phase
from config import STAGES, SIGNAL, SCORE_WEIGHTS, MODE_RISK_BUDGET
from strategic_brain import StrategicBrain
from calendar import detect_regime


def run_optimized_backtest(klines, stop_pct, rr_ratio, min_score):
    """固定盈亏比回测"""
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    times = [k["time"] for k in klines]
    volumes = [k["volume"] for k in klines]

    phase_list, _ = _ht_phase(closes)
    strategic = StrategicBrain()

    equity = 100.0
    peak = 100.0
    position = None
    trades = []
    consecutive_losses = 0
    daily_trades = 0
    last_date = ""
    stage = 1

    for i in range(50, len(klines) - 1):
        dt = datetime.fromtimestamp(times[i])
        peak = max(peak, equity)

        # Stage
        for sid in [4, 3, 2, 1]:
            lo, hi = STAGES[sid]["equity_range"]
            if lo <= equity < hi:
                stage = sid
                break
        else:
            stage = 4 if equity >= STAGES[4]["equity_range"][1] else 1

        ds = dt.strftime("%Y-%m-%d")
        if ds != last_date:
            daily_trades = 0
            last_date = ds

        # 持仓 → 固定盈亏比出场
        if position:
            p = position
            ep = p["entry_price"]
            exited = False
            exit_price = 0
            reason = ""

            if p["side"] == "long":
                if highs[i] >= p["tp"]:
                    exit_price = p["tp"]
                    reason = "tp"
                    exited = True
                elif lows[i] <= p["sl"]:
                    exit_price = p["sl"]
                    reason = "sl"
                    exited = True
            else:
                if lows[i] <= p["tp"]:
                    exit_price = p["tp"]
                    reason = "tp"
                    exited = True
                elif highs[i] >= p["sl"]:
                    exit_price = p["sl"]
                    reason = "sl"
                    exited = True

            if exited:
                pnl = (exit_price - ep) * p["size"] if p["side"] == "long" else (ep - exit_price) * p["size"]
                equity += pnl
                trades.append({
                    "pnl": round(pnl, 2),
                    "exit_reason": reason,
                    "score": p.get("score", 0),
                })
                consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1
                position = None
            continue

        # 锁
        hour = (dt.hour + 8) % 24
        if hour < SIGNAL["active_hour_start"] or hour >= SIGNAL["active_hour_end"]:
            continue
        if daily_trades >= 2:
            continue
        if consecutive_losses >= 3:
            continue

        # ── HT相位信号 ──
        if i < 2:
            continue
        phase_curr = phase_list[i]
        phase_prev = phase_list[i - 1]

        direction = None
        if phase_prev < 90 and phase_curr >= 90:
            direction = "long"
        elif phase_prev > 270 and phase_curr <= 270:
            direction = "short"

        if direction is None:
            if 0 < phase_curr < 30 or phase_curr > 330:
                direction = "long"
            elif 150 < phase_curr < 210:
                direction = "short"

        if direction is None:
            continue

        # 集成打分
        score = SCORE_WEIGHTS["ht_phase"]
        if (direction == "long" and phase_curr - phase_prev > 0) or (direction == "short" and phase_curr - phase_prev < 0):
            score += 1

        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
        if volumes[-1] > avg_vol * SIGNAL["volume_multiplier"]:
            if volumes[-1] > avg_vol * SIGNAL["volume_multiplier_bonus"]:
                score += SCORE_WEIGHTS["volume_confirm"] + SCORE_WEIGHTS["volume_strong"]
            else:
                score += SCORE_WEIGHTS["volume_confirm"]

        if len(closes) >= 5:
            mom = closes[-1] - closes[i - 4]
            if (direction == "long" and mom > 0) or (direction == "short" and mom < 0):
                score += SCORE_WEIGHTS["momentum_align"]

        score += SCORE_WEIGHTS["active_session"]

        if score < min_score:
            continue

        # 入场
        entry_price = klines[i + 1]["open"]
        stg = STAGES[stage]
        margin = equity * stg["margin_pct"]
        size = margin * stg["leverage"] / entry_price

        if direction == "long":
            sl = entry_price * (1 - stop_pct)
            tp = entry_price * (1 + stop_pct * rr_ratio)
        else:
            sl = entry_price * (1 + stop_pct)
            tp = entry_price * (1 - stop_pct * rr_ratio)

        position = {
            "side": direction,
            "entry_price": entry_price,
            "size": size,
            "tp": tp,
            "sl": sl,
            "score": score,
        }
        daily_trades += 1

    # EOD
    if position:
        pnl = (closes[-1] - position["entry_price"]) * position["size"] if position["side"] == "long" else (position["entry_price"] - closes[-1]) * position["size"]
        equity += pnl
        trades.append({"pnl": round(pnl, 2), "exit_reason": "eod"})

    if not trades:
        return {"trades": 0, "equity": 100, "return": 0, "win_rate": 0, "pf": 0, "sharpe": 0, "max_dd": 0, "tp_exits": 0, "sl_exits": 0}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    eq = [100.0]
    for t in trades:
        eq.append(eq[-1] + t["pnl"])
    pk = 100.0
    max_dd = 0
    for v in eq:
        pk = max(pk, v)
        max_dd = max(max_dd, (pk - v) / pk) if pk > 0 else 0

    sharpe = 0
    if len(trades) >= 5:
        rets = [w / (100 * 0.05) for w in [100 + sum(t["pnl"] for t in trades[:j+1]) for j in range(len(trades))]]
        if len(rets) > 1:
            avg = statistics.mean(rets)
            std = statistics.stdev(rets) if len(rets) > 1 else 0.01
            sharpe = (avg / std) * math.sqrt(252 * 96) if std > 0 else 0

    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gp / gl if gl > 0 else float("inf")

    tp_count = sum(1 for t in trades if t["exit_reason"] == "tp")
    sl_count = sum(1 for t in trades if t["exit_reason"] == "sl")

    return {
        "trades": len(trades),
        "equity": round(equity, 2),
        "return": round(equity - 100, 2),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
        "pf": round(pf, 2),
        "sharpe": round(sharpe, 3),
        "max_dd": round(max_dd * 100, 1),
        "tp_exits": tp_count,
        "sl_exits": sl_count,
    }


# ── 网格搜索 ──
if __name__ == "__main__":
    print("=== HT_DCPHASE 固定盈亏比 网格优化 ===\n")

    # 加载数据
    k = fetch_klines('ETH_USDT', '15m', limit=500)
    seen = set()
    unique = []
    for c in k:
        if c['time'] not in seen:
            seen.add(c['time'])
            unique.append(c)
    unique.sort(key=lambda x: x['time'])
    print(f"数据: {len(unique)} K线, {datetime.fromtimestamp(unique[0]['time'])} -> {datetime.fromtimestamp(unique[-1]['time'])}\n")

    # 网格
    stops = [0.01, 0.015, 0.02]
    rrs = [1.5, 2.0, 2.5, 3.0]
    scores = [6, 7, 8]

    results = []
    for stop in stops:
        for rr in rrs:
            for ms in scores:
                r = run_optimized_backtest(unique, stop, rr, ms)
                r["stop"] = stop
                r["rr"] = rr
                r["min_score"] = ms
                results.append(r)

    # 排序：优先盈利 > 0
    results.sort(key=lambda x: (x["return"], x["pf"], -x["max_dd"]), reverse=True)

    print(f"{'停损':>5} {'RR':>5} {'门槛':>4} {'交易':>4} {'胜率':>6} {'PF':>6} {'盈亏':>8} {'回撤':>6} {'TP':>4} {'SL':>4}")
    print("-" * 65)
    for r in results[:20]:
        ret_str = f'{r["return"]:+.2f}%' if r["trades"] > 0 else '--'
        wr_str = f'{r["win_rate"]*100:.0f}%' if r["trades"] > 0 else '--'
        pf_str = f'{r["pf"]}' if r["trades"] > 0 else '--'
        print(f'{r["stop"]*100:>4.1f}% {r["rr"]:>4.1f} {r["min_score"]:>4} {r["trades"]:>4} {wr_str:>6} {pf_str:>6} {ret_str:>8} {r["max_dd"]:>5.1f}% {r["tp_exits"]:>4} {r["sl_exits"]:>4}')

    print("\n--- 最优3组 ---")
    for i, r in enumerate(results[:3]):
        print(f"\n#{i+1}: 止损={r['stop']*100:.1f}%  RR={r['rr']}  门槛={r['min_score']}")
        print(f"     交易{r['trades']}笔  胜率{r['win_rate']*100:.0f}%  PF={r['pf']}  盈亏{r['return']:+.2f}%  回撤{r['max_dd']}%")
        if r['trades'] > 0:
            print(f"     TP达成{r['tp_exits']}次  SL命中{r['sl_exits']}次")
