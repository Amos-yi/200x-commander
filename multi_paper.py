"""
Multi-Strategy Paper Trading — 6 策略并行虚拟交易
完全独立仓位/净值/统计，同一 K 线数据源
"""

import json
import os
import sys
import time
import logging
import statistics
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import STAGES, EXIT, LOCKS, MODE_RISK_BUDGET
from strategy_factory import build_strategies
from strategies import check_hard_stop, check_tp, Signal
from gate_data import fetch_klines
from pushplus import PushPlus

STRATEGIES = build_strategies()  # 141 strategies

log = logging.getLogger("multipaper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

STATE_FILE = "multi_paper_state.json"
HISTORY_FILE = "multi_paper_trades.json"
LEADERBOARD_FILE = "multi_paper_leaderboard.json"

_push = PushPlus()


def safe_push(title, content):
    try:
        _push.send(title, content)
    except Exception:
        pass


# ═══════════════════════════════════════════
# 单策略跑道
# ═══════════════════════════════════════════

@dataclass
class StrategyTrack:
    key: str
    name: str
    equity: float = 100.0
    peak_equity: float = 100.0
    position: Optional[dict] = None
    trades: List[dict] = field(default_factory=list)
    consecutive_losses: int = 0
    daily_trades: int = 0
    last_trade_date: str = ""
    locked_until: Optional[datetime] = None
    locked_reason: str = ""

    @property
    def locked(self) -> bool:
        return self.locked_until is not None and datetime.now() < self.locked_until

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0
        w = sum(1 for t in self.trades if t["pnl"] > 0)
        return w / len(self.trades) * 100

    @property
    def avg_r(self) -> float:
        if not self.trades:
            return 0
        return statistics.mean(t["r_multiple"] for t in self.trades)

    @property
    def profit_factor(self) -> float:
        gw = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in self.trades if t["pnl"] <= 0))
        return gw / gl if gl > 0 else (1.0 if gw > 0 else 0)

    @property
    def total_pnl(self) -> float:
        return sum(t["pnl"] for t in self.trades)

    @property
    def return_pct(self) -> float:
        return self.total_pnl / 100.0 * 100

    @property
    def max_dd(self) -> float:
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

    def lock(self, reason: str, hours: float = 24):
        self.locked_until = datetime.now() + timedelta(hours=hours)
        self.locked_reason = reason

    def reset_daily(self, dt_str: str):
        if dt_str != self.last_trade_date:
            self.daily_trades = 0
            self.last_trade_date = dt_str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "trades": len(self.trades),
            "win_rate": round(self.win_rate, 1),
            "avg_r": round(self.avg_r, 2),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_pnl, 2),
            "return_pct": round(self.return_pct, 1),
            "max_dd": round(self.max_dd, 1),
            "position": bool(self.position),
            "locked": self.locked,
        }


# ═══════════════════════════════════════════
# 多策略引擎
# ═══════════════════════════════════════════

