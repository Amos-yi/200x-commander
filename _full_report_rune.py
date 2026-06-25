import json, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

STATE_FILE = r"C:\Users\Administrator\200x_commander\rt_paper_v2_state_rune.json"
with open(STATE_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

rows = []
for key, d in data.items():
    eq = d.get("equity", 100)
    peak = d.get("peak_equity", eq)
    trades = d.get("trades", [])
    tc = len(trades)

    tp2_ct = sum(1 for t in trades if "TP2" in str(t.get("exit_reason", "")))
    sl_ct = sum(1 for t in trades if "止损" in str(t.get("exit_reason", "")))
    rev_ct = sum(1 for t in trades if "反转" in str(t.get("exit_reason", "")))
    time_ct = sum(1 for t in trades if "时间" in str(t.get("exit_reason", "")))

    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    losses = sum(1 for t in trades if t.get("pnl", 0) < 0)
    total_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
    total_loss = sum(abs(t.get("pnl", 0)) for t in trades if t.get("pnl", 0) < 0)

    wr = wins / tc * 100 if tc else 0
    pf = total_profit / total_loss if total_loss > 0 else 99.9
    avg_r = sum(abs(t.get("pnl", 0)) / 15.0 for t in trades) / tc if tc else 0

    dd_pct = (1 - eq / peak) * 100 if peak > 0 else 0

    pos = d.get("position")
    pos_side = pos["side"].upper() if pos else ""

    rows.append([key, eq, eq - 100, tc, tp2_ct, sl_ct, rev_ct, time_ct,
                 wr, pf, avg_r, peak, dd_pct, pos_side])

rows.sort(key=lambda x: x[1], reverse=True)

print(f"=== RUNE_USDT 5m Full Report ===")
print(f"{'#':>3s} {'Strategy':<26s} {'Eq':>8s} {'PnL%':>7s} {'Trade':>5s} {'TP2':>4s} {'SL':>4s} {'Rev':>4s} {'Win%':>5s} {'PF':>6s} {'|R|':>5s} {'DD%':>5s}")
print("-" * 130)

for i, r in enumerate(rows[:30], 1):
    n, eq, pnl, tc, tp2, sl, rev, tm, wr, pf, ar, pk, dd, side = r
    print(f"{i:>3d} {n:<26s} {eq:>8.2f} {pnl:>+6.1f}% {tc:>5d} {tp2:>4d} {sl:>4d} {rev:>4d} {wr:>4.0f}% {pf:>6.2f} {ar:>5.2f} {dd:>4.1f}% {side:>5s}")

total_eq = sum(r[1] for r in rows)
total_trades = sum(r[3] for r in rows)
total_tp2 = sum(r[4] for r in rows)
total_sl = sum(r[5] for r in rows)
total_rev = sum(r[6] for r in rows)
total_time = sum(r[7] for r in rows)
print()
print(f"TOTAL: {total_eq:.0f}U | Trades:{total_trades} | TP2:{total_tp2} SL:{total_sl} Rev:{total_rev} | {len(rows)} strategies")

bins = {"+100%": 0, "+50~100%": 0, "+0~50%": 0, "-0~-15%": 0, "-15%以下": 0, "未交易": 0}
for r in rows:
    pct = r[2]
    tc = r[3]
    if tc == 0:
        bins["未交易"] += 1
    elif pct >= 100:
        bins["+100%"] += 1
    elif pct >= 50:
        bins["+50~100%"] += 1
    elif pct >= 0:
        bins["+0~50%"] += 1
    elif pct >= -15:
        bins["-0~-15%"] += 1
    else:
        bins["-15%以下"] += 1

print()
for bk, ct in bins.items():
    bar = "█" * ct
    print(f"  {bk:<10s} {ct:>3d}  {bar}")
