"""通用报告：python _report.py [state_file_path]"""
import json, sys, io, os
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_BASE = Path(__file__).resolve().parent

if len(sys.argv) > 1:
    STATE_FILE = sys.argv[1]
else:
    STATE_FILE = str(_BASE / "rt_paper_v2_state.json")

if not os.path.exists(STATE_FILE):
    print(f"  :: 文件不存在: {STATE_FILE}")
    sys.exit(0)

try:
    data = json.load(open(STATE_FILE, encoding='utf-8'))
except (json.JSONDecodeError, FileNotFoundError, Exception) as e:
    print(f"  :: 文件损坏或空: {STATE_FILE} ({e})")
    sys.exit(0)

symbol = os.path.basename(STATE_FILE).replace("rt_paper_v2_state_", "").replace(".json", "").upper() or "SOL"

rows = []
for key, d in data.items():
    eq = d.get("equity", 100)
    peak = d.get("peak_equity", eq)
    trades = d.get("trades", [])
    tc = len(trades)
    tp2_ct = sum(1 for t in trades if "TP2" in str(t.get("exit_reason", "")))
    sl_ct = sum(1 for t in trades if "止损" in str(t.get("exit_reason", "")))
    rev_ct = sum(1 for t in trades if "反转" in str(t.get("exit_reason", "")))
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    total_p = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
    total_l = sum(abs(t.get("pnl", 0)) for t in trades if t.get("pnl", 0) < 0)
    wr = wins / tc * 100 if tc else 0
    pf = total_p / total_l if total_l > 0 else 99.9
    # TODO(CONFIG): avg_r uses hardcoded risk=15.0U based on margin=5U × stop=1.5% × leverage=200x.
    # If MARGIN_PCT, HARD_STOP_PCT, or LEVERAGE change in rt_paper_v2.py, update this constant.
    avg_r = sum(abs(t.get("pnl", 0)) / 15.0 for t in trades) / tc if tc else 0
    dd = (1 - eq / peak) * 100 if peak > 0 else 0
    pos = d.get("position")
    side = pos["side"].upper() if pos else ""
    rows.append([key, eq, eq - 100, tc, tp2_ct, sl_ct, rev_ct, wr, pf, avg_r, dd, side])

rows.sort(key=lambda x: x[1], reverse=True)

print(f"=== {symbol}_USDT 5m Report ===")
print(f"{'#':>3s} {'Strategy':<26s} {'Eq':>8s} {'PnL%':>7s} {'Trade':>5s} {'TP2':>4s} {'SL':>4s} {'Rev':>4s} {'Win%':>5s} {'PF':>6s} {'|R|':>5s} {'DD%':>5s} {'Pos'}")
print("-" * 110)

for i, r in enumerate(rows[:30], 1):
    n, eq, pnl, tc, tp2, sl, rev, wr, pf, ar, dd, side = r
    print(f"{i:>3d} {n:<26s} {eq:>8.2f} {pnl:>+6.1f}% {tc:>5d} {tp2:>4d} {sl:>4d} {rev:>4d} {wr:>4.0f}% {pf:>6.2f} {ar:>5.2f} {dd:>4.1f}% {side:>4s}")

te = sum(r[1] for r in rows)
tt = sum(r[3] for r in rows)
print(f"\nTOTAL: {te:.0f}U | Trades: {tt} | Strategies: {len(rows)}")

bins = {"+50%": 0, "+30~50%": 0, "+10~30%": 0, "+0~10%": 0, "-0~-15%": 0, "-15%↓": 0, "未交易": 0}
for r in rows:
    pnl, tc = r[2], r[3]
    if tc == 0: bins["未交易"] += 1
    elif pnl >= 50: bins["+50%"] += 1
    elif pnl >= 30: bins["+30~50%"] += 1
    elif pnl >= 10: bins["+10~30%"] += 1
    elif pnl >= 0: bins["+0~10%"] += 1
    elif pnl >= -15: bins["-0~-15%"] += 1
    else: bins["-15%↓"] += 1
print()
for k, v in bins.items():
    print(f"  {k:<8s} {v:>3d}  {'█' * v}")
