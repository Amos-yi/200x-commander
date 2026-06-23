"""
宏观过滤器 —— DXY / VIX / Fear & Greed
零外部 API Key 依赖，全部走 yfinance + alternative.me
"""

import logging
import time
import json
import urllib.request

log = logging.getLogger("commander.macro")


class MacroFilter:
    """
    三指标过滤器：
    - DXY 5 日涨幅 > 2% → 不开多
    - VIX > 30 → 仓位减半；> 35 → 不开仓
    - Fear & Greed 极度恐惧(<25) → 多单信号加分；极度贪婪(>75) → 不开多
    """

    def __init__(self, cache_seconds: int = 3600):
        self.cache_seconds = cache_seconds
        self._dxy = {
            "dxy_5d_pct": 0.0,
            "dxy_current": 0.0,
            "ts": 0,
        }
        self._vix = {
            "vix_current": 0.0,
            "ts": 0,
        }
        self._fng = {
            "value": 50,
            "classification": "",
            "ts": 0,
        }

    # ── 主入口 ──────────────────────────────

    def check(self, direction: str) -> tuple:
        """
        返回: (allowed: bool, reason: str, risk_adj: float)
        risk_adj: 1.0=正常, 0.5=减半, 0.0=禁止
        """
        self._refresh_all()

        # DXY 强势 → 禁止做多
        if direction == "long" and self._dxy["dxy_5d_pct"] > 0.02:
            return False, f"DXY 5日涨{self._dxy['dxy_5d_pct']:.1%}>2%", 0.0

        # VIX 极端
        vix = self._vix["vix_current"]
        if vix > 35:
            return False, f"VIX={vix:.1f}>35", 0.0
        if vix > 30:
            return True, f"VIX={vix:.1f}>30,仓位减半", 0.5

        # Fear & Greed
        fng = self._fng["value"]
        if direction == "long" and fng > 75:
            return False, f"恐惧贪婪={fng}(极度贪婪)>75", 0.0

        return True, "", 1.0

    # ── 数据刷新 ────────────────────────────

    def _refresh_all(self):
        now = time.time()

        if now - self._dxy["ts"] < self.cache_seconds:
            return  # 全部一起刷新

        self._fetch_dxy()
        self._fetch_vix()
        self._fetch_fng()

    def _fetch_dxy(self):
        try:
            data = self._yf_chart("DX-Y.NYB", "10d")
            if data and len(data) >= 6:
                current = data[-1]
                five_days_ago = data[-6]
                pct = (current - five_days_ago) / five_days_ago
                self._dxy = {
                    "dxy_5d_pct": round(pct, 6),
                    "dxy_current": round(current, 2),
                    "ts": time.time(),
                }
                log.debug(f"DXY: {current:.2f} 5日涨跌幅:{pct:+.2%}")
        except Exception as e:
            log.warning(f"DXY 获取失败: {e}")

    def _fetch_vix(self):
        try:
            data = self._yf_chart("^VIX", "2d")
            if data and len(data) >= 1:
                current = data[-1]
                self._vix = {
                    "vix_current": round(current, 2),
                    "ts": time.time(),
                }
                log.debug(f"VIX: {current:.2f}")
        except Exception as e:
            log.warning(f"VIX 获取失败: {e}")

    @staticmethod
    def _yf_chart(symbol: str, range_str: str) -> list:
        """通过 Yahoo Finance v8 chart API 获取收盘价序列，零外部依赖"""
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?range={range_str}&interval=1d"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "200x-commander/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
        result = raw["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        return [c for c in closes if c is not None]

    def _fetch_fng(self):
        try:
            url = "https://api.alternative.me/fng/?limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "200x-commander/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            item = data.get("data", [{}])[0]
            value = int(item.get("value", 50))
            classification = item.get("value_classification", "")
            self._fng = {
                "value": value,
                "classification": classification,
                "ts": time.time(),
            }
            log.debug(f"Fear & Greed: {value} ({classification})")
        except Exception as e:
            log.warning(f"Fear & Greed 获取失败: {e}")

    # ── 查询 ────────────────────────────────

    @property
    def dxy(self) -> float:
        return self._dxy["dxy_current"]

    @property
    def dxy_trend(self) -> float:
        return self._dxy["dxy_5d_pct"]

    @property
    def vix(self) -> float:
        return self._vix["vix_current"]

    @property
    def fng(self) -> int:
        return self._fng["value"]

    def summary(self) -> str:
        dxy_str = f"DXY={self.dxy:.1f}({self.dxy_trend:+.1%})" if self.dxy else "DXY=?"
        vix_str = f"VIX={self.vix:.1f}" if self.vix else "VIX=?"
        fng_str = f"F&G={self.fng}" if self.fng else "F&G=?"
        return f"宏观: {dxy_str} {vix_str} {fng_str}"
