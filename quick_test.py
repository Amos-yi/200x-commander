"""HT_DCPHASE 快速回测"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gate_data import fetch_klines
from backtest import BacktestEngine, _ht_phase
from datetime import datetime

k = fetch_klines('ETH_USDT', '15m', limit=500)
seen = set()
unique = []
for c in k:
    if c['time'] not in seen:
        seen.add(c['time'])
        unique.append(c)
unique.sort(key=lambda x: x['time'])

closes = [c['close'] for c in unique]
phase, dc = _ht_phase(closes)
crosses = sum(1 for i in range(1, len(phase)) if (phase[i-1] < 90 and phase[i] >= 90) or (phase[i-1] > 270 and phase[i] <= 270))
print(f'{len(unique)} klines | 相位穿越: {crosses} | 主导周期: {dc:.0f}')

e = BacktestEngine(unique, 100.0, 'ETH_USDT')
r = e.run()

print()
print('='*50)
print('  HT_DCPHASE ETH_USDT 5天回测')
print('='*50)
if 'error' in r:
    print(f'  {r["error"]}')
else:
    print(f'  最终净值: {r["final_equity"]} U ({r["total_return_pct"]:+.2f}%)')
    print(f'  交易: {r["total_trades"]}笔 | 胜率:{r["win_rate"]*100:.0f}% | PF:{r["profit_factor"]}')
    print(f'  夏普: {r["sharpe_ratio"]} | 回撤: {r["max_drawdown_pct"]}%')
    print(f'  退出: {r["exit_reasons"]}')
print('='*50)
