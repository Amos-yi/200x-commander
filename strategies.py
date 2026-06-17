"""
策略库 — 六种独立策略的入场/出场逻辑
每个策略是一个闭包/类，统一接口: generate(klines, briefing) -> Signal | None
"""

import math
from dataclasses import dataclass
from typing import Optional, List, Callable
from config import SIGNAL, EXIT

# ═══════════════════════════════════════════
# 通用信号结构
# ═══════════════════════════════════════════

@dataclass
class Signal:
    direction: str       # "long" / "short"
    entry_price: float
    score: int
    extra: dict          # 策略特定数据（如 phase, ema 值等）


# ═══════════════════════════════════════════
# 指标工具
# ═══════════════════════════════════════════

def _ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return [values[-1]] * len(values) if values else []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return [result[0]] * (period - 1) + result


def _tema(data: List[float], period: int) -> List[float]:
    e1 = _ema(data, period)
    e2 = _ema(e1, period)
    e3 = _ema(e2, period)
    return [3 * e1[i] - 3 * e2[i] + e3[i] for i in range(len(e1))]


def _rsi(closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < period + 1:
        return [50] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50] * period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l > 0 else 100
        result.append(100 - 100 / (1 + rs))
    return result


def _atr(highs, lows, closes, period: int = 10) -> List[float]:
    if len(closes) < 2:
        return [0] * len(closes)
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr_ema = _ema(tr, period)
    return [atr_ema[0]] + atr_ema


def _fos(closes: List[float], period: int) -> List[float]:
    result = []
    for i in range(len(closes)):
        if i < period:
            result.append(0.0)
            continue
        y = closes[i - period:i]
        n = len(y)
        x = list(range(n))
        sx = sum(x)
        sy = sum(y)
        sxy = sum(x[j] * y[j] for j in range(n))
        sx2 = sum(xj * xj for xj in x)
        denom = n * sx2 - sx * sx
        a = (n * sxy - sx * sy) / denom if denom != 0 else 0
        b = (sy - a * sx) / n
        forecast = a * (n - 1) + b
        result.append((closes[i] - forecast) / forecast * 100 if forecast != 0 else 0.0)
    return result


def _ht_phase(closes: List[float]):
    n = len(closes)
    if n < 7:
        return [50] * n, 14
    period = 10
    a1 = math.exp(-1.414 * math.pi / period)
    b1 = 2 * a1 * math.cos(1.414 * math.pi / period)
    c1 = 1 - b1 - a1
    smooth = [closes[0]] * n
    for i in range(2, n):
        smooth[i] = c1 * (closes[i] + closes[i - 1]) / 2 + b1 * smooth[i - 1] - a1 * smooth[i - 2]
    hp_period = 48
    alpha = math.cos(2 * math.pi / hp_period)
    a2 = (1 - alpha) / 2
    detrender = [0.0] * n
    for i in range(3, n):
        detrender[i] = (1 - a2) * (smooth[i] - smooth[i - 2]) + alpha * (1 + a2) * detrender[i - 1] - a2 * detrender[i - 2]
    q1 = [0.0] * n
    i1 = [0.0] * n
    for i in range(7, n):
        q1[i] = (0.0962 * detrender[i] + 0.5769 * detrender[i - 2] - 0.5769 * detrender[i - 4] - 0.0962 * detrender[i - 6]) * (0.075 * period + 0.54)
        i1[i] = detrender[i - 3]
    phase = [0.0] * n
    for i in range(7, n):
        if i1[i] != 0:
            raw = math.atan(q1[i] / i1[i]) * 180 / math.pi
            if i1[i] < 0 and q1[i] >= 0:
                raw += 180
            elif i1[i] < 0 and q1[i] < 0:
                raw += 180
            elif i1[i] > 0 and q1[i] < 0:
                raw += 360
            phase[i] = raw % 360
        else:
            phase[i] = phase[i - 1] if i > 0 else 0
    ps = [phase[0]]
    for i in range(1, n):
        diff = phase[i] - ps[i - 1]
        if diff > 180:
            diff -= 360
        elif diff < -180:
            diff += 360
        ps.append((ps[i - 1] + 0.3 * diff) % 360)
    dc_period = 20
    last_cross = 0
    for i in range(8, n):
        if ps[i - 1] < 180 and ps[i] >= 180:
            if last_cross > 0:
                cl = i - last_cross
                if 6 < cl < 60:
                    dc_period = int(0.7 * dc_period + 0.3 * cl)
            last_cross = i
    return ps, dc_period


# ═══════════════════════════════════════════
# 出场检查函数 (统一签名)
# ═══════════════════════════════════════════

def check_hard_stop(position: dict, high: float, low: float, close: float) -> Optional[str]:
    """返回 'stop_loss' 如果触发硬止损"""
    if position["side"] == "long" and low <= position["stop_price"]:
        return "stop_loss"
    if position["side"] == "short" and high >= position["stop_price"]:
        return "stop_loss"
    return None


