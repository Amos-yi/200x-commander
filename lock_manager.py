"""
纪律锁管理器 —— 整个系统最不可绕过的模块
"""

import json
import os
from datetime import datetime, timedelta
from typing import Tuple, Optional
from config import LOCKS, SIGNAL


STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")


class LockManager:
    """
    主循环第一行调用 is_locked()。
    返回 True → 跳过所有信号、循环继续等。
    """

    def __init__(self):
        self.state = self._load_state()
        self._reset_daily_if_new_day()

    # ── 主入口 ──

    def is_locked(self) -> Tuple[bool, str]:
        """返回 (是否锁定, 原因)"""
        now = datetime.now()

        # [1] 时段锁
        if now.hour < SIGNAL["active_hour_start"] or now.hour >= SIGNAL["active_hour_end"]:
            return True, f"非交易时段 ({SIGNAL['active_hour_start']:02d}:00-{SIGNAL['active_hour_end']:02d}:00)"

        # [2] 事件锁
        from calendar import Calendar
        if Calendar.is_event_window(now):
            return True, f"宏观事件窗口: {Calendar.event_name(now)}"

        # [3] 日变更 — 重置计数
        self._reset_daily_if_new_day()

        # [4] 日内笔数锁
        if self.state["daily_trades"] >= LOCKS["max_trades_per_day"]:
            return True, f"今日已达{self.state['daily_trades']}/{LOCKS['max_trades_per_day']}笔上限"

        # [5] 连续亏损锁
        lock = self._check_timed_lock("consecutive_loss")
        if lock:
            return lock

        # [6] 日亏损锁
        lock = self._check_timed_lock("daily_loss")
        if lock:
            return lock

        # [7] 手动锁
        lock = self._check_timed_lock("manual")
        if lock:
            return lock

        # [8] 撤止损锁
        lock = self._check_timed_lock("stop_cancel")
        if lock:
            return lock

        return False, ""

    # ── 事件钩子 ──

    def on_trade_closed(self, pnl: float, equity: float):
        """每笔平仓后调用"""
        self.state["daily_trades"] += 1
        self.state["daily_pnl"] += pnl

        if pnl < 0:
            self.state["consecutive_losses"] += 1
            if self.state["consecutive_losses"] >= LOCKS["max_consecutive_losses"]:
                self._set_timed_lock(
                    "consecutive_loss",
                    LOCKS["consecutive_loss_hours"]
                )
        else:
            self.state["consecutive_losses"] = 0

        # 日亏损检查
        if equity > 0 and self.state["daily_pnl"] < -(equity * LOCKS["daily_loss_pct"]):
            self._set_timed_lock("daily_loss", LOCKS["daily_loss_hours"])

        self._save_state()

    def on_stop_cancelled(self):
        """检测到手动撤销止损 → 锁 7 天"""
        self._set_timed_lock("stop_cancel", LOCKS["stop_cancel_lock_days"] * 24)
        self._save_state()

    def manual_lock(self):
        self._set_timed_lock("manual", LOCKS["manual_lock_hours"])
        self._save_state()

    # ── 状态查询 ──

    def daily_trades(self) -> int:
        return self.state["daily_trades"]

    def daily_trades_left(self) -> int:
        return max(0, LOCKS["max_trades_per_day"] - self.state["daily_trades"])

    def consecutive_losses(self) -> int:
        return self.state["consecutive_losses"]

    def daily_pnl(self) -> float:
        return self.state["daily_pnl"]

    def status(self) -> dict:
        locked, reason = self.is_locked()
        return {
            "locked": locked,
            "reason": reason,
            "daily_trades": self.state["daily_trades"],
            "max_daily": LOCKS["max_trades_per_day"],
            "consecutive_losses": self.state["consecutive_losses"],
            "daily_pnl": round(self.state["daily_pnl"], 2),
            "locks": {k: v for k, v in self.state["locks"].items() if v},
        }

    # ── 内部 ──

    def _reset_daily_if_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.state.get("last_trade_date", ""):
            self.state["last_trade_date"] = today
            self.state["daily_trades"] = 0
            self.state["daily_pnl"] = 0.0

    def _check_timed_lock(self, name: str) -> Optional[Tuple[bool, str]]:
        until = self.state["locks"].get(name)
        if until:
            dt = datetime.fromisoformat(until)
            if datetime.now() < dt:
                labels = {
                    "consecutive_loss": f"连亏{LOCKS['max_consecutive_losses']}笔强制冷静",
                    "daily_loss": f"日亏损超{LOCKS['daily_loss_pct']*100:.0f}%熔断",
                    "manual": "手动锁定",
                    "stop_cancel": "撤止损惩罚",
                }
                return True, f"{labels.get(name, name)} → {dt.strftime('%m-%d %H:%M')} 解锁"
            else:
                del self.state["locks"][name]
                if name == "consecutive_loss":
                    self.state["consecutive_losses"] = 0
        return None

    def _set_timed_lock(self, name: str, hours: float):
        self.state["locks"][name] = (
            datetime.now() + timedelta(hours=hours)
        ).isoformat()
        self._save_state()

    def _load_state(self) -> dict:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                return json.load(f)
        return self._default_state()

    def _save_state(self):
        with open(STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _default_state() -> dict:
        return {
            "last_trade_date": "",
            "daily_trades": 0,
            "daily_pnl": 0.0,
            "consecutive_losses": 0,
            "locks": {},
        }
