import json
with open(r'C:\Users\Administrator\200x_commander\rt_paper_v2_leaderboard.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print('更新时间:', data.get('updated_at', '?'))
print('总策略:', data.get('total_tracks', '?'))
print('活跃持仓:', data.get('active_positions', '?'))
print('总净值:', data.get('total_equity', '?'))
ranked = sorted(data.get('tracks', []), key=lambda x: x['equity'], reverse=True)
for t in ranked[:15]:
    side = '—'
    if t.get('position'):
        side = 'LONG' if t['position'].get('side') == 'long' else 'SHORT'
    print(f"{t['name'][:22]:<22s} {t['equity']:>7.2f} {t['return_pct']:>+6.1f}% {t['trades']:>4d}  {t['win_rate']:>5.1f}% {t['avg_r']:>+6.2f}R {side:>6s}")
