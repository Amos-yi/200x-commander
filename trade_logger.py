"""
交易日志 —— 一行 JSON，30 天出真相
"""

import json
import os
from datetime import datetime
from typing import Optional

LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_history.jsonl")


def log_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    margin: float,
    nominal: float,
    equity_before: float,
    equity_after: float,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
    stage: int,
    stop_hit: bool,
    score: int = 0,
    mode: str = "",
) -> dict:
    record = {
        "time": datetime.now().isoformat(),
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry_price, 4),
        "exit": round(exit_price, 4),
        "margin": round(margin, 2),
        "nominal": round(nominal, 0),
        "equity_before": round(equity_before, 2),
        "equity_after": round(equity_after, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 4),
        "exit_reason": exit_reason,
        "stage": stage,
        "stop_hit": stop_hit,
        "score": score,
        "mode": mode,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_history() -> list:
    if not os.path.exists(LOG_PATH):
        return []
    records = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def recent_stats(n: int = 10) -> dict:
    """近 N 笔统计"""
    history = load_history()
    recent = history[-n:]
    if not recent:
        return {"win_rate": None, "avg_pnl": None, "avg_win": None, "avg_loss": None, "count": 0}

    wins = [r for r in recent if r["pnl"] > 0]
    losses = [r for r in recent if r["pnl"] <= 0]

    return {
        "count": len(recent),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(recent), 3) if recent else 0,
        "avg_win": round(sum(r["pnl"] for r in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(r["pnl"] for r in losses) / len(losses), 2) if losses else 0,
        "total_pnl": round(sum(r["pnl"] for r in recent), 2),
        "max_win": round(max(r["pnl"] for r in recent), 2),
        "max_loss": round(min(r["pnl"] for r in recent), 2),
        "avg_score": round(sum(r.get("score", 0) for r in recent) / len(recent), 1),
    }
