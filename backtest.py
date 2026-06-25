"""
回测引擎 — HT_DCPHASE × Ensemble
"""

import math
import statistics
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from config import STAGES, SIGNAL, EXIT, MODE_RISK_BUDGET
from strategic_brain import StrategicBrain
from tactical_brain import TacticalBrain
from _macro_calendar import detect_regime


# ── HT_DCPHASE ──

def _ht_phase(closes: List[float]):
    """Hilbert Transform phase, returns (phase_list, dc_period)"""
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
            if i1[i] < 0 and q1[i] > 0:
                raw += 180
            elif i1[i] < 0 and q1[i] < 0:
                raw += 180
            elif i1[i] > 0 and q1[i] < 0:
                raw += 360
            phase[i] = raw % 360
        else:
            phase[i] = phase[i - 1] if i > 0 else 0

    # Smooth phase (circular)
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

    # Dominant cycle detection using smoothed phase
    dc_period = 20
    last_cross = 0
    for i in range(8, n):
        if phase_smooth[i - 1] < 180 and phase_smooth[i] >= 180:
            if last_cross > 0:
                cycle_len = i - last_cross
                if 6 < cycle_len < 60:
                    dc_period = int(0.7 * dc_period + 0.3 * cycle_len)
            last_cross = i

    return phase_smooth, dc_period


# ── 引擎 ──

