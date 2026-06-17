"""
策略工厂 — 参数扫荡生成器
6 个模板 × 参数网格 = 141 个策略变体
"""

import math
from typing import Optional, List, Callable
from dataclasses import dataclass
from strategies import (
    _ema, _tema, _rsi, _atr, _fos, _ht_phase,
    check_hard_stop, check_tp, Signal,
)


# ═══════════════════════════════════════════
# 策略生成器工厂
# ═══════════════════════════════════════════

def make_ema_cross(fast: int, slow: int, vol_filter: float):
    """EMA 交叉策略"""

    def generate(klines, briefing):
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]
        if len(closes) < slow + 5:
            return None
        e1 = _ema(closes, fast)
        e2 = _ema(closes, slow)
        prev = e1[-3] - e2[-3]
        curr = e1[-1] - e2[-1]
        cross = None
        if prev < 0 < curr:
            cross = "long"
        elif prev > 0 > curr:
            cross = "short"
        if cross is None:
            return None
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] < avg_vol * vol_filter:
            return None
        return Signal(direction=cross, entry_price=closes[-1], score=6, extra={})

    def exit_func(position, klines):
        closes = [k["close"] for k in klines]
        if len(closes) < slow + 5:
            return None
        e1 = _ema(closes, fast)
        e2 = _ema(closes, slow)
        prev = e1[-3] - e2[-3]
        curr = e1[-1] - e2[-1]
        if position["side"] == "long" and prev > 0 > curr:
            return "ema_dead"
        if position["side"] == "short" and prev < 0 < curr:
            return "ema_golden"
        return None

    return {"name": f"EMA{fast}x{slow}", "generate": generate, "exit": exit_func}


def make_rsi(period: int, oversold: int, overbought: int, vol_filter: float):
    """RSI 超卖/超买反弹"""

    def generate(klines, briefing):
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]
        if len(closes) < period + 10:
            return None
        r = _rsi(closes, period)
        rc = r[-1]
        rp = r[-2]
        direction = None
        if rp < oversold and rc >= oversold:
            direction = "long"
        elif rp > overbought and rc <= overbought:
            direction = "short"
        if direction is None:
            return None
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] < avg_vol * vol_filter:
            return None
        return Signal(direction=direction, entry_price=closes[-1], score=6, extra={"rsi": rc})

    def exit_func(position, klines):
        closes = [k["close"] for k in klines]
        if len(closes) < period + 5:
            return None
        r = _rsi(closes, period)
        rc = r[-1]
        if position["side"] == "long" and rc > 50:
            return "rsi_50_exit"
        if position["side"] == "short" and rc < 50:
            return "rsi_50_exit"
        return None

    name = f"RSI{period}[{oversold},{overbought}]"
    return {"name": name, "generate": generate, "exit": exit_func}


def make_ema_rsi(ema_fast: int, ema_slow: int, rsi_max_long: int, rsi_min_short: int, vol_filter: float):
    """EMA 交叉 + RSI 确认"""

    def generate(klines, briefing):
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]
        if len(closes) < ema_slow + 15:
            return None
        e1 = _ema(closes, ema_fast)
        e2 = _ema(closes, ema_slow)
        r = _rsi(closes, 14)
        prev = e1[-3] - e2[-3]
        curr = e1[-1] - e2[-1]
        cross = None
        if prev < 0 < curr:
            cross = "long"
        elif prev > 0 > curr:
            cross = "short"
        if cross is None:
            return None
        rc = r[-1]
        if cross == "long" and rc > rsi_max_long:
            return None
        if cross == "short" and rc < rsi_min_short:
            return None
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] < avg_vol * vol_filter:
            return None
        return Signal(direction=cross, entry_price=closes[-1], score=7, extra={})

    def exit_func(position, klines):
        closes = [k["close"] for k in klines]
        if len(closes) < ema_slow + 15:
            return None
        e1 = _ema(closes, ema_fast)
        e2 = _ema(closes, ema_slow)
        r = _rsi(closes, 14)
        prev = e1[-3] - e2[-3]
        curr = e1[-1] - e2[-1]
        if position["side"] == "long" and prev > 0 > curr and r[-1] > 50:
            return "ema_rsix_exit"
        if position["side"] == "short" and prev < 0 < curr and r[-1] < 50:
            return "ema_rsix_exit"
        return None

    name = f"xRSI{ema_fast}x{ema_slow}r{rsi_max_long}{rsi_min_short}"
    return {"name": name, "generate": generate, "exit": exit_func}


