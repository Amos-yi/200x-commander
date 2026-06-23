"""
执行层 —— 开仓 / 双层止损 / 分批止盈 / 出场管理
通过 Gate.io API 执行
"""

import time
import logging
from datetime import datetime
from typing import Optional
from config import EXIT, SIGNAL

log = logging.getLogger("commander.exec")


class ExecutionLayer:
    """
    不依赖 pandas/numpy。纯 Gate API + 标准库。
    """

    def __init__(self, gate_client, stage_params: dict, lock_manager):
        self.client = gate_client
        self.params = stage_params
        self.locks = lock_manager
        self._active_stop_orders = {}  # order_id -> type

    # ── 开仓 ──

    def open_position(self, signal) -> Optional[dict]:
        """
        返回 {"order_id", "entry_price", "contract_size", "stop_order_id", "market_stop_order_id"}
        或 None
        """
        symbol = signal.symbol
        direction = signal.direction

        # [1] 单仓位硬约束
        positions = self._list_positions()
        if len(positions) > 0:
            log.info(f"已有持仓: {[p['contract'] for p in positions]}，不开新仓")
            return None

        # [2] 锁检查
        locked, reason = self.locks.is_locked()
        if locked:
            log.info(f"封锁中，跳过开仓: {reason}")
            return None

        # [3] 计算合约数量
        margin = self.params["margin"]
        leverage = self.params["leverage"]
        entry_price = signal.entry_price
        nominal = margin * leverage
        contract_size = int(nominal / entry_price)  # 向下取整

        if contract_size <= 0:
            log.error(f"合约数量为 0: margin={margin}, nominal={nominal}")
            return None

        # 周末减半
        from _macro_calendar import Calendar
        if Calendar.is_weekend():
            contract_size = max(1, int(contract_size * SIGNAL["weekend_margin_mult"]))

        # [4] 市价开仓
        side = "long" if direction == "long" else "short"
        try:
            order = self.client.futures_create_order(
                settle="usdt",
                contract=symbol,
                size=contract_size,
                price="0",
                tif="ioc",
                text="t-commander",
                side=side,
            )
            actual_entry = float(order.fill_price) if hasattr(order, "fill_price") and order.fill_price else entry_price
            log.info(f"开仓: {symbol} {side} x{contract_size} @ ~{actual_entry}")
        except Exception as e:
            log.error(f"开仓失败: {e}")
            return None

        # [5] 双层止损
        hard_stop_pct = self.params["hard_stop_pct"]
        buffer_pct = self.params.get("liquidation_buffer_pct", 0.01)

        if direction == "long":
            stop_price = actual_entry * (1 - hard_stop_pct)
            market_stop_price = actual_entry * (1 - hard_stop_pct - buffer_pct)
            stop_rule = "<="
        else:
            stop_price = actual_entry * (1 + hard_stop_pct)
            market_stop_price = actual_entry * (1 + hard_stop_pct + buffer_pct)
            stop_rule = ">="

        # 第一层：限价止损
        try:
            limit_stop = self.client.futures_create_price_triggered_order(
                settle="usdt",
                contract=symbol,
                size=-contract_size,
                trigger_price=str(stop_price),
                trigger_condition=stop_rule,
                order_type="limit",
                price=str(stop_price),
            )
            self._active_stop_orders[limit_stop.id] = "limit_stop"
            log.info(f"限价止损已设: {stop_price:.2f}")
        except Exception as e:
            log.error(f"限价止损设置失败: {e}")
            limit_stop = None

        # 第二层：市价止损兜底
        try:
            market_stop = self.client.futures_create_price_triggered_order(
                settle="usdt",
                contract=symbol,
                size=-contract_size,
                trigger_price=str(market_stop_price),
                trigger_condition=stop_rule,
                order_type="market",
                price="0",
            )
            self._active_stop_orders[market_stop.id] = "market_stop"
            log.info(f"市价兜底止损已设: {market_stop_price:.2f}")
        except Exception as e:
            log.error(f"市价兜底止损设置失败: {e}")
            market_stop = None

        # [6] 分批止盈
        self._set_take_profits(symbol, direction, actual_entry, contract_size)

        return {
            "order_id": order.id,
            "entry_price": actual_entry,
            "contract_size": contract_size,
            "direction": direction,
            "stop_order_id": limit_stop.id if limit_stop else None,
            "market_stop_order_id": market_stop.id if market_stop else None,
        }

    # ── 止盈 ──

    def _set_take_profits(self, symbol, direction, entry, size):
        tp1_pct = EXIT["tp1_pct"]
        tp2_pct = EXIT["tp2_pct"]
        tp1_size = -int(size * EXIT["tp1_ratio"])
        tp2_size = -int(size * EXIT["tp2_ratio"])

        if direction == "long":
            tp1_price = entry * (1 + tp1_pct)
            tp2_price = entry * (1 + tp2_pct)
            rule = ">="
        else:
            tp1_price = entry * (1 - tp1_pct)
            tp2_price = entry * (1 - tp2_pct)
            rule = "<="

        for label, tp_price, tp_size in [
            ("TP1", tp1_price, tp1_size),
            ("TP2", tp2_price, tp2_size),
        ]:
            if abs(tp_size) == 0:
                continue
            try:
                self.client.futures_create_price_triggered_order(
                    settle="usdt",
                    contract=symbol,
                    size=tp_size,
                    trigger_price=str(tp_price),
                    trigger_condition=rule,
                    order_type="limit",
                    price=str(tp_price),
                )
                log.info(f"{label} 止盈单: {tp_price:.2f} x {abs(tp_size)}")
            except Exception as e:
                log.error(f"{label} 止盈单设置失败: {e}")

    # ── 持仓管理（主循环调用）──

    def manage_positions(self, entry_price: float, entry_time, direction: str, klines_5m: list) -> Optional[str]:
        """
        管理现有持仓的出场逻辑。
        返回: 'breakeven_moved' | 'trailing_exit' | 'time_exit' | None
        """
        positions = self._list_positions()
        if len(positions) == 0:
            return None

        # 只管理第一个持仓
        pos = positions[0]
        current_price = float(pos.get("mark_price", 0))
        if current_price == 0:
            return None

        # 计算浮盈
        if direction == "long":
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        hard_stop_pct = self.params["hard_stop_pct"]

        # [1] 保本线
        breakeven_threshold = hard_stop_pct * EXIT["breakeven_multiplier"]
        if pnl_pct >= breakeven_threshold:
            # 检查止损是否已在开仓价之上（通过查条件单）
            self._move_stop_to_breakeven(entry_price, direction)
            return "breakeven_moved"

        # [2] 时间止损
        import datetime
        elapsed = datetime.datetime.now() - entry_time
        if elapsed.total_seconds() / 3600 > EXIT["time_stop_hours"]:
            if pnl_pct < EXIT["time_stop_min_pnl"]:
                self._close_all()
                return "time_exit"

        # [3] 跟踪止损（基于 5min EMA）
        if klines_5m and len(klines_5m) >= 5:
            closes = [k["close"] for k in klines_5m]
            ema5 = self._ema_last(closes, EXIT["trailing_ema"])
            if direction == "long" and current_price < ema5:
                self._close_all()
                return "trailing_exit"
            elif direction == "short" and current_price > ema5:
                self._close_all()
                return "trailing_exit"

        return None

    def _move_stop_to_breakeven(self, entry_price, direction):
        """将限价止损移到开仓价（保本），取消原条件单"""
        pass  # 需要查现有条件单 → 取消 → 重新挂单。依赖 Gate API。

    def _close_all(self):
        """市价全平当前持仓"""
        positions = self._list_positions()
        for pos in positions:
            try:
                side = "long" if pos["size"] < 0 else "short"
                size = abs(pos["size"])
                self.client.futures_create_order(
                    settle="usdt",
                    contract=pos["contract"],
                    size=size,
                    price="0",
                    tif="ioc",
                    text="t-commander-close",
                    side=side,
                    reduce_only=True,
                )
                log.info(f"平仓: {pos['contract']} x{size}")
            except Exception as e:
                log.error(f"平仓失败: {e}")

    # ── 止损撤销检测 ──

    def check_stop_cancelled(self) -> bool:
        """检测是否有手动撤销的止损单"""
        try:
            open_orders = self.client.futures_list_price_triggered_orders(
                settle="usdt", status="cancelled"
            )
            for o in open_orders:
                if hasattr(o, "id") and o.id in self._active_stop_orders:
                    if self._active_stop_orders[o.id] == "limit_stop":
                        log.critical("检测到手动撤销限价止损！")
                        return True
        except Exception:
            pass
        return False

    # ── 辅助 ──

    def _list_positions(self) -> list:
        try:
            positions = self.client.futures_list_positions(settle="usdt")
            return [p for p in positions if float(p.size) != 0]
        except Exception:
            return []

    @staticmethod
    def _ema_last(values, period):
        if len(values) < period:
            return values[-1]
        k = 2 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema


