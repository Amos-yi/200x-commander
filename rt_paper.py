"""
Real-Time Paper Trading — Gate.io WebSocket + 141策略并联
WebSocket 推送 15m K线实时更新 → 即刻算信号 → 即刻模拟成交
"""

import json
import os
import sys
import time
import logging
import threading
import statistics
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import websocket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import EXIT, LOCKS
from strategy_factory import build_strategies
from strategies import check_hard_stop, check_tp, Signal
from pushplus import PushPlus

log = logging.getLogger("rt_paper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
STATE_FILE = "rt_paper_state.json"
LEADERBOARD_FILE = "rt_paper_leaderboard.json"

_push = PushPlus()

def safe_push(title, content):
    try:
        _push.send(title, content)
    except Exception:
        pass


class StrategyTrack:
    """单策略跑道（与 multi_paper 兼容）"""
    def __init__(self, key, name, equity=100.0):
        self.key = key
        self.name = name
        self.equity = equity
        self.peak_equity = equity
        self.position = None
        self.trades = []
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.last_trade_date = ""
        self.locked_until = None
        self.locked_reason = ""

    @property
    def locked(self):
        return self.locked_until is not None and datetime.now() < self.locked_until

    @property
    def trade_count(self):
        return len(self.trades)

    @property
    def win_rate(self):
        if not self.trades:
            return 0
        return sum(1 for t in self.trades if t["pnl"] > 0) / len(self.trades) * 100

    @property
    def avg_r(self):
        if not self.trades:
            return 0
        return statistics.mean(t["r_multiple"] for t in self.trades)

    @property
    def profit_factor(self):
        gw = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in self.trades if t["pnl"] <= 0))
        return gw / gl if gl > 0 else (1.0 if gw > 0 else 0)

    @property
    def total_pnl(self):
        return sum(t["pnl"] for t in self.trades)

    @property
    def return_pct(self):
        return self.total_pnl / 100.0 * 100

    @property
    def max_dd(self):
        if not self.trades:
            return 0
        peak = 100.0
        eq = [100.0]
        for t in self.trades:
            eq.append(eq[-1] + t["pnl"])
        max_dd = 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)
        return max_dd * 100

    def lock(self, reason, hours=24):
        self.locked_until = datetime.now() + timedelta(hours=hours)
        self.locked_reason = reason

    def reset_daily(self, dt_str):
        if dt_str != self.last_trade_date:
            self.daily_trades = 0
            self.last_trade_date = dt_str

    def to_dict(self):
        return {
            "key": self.key,
            "name": self.name,
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "trades": self.trade_count,
            "win_rate": round(self.win_rate, 1),
            "avg_r": round(self.avg_r, 2),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_pnl, 2),
            "return_pct": round(self.return_pct, 1),
            "max_dd": round(self.max_dd, 1),
            "position": bool(self.position),
            "locked": self.locked,
        }