def check_tp(position: dict, high: float, low: float, close: float) -> None:
    """原地修改 position 的 tp 和 breakeven 状态"""
    if position["side"] == "long":
        if high >= position["tp1"] and not position.get("tp1_hit"):
            position["tp1_hit"] = True
            position["breakeven_activated"] = True
            position["stop_price"] = position["entry_price"]
        if high >= position["tp2"] and not position.get("tp2_hit"):
            position["tp2_hit"] = True
    else:
        if low <= position["tp1"] and not position.get("tp1_hit"):
            position["tp1_hit"] = True
            position["breakeven_activated"] = True
            position["stop_price"] = position["entry_price"]
        if low <= position["tp2"] and not position.get("tp2_hit"):
            position["tp2_hit"] = True


# ═══════════════════════════════════════════
# 策略 1: EMA5×20 交叉
# ═══════════════════════════════════════════

def ema_cross_generate(klines: List[dict], briefing: dict) -> Optional[Signal]:
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    if len(closes) < 25:
        return None
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    prev = ema5[-3] - ema20[-3]
    curr = ema5[-1] - ema20[-1]
    cross = None
    if prev < 0 < curr:
        cross = "long"
    elif prev > 0 > curr:
        cross = "short"
    if cross is None:
        return None
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol * 1.2:
        return None
    score = 6
    return Signal(direction=cross, entry_price=closes[-1], score=score,
                  extra={"ema5": ema5[-1], "ema20": ema20[-1]})


def ema_cross_exit(position: dict, klines: List[dict]) -> Optional[str]:
    """返回退出原因或 None"""
    closes = [k["close"] for k in klines]
    if len(closes) < 25:
        return None
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    prev = ema5[-3] - ema20[-3]
    curr = ema5[-1] - ema20[-1]
    if position["side"] == "long" and prev > 0 > curr:
        return "ema_dead_cross"
    if position["side"] == "short" and prev < 0 < curr:
        return "ema_golden_cross"
    return None


# ═══════════════════════════════════════════
# 策略 2: RSI 超卖/超买反弹
# ═══════════════════════════════════════════

def rsi_rebound_generate(klines: List[dict], briefing: dict) -> Optional[Signal]:
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    if len(closes) < 20:
        return None
    rsi14 = _rsi(closes, 14)
    rsi_curr = rsi14[-1]
    rsi_prev = rsi14[-2]
    direction = None
    if rsi_prev < 35 and rsi_curr >= 35:
        direction = "long"
    elif rsi_prev > 65 and rsi_curr <= 65:
        direction = "short"
    if direction is None:
        return None
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol * 1.2:
        return None
    score = 7
    return Signal(direction=direction, entry_price=closes[-1], score=score, extra={"rsi": rsi_curr})


def rsi_rebound_exit(position: dict, klines: List[dict]) -> Optional[str]:
    closes = [k["close"] for k in klines]
    if len(closes) < 15:
        return None
    rsi14 = _rsi(closes, 14)
    rsi_curr = rsi14[-1]
    if position["side"] == "long" and rsi_curr > 50:
        return "rsi_neutral_exit"
    if position["side"] == "short" and rsi_curr < 50:
        return "rsi_neutral_exit"
    return None


# ═══════════════════════════════════════════
# 策略 3: EMA×RSI 双确认
# ═══════════════════════════════════════════

def ema_rsi_generate(klines: List[dict], briefing: dict) -> Optional[Signal]:
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    if len(closes) < 25:
        return None
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    rsi14 = _rsi(closes, 14)
    prev = ema5[-3] - ema20[-3]
    curr = ema5[-1] - ema20[-1]
    cross = None
    if prev < 0 < curr:
        cross = "long"
    elif prev > 0 > curr:
        cross = "short"
    if cross is None:
        return None
    rsi_curr = rsi14[-1]
    if cross == "long" and rsi_curr > 60:
        return None
    if cross == "short" and rsi_curr < 40:
        return None
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol * 1.2:
        return None
    score = 7
    return Signal(direction=cross, entry_price=closes[-1], score=score, extra={"ema5": ema5[-1], "ema20": ema20[-1], "rsi": rsi_curr})


def ema_rsi_exit(position: dict, klines: List[dict]) -> Optional[str]:
    closes = [k["close"] for k in klines]
    if len(closes) < 25:
        return None
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    rsi14 = _rsi(closes, 14)
    prev = ema5[-3] - ema20[-3]
    curr = ema5[-1] - ema20[-1]
    rsi_curr = rsi14[-1]
    if position["side"] == "long" and prev > 0 > curr and rsi_curr > 50:
        return "ema_dead_rsi_high"
    if position["side"] == "short" and prev < 0 < curr and rsi_curr < 50:
        return "ema_golden_rsi_low"
    return None


# ═══════════════════════════════════════════
# 策略 4: 肯特纳通道突破
# ═══════════════════════════════════════════

