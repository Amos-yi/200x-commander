import json, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

STATE_FILE = r"C:\Users\Administrator\200x_commander\rt_paper_v2_state.json"
if not os.path.exists(STATE_FILE):
    print("NO STATE FILE")
    exit()

with open(STATE_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

traded = []
for key, d in data.items():
    eq = d.get("equity", 100)
    trades = d.get("trades", [])
    win = sum(1 for t in trades if t.get("pnl", 0) > 0) if trades else 0
    wr = win / len(trades) * 100 if trades else 0
    daily_losses = d.get("consecutive_losses", 0)
    locked = "LOCK" if d.get("locked_until") else ""
    pos = d.get("position")
    pos_side = pos["side"] if pos else ""
    traded.append((key, eq, len(trades), wr, win, d.get("peak_equity", 100), pos_side, locked))

traded.sort(key=lambda x: x[1], reverse=True)

for i, (name, eq, tc, wr, wn, peak, side, lk) in enumerate(traded[:20], 1):
    pct = eq - 100
    print(f"#{i:02d} {name:<24s} {eq:>8.2f} {pct:>+7.1f}%  {tc}trades  WR:{wr:>5.1f}%  peak:{peak:>7.2f}  {side:>5s} {lk}")
