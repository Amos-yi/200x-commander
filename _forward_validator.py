"""
前瞻验证器 —— 信号出现后先跟踪 3 根 K 线（45 分钟），方向对了才开仓。
"""

import logging
from datetime import datetime

log = logging.getLogger("commander.fwd")


class ForwardValidator:
    """
    信号 → 跟踪 N 根 K 线 → 浮盈 → 开仓；浮亏 → 丢弃。
    作用：过滤假突破，在"信号→开仓"之间插一层实时市场验证。
    """

    def __init__(self, bars_to_wait: int = 3, min_profit_pct: float = 0.001):
        self.bars_to_wait = bars_to_wait
        self.min_profit_pct = min_profit_pct

        self.pending = None          # dict or None
        self.bar_count = 0
        self.stats = {
            "passed": 0,
            "failed": 0,
            "total_passed_pnl": 0.0,
            "total_failed_pnl": 0.0,
        }

    # ── 注册信号 ────────────────────────────

    def register(self, symbol: str, direction: str, entry_price: float, score: int):
        """接收 TacticalBrain 输出的信号，挂起不执行"""
        self.pending = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "score": score,
            "registered_at": datetime.now(),
        }
        self.bar_count = 0
        log.info(
            f"前瞻验证注册: {symbol} {direction} @{entry_price:.4f} "
            f"评分:{score} 等待{self.bars_to_wait}根K线验证"
        )

    # ── 每根 K 线调用 ──────────────────────

    def tick(self, current_price: float) -> str:
        """
        每根新 K 线（15m）调用一次。
        返回: "waiting" | "pass" | "fail"
        """
        if self.pending is None:
            return "waiting"

        self.bar_count += 1

        if self.bar_count < self.bars_to_wait:
            # 计算当前浮盈百分比
            pct = self._pnl_pct(current_price)
            log.debug(
                f"前瞻验证 [{self.bar_count}/{self.bars_to_wait}] "
                f"{self.pending['symbol']} {self.pending['direction']} "
                f"浮盈:{pct:+.3%}"
            )
            return "waiting"

        # 达到等待根数，判断
        pnl_pct = self._pnl_pct(current_price)
        symbol = self.pending["symbol"]
        direction = self.pending["direction"]
        entry = self.pending["entry_price"]

        if pnl_pct >= self.min_profit_pct:
            self.stats["passed"] += 1
            self.stats["total_passed_pnl"] += pnl_pct
            log.info(
                f"前瞻验证 PASS: {symbol} {direction} "
                f"浮盈:{pnl_pct:+.3%} ({self.stats['passed']}P/{self.stats['failed']}F)"
            )
            result = "pass"
        else:
            self.stats["failed"] += 1
            self.stats["total_failed_pnl"] += pnl_pct
            log.info(
                f"前瞻验证 FAIL: {symbol} {direction} "
                f"浮盈:{pnl_pct:+.3%} ({self.stats['passed']}P/{self.stats['failed']}F)"
            )
            result = "fail"

        self.pending = None
        self.bar_count = 0
        return result

    # ── 查询 ─────────────────────────────────

    def is_waiting(self) -> bool:
        return self.pending is not None

    @property
    def pass_rate(self) -> float:
        total = self.stats["passed"] + self.stats["failed"]
        if total == 0:
            return 0.0
        return self.stats["passed"] / total

    def summary(self) -> str:
        total = self.stats["passed"] + self.stats["failed"]
        if total == 0:
            return "前瞻验证: 暂无数据"
        avg_pass = self.stats["total_passed_pnl"] / max(self.stats["passed"], 1)
        avg_fail = self.stats["total_failed_pnl"] / max(self.stats["failed"], 1)
        return (
            f"前瞻验证: {self.stats['passed']}P/{self.stats['failed']}F "
            f"通过率:{self.pass_rate:.0%} "
            f"均盈:{avg_pass:+.3%} 均亏:{avg_fail:+.3%}"
        )

    # ── 内部 ─────────────────────────────────

    def _pnl_pct(self, current_price: float) -> float:
        if not self.pending:
            return 0.0
        entry = self.pending["entry_price"]
        if entry <= 0:
            return 0.0
        if self.pending["direction"] == "long":
            return (current_price - entry) / entry
        else:
            return (entry - current_price) / entry
