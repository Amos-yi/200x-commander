"""
战术脑 —— HT_DCPHASE 希尔伯特变换主导周期相位 + 集成打分
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional, List
from config import SIGNAL, SCORE_WEIGHTS, MODE_QUALITY_THRESHOLD

log = logging.getLogger("commander.tactical")


@dataclass
class Signal:
    symbol: str
    direction: str
    entry_price: float
    score: int
    phase: float
    cycle_period: float


class TacticalBrain:
    """
    HT_DCPHASE: Hilbert Transform Dominant Cycle Phase
    相位角 0-360: 0=谷底, 90=上升, 180=峰顶, 270=下降
    信号: 相位上穿 90° → 做多, 相位下穿 270° → 做空
    集成打分: HT相位 + 量能 + 动量 + 时段
    """

    def generate(
        self,
        symbol: str,
        klines: List[dict],
        briefing: dict,
    ) -> Optional[Signal]:
        if briefing.get("locked"):
            return None

        if len(klines) < 50:
            return None

        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]

        # ── HT_DCPHASE ──
        phase, dc_period = self._ht_dcphase(closes)

        if len(phase) < 3:
            return None

        phase_curr = phase[-1]
        phase_prev = phase[-2]

        # ── 相位穿越检测 ──
        direction = None
        if phase_prev < 90 and phase_curr >= 90:
            direction = "long"
        elif phase_prev > 270 and phase_curr <= 270:
            direction = "short"

        if direction is None:
            # 放宽: 相位在强趋势区
            if phase_curr < 30 and phase_curr > 0:
                direction = "long"
            elif phase_curr > 330:
                direction = "long"
            elif 150 < phase_curr < 210:
                direction = "short"

        if direction is None:
            return None

        # ── 量确认 ──
        last_volume = volumes[-1]
        avg_volume = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else last_volume
        if last_volume < avg_volume * SIGNAL["volume_multiplier"]:
            return None

        # ── 集成打分 ──
        score = 0
        details = {}

        # 1. HT 相位（核心）
        phase_score = SCORE_WEIGHTS["ht_phase"]
        # 相位穿越越干净分越高
        if direction == "long":
            if phase_curr - phase_prev > 0:  # 相位在上升
                phase_score += 1
        else:
            if phase_curr - phase_prev < 0:
                phase_score += 1
        score += phase_score
        details["phase"] = phase_score

        # 2. 成交量
        if last_volume > avg_volume * SIGNAL["volume_multiplier_bonus"]:
            score += SCORE_WEIGHTS["volume_confirm"] + SCORE_WEIGHTS["volume_strong"]
            details["volume"] = SCORE_WEIGHTS["volume_confirm"] + SCORE_WEIGHTS["volume_strong"]
        else:
            score += SCORE_WEIGHTS["volume_confirm"]
            details["volume"] = SCORE_WEIGHTS["volume_confirm"]

        # 3. 动量方向
        if len(closes) >= 5:
            momentum = closes[-1] - closes[-5]
            if (direction == "long" and momentum > 0) or (direction == "short" and momentum < 0):
                score += SCORE_WEIGHTS["momentum_align"]
                details["momentum"] = SCORE_WEIGHTS["momentum_align"]

        # 4. 活跃时段
        score += SCORE_WEIGHTS["active_session"]
        details["session"] = SCORE_WEIGHTS["active_session"]

        # ── 门槛 ──
        threshold = briefing.get("quality_threshold", MODE_QUALITY_THRESHOLD.get("offensive", 6))
        if score < threshold:
            log.info(f"HT相位信号不足: {score}/{threshold} | {direction} phase:{phase_curr:.0f}° | {details}")
            return None

        dir_cn = "多头" if direction == "long" else "空头"
        log.info(f"HT相位信号: {symbol} {dir_cn} | 相位:{phase_curr:.0f}° 周期:{dc_period:.0f} | 分:{score}")
        return Signal(
            symbol=symbol,
            direction=direction,
            entry_price=closes[-1],
            score=score,
            phase=phase_curr,
            cycle_period=dc_period,
        )

    # ── HT_DCPHASE 实现 ──

    @staticmethod
    def _ht_dcphase(closes: List[float]):
        """
        Hilbert Transform Dominant Cycle Phase
        返回 (phase, dominant_cycle_period)
        phase: 0-360 度
        """
        n = len(closes)
        if n < 7:
            return [50] * n, 14

        # ── 1. 平滑 (Super Smoother, period ~10) ──
        period = 10
        a1 = math.exp(-1.414 * math.pi / period)
        b1 = 2 * a1 * math.cos(1.414 * math.pi / period)
        c1 = 1 - b1 - a1

        smooth = [closes[0]] * n
        for i in range(2, n):
            smooth[i] = c1 * (closes[i] + closes[i - 1]) / 2 + b1 * smooth[i - 1] - a1 * smooth[i - 2]

        # ── 2. 去趋势 (2-pole high-pass, period 48) ──
        hp_period = 48
        alpha = math.cos(2 * math.pi / hp_period)
        a2 = (1 - alpha) / 2

        detrender = [0.0] * n
        for i in range(3, n):
            detrender[i] = (1 - a2) * (smooth[i] - smooth[i - 2]) + alpha * (1 + a2) * detrender[i - 1] - a2 * detrender[i - 2]

        # ── 3. 希尔伯特变换 → I 和 Q ──
        q1 = [0.0] * n
        i1 = [0.0] * n

        for i in range(7, n):
            q1[i] = (0.0962 * detrender[i] + 0.5769 * detrender[i - 2]
                     - 0.5769 * detrender[i - 4] - 0.0962 * detrender[i - 6]) * (0.075 * period + 0.54)
            i1[i] = detrender[i - 3]

        # ── 4. 相位角 ──
        phase = [0.0] * n
        for i in range(7, n):
            if i1[i] != 0:
                raw = math.atan(q1[i] / i1[i]) * 180 / math.pi
                # 调整到 0-360
                if i1[i] < 0 and q1[i] > 0:
                    raw += 180
                elif i1[i] < 0 and q1[i] < 0:
                    raw += 180
                elif i1[i] > 0 and q1[i] < 0:
                    raw += 360
                phase[i] = raw % 360
            else:
                phase[i] = phase[i - 1] if i > 0 else 0

        # ── 5. 平滑相位（环形处理）──
        phase_smooth = [phase[0]]
        for i in range(1, n):
            diff = phase[i] - phase_smooth[i - 1]
            if diff > 180:
                diff -= 360
            elif diff < -180:
                diff += 360
            smoothed = phase_smooth[i - 1] + 0.3 * diff
            phase_smooth.append(smoothed % 360)
        phase = phase_smooth

        # ── 6. 主导周期检测 ──
        dc_period = 20
        last_cross = 0
        for i in range(8, len(phase)):
            if phase[i - 1] < 180 and phase[i] >= 180:
                if last_cross > 0:
                    cycle_len = i - last_cross
                    if 6 < cycle_len < 60:
                        dc_period = int(0.7 * dc_period + 0.3 * cycle_len)
                last_cross = i

        return phase, dc_period
