import json
with open(r'C:\Users\Administrator\200x_commander\rt_paper_v2_state.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

tracks = []
for k, d in data.items():
    eq = d.get('equity', 100)
    tracks.append({
        'name': k,
        'equity': eq,
        'return_pct': round((eq - 100) / 100 * 100, 1),
        'trades': len(d.get('trades', [])),
        'pnl': round(eq - 100, 2),
    })

tracks.sort(key=lambda x: x['equity'], reverse=True)
total_eq = sum(t['equity'] for t in tracks)
active = sum(1 for t in tracks if t['trades'] > 0)

print(f"Total tracks: {len(tracks)}")
print(f"Total equity: {total_eq:.2f}")
print(f"Active (traded): {active}")
print()
for t in tracks[:15]:
    print(f"{t['name'][:25]:<25s} {t['equity']:>8.2f} {t['return_pct']:>+7.1f}%  {t['trades']:>3d}trades  PnL:{t['pnl']:>+8.2f}")