class RealTimePaperTrader:
    """
    WebSocket 驱动的实时虚拟交易引擎。
    每个 WebSocket 推送立即触发完整决策链：出场→入场→推送→排行榜刷新。
    """

    def __init__(self, symbol="ETH_USDT"):
        self.symbol = symbol
        self.strategies = build_strategies()
        self.tracks: Dict[str, StrategyTrack] = {}
        for key, cfg in self.strategies.items():
            self.tracks[key] = StrategyTrack(key, cfg["name"], 100.0)

        # K线缓冲: 16进制时间戳 → dict
        self.klines: Dict[int, dict] = {}
        self._load_state()
        self._last_leaderboard_print = datetime.min
        self._ws = None
        self._msg_count = 0  # 调试计数器

    # ── WebSocket ──

    def connect_ws(self):
        """建立 WebSocket 连接并进入事件循环"""
        log.info(f"连接 Gate.io WebSocket: {WS_URL}")
        self._ws = websocket.WebSocketApp(
            WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # 先拉历史 K 线填充缓冲
        self._load_history()
        # 持续运行
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    def _load_history(self):
        """WebSocket 启动前补历史数据"""
        from gate_data import fetch_klines
        k = fetch_klines(self.symbol, "15m", limit=200)
        for c in k:
            self.klines[c["time"]] = c
        log.info(f"历史K线已加载: {len(self.klines)} 根")

    def _on_open(self, ws):
        log.info("WebSocket 已连接")
        sub = {
            "time": int(time.time()),
            "channel": "futures.candlesticks",
            "event": "subscribe",
            "payload": ["15m", self.symbol],
        }
        ws.send(json.dumps(sub))
        log.info(f"已订阅: {self.symbol} 15m")

    def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        channel = msg.get("channel", "")
        event = msg.get("event", "")
        result = msg.get("result", {})

        if channel != "futures.candlesticks" or event != "update":
            return

        # result 可能是数组（取第一项）或单对象
        if isinstance(result, list):
            if not result:
                return
            result = result[0]

        candle = {
            "time": int(result.get("t", 0)),
            "open": float(result.get("o", 0)),
            "high": float(result.get("h", 0)),
            "low": float(result.get("l", 0)),
            "close": float(result.get("c", 0)),
            "volume": float(result.get("v", 0)),
        }
        if candle["time"] == 0:
            return

        self.klines[candle["time"]] = candle

        # 触发决策
        self._msg_count += 1
        if self._msg_count % 50 == 1:
            log.info(f"WS 推送 #{self._msg_count} | 当前价: {candle['close']} | K线: {len(self.klines)}")
        self._on_tick(candle)

    def _on_error(self, ws, error):
        log.error(f"WebSocket 错误: {error}")

    def _on_close(self, ws, code, msg):
        log.warning(f"WebSocket 断开: {code} {msg}")

    # ── 决策核心 ──

    def _on_tick(self, current_candle):
        """每次 WebSocket 推送触发"""
        now = datetime.now()
        dt_str = now.strftime("%Y-%m-%d")

        # 构建有序 K 线列表
        sorted_klines = sorted(self.klines.values(), key=lambda x: x["time"])
        if len(sorted_klines) < 50:
            return

        hour_china = (now.hour + 8) % 24
        if hour_china < 7 or hour_china >= 23:
            return  # 非活跃时段

        kline_list = sorted_klines

        for key, track in self.tracks.items():
            try:
                self._process_track(key, track, kline_list, now, dt_str)
            except Exception as e:
                log.error(f"[{track.name}] {e}")

        # 每 30 秒刷新排行榜
        if (now - self._last_leaderboard_print).total_seconds() >= 30:
            self._print_top()
            self._save_leaderboard()
            self._last_leaderboard_print = now

    def _process_track(self, key, track, klines, now, dt_str):
        cfg = self.strategies[key]
        last = klines[-1]

        # 解锁
        if track.locked_until and now >= track.locked_until:
            track.locked_until = None

        track.reset_daily(dt_str)

        # ── 持仓 → 出场 ──
        if track.position:
            exit_price, exit_reason = self._check_exit(track, key, klines, now)
            if exit_price is not None:
                self._close_track(track, exit_price, exit_reason, now)
            return

        # ── 锁检查 ──
        if track.locked:
            return
        if track.daily_trades >= 2:
            return
        if track.consecutive_losses >= 3:
            track.lock(f"连亏3笔", 24)
            return

        # ── 入场 ──
        briefing = {
            "mode": "offensive" if track.consecutive_losses == 0 else "standard",
            "quality_threshold": 5,
            "locked": False,
            "regime": "ranging",
        }
        signal = cfg["generate"](klines, briefing)
        if signal is None:
            return

        entry_price = last["close"]
        direction = signal.direction
        margin = track.equity * 0.05
        size = margin * 200 / entry_price
        hard_stop = 0.015
        stop_price = entry_price * (1 - hard_stop) if direction == "long" else entry_price * (1 + hard_stop)
        tp1 = entry_price * 1.03 if direction == "long" else entry_price * 0.97
        tp2 = entry_price * 1.05 if direction == "long" else entry_price * 0.95

        track.position = {
            "side": direction,
            "entry_price": entry_price,
            "entry_time": now,
            "size": size,
            "margin": round(margin, 2),
            "stop_price": stop_price,
            "tp1": tp1,
            "tp2": tp2,
            "breakeven_activated": False,
            "score": signal.score,
            "extra": signal.extra,
        }
        track.daily_trades += 1
        log.info(f"[{track.name}] 实时开仓 {direction.upper()} @ {entry_price:.2f} 止损:{stop_price:.2f}")

    def _check_exit(self, track, key, klines, now):
        pos = track.position
        cfg = self.strategies[key]
        last = klines[-1]
        high = last["high"]
        low = last["low"]
        close = last["close"]

        # [1] 硬止损（实时穿透：WS 推送的 high/low 包含当前影线）
        reason = check_hard_stop(pos, high, low, close)
        if reason:
            return (pos["stop_price"], reason)

        # [2] TP
        check_tp(pos, high, low, close)

        # [3] 策略特有退出
        reason = cfg["exit"](pos, klines)
        if reason:
            return (close, reason)

        # [4] 时间止损
        hours = (now - pos["entry_time"]).total_seconds() / 3600
        if hours >= 8 and abs(close - pos["entry_price"]) / pos["entry_price"] < 0.01:
            return (close, "time_exit")

        return (None, None)

    def _close_track(self, track, exit_price, exit_reason, now):
        pos = track.position
        entry = pos["entry_price"]
        direction = pos["side"]

        pnl = (exit_price - entry) * pos["size"] if direction == "long" else (entry - exit_price) * pos["size"]
        track.equity += pnl
        if track.equity > track.peak_equity:
            track.peak_equity = track.equity

        risk_amount = pos["margin"] * 0.015 * 200
        r_mult = pnl / risk_amount if risk_amount > 0 else 0
        track.consecutive_losses = 0 if pnl > 0 else track.consecutive_losses + 1

        trade = {
            "strategy": track.name,
            "direction": direction,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "r_multiple": round(r_mult, 2),
            "margin": pos["margin"],
            "exit_reason": exit_reason,
            "entry_time": pos["entry_time"].isoformat(),
            "exit_time": now.isoformat(),
        }
        track.trades.append(trade)

        emoji = "+" if pnl > 0 else "-"
        log.info(f"[{track.name}] {emoji}{pnl:+.2f}U ({r_mult:+.2f}R) {exit_reason} 净值:{track.equity:.2f}")

        track.position = None
        self._save_state()

    # ── 排行榜 ──

    def _print_top(self):
        ranked = sorted(self.tracks.values(), key=lambda t: t.equity, reverse=True)
        changed = [t for t in ranked if t.position or t.trades]
        if not changed:
            return

        lines = ["\n" + "=" * 85]
        lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] 实时排行榜 (活跃策略)")
        lines.append(f"{'策略':<20s} {'净值':>7s} {'盈亏%':>8s} {'交易':>5s} {'胜率':>6s} {'均R':>7s} {'PF':>6s} {'持仓':>5s}")
        lines.append("-" * 85)
        for t in changed[:20]:
            pos_marker = f"{'LONG' if t.position and t.position['side'] == 'long' else 'SHORT' if t.position else '--'}"
            lines.append(
                f"{t.name:<20s} {t.equity:>7.2f} {t.return_pct:>+7.1f}% "
                f"{t.trade_count:>5d} {t.win_rate:>5.1f}% {t.avg_r:>+6.2f}R "
                f"{t.profit_factor:>5.2f}  {pos_marker:>5s}"
            )
        lines.append(f"--- 共 {len(self.tracks)} 策略, {sum(1 for t in self.tracks.values() if t.position)} 持仓 ---")
        print("\n".join(lines))

    def _save_leaderboard(self):
        data = {key: track.to_dict() for key, track in self.tracks.items()}
        with open(LEADERBOARD_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _save_state(self):
        data = {}
        for key, track in self.tracks.items():
            data[key] = {
                "equity": track.equity,
                "peak_equity": track.peak_equity,
                "position": track.position,
                "consecutive_losses": track.consecutive_losses,
                "daily_trades": track.daily_trades,
                "last_trade_date": track.last_trade_date,
                "locked_until": track.locked_until.isoformat() if track.locked_until else None,
                "locked_reason": track.locked_reason,
            }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            for key, d in data.items():
                if key in self.tracks:
                    t = self.tracks[key]
                    t.equity = d.get("equity", 100.0)
                    t.peak_equity = d.get("peak_equity", 100.0)
                    t.position = d.get("position")
                    t.consecutive_losses = d.get("consecutive_losses", 0)
                    t.daily_trades = d.get("daily_trades", 0)
                    t.last_trade_date = d.get("last_trade_date", "")
                    if d.get("locked_until"):
                        t.locked_until = datetime.fromisoformat(d["locked_until"])
                        t.locked_reason = d.get("locked_reason", "")
            log.info("状态已恢复")
        except Exception as e:
            log.warning(f"恢复失败: {e}")


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="ETH_USDT")
    args = parser.parse_args()

    trader = RealTimePaperTrader(args.symbol)
    print(f"Real-Time Paper Trading 启动: {args.symbol}")
    print(f"策略: {len(trader.tracks)} 个 | 每 WS 推送即刻决策")
    print("=" * 60)

    try:
        trader.connect_ws()
    except KeyboardInterrupt:
        print("\n退出")
        trader._save_state()
    except Exception as e:
        log.error(f"致命错误: {e}")
        trader._save_state()


if __name__ == "__main__":
    main()