def kc_breakout_generate(klines: List[dict], briefing: dict) -> Optional[Signal]:
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]
    if len(closes) < 35:
        return None
    ema20 = _ema(closes, 20)
    atr10 = _atr(highs, lows, closes, 10)
    mult = 2.0
    upper = [ema20[i] + atr10[i] * mult for i in range(len(ema20))]
    lower = [ema20[i] - atr10[i] * mult for i in range(len(ema20))]
    last_c = closes[-1]
    prev_c = closes[-2]
    if last_c > upper[-1] and prev_c <= upper[-2]:
        direction = "long"
    elif last_c < lower[-1] and prev_c >= lower[-2]:
        direction = "short"
    else:
        return None
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol * 1.2:
        return None
    score = 7
    return Signal(direction=direction, entry_price=last_c, score=score,
                  extra={"ema": ema20[-1], "upper": upper[-1], "lower": lower[-1]})


def kc_breakout_exit(position: dict, klines: List[dict]) -> Optional[str]:
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    if len(closes) < 35:
        return None
    ema20 = _ema(closes, 20)
    if position["side"] == "long" and closes[-1] <= ema20[-1]:
        return "kc_mid_return"
    if position["side"] == "short" and closes[-1] >= ema20[-1]:
        return "kc_mid_return"
    return None


# ═══════════════════════════════════════════
# 策略 5: TEMA×FOS
# ═══════════════════════════════════════════

def tema_fos_generate(klines: List[dict], briefing: dict) -> Optional[Signal]:
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    if len(closes) < 35:
        return None
    t5 = _tema(closes, 5)
    t20 = _tema(closes, 20)
    f14 = _fos(closes, 14)
    prev = t5[-3] - t20[-3]
    curr = t5[-1] - t20[-1]
    cross = None
    if prev < 0 < curr:
        cross = "long"
    elif prev > 0 > curr:
        cross = "short"
    if cross is None:
        return None
    fos_curr = f14[-1]
    if cross == "long" and fos_curr > 1.0:
        return None
    if cross == "short" and fos_curr < -1.0:
        return None
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol * 1.2:
        return None
    score = 7
    return Signal(direction=cross, entry_price=closes[-1], score=score,
                  extra={"tema5": t5[-1], "tema20": t20[-1], "fos": fos_curr})


def tema_fos_exit(position: dict, klines: List[dict]) -> Optional[str]:
    closes = [k["close"] for k in klines]
    if len(closes) < 35:
        return None
    t5 = _tema(closes, 5)
    t20 = _tema(closes, 20)
    f14 = _fos(closes, 14)
    prev = t5[-3] - t20[-3]
    curr = t5[-1] - t20[-1]
    fos_curr = f14[-1]
    if position["side"] == "long" and prev > 0 > curr and fos_curr > 0:
        return "tema_dead_fos_pos"
    if position["side"] == "short" and prev < 0 < curr and fos_curr < 0:
        return "tema_golden_fos_neg"
    return None


# ═══════════════════════════════════════════
# 策略 6: HT_DCPHASE
# ═══════════════════════════════════════════

def ht_phase_generate(klines: List[dict], briefing: dict) -> Optional[Signal]:
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    if len(closes) < 50:
        return None
    phase_list, _ = _ht_phase(closes)
    p_curr = phase_list[-1]
    p_prev = phase_list[-2]
    direction = None
    if p_prev < 90 and p_curr >= 90:
        direction = "long"
    elif p_prev > 270 and p_curr <= 270:
        direction = "short"
    if direction is None:
        if p_curr < 30 or p_curr > 330:
            direction = "long"
        elif 150 < p_curr < 210:
            direction = "short"
    if direction is None:
        return None
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol * 1.2:
        return None
    score = 7
    return Signal(direction=direction, entry_price=closes[-1], score=score,
                  extra={"phase": p_curr, "dc": _ht_phase(closes)[1]})


def ht_phase_exit(position: dict, klines: List[dict]) -> Optional[str]:
    closes = [k["close"] for k in klines]
    if len(closes) < 50:
        return None
    phase_list, _ = _ht_phase(closes)
    p_curr = phase_list[-1]
    p_prev = phase_list[-2]
    if position["side"] == "long" and p_prev > 180 and p_curr <= 180:
        return "ht_phase_flip"
    if position["side"] == "short" and p_prev < 360 and p_curr >= 360:
        return "ht_phase_flip"
    return None


# ═══════════════════════════════════════════
# 策略注册表
# ═══════════════════════════════════════════

STRATEGIES = {
    "ema_cross": {
        "name": "EMA5×20 交叉",
        "generate": ema_cross_generate,
        "exit": ema_cross_exit,
    },
    "rsi_rebound": {
        "name": "RSI 超卖反弹",
        "generate": rsi_rebound_generate,
        "exit": rsi_rebound_exit,
    },
    "ema_rsi": {
        "name": "EMA+RSI 双确认",
        "generate": ema_rsi_generate,
        "exit": ema_rsi_exit,
    },
    "kc_breakout": {
        "name": "肯特纳通道",
        "generate": kc_breakout_generate,
        "exit": kc_breakout_exit,
    },
    "tema_fos": {
        "name": "TEMA×FOS",
        "generate": tema_fos_generate,
        "exit": tema_fos_exit,
    },
    "ht_phase": {
        "name": "HT 相位",
        "generate": ht_phase_generate,
        "exit": ht_phase_exit,
    },
}