class MultiPaperTrader:
    def __init__(self, symbol: str = "ETH_USDT", equity_per: float = 100.0):
        self.symbol = symbol
        self.tracks: Dict[str, StrategyTrack] = {}
        for key, cfg in STRATEGIES.items():
            self.tracks[key] = StrategyTrack(key=key, name=cfg["name"], equity=equity_per)
        self._load_state()

    def tick(self):
        klines = fetch_klines(self.symbol, "15m", limit=200)
        if not klines:
            log.warning("无法获取 K 线")
            return

        seen = set()
        klines = sorted(
            [k for k in klines if not (k["time"] in seen or seen.add(k["time"]))],
            key=lambda x: x["time"],
        )

        now = datetime.now()
        dt_str = now.strftime("%Y-%m-%d")
        hour_china = (now.hour + 8) % 24

        if hour_china < 7 or hour_china >= 23:
            return  # 非活跃时段

        for key, track in self.tracks.items():
            try:
                self._process_track(key, track, klines, now, dt_str)
            except Exception as e:
                log.error(f"[{track.name}] 异常: {e}")

        self._save_state()
        self._save_leaderboard()

    def _process_track(self, key: str, track: StrategyTrack, klines: list, now: datetime, dt_str: str):
        cfg = STRATEGIES[key]
        high = klines[-1]["high"]
        low = klines[-1]["low"]
        close = klines[-1]["close"]

        # 解锁
        if track.locked_until and now >= track.locked_until:
            track.locked_until = None

        track.reset_daily(dt_str)

        # ── 持仓 → 出场 ──
        if track.position:
            exit_price, exit_reason = self._check_strategy_exit(track, key, klines, now, close, high, low)
            if exit_price is not None:
                self._close_position(track, exit_price, exit_reason, now)
            return

        # ── 锁 ──
        if track.locked:
            return
        if track.daily_trades >= 2:
            return
        if track.consecutive_losses >= 3:
            track.lock(f"连亏3笔", 24)
            safe_push(f"200x Lock: {track.name}", f"{track.name} 连亏3笔，锁24h")
            return

        # ── 入场 ──
        # 构造简化 briefing
        wr = track.win_rate / 100 if track.trades else 0.5
        briefing = {
            "mode": "offensive" if track.consecutive_losses == 0 else "standard",
            "quality_threshold": 5,
            "locked": False,
            "regime": "ranging",
        }
        signal = cfg["generate"](klines, briefing)
        if signal is None:
            return

        entry_price = close
        direction = signal.direction

        # 仓位计算
        margin = track.equity * 0.05  # Stage 1
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
        log.info(f"[{track.name}] 开仓 {direction.upper()} @ {entry_price:.2f} | 止损:{stop_price:.2f} | 分:{signal.score}")

    # ── 出场判断 ──

    def _check_strategy_exit(self, track: StrategyTrack, key: str, klines: list,
                             now: datetime, close: float, high: float, low: float):
        pos = track.position
        cfg = STRATEGIES[key]

        # [1] 硬止损
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

    # ── 平仓 ──

    def _close_position(self, track: StrategyTrack, exit_price: float, exit_reason: str, now: datetime):
        pos = track.position
        entry = pos["entry_price"]
        direction = pos["side"]

        pnl = (exit_price - entry) * pos["size"] if direction == "long" else (entry - exit_price) * pos["size"]
        track.equity += pnl
        if track.equity > track.peak_equity:
            track.peak_equity = track.equity

        # R = PnL / 实际风险金额（止损触发时最大亏损）
        risk_amount = pos["margin"] * 0.015 * 200  # 200x, 1.5% stop
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
        log.info(f"[{track.name}] {emoji}{pnl:+.2f}U ({r_mult:+.2f}R) | {exit_reason} | 净值:{track.equity:.2f}")

        track.position = None

        # 日亏损锁
        if self._daily_loss(track) < -0.20:
            track.lock(f"日亏损>20%", 72)

    def _daily_loss(self, track: StrategyTrack) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        day_trades = [t for t in track.trades if t["exit_time"][:10] == today]
        return sum(t["pnl"] for t in day_trades) / track.peak_equity if day_trades else 0

    # ── 排行榜 ──

    def leaderboard(self) -> str:
        lines = ["=" * 85]
        lines.append(f"{'策略':<16s} {'净值':>7s} {'盈亏%':>8s} {'交易':>5s} {'胜率':>6s} {'均R':>7s} {'PF':>6s} {'DD':>6s} {'持仓':>4s}")
        lines.append("-" * 85)

        ranked = sorted(self.tracks.values(), key=lambda t: t.equity, reverse=True)
        for t in ranked:
            pos_marker = "●" if t.position else "○"
            lock_marker = "🔒" if t.locked else ""
            lines.append(
                f"{t.name:<16s} {t.equity:>7.2f} {t.return_pct:>+7.1f}% "
                f"{t.trade_count:>5d} {t.win_rate:>5.1f}% {t.avg_r:>+6.2f}R "
                f"{t.profit_factor:>5.2f} {t.max_dd:>5.1f}%  {pos_marker}{lock_marker}"
            )
        lines.append("=" * 85)
        lines.append(f"本金: 100U × {len(self.tracks)} = {len(self.tracks)*100}U | R值 = 盈亏 ÷ 风险金额(15U)")
        return "\n".join(lines)

    # ── 持久化 ──

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

    def _save_leaderboard(self):
        data = {key: track.to_dict() for key, track in self.tracks.items()}
        with open(LEADERBOARD_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="单次 tick")
    parser.add_argument("--status", action="store_true", help="显示排行榜")
    parser.add_argument("--symbol", default="ETH_USDT")
    parser.add_argument("--equity", type=float, default=100.0)
    args = parser.parse_args()

    trader = MultiPaperTrader(args.symbol, args.equity)

    if args.status:
        print(trader.leaderboard())
        return

    if args.once:
        trader.tick()
        print()
        print(trader.leaderboard())
        return

    print(f"Multi-Strategy Paper Trading 启动: {args.symbol}")
    print(trader.leaderboard())
    print()

    while True:
        try:
            trader.tick()
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}]")
            print(trader.leaderboard())
            wait = 15 * 60
            time.sleep(wait)
        except KeyboardInterrupt:
            print("\n退出")
            trader._save_state()
            break
        except Exception as e:
            log.error(f"异常: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