class BacktestEngine:
    def __init__(self, klines: List[Dict], initial_equity: float = 100.0, symbol: str = "ETH_USDT"):
        self.klines = klines
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.symbol = symbol
        self.strategic = StrategicBrain()
        self.tactical = TacticalBrain()
        self.position = None
        self.trades = []
        self.equity_curve = []
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.last_trade_date = ""
        self.stage = 1

    def run(self) -> dict:
        closes = [k["close"] for k in self.klines]
        highs = [k["high"] for k in self.klines]
        lows = [k["low"] for k in self.klines]
        times = [k["time"] for k in self.klines]

        phase_list, _ = _ht_phase(closes)

        start_idx = 50
        for i in range(start_idx, len(self.klines) - 1):
            dt = datetime.fromtimestamp(times[i])

            if self.equity > self.peak_equity:
                self.peak_equity = self.equity

            self._update_stage()
            self._reset_daily(dt)

            # 持仓 → 出场检查
            if self.position:
                exit_result = self._check_exit(
                    high=highs[i], low=lows[i], close=closes[i],
                    phase=phase_list[i], phase_prev=phase_list[i - 1] if i > 0 else phase_list[i],
                    dt=dt,
                )
                if exit_result:
                    self.trades.append(exit_result)
                    self.position = None
                    self.equity_curve.append((times[i], self.equity))
                continue

            # 锁
            hour = (dt.hour + 8) % 24
            if hour < SIGNAL["active_hour_start"] or hour >= SIGNAL["active_hour_end"]:
                continue
            if self.daily_trades >= 2:
                continue
            if self.consecutive_losses >= 3:
                continue

            regime = "ranging"
            if i >= 25:
                regime = detect_regime(self.klines[i - 25:i + 1])

            stats_win_rate = None
            stats_pnl_ratio = None
            if self.trades:
                recent = self.trades[-10:]
                w = [t for t in recent if t["pnl"] > 0]
                l = [t for t in recent if t["pnl"] <= 0]
                stats_win_rate = len(w) / len(recent)
                if l:
                    avg_w = sum(t["pnl"] for t in w) / len(w) if w else 0
                    avg_l = abs(sum(t["pnl"] for t in l) / len(l))
                    stats_pnl_ratio = avg_w / avg_l if avg_l > 0 else None

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
                continue

            signal = self.tactical.generate(self.symbol, self.klines[:i + 1], briefing)
            if signal is None:
                continue

            next_candle = self.klines[i + 1]
            entry_price = next_candle["open"]
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
                "entry_time": next_candle["time"],
                "entry_dt": datetime.fromtimestamp(next_candle["time"]),
                "size": size,
                "margin": margin,
                "stop_price": stop_price,
                "tp1": tp1,
                "tp2": tp2,
                "breakeven_activated": False,
                "score": signal.score,
                "mode": briefing.get("mode", "standard"),
            }
            self.daily_trades += 1

        if self.position:
            pnl = self._calc_pnl(closes[-1])
            self.equity += pnl
            self.trades.append(self._make_trade(closes[-1], pnl, "eod_close"))

        return self._report()

    def _check_exit(self, high, low, close, phase, phase_prev, dt) -> Optional[dict]:
        pos = self.position
        entry = pos["entry_price"]

        # [1] 硬止损
        if (pos["side"] == "long" and low <= pos["stop_price"]) or (pos["side"] == "short" and high >= pos["stop_price"]):
            pnl = self._calc_pnl(pos["stop_price"])
            self.equity += pnl
            self._update_loss_streak(pnl)
            return self._make_trade(pos["stop_price"], pnl, "stop_loss")

        # [2] TP1 / TP2
        if pos["side"] == "long":
            if high >= pos["tp1"] and not pos.get("tp1_hit"):
                pos["tp1_hit"] = True
                pos["breakeven_activated"] = True
                pos["stop_price"] = entry
            if high >= pos["tp2"] and not pos.get("tp2_hit"):
                pos["tp2_hit"] = True
        else:
            if low <= pos["tp1"] and not pos.get("tp1_hit"):
                pos["tp1_hit"] = True
                pos["breakeven_activated"] = True
                pos["stop_price"] = entry
            if low <= pos["tp2"] and not pos.get("tp2_hit"):
                pos["tp2_hit"] = True

        # [3] HT 相位反转 → 出场
        if pos["side"] == "long":
            # 做多: 相位下跌穿过 180° → 多头结束
            if phase_prev > 180 and phase <= 180:
                pnl = self._calc_pnl(close)
                self.equity += pnl
                self._update_loss_streak(pnl)
                return self._make_trade(close, pnl, "ht_phase_flip")
        else:
            # 做空: 相位上涨穿过 0° → 空头结束
            if phase_prev < 360 and phase >= 360:
                pnl = self._calc_pnl(close)
                self.equity += pnl
                self._update_loss_streak(pnl)
                return self._make_trade(close, pnl, "ht_phase_flip")

        # [4] 时间止损
        hours_held = (dt - pos["entry_dt"]).total_seconds() / 3600
        if hours_held >= EXIT["time_stop_hours"] and abs(close - entry) / entry < EXIT["time_stop_min_pnl"]:
            pnl = self._calc_pnl(close)
            self.equity += pnl
            self._update_loss_streak(pnl)
            return self._make_trade(close, pnl, "time_exit")

        return None

    def _calc_pnl(self, exit_price):
        if self.position["side"] == "long":
            return (exit_price - self.position["entry_price"]) * self.position["size"]
        return (self.position["entry_price"] - exit_price) * self.position["size"]

    def _make_trade(self, exit_price, pnl, reason):
        p = self.position
        return {
            "symbol": self.symbol,
            "direction": p["side"],
            "entry_price": p["entry_price"],
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / p["margin"], 4) if p["margin"] > 0 else 0,
            "margin": round(p["margin"], 2),
            "exit_reason": reason,
            "score": p.get("score", 0),
            "mode": p.get("mode", "standard"),
            "stage": self.stage,
            "time": datetime.fromtimestamp(p["entry_time"]).isoformat(),
        }

    def _update_loss_streak(self, pnl):
        self.consecutive_losses = 0 if pnl > 0 else self.consecutive_losses + 1

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

    def _report(self):
        if not self.trades:
            return {"error": "无交易"}

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.trades)

        peak = 100.0
        eq = [100.0]
        for t in self.trades:
            eq.append(eq[-1] + t["pnl"])
        max_dd = 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)

        sharpe = 0
        if len(self.trades) >= 5:
            returns = [t["pnl_pct"] for t in self.trades]
            avg = statistics.mean(returns)
            std = statistics.stdev(returns) if len(returns) > 1 else 0.01
            sharpe = (avg / std) * math.sqrt(252) if std > 0 else 0

        gp = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses))
        pf = gp / gl if gl > 0 else float("inf")

        exit_reasons = {}
        for t in self.trades:
            r = t["exit_reason"]
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        return {
            "symbol": self.symbol,
            "initial_equity": 100.0,
            "final_equity": round(100.0 + total_pnl, 2),
            "total_return_pct": round(total_pnl, 2),
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades), 3) if self.trades else 0,
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(abs(sum(t["pnl"] for t in losses)) / len(losses), 2) if losses else 0,
            "profit_factor": round(pf, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "best_trade": round(max(t["pnl"] for t in self.trades), 2),
            "worst_trade": round(min(t["pnl"] for t in self.trades), 2),
            "exit_reasons": exit_reasons,
            "avg_score": round(sum(t.get("score", 0) for t in self.trades) / len(self.trades), 1),
        }
