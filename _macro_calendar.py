"""
宏观事件日历 + 市场体制判断
"""

from datetime import datetime


# ============================================================
# 硬编码事件表（每季度手动更新）
# ============================================================

EVENTS = {
    "2026-06-17 18:00": ("CPI", "2026-06-18 07:00"),
    "2026-06-30 18:00": ("FOMC", "2026-07-01 07:00"),
    "2026-07-08 18:00": ("NFP", "2026-07-09 07:00"),
    "2026-07-15 18:00": ("CPI", "2026-07-16 07:00"),
    "2026-07-29 18:00": ("FOMC", "2026-07-30 07:00"),
    "2026-08-05 18:00": ("NFP", "2026-08-06 07:00"),
    "2026-08-12 18:00": ("CPI", "2026-08-13 07:00"),
    "2026-08-25 18:00": ("PCE", "2026-08-26 07:00"),
    "2026-09-02 18:00": ("NFP", "2026-09-03 07:00"),
    "2026-09-16 18:00": ("FOMC", "2026-09-17 07:00"),
}

# 注意：此表需人工维护。建议每季度初补充未来 3 个月的事件。


class Calendar:
    @staticmethod
    def is_event_window(now: datetime) -> bool:
        for start_str, (_, end_str) in EVENTS.items():
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
            if start <= now <= end:
                return True
        return False

    @staticmethod
    def event_name(now: datetime) -> str:
        for start_str, (name, end_str) in EVENTS.items():
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
            if start <= now <= end:
                return name
        return ""

    @staticmethod
    def upcoming_events(days: int = 7) -> list:
        """未来 N 天内的事件"""
        now = datetime.now()
        cutoff = now + __import__("datetime").timedelta(days=days)
        result = []
        for start_str, (name, end_str) in EVENTS.items():
            start = datetime.fromisoformat(start_str)
            if now <= start <= cutoff:
                result.append((start, name))
        result.sort()
        return result

    @staticmethod
    def is_weekend(now: datetime = None) -> bool:
        if now is None:
            now = datetime.now()
        return now.weekday() >= 5  # 5=周六, 6=周日


# ============================================================
# 市场体制判断（简化版——基于 BTC 近 20 根 1h K 线）
# ============================================================

def detect_regime(klines_1h: list) -> str:
    """
    输入：BTC 近 20 根 1h K 线 [{close, high, low}, ...]
    输出：'trending_up' | 'trending_down' | 'ranging' | 'volatile'
    """
    if len(klines_1h) < 20:
        return "ranging"

    closes = [k["close"] for k in klines_1h]
    highs = [k["high"] for k in klines_1h]
    lows = [k["low"] for k in klines_1h]

    # 趋势判断：EMA5 和 EMA20 斜率
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)

    ema5_slope = (ema5[-1] - ema5[-5]) / ema5[-5] if len(ema5) >= 5 else 0
    ema20_slope = (ema20[-1] - ema20[-5]) / ema20[-5] if len(ema20) >= 5 else 0

    # 波动判断：ATR(14) / 均价
    avg_price = sum(closes[-20:]) / 20
    atr14 = _atr(highs, lows, closes, 14)
    volatility = atr14 / avg_price if avg_price > 0 else 0

    if volatility > 0.05:  # ATR > 5% → 高波
        return "volatile"

    if ema5_slope > 0.003 and ema20_slope > 0:
        return "trending_up"
    elif ema5_slope < -0.003 and ema20_slope < 0:
        return "trending_down"
    else:
        return "ranging"


def _ema(values: list, period: int) -> list:
    if len(values) < period:
        return values[-1:] if values else []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _atr(highs: list, lows: list, closes: list, period: int) -> float:
    if len(closes) < period + 1:
        return 0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period
