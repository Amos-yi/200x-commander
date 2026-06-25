"""
战略脑 —— 整个 Bot 的灵魂
每日 07:00 运行一次，输出"今日作战指令"
"""

from datetime import datetime
from typing import Optional
from config import (
    STAGES, MODE_RISK_BUDGET, MODE_MAX_TRADES, MODE_QUALITY_THRESHOLD,
    SIGNAL,
)
from _macro_calendar import Calendar, detect_regime


class StrategicBrain:
    """
    每次主循环调用 decide()。
    如果今天是新的一天 + 过了 07:00 + 还没出过今日指令 → 重新评估。
    """

    def __init__(self):
        self._last_briefing_date = ""
        self._current_mode = "standard"
        self._mode_reason = ""
        self._today_briefing = {}

    # ── 主入口 ──

    def decide(
        self,
        equity: float,
        stage: int,
        stage_name: str,
        distance_to_target: float,
        progress_pct: float,
        consecutive_losses: int,
        recent_win_rate: Optional[float],
        recent_pnl_ratio: Optional[float],
        regime: str,
    ) -> dict:
        """
        返回今日作战指令 dict。
        如果今天已经出过指令 → 返回缓存的指令。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now()

        # 07:00 后才重新评估
        if today == self._last_briefing_date and now.hour >= SIGNAL["active_hour_start"]:
            return self._today_briefing

        if now.hour < SIGNAL["active_hour_start"]:
            self._current_mode = "forbidden"
            self._mode_reason = f"未到交易时段（{SIGNAL['active_hour_start']:02d}:00 开始）"
            self._today_briefing = self._build_briefing(
                equity, stage, stage_name, distance_to_target, progress_pct, regime
            )
            return self._today_briefing

        # ── 宏观事件日 → 禁战 ──
        if Calendar.is_event_window(now):
            self._current_mode = "forbidden"
            self._mode_reason = f"宏观事件日: {Calendar.event_name(now)}"
            self._today_briefing = self._build_briefing(
                equity, stage, stage_name, distance_to_target, progress_pct, regime
            )
            self._last_briefing_date = today
            return self._today_briefing

        # ── 连亏 2 笔 + 距离目标拉大 → 生存模式 ──
        if consecutive_losses >= 2 and distance_to_target / STAGES[stage]["target"] > 0.3:
            self._current_mode = "survival"
            self._mode_reason = f"连亏{consecutive_losses}笔且距目标较远，生存优先"
        # ── 距阶段目标 < 10% → 收官模式 ──
        elif progress_pct > 0.90:
            self._current_mode = "harvest"
            self._mode_reason = f"距阶段目标仅 {distance_to_target:.0f}U ({progress_pct:.0%})，收官保成果"
        # ── 连盈 + 趋势顺 → 进攻模式 ──
        elif (
            consecutive_losses == 0
            and (recent_win_rate is not None and recent_win_rate >= 0.5)
            and regime in ("trending_up", "trending_down")
            and progress_pct < 0.70
        ):
            self._current_mode = "offensive"
            self._mode_reason = f"连盈+趋势{regime}+距目标尚远，积极进攻"
        # ── 刚亏 1 笔 → 标准模式 ──
        elif consecutive_losses <= 1:
            self._current_mode = "standard"
            self._mode_reason = "正常节奏"
        # ── 连亏中但未触发生存 → 修复模式 ──
        else:
            self._current_mode = "repair"
            self._mode_reason = f"连亏{consecutive_losses}笔，谨慎修复"

        self._last_briefing_date = today
        self._today_briefing = self._build_briefing(
            equity, stage, stage_name, distance_to_target, progress_pct, regime
        )
        return self._today_briefing

    # ── 指令构建 ──

    def _build_briefing(
        self,
        equity: float,
        stage: int,
        stage_name: str,
        distance_to_target: float,
        progress_pct: float,
        regime: str,
    ) -> dict:
        risk_mult = MODE_RISK_BUDGET[self._current_mode]
        max_trades = MODE_MAX_TRADES[self._current_mode]
        quality_threshold = MODE_QUALITY_THRESHOLD[self._current_mode]

        # 周末减半
        if Calendar.is_weekend():
            risk_mult *= 0.5

        briefing = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "equity": round(equity, 2),
            "stage": stage,
            "stage_name": stage_name,
            "distance_to_target": round(distance_to_target, 2),
            "distance_to_final": round(60000 - equity, 2),
            "progress_in_stage": round(progress_pct, 3),
            "regime": regime,
            "mode": self._current_mode,
            "mode_reason": self._mode_reason,
            "risk_budget_multiplier": risk_mult,
            "max_trades_today": max_trades,
            "quality_threshold": quality_threshold,
            "is_weekend": Calendar.is_weekend(),
            "locked": self._current_mode == "forbidden",
        }
        return briefing

    # ── 查询 ──

    def mode(self) -> str:
        return self._current_mode

    def briefing(self) -> dict:
        return self._today_briefing

    def briefing_text(self) -> str:
        b = self._today_briefing
        if not b:
            return "今日作战指令尚未生成"

        emoji = {"offensive": "⚔️", "standard": "✅", "repair": "🩹",
                  "survival": "🛡️", "harvest": "🏁", "forbidden": "🚫"}
        e = emoji.get(b["mode"], "❓")

        lines = [
            f"{'='*50}",
            f"  {e} 今日作战指令 | {b['date']} {b['time']}",
            f"  {'='*50}",
            f"  净值: {b['equity']:.2f}U | 阶段{b['stage']}·{b['stage_name']}",
            f"  距阶段目标: {b['distance_to_target']:.0f}U ({b['progress_in_stage']:.0%})",
            f"  距终点: {b['distance_to_final']:.0f}U",
            f"  市场体制: {b['regime']}",
            f"  {'='*50}",
            f"  模式: {b['mode']} ({b['mode_reason']})",
            f"  风险预算: x{b['risk_budget_multiplier']} | 最多{b['max_trades_today']}笔 | 质量门槛: {b['quality_threshold']}/10",
        ]

        if b["locked"]:
            lines.append(f"  🔒 今日禁战 —— {b['mode_reason']}")
            lines.append(f"  心态: 不交易也是交易的一部分")

        lines.append(f"{'='*50}")
        return "\n".join(lines)

    def mindset_hint(self) -> str:
        """根据当前模式给出心态提示"""
        hints = {
            "offensive": "顺势而为，别飘",
            "standard": "按部就班，不急不躁",
            "repair": "伤好了再跑，不急这一下",
            "survival": "活下来比什么都重要",
            "harvest": "最后一步最容易摔，稳一点",
            "forbidden": "今天最大的胜利就是不开仓",
        }
        return hints.get(self._current_mode, "保持冷静")
