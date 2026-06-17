"""
净值 → 阶段 → 仓位参数 + 进度追踪
"""

from typing import Optional, Tuple
from config import STAGES, FINAL_TARGET


class StageManager:
    """负责回答：我们在哪？离目标多远？该用多大仓位？"""

    def __init__(self, equity: float):
        self.equity = equity
        self.stage = self._resolve_stage()
        self._override_mult = 1.0  # 跨阶段首日半仓用

    def _resolve_stage(self) -> int:
        for sid in sorted(STAGES.keys()):
            lo, hi = STAGES[sid]["equity_range"]
            if lo <= self.equity < hi:
                return sid
        return 4

    # ── 查询 ──

    def stage_name(self) -> str:
        return STAGES[self.stage]["name"]

    def stage_target(self) -> float:
        return STAGES[self.stage]["target"]

    def distance_to_stage_target(self) -> float:
        return max(0.0, self.stage_target() - self.equity)

    def distance_to_final(self) -> float:
        return max(0.0, FINAL_TARGET - self.equity)

    def progress_in_stage(self) -> float:
        """阶段内进度 0.0 ~ 1.0"""
        lo, hi = STAGES[self.stage]["equity_range"]
        span = hi - lo
        if span == 0:
            return 1.0
        return min(1.0, max(0.0, (self.equity - lo) / span))

    def progress_bar(self, width: int = 20) -> str:
        p = self.progress_in_stage()
        filled = int(p * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"{STAGES[self.stage]['equity_range'][0]}──{bar}──{STAGES[self.stage]['target']}"

    def get_params(self) -> dict:
        """当前阶段仓位参数（含覆盖系数）"""
        s = STAGES[self.stage]
        margin = self.equity * s["margin_pct"] * self._override_mult
        nominal = margin * s["leverage"]
        return {
            **s,
            "margin": round(margin, 2),
            "nominal": round(nominal, 2),
            "liquidation_distance": round(
                (self.equity - margin * 0.005) / nominal, 3
            ) if nominal > 0 else 0.0,
            "hard_stop_pct": s["hard_stop_pct"],
            "override_mult": self._override_mult,
        }

    # ── 状态变更 ──

    def update_equity(self, new_equity: float) -> Optional[str]:
        """
        返回: 'up' | 'down' | None
        阶段切换时自动设置半仓覆盖
        """
        old_stage = self.stage
        self.equity = new_equity
        self.stage = self._resolve_stage()

        if self.stage > old_stage:
            self._override_mult = 0.5  # 升级首日半仓
            return "up"
        elif self.stage < old_stage:
            self._override_mult = 0.5  # 降级首日半仓
            return "down"
        return None

    def clear_override(self):
        self._override_mult = 1.0

    def set_half_margin(self):
        self._override_mult = 0.5

    # ── 摘要 ──

    def summary(self) -> str:
        params = self.get_params()
        return (
            f"[阶段{self.stage}·{self.stage_name()}] "
            f"净值:{self.equity:.2f}U | "
            f"距阶段目标:{self.distance_to_stage_target():.0f}U | "
            f"距终点:{self.distance_to_final():.0f}U | "
            f"保证金:{params['margin']:.2f}U | "
            f"名义:{params['nominal']:.0f}U | "
            f"爆仓距:{params['liquidation_distance']:.2%} | "
            f"止损:{params['hard_stop_pct']:.1%}"
        )