# ═══════════════════════════════════════════════════════════════════
#  纸面执行层 —— 手续费 + 滑点
# ═══════════════════════════════════════════════════════════════════

class PaperExecutionLayer:
    """与 ExecutionLayer 同接口，但不调用 Gate API。
    所有成交在内存模拟，每次开仓/平仓扣除手续费 + 滑点。
    """

    def __init__(self, stage_params: dict, lock_manager, taker_fee=0.0005, slippage=0.0003):
        self.params = stage_params
        self.locks = lock_manager
        self.taker_fee = taker_fee
        self.slippage = slippage

        self._position = None       # dict or None
        self._stops = {}            # { "limit": price, "market": price }
        self._take_profits = []     # [(tp_price, tp_size), ...]
        self._equity = 100.0        # 纸面净值
        self._log = logging.getLogger("commander.paper")

    # ── 开仓 ────────────────────────────────────

    def open_position(self, signal) -> Optional[dict]:
        symbol = getattr(signal, "symbol", "ETH_USDT")
        direction = getattr(signal, "direction", "long")
        entry_price = getattr(signal, "entry_price", 0.0)

        if self._position is not None:
            self._log.info("已有持仓，不开新仓")
            return None

        locked, reason = self.locks.is_locked()
        if locked:
            self._log.info(f"封锁中，跳过: {reason}")
            return None

        margin = float(self.params.get("margin", 5.0))
        leverage = int(self.params.get("leverage", 200))
        nominal = margin * leverage
        contract_size = int(nominal / entry_price) if entry_price > 0 else 0
        if contract_size <= 0:
            self._log.error(f"合约数量 0")
            return None

        from _macro_calendar import Calendar
        if Calendar.is_weekend():
            from config import SIGNAL
            contract_size = max(1, int(contract_size * SIGNAL["weekend_margin_mult"]))

        # 模拟成交价：方向不利滑点
        if direction == "long":
            fill_price = entry_price * (1 + self.slippage)
        else:
            fill_price = entry_price * (1 - self.slippage)

        # 手续费从净值扣除
        fee = nominal * self.taker_fee
        self._equity -= fee

        self._position = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": fill_price,
            "contract_size": contract_size,
            "margin": margin,
            "nominal": nominal,
            "entry_time": datetime.now(),
        }

        # 止损
        hard_stop_pct = float(self.params.get("hard_stop_pct", 0.015))
        buffer_pct = float(self.params.get("liquidation_buffer_pct", 0.01))
        if direction == "long":
            self._stops["limit"] = fill_price * (1 - hard_stop_pct)
            self._stops["market"] = fill_price * (1 - hard_stop_pct - buffer_pct)
        else:
            self._stops["limit"] = fill_price * (1 + hard_stop_pct)
            self._stops["market"] = fill_price * (1 + hard_stop_pct + buffer_pct)

        # 止盈
        self._take_profits = self._calc_take_profits(direction, fill_price, contract_size)

        self._log.info(
            f"纸面开仓: {symbol} {direction} x{contract_size} @ {fill_price:.4f} "
            f"滑点:{self.slippage:.4f} 手续费:{fee:.4f} 净值:{self._equity:.2f}"
        )
        return {
            "order_id": "paper",
            "entry_price": fill_price,
            "contract_size": contract_size,
            "direction": direction,
            "stop_order_id": "paper_limit_stop",
            "market_stop_order_id": "paper_market_stop",
        }

    def _calc_take_profits(self, direction, entry, size):
        from config import EXIT
        tp1_pct = EXIT["tp1_pct"]
        tp2_pct = EXIT["tp2_pct"]
        tp1_size = -int(size * EXIT["tp1_ratio"])
        tp2_size = -int(size * EXIT["tp2_ratio"])
        result = []
        if direction == "long":
            if abs(tp1_size) > 0:
                result.append((entry * (1 + tp1_pct), tp1_size))
            if abs(tp2_size) > 0:
                result.append((entry * (1 + tp2_pct), tp2_size))
        else:
            if abs(tp1_size) > 0:
                result.append((entry * (1 - tp1_pct), tp1_size))
            if abs(tp2_size) > 0:
                result.append((entry * (1 - tp2_pct), tp2_size))
        return result

    # ── 持仓管理 ────────────────────────────────

    def manage_positions(self, entry_price, entry_time, direction, klines_5m) -> Optional[str]:
        if self._position is None:
            return None
        if not klines_5m or len(klines_5m) == 0:
            return None

        current_price = float(klines_5m[-1].get("close", 0))
        if current_price <= 0:
            return None

        pos = self._position
        entry = float(pos["entry_price"])
        pnl_pct = ((current_price - entry) / entry) if direction == "long" else ((entry - current_price) / entry)

        # 止损触发
        if direction == "long":
            if current_price <= self._stops.get("market", -99999):
                self._close_position(current_price, "硬止损(兜底)")
                return "stop_loss"
            if current_price <= self._stops.get("limit", -99999):
                self._close_position(current_price, "硬止损(限价)")
                return "stop_loss"
        else:
            if current_price >= self._stops.get("market", 99999):
                self._close_position(current_price, "硬止损(兜底)")
                return "stop_loss"
            if current_price >= self._stops.get("limit", 99999):
                self._close_position(current_price, "硬止损(限价)")
                return "stop_loss"

        # 止盈触发
        for tp_price, tp_size in self._take_profits:
            hit = ((direction == "long" and current_price >= tp_price) or
                   (direction == "short" and current_price <= tp_price))
            if hit:
                self._close_position(current_price, "止盈")
                return "take_profit"

        # 保本线
        hard_stop_pct = float(self.params.get("hard_stop_pct", 0.015))
        from config import EXIT
        breakeven_threshold = hard_stop_pct * EXIT["breakeven_multiplier"]
        if pnl_pct >= breakeven_threshold:
            self._move_stop_to_breakeven(entry, direction)
            return "breakeven_moved"

        # 时间止损
        elapsed = datetime.now() - entry_time
        if elapsed.total_seconds() / 3600 > EXIT["time_stop_hours"]:
            if pnl_pct < EXIT["time_stop_min_pnl"]:
                self._close_position(current_price, "时间止损")
                return "time_exit"

        # 跟踪止损
        if klines_5m and len(klines_5m) >= 5:
            closes = [float(k["close"]) for k in klines_5m]
            ema5 = self._ema_last(closes, EXIT.get("trailing_ema", 5))
            if (direction == "long" and current_price < ema5) or \
               (direction == "short" and current_price > ema5):
                self._close_position(current_price, "跟踪止损")
                return "trailing_exit"

        return None

    def _move_stop_to_breakeven(self, entry_price, direction):
        if direction == "long" and self._stops.get("limit", 0) < entry_price:
            self._stops["limit"] = entry_price
        elif direction == "short" and self._stops.get("limit", 99999) > entry_price:
            self._stops["limit"] = entry_price

    def _close_position(self, current_price, reason=""):
        """平仓：扣除手续费，更新净值"""
        if self._position is None:
            return
        pos = self._position
        direction = pos["direction"]
        entry = pos["entry_price"]
        size = pos["contract_size"]
        nominal = pos["nominal"]

        # 平仓滑点（不利方向）
        if direction == "long":
            exit_price = current_price * (1 - self.slippage)
            pnl = (exit_price - entry) * size
        else:
            exit_price = current_price * (1 + self.slippage)
            pnl = (entry - exit_price) * size

        # 平仓手续费
        fee = nominal * self.taker_fee
        pnl -= fee
        self._equity += pnl

        self._log.info(
            f"纸面平仓: {pos['symbol']} {direction} x{size} "
            f"@{entry:.4f}→{exit_price:.4f} 盈亏:{pnl:+.4f}U "
            f"手续费:{fee:.4f} 净值:{self._equity:.2f} ({reason})"
        )

        self._position = None
        self._stops = {}
        self._take_profits = []

    def _close_all(self):
        self._close_position(self._position["entry_price"], "市价全平") if self._position else None

    def check_stop_cancelled(self) -> bool:
        return False  # 纸面不存在手动撤销

    @property
    def equity(self) -> float:
        return round(self._equity, 8)

    @staticmethod
    def _ema_last(values, period):
        if len(values) < period:
            return values[-1]
        k = 2 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema
