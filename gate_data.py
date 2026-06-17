"""
Gate.io 数据层 — REST K 线 + 公开行情
回测和实盘共用同一接口
"""

import json
import time
import logging
from datetime import datetime
from typing import Optional, List, Dict
from urllib.request import Request, urlopen
from urllib.error import URLError

log = logging.getLogger("gate_data")

BASE_URL = "https://api.gateio.ws/api/v4"
TIMEOUT = 10  # 秒


def _get(endpoint: str) -> dict:
    """GET 请求，返回 JSON"""
    url = f"{BASE_URL}{endpoint}"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
            return json.loads(body)
    except URLError as e:
        log.warning(f"Gate API 请求失败: {endpoint} | {e}")
        return {}
    except json.JSONDecodeError:
        log.warning(f"Gate API 返回非 JSON: {endpoint}")
        return {}


def fetch_klines(
    symbol: str,
    interval: str = "15m",
    limit: int = 100,
    start: Optional[float] = None,
    end: Optional[float] = None,
) -> List[Dict]:
    """
    拉取永续合约 K 线（公开接口，无需 API Key）
    
    返回格式: [{t, o, h, l, c, v, ...}, ...]
      t = 时间戳(秒), o = 开, h = 高, l = 低, c = 收, v = 量(USD)
    
    转为统一格式:
      {time, open, high, low, close, volume}
    """
    params = f"contract={symbol}&interval={interval}&limit={limit}"

    endpoint = f"/futures/usdt/candlesticks?{params}"
    data = _get(endpoint)

    if not data:
        return []

    result = []
    for candle in data:
        result.append({
            "time": int(candle.get("t", 0)),
            "open": float(candle.get("o", 0)),
            "high": float(candle.get("h", 0)),
            "low": float(candle.get("l", 0)),
            "close": float(candle.get("c", 0)),
            "volume": float(candle.get("v", 0)),
        })
    return result


def fetch_ticker(symbol: str) -> dict:
    """拉取最新 ticker"""
    endpoint = f"/futures/usdt/tickers?contract={symbol}"
    data = _get(endpoint)
    if not data:
        return {}
    item = data[0] if isinstance(data, list) else data
    return {
        "last": float(item.get("last", 0)),
        "mark_price": float(item.get("mark_price", 0)),
        "index_price": float(item.get("index_price", 0)),
        "high_24h": float(item.get("high_24h", 0)),
        "low_24h": float(item.get("low_24h", 0)),
        "volume_24h": float(item.get("volume_24h_usdt", 0)),
        "change_pct": float(item.get("change_percentage", 0)),
        "funding_rate": float(item.get("funding_rate", 0)),
    }


def fetch_all_tickers() -> List[Dict]:
    """拉取全市场 ticker"""
    endpoint = "/futures/usdt/tickers"
    data = _get(endpoint)
    if not data:
        return []
    result = []
    for item in data:
        result.append({
            "contract": item.get("contract", ""),
            "last": float(item.get("last", 0)),
            "mark_price": float(item.get("mark_price", 0)),
            "volume_24h": float(item.get("volume_24h_usdt", 0)),
            "funding_rate": float(item.get("funding_rate", 0)),
        })
    return result


def fetch_contract_info(symbol: str) -> dict:
    """拉取合约信息（最小下单量等）"""
    endpoint = f"/futures/usdt/contracts/{symbol}"
    data = _get(endpoint)
    if not data:
        return {}
    return {
        "quanto_multiplier": float(data.get("quanto_multiplier", 1)),
        "order_size_min": int(data.get("order_size_min", 1)),
        "order_size_max": int(data.get("order_size_max", 1000000)),
        "mark_price": float(data.get("mark_price", 0)),
        "last_price": float(data.get("last_price", 0)),
        "funding_rate": float(data.get("funding_rate", 0)),
        "funding_next_apply": float(data.get("funding_next_apply", 0)),
    }
