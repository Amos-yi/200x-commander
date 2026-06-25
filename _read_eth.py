import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ETH_FILE = r"C:\Users\Administrator\200x_commander\rt_paper_v2_state_eth.json"
try:
    with open(ETH_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
except:
    print("ETH state not found yet")
    exit()

tracks = []
for k, d in data.items():
    eq = d.get('equity', 100)
    trades = d.get('trades', [])
    tracks.append({'name': k, 'equity': eq, 'trades': len(trades),
                   'pnl': round(eq - 100, 2)})

tracks.sort(key=lambda x: x['equity'], reverse=True)
total_eq = sum(t['equity'] for t in tracks)
active = sum(1 for t in tracks if t['trades'] > 0)

print(f"=== ETH_USDT 5m ===")
print(f"Total: {total_eq:.2f}U | Traded: {active}/{len(tracks)}")
print()
for t in tracks[:15]:
    print(f"{t['name'][:25]:<25s} {t['equity']:>8.2f} {t['pnl']:>+7.1f}%  {t['trades']:>3d}trades")
