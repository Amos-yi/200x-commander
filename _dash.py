#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仪表盘：python _dash.py —— 扫描所有 state 文件，显示全部在跑币种的净值"""
import json, sys, io, os, glob, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")

BASE = r"C:\Users\Administrator\200x_commander"

# All 25 coins the auto_deploy scans
ALL_COINS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "AVAX_USDT", "DOT_USDT", "LINK_USDT",
    "MATIC_USDT", "UNI_USDT", "ATOM_USDT", "LTC_USDT", "FIL_USDT",
    "APT_USDT", "ARB_USDT", "OP_USDT", "SUI_USDT", "SEI_USDT",
    "TIA_USDT", "INJ_USDT", "RUNE_USDT", "FET_USDT", "RNDR_USDT",
]


def coin_to_state_file(symbol):
    """Map symbol to expected state file path."""
    prefix = symbol.split("_")[0].lower()
    if prefix == "sol":
        return os.path.join(BASE, "rt_paper_v2_state.json")
    return os.path.join(BASE, f"rt_paper_v2_state_{prefix}.json")


def load_state(fp):
    """Load state JSON. Returns (data, error_string)."""
    try:
        with open(fp, encoding="utf-8") as fh:
            data = json.load(fh)
        return data, None
    except json.JSONDecodeError as e:
        logging.warning("Corrupted JSON in %s: %s", os.path.basename(fp), e)
        return None, "CORRUPTED"
    except FileNotFoundError:
        return None, "NOT_STARTED"
    except Exception as e:
        logging.warning("Failed to load %s: %s", os.path.basename(fp), e)
        return None, f"ERROR: {e}"


def scan_disk_states():
    """Return dict {symbol: (data, error_string)} for all 25 coins."""
    result = {}
    for sym in ALL_COINS:
        fp = coin_to_state_file(sym)
        data, err = load_state(fp)
        result[sym] = (data, err)
    return result


print("=" * 80)
print("  仪表盘 — 全币种概览")
print("=" * 80)

total_equity = 0
total_trades = 0
coins_seen = 0

states = scan_disk_states()

for sym, (data, err) in sorted(states.items()):
    if err:
        status_icon = {"NOT_STARTED": "○", "CORRUPTED": "✗ BROKEN"}.get(err, f"✗ {err}")
        print(f"\n  {status_icon} {sym:<14}  -- {err} --")
        continue

    eq = sum(v.get("equity", 100) for v in data.values())
    tr = sum(len(v.get("trades", [])) for v in data.values())
    top = sorted(data.items(), key=lambda x: x[1].get("equity", 100), reverse=True)[:3]
    total_equity += eq
    total_trades += tr
    coins_seen += 1

    print(f"\n  ▸ {sym:<14} 净值 {eq:>8.0f}U | 交易 {tr:>5d} | 策略 {len(data):>3d}")
    for key, d in top:
        eq_s = d.get("equity", 100)
        pnl = eq_s - 100
        tc = len(d.get("trades", []))
        wr = sum(1 for t in d.get("trades", []) if t.get("pnl", 0) > 0) / tc * 100 if tc else 0
        print(f"     {key:<28s} {eq_s:>8.2f} {pnl:>+6.1f}%  {tc:>3d}t  WR:{wr:.0f}%")

print(f"\n{'='*80}")
print(f"  总计: {total_equity:.0f}U | 交易: {total_trades} | 币种: {coins_seen}/{len(ALL_COINS)}")
print()