def make_kc(ema_period: int, atr_period: int, multiplier: float, vol_filter: float):
    """肯特纳通道突破"""

    def generate(klines, briefing):
        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        volumes = [k["volume"] for k in klines]
        if len(closes) < ema_period + atr_period + 5:
            return None
        ema = _ema(closes, ema_period)
        atr = _atr(highs, lows, closes, atr_period)
        upper = [ema[i] + atr[i] * multiplier for i in range(len(ema))]
        lower = [ema[i] - atr[i] * multiplier for i in range(len(ema))]
        lc = closes[-1]
        pc = closes[-2]
        direction = None
        if lc > upper[-1] and pc <= upper[-2]:
            direction = "long"
        elif lc < lower[-1] and pc >= lower[-2]:
            direction = "short"
        if direction is None:
            return None
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] < avg_vol * vol_filter:
            return None
        return Signal(direction=direction, entry_price=lc, score=7, extra={})

    def exit_func(position, klines):
        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        if len(closes) < ema_period + atr_period + 5:
            return None
        ema = _ema(closes, ema_period)
        if position["side"] == "long" and closes[-1] <= ema[-1]:
            return "kc_mid"
        if position["side"] == "short" and closes[-1] >= ema[-1]:
            return "kc_mid"
        return None

    name = f"KC{ema_period}/{atr_period}x{multiplier}"
    return {"name": name, "generate": generate, "exit": exit_func}


def make_tema_fos(fast: int, slow: int, fos_period: int, fos_threshold: float, vol_filter: float):
    """TEMA 交叉 + FOS 确认"""

    def generate(klines, briefing):
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]
        if len(closes) < slow + fos_period + 5:
            return None
        t1 = _tema(closes, fast)
        t2 = _tema(closes, slow)
        f = _fos(closes, fos_period)
        prev = t1[-3] - t2[-3]
        curr = t1[-1] - t2[-1]
        cross = None
        if prev < 0 < curr:
            cross = "long"
        elif prev > 0 > curr:
            cross = "short"
        if cross is None:
            return None
        fc = f[-1]
        if cross == "long" and fc > fos_threshold:
            return None
        if cross == "short" and fc < -fos_threshold:
            return None
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] < avg_vol * vol_filter:
            return None
        return Signal(direction=cross, entry_price=closes[-1], score=7, extra={})

    def exit_func(position, klines):
        closes = [k["close"] for k in klines]
        if len(closes) < slow + fos_period + 5:
            return None
        t1 = _tema(closes, fast)
        t2 = _tema(closes, slow)
        f = _fos(closes, fos_period)
        prev = t1[-3] - t2[-3]
        curr = t1[-1] - t2[-1]
        if position["side"] == "long" and prev > 0 > curr and f[-1] > 0:
            return "tema_fos_exit"
        if position["side"] == "short" and prev < 0 < curr and f[-1] < 0:
            return "tema_fos_exit"
        return None

    name = f"TEMA{fast}x{slow}_FOS{fos_period}_t{fos_threshold}"
    return {"name": name, "generate": generate, "exit": exit_func}


