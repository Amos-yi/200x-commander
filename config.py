"""
200x Commander Bot — HT_DCPHASE × Ensemble
"""

STAGES = {
    1: {
        "name": "脆弱期",
        "equity_range": (100, 500),
        "margin_pct": 0.05,
        "leverage": 200,
        "hard_stop_pct": 0.015,
        "liquidation_buffer_pct": 0.005,
        "symbols": ["ETH_USDT"],
        "target": 500,
    },
    2: {
        "name": "安全垫期",
        "equity_range": (500, 2000),
        "margin_pct": 0.05,
        "leverage": 200,
        "hard_stop_pct": 0.02,
        "liquidation_buffer_pct": 0.01,
        "symbols": ["ETH_USDT"],
        "target": 2000,
    },
    3: {
        "name": "加速期",
        "equity_range": (2000, 10000),
        "margin_pct": 0.03,
        "leverage": 200,
        "hard_stop_pct": 0.015,
        "liquidation_buffer_pct": 0.008,
        "symbols": ["ETH_USDT", "SOL_USDT"],
        "target": 10000,
    },
    4: {
        "name": "收官期",
        "equity_range": (10000, 60000),
        "margin_pct": 0.02,
        "leverage": 200,
        "hard_stop_pct": 0.01,
        "liquidation_buffer_pct": 0.005,
        "symbols": ["ETH_USDT", "SOL_USDT"],
        "target": 60000,
    },
}

FINAL_TARGET = 60000

# ============================================================
# 信号规则 — HT_DCPHASE × Ensemble
# ============================================================

SIGNAL = {
    "ht_cycle_min": 8,
    "ht_cycle_max": 30,
    "kline_interval": "15m",
    "volume_multiplier": 1.2,
    "volume_multiplier_bonus": 2.0,
    "min_24h_volume_usdt": 30_000_000,
    "active_hour_start": 7,
    "active_hour_end": 23,
    "weekend_margin_mult": 0.5,
}

SCORE_WEIGHTS = {
    "ht_phase": 5,             # HT 相位信号（核心）
    "volume_confirm": 2,       # 量确认
    "volume_strong": 1,        # 巨量加分
    "momentum_align": 2,       # 动量方向一致
    "active_session": 1,       # 活跃时段
}

MODE_QUALITY_THRESHOLD = {
    "offensive": 6,
    "standard": 7,
    "repair": 8,
    "survival": 9,
    "harvest": 7,
    "forbidden": 10,
}

EXIT = {
    "tp1_pct": 0.03,
    "tp1_ratio": 0.40,
    "tp2_pct": 0.05,
    "tp2_ratio": 0.30,
    "breakeven_multiplier": 1.5,
    "time_stop_hours": 8,
    "time_stop_min_pnl": 0.01,
}

LOCKS = {
    "max_trades_per_day": 2,
    "max_consecutive_losses": 3,
    "daily_loss_pct": 0.20,
    "consecutive_loss_hours": 24,
    "daily_loss_hours": 72,
    "manual_lock_hours": 24,
    "stop_cancel_lock_days": 7,
}

PROFIT_RULES = {
    1: {"withdraw_pct": 0.0},
    2: {"withdraw_pct": 0.10, "trigger_every": 500},
    3: {"withdraw_pct": 0.15, "trigger_every": 1000},
    4: {"withdraw_pct": 0.50, "trigger_every": 0},
}

MODE_RISK_BUDGET = {
    "offensive": 1.2,
    "standard": 1.0,
    "repair": 0.5,
    "survival": 0.3,
    "harvest": 0.5,
    "forbidden": 0.0,
}

MODE_MAX_TRADES = {
    "offensive": 2,
    "standard": 2,
    "repair": 1,
    "survival": 1,
    "harvest": 1,
    "forbidden": 0,
}
