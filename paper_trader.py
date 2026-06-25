"""
Paper Trading — 虚拟交易系统
HT_DCPHASE 策略，实时数据，模拟成交
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import STAGES, SIGNAL, EXIT, LOCKS, MODE_RISK_BUDGET, MODE_QUALITY_THRESHOLD
from strategic_brain import StrategicBrain
from tactical_brain import TacticalBrain
from _macro_calendar import detect_regime
from gate_data import fetch_klines
from pushplus import PushPlus

_push_client = PushPlus()  # token 从环境变量 PUSHPLUS_TOKEN 读，没有则静默

def _safe_push(title, content):
    try:
        _push_client.send(title, content)
    except Exception:
        pass  # PushPlus 非强制

log = logging.getLogger("commander.paper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

STATE_FILE = "paper_state.json"
STATE_HISTORY_FILE = "paper_trades.json"


class PaperTrader:
    """
    虚拟交易引擎。
    每 15 分钟 tick 一次：拉 K 线 → 检查出场 → 检查入场 → 推送
    所有成交模拟：用下一根 K 线开盘价入场，触及价出场
    """

    def __init__(self, symbol: str = "ETH_USDT", equity: float = 100.0):
        self.symbol = symbol
        self.equity = equity
        self.peak_equity = equity

        self.strategic = StrategicBrain()
        self.tactical = TacticalBrain()

        # 持仓状态
        self.position: Optional[dict] = None

        # 纪律状态
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.last_trade_date = ""
        self.stage = 1
        self.locked_until = None
        self.locked_reason = ""

        # 历史
        self.trades: list = []
        self._load_state()

    # ── 主循环 ──

    def tick(self):
        """一次检查周期"""
        klines = fetch_klines(self.symbol, "15m", limit=200)
        if not klines:
            log.warning("无法获取 K 线，跳过本 tick")
            return

        # 去重排序
        seen = set()
        klines = sorted(
            [k for k in klines if not (k["time"] in seen or seen.add(k["time"]))],
            key=lambda x: x["time"],
        )

        now = datetime.now()
        dt_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ── 解锁检查 ──
        if self.locked_until and now >= self.locked_until:
            log.info(f"封锁解除 ({self.locked_reason})")
            self.locked_until = None
            self.locked_reason = ""

        # ── 每日重置 ──
        self._reset_daily(now)

        # ── 更新阶段 ──
        self._update_stage()

        # ── 持仓 → 出场检查 ──
        if self.position:
            self._check_paper_exit(klines, now)

        # ── 锁状态 → 不开新仓 ──
        if self.locked_until:
            return

        hour_china = (now.hour + 8) % 24
        if hour_china < SIGNAL["active_hour_start"] or hour_china >= SIGNAL["active_hour_end"]:
            return
        if self.daily_trades >= LOCKS["max_trades_per_day"]:
            return
        if self.consecutive_losses >= LOCKS["max_consecutive_losses"]:
            self._lock(f"连亏{LOCKS['max_consecutive_losses']}次", hours=LOCKS["consecutive_loss_hours"])
            return

        # ── 战略脑 ──
        regime = detect_regime(klines[-25:]) if len(klines) >= 25 else "ranging"

        stats_win_rate, stats_pnl_ratio = self._recent_stats()
        stg = STAGES[self.stage]

        briefing = self.strategic.decide(
            equity=self.equity,
            stage=self.stage,
            stage_name=stg["name"],
            distance_to_target=stg["target"] - self.equity,
            progress_pct=(self.equity - stg["equity_range"][0]) / (stg["equity_range"][1] - stg["equity_range"][0]),
            consecutive_losses=self.consecutive_losses,
            recent_win_rate=stats_win_rate,
            recent_pnl_ratio=stats_pnl_ratio,
            regime=regime,
        )
        if briefing.get("locked"):
            self._lock(briefing.get("lock_reason", "战略脑"), hours=LOCKS["manual_lock_hours"])
            return

        # ── 战术脑 ──
        signal = self.tactical.generate(self.symbol, klines, briefing)
        if signal is None:
            return

        # ── 模拟入场（用最后一根 K 线收盘价）──
        entry_price = klines[-1]["close"]
        direction = signal.direction

        margin = self.equity * stg["margin_pct"]
        risk_mult = MODE_RISK_BUDGET.get(briefing.get("mode", "standard"), 1.0)
        margin *= risk_mult
        size = margin * stg["leverage"] / entry_price * risk_mult
        hard_stop = stg["hard_stop_pct"]

        stop_price = entry_price * (1 - hard_stop) if direction == "long" else entry_price * (1 + hard_stop)
        tp1 = entry_price * (1 + EXIT["tp1_pct"]) if direction == "long" else entry_price * (1 - EXIT["tp1_pct"])
        tp2 = entry_price * (1 + EXIT["tp2_pct"]) if direction == "long" else entry_price * (1 - EXIT["tp2_pct"])

        self.position = {
            "side": direction,
            "entry_price": entry_price,
            "entry_time": klines[-1]["time"],
            "entry_dt": now,
            "size": size,
            "margin": round(margin, 2),
            "stop_price": stop_price,
            "tp1": tp1,
            "tp2": tp2,
            "breakeven_activated": False,
            "score": signal.score,
            "phase": signal.phase,
            "mode": briefing.get("mode", "standard"),
            "stage": self.stage,
        }
        self.daily_trades += 1

        msg = (
            f"📊 Paper Trade 开仓\n"
            f"币种: {self.symbol}\n"
            f"方向: {direction.upper()}\n"
            f"入场价: {entry_price:.2f}\n"
            f"止损价: {stop_price:.2f}\n"
            f"止盈: TP1{tp1:.2f} TP2{tp2:.2f}\n"
            f"保证金: {margin:.2f}U | 杠杆:{stg['leverage']}x\n"
            f"相位: {signal.phase:.0f}° | 评分: {signal.score}\n"
            f"模式: {briefing.get('mode')}"
        )
        log.info(msg.replace("\n", " | "))
        _safe_push(f"200x Paper: {direction.upper()} {self.symbol} @ {entry_price:.2f}", msg)
        self._save_state()

    # ── 出场 ──

    def _check_paper_exit(self, klines: list, now: datetime):
        pos = self.position
        entry = pos["entry_price"]
        direction = pos["side"]

        # 最新价格
        current = klines[-1]["close"]
        current_high = klines[-1]["high"]
        current_low = klines[-1]["low"]

        exit_price = None
        exit_reason = ""

        # [1] 硬止损
        if direction == "long" and current_low <= pos["stop_price"]:
            exit_price = pos["stop_price"]
            exit_reason = "stop_loss"
        elif direction == "short" and current_high >= pos["stop_price"]:
            exit_price = pos["stop_price"]
            exit_reason = "stop_loss"

        # [2] TP1/TP2
        if exit_price is None:
            if direction == "long":
                if current_high >= pos["tp1"] and not pos.get("tp1_hit"):
                    pos["tp1_hit"] = True
                    pos["breakeven_activated"] = True
                    pos["stop_price"] = entry
                if current_high >= pos["tp2"] and not pos.get("tp2_hit"):
                    pos["tp2_hit"] = True
            else:
                if current_low <= pos["tp1"] and not pos.get("tp1_hit"):
                    pos["tp1_hit"] = True
                    pos["breakeven_activated"] = True
                    pos["stop_price"] = entry
                if current_low <= pos["tp2"] and not pos.get("tp2_hit"):
                    pos["tp2_hit"] = True

        # [3] HT 相位反转
        if exit_price is None and len(klines) >= 8:
            from backtest import _ht_phase
            closes = [k["close"] for k in klines]
            phase_list, _ = _ht_phase(closes)
            p_curr = phase_list[-1]
            p_prev = phase_list[-2] if len(phase_list) > 1 else p_curr

            if direction == "long" and p_prev > 180 and p_curr <= 180:
                exit_price = current
                exit_reason = "ht_phase_flip"
            elif direction == "short" and p_prev < 360 and p_curr >= 360:
                exit_price = current
                exit_reason = "ht_phase_flip"

        # [4] 时间止损
        if exit_price is None:
            hours_held = (now - pos["entry_dt"]).total_seconds() / 3600
            if hours_held >= EXIT["time_stop_hours"] and abs(current - entry) / entry < EXIT["time_stop_min_pnl"]:
                exit_price = current
                exit_reason = "time_exit"

        if exit_price is None:
            return  # 继续持有

        # 执行出场
        pnl = (exit_price - entry) * pos["size"] if direction == "long" else (entry - exit_price) * pos["size"]
        self.equity += pnl
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        risk_per_trade = pos["margin"] * STAGES[pos.get("stage", 1)]["hard_stop_pct"] * STAGES[pos.get("stage", 1)]["leverage"]
        r_multiple = pnl / pos["margin"] if pos["margin"] > 0 else 0

        self.consecutive_losses = 0 if pnl > 0 else self.consecutive_losses + 1

        trade_record = {
            "symbol": self.symbol,
            "direction": direction,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "r_multiple": round(r_multiple, 2),
            "margin": pos["margin"],
            "exit_reason": exit_reason,
            "score": pos.get("score", 0),
            "phase": pos.get("phase", 0),
            "mode": pos.get("mode", "standard"),
            "stage": self.stage,
            "entry_time": pos["entry_dt"].isoformat(),
            "exit_time": now.isoformat(),
        }
        self.trades.append(trade_record)

        dd = (self.peak_equity - self.equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.trades)
        wr = len(wins) / len(self.trades) * 100 if self.trades else 0

        emoji = "🟢" if pnl > 0 else "🔴"
        msg = (
            f"{emoji} Paper Trade 平仓\n"
            f"币种: {self.symbol} | {direction.upper()}\n"
            f"入场: {entry:.2f} → 出场: {exit_price:.2f}\n"
            f"盈亏: {pnl:+.2f}U ({r_multiple:+.2f}R)\n"
            f"原因: {exit_reason}\n"
            f"净值: {self.equity:.2f}U | 胜率:{wr:.0f}% | DD:{dd:.1f}%\n"
            f"总盈亏: {total_pnl:+.2f}U"
        )
        log.info(msg.replace("\n", " | "))
        _safe_push(f"{'WIN' if pnl > 0 else 'LOSS'} {pnl:+.2f}U | {self.symbol}", msg)

        self.position = None
        self._save_state()
        self._save_history()

        # 日亏损锁
        if self._daily_pnl_pct() < -LOCKS["daily_loss_pct"]:
            self._lock(f"日亏损>{LOCKS['daily_loss_pct']*100:.0f}%", hours=LOCKS["daily_loss_hours"])

    # ── 辅助 ──

    def _lock(self, reason: str, hours: float):
        self.locked_until = datetime.now() + timedelta(hours=hours)
        self.locked_reason = reason
        msg = f"🔒 封锁 {hours}h: {reason}"
        log.warning(msg)
        _safe_push("200x Locked", msg)

    def _daily_pnl_pct(self) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        day_trades = [t for t in self.trades if t["exit_time"][:10] == today or t["entry_time"][:10] == today]
        if not day_trades:
            return 0
        return sum(t["pnl"] for t in day_trades) / self.peak_equity

    def _recent_stats(self):
        if len(self.trades) < 3:
            return None, None
        recent = self.trades[-10:]
        w = [t for t in recent if t["pnl"] > 0]
        l = [t for t in recent if t["pnl"] <= 0]
        wr = len(w) / len(recent)
        if l and w:
            avg_w = sum(t["pnl"] for t in w) / len(w)
            avg_l = abs(sum(t["pnl"] for t in l)) / len(l)
            return wr, avg_w / avg_l if avg_l > 0 else None
        return wr, None

    def _update_stage(self):
        for sid in [4, 3, 2, 1]:
            lo, hi = STAGES[sid]["equity_range"]
            if lo <= self.equity < hi:
                self.stage = sid
                return
        self.stage = 4 if self.equity >= STAGES[4]["equity_range"][1] else 1

    def _reset_daily(self, dt):
        ds = dt.strftime("%Y-%m-%d")
        if ds != self.last_trade_date:
            self.daily_trades = 0
            self.last_trade_date = ds

    # ── 持久化 ──

    def _save_state(self):
        data = {
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "position": self.position,
            "consecutive_losses": self.consecutive_losses,
            "daily_trades": self.daily_trades,
            "last_trade_date": self.last_trade_date,
            "stage": self.stage,
            "locked_until": self.locked_until.isoformat() if self.locked_until else None,
            "locked_reason": self.locked_reason,
        }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
        log.debug(f"状态已保存 → {STATE_FILE}")

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            self.equity = data.get("equity", self.equity)
            self.peak_equity = data.get("peak_equity", self.equity)
            self.position = data.get("position")
            self.consecutive_losses = data.get("consecutive_losses", 0)
            self.daily_trades = data.get("daily_trades", 0)
            self.last_trade_date = data.get("last_trade_date", "")
            self.stage = data.get("stage", 1)
            if data.get("locked_until"):
                self.locked_until = datetime.fromisoformat(data["locked_until"])
                self.locked_reason = data.get("locked_reason", "")
            log.info(f"状态已恢复: 净值={self.equity:.2f}, 持仓={bool(self.position)}")
        except Exception as e:
            log.warning(f"状态恢复失败: {e}")

    def _save_history(self):
        with open(STATE_HISTORY_FILE, "w") as f:
            json.dump(self.trades, f, indent=2, default=str)

    def status(self) -> str:
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
        lines = [
            f"净值: {self.equity:.2f}U | 峰值: {self.peak_equity:.2f}U | DD: {dd:.1f}%",
            f"阶段: {STAGES[self.stage]['name']} | 持仓: {'是' if self.position else '否'}",
            f"连亏: {self.consecutive_losses} | 今日交易: {self.daily_trades}",
        ]
        if self.position:
            lines.append(
                f"持仓: {self.position['side'].upper()} @ {self.position['entry_price']:.2f}"
                f" | 止损: {self.position['stop_price']:.2f}"
            )
        if self.locked_until:
            lines.append(f"🔒 封锁至: {self.locked_until.strftime('%Y-%m-%d %H:%M')} ({self.locked_reason})")
        if self.trades:
            recent = self.trades[-5:]
            parts = []
            for t in recent:
                parts.append(f"{t['direction']} {t['pnl']:+.2f}U")
            lines.append(f"最近交易: {' | '.join(parts)}")
        return "\n".join(lines)


# ── CLI ──

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Paper Trading")
    parser.add_argument("--once", action="store_true", help="只跑一次 tick")
    parser.add_argument("--status", action="store_true", help="显示状态")
    parser.add_argument("--symbol", default="ETH_USDT")
    parser.add_argument("--equity", type=float, default=100.0)
    args = parser.parse_args()

    trader = PaperTrader(args.symbol, args.equity)

    if args.status:
        print(trader.status())
        return

    if args.once:
        trader.tick()
        print(trader.status())
        return

    # 持续运行
    print(f"200x Paper Trading 启动: {args.symbol}")
    print(trader.status())
    print("-" * 40)

    while True:
        try:
            trader.tick()
            wait = 15 * 60  # 15 分钟
            next_tick = datetime.now() + timedelta(seconds=wait)
            print(f"  [{(datetime.now()).strftime('%H:%M:%S')}] 净值:{trader.equity:.2f}U | 持仓:{'是' if trader.position else '否'} | 下次:{next_tick.strftime('%H:%M:%S')}")
            time.sleep(wait)
        except KeyboardInterrupt:
            print("\n退出")
            trader._save_state()
            break
        except Exception as e:
            log.error(f"Tick 异常: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