def make_ht_phase(cycle_min: int, cycle_max: int, vol_filter: float):
    """HT 相位策略 — 简化版用固定参数，周期参数预留"""

    def generate(klines, briefing):
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]
        if len(closes) < 50:
            return None
        phase_list, _ = _ht_phase(closes)
        pc = phase_list[-1]
        pp = phase_list[-2]
        direction = None
        if pp < 90 and pc >= 90:
            direction = "long"
        elif pp > 270 and pc <= 270:
            direction = "short"
        if direction is None:
            if pc < 30 or pc > 330:
                direction = "long"
            elif 150 < pc < 210:
                direction = "short"
        if direction is None:
            return None
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] < avg_vol * vol_filter:
            return None
        return Signal(direction=direction, entry_price=closes[-1], score=7, extra={"phase": pc})

    def exit_func(position, klines):
        closes = [k["close"] for k in klines]
        if len(closes) < 50:
            return None
        phase_list, _ = _ht_phase(closes)
        pc = phase_list[-1]
        pp = phase_list[-2]
        if position["side"] == "long" and pp > 180 and pc <= 180:
            return "ht_flip"
        if position["side"] == "short" and pp < 360 and pc >= 360:
            return "ht_flip"
        return None

    name = f"HT[{cycle_min},{cycle_max}]"
    return {"name": name, "generate": generate, "exit": exit_func}


# ═══════════════════════════════════════════
# 参数网格
# ═══════════════════════════════════════════

PARAM_GRIDS = {
    "ema":     [("EMA", make_ema_cross,   dict(fast=f, slow=s, vol_filter=v))
                for f in [5, 8, 10] for s in [20, 26, 30] for v in [1.0, 1.2]],
    "rsi":     [("RSI", make_rsi,         dict(period=p, oversold=o, overbought=b, vol_filter=1.2))
                for p in [10, 14, 20] for o in [30, 35, 40] for b in [65, 70, 75]],
    "emarsi":  [("xRSI", make_ema_rsi,    dict(ema_fast=f, ema_slow=s, rsi_max_long=rl, rsi_min_short=rs, vol_filter=1.2))
                for f in [5, 8] for s in [20, 26, 30] for rl in [55, 65] for rs in [35, 45]],
    "kc":      [("KC", make_kc,           dict(ema_period=e, atr_period=a, multiplier=m, vol_filter=1.2))
                for e in [15, 20, 26] for a in [8, 10, 14] for m in [1.5, 2.0, 2.5]],
    "tema":    [("TEMA", make_tema_fos,   dict(fast=f, slow=s, fos_period=p, fos_threshold=t, vol_filter=1.2))
                for f in [5, 8] for s in [20, 26] for p in [10, 14, 20] for t in [0.5, 1.0, 1.5]],
    "ht":      [("HT", make_ht_phase,     dict(cycle_min=cm, cycle_max=cx, vol_filter=1.2))
                for cm in [6, 8, 10] for cx in [24, 30, 40]],
}

# ═══════════════════════════════════════════
# 构建完整策略表
# ═══════════════════════════════════════════

def build_strategies(grids=None):
    """从参数网格生成所有策略变体"""
    if grids is None:
        grids = PARAM_GRIDS
    result = {}
    for family, entries in grids.items():
        for prefix, factory, kwargs in entries:
            cfg = factory(**kwargs)
            # 关键参数加入 key 防覆盖
            key_parts = [family, cfg["name"]]
            for k, v in kwargs.items():
                if k == "vol_filter":
                    key_parts.append(f"v{v}")
                elif k == "fos_threshold":
                    key_parts.append(f"th{v}")
                elif k == "multiplier":
                    key_parts.append(f"m{v}")
            key = "_".join(key_parts)
            result[key] = cfg
    return result


def build_summary(strategies):
    """统计生成结果"""
    families = {}
    for key in strategies:
        fam = key.split("_")[0]
        families[fam] = families.get(fam, 0) + 1
    lines = [f"总策略: {len(strategies)}"]
    for fam, cnt in sorted(families.items()):
        lines.append(f"  {fam}: {cnt}")
    return "\n".join(lines)


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

if __name__ == "__main__":
    strategies = build_strategies()
    print(build_summary(strategies))
    print()
    for i, (key, cfg) in enumerate(strategies.items()):
        print(f"{i+1:3d}. {cfg['name']}")
    print(f"\nTotal: {len(strategies)} strategies")
