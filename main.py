"""
200x Commander Bot — 主循环
三脑架构：战略脑 → 战术脑 → 执行层
"""

import os
import sys
import json
import time
import logging
import signal as os_signal
from datetime import datetime, timedelta
from typing import Optional

from config import STAGES, SIGNAL
from stage_manager import StageManager
from lock_manager import LockManager
from calendar import Calendar, detect_regime
from strategic_brain import StrategicBrain
from tactical_brain import TacticalBrain, Signal
from execution_layer import ExecutionLayer
from trade_logger import log_trade, recent_stats

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "commander.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("commander")

# ── 状态文件 ──
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


class Commander:
    """
    200x Commander Bot
    专门服务 100U→60,000U 的作战系统
    """

    def __init__(self, gate_client):
        self.client = gate_client
        self.state = self._load_or_init()
        self.stage = StageManager(self.state.get("equity", 100.0))
        self.locks = LockManager()
        self.strategic = StrategicBrain()
        self.tactical = TacticalBrain()
        self.executor = ExecutionLayer(gate_client, self.stage.get_params(), self.locks)

        self._entry_price = 0.0
        self._entry_time = None
        self._entry_direction = ""
        self._shutdown = False

        os_signal.signal(os_signal.SIGINT, self._on_shutdown)
        os_signal.signal(os_signal.SIGTERM, self._on_shutdown)

    # ═══════════════════════════════════════════
    #  主循环
    # ═══════════════════════════════════════════

    def run(self):
        log.info("=" * 50)
        log.info("200x Commander Bot 启动")
        log.info(self.stage.summary())
        log.info("=" * 50)

        # 输出首次作战指令
        self._print_briefing()

        while not self._shutdown:
            try:
                self._tick()
            except Exception as e:
                log.exception(f"主循环异常: {e}")
            time.sleep(60)

        log.info("Commander Bot 已安全退出")

    def _tick(self):
        now = datetime.now()

        # ── 更新净值 ──
        equity = self._fetch_equity()
        if equity is None:
            return

        transition = self.stage.update_equity(equity)
        if transition:
            self._on_stage_transition(transition)
            self.executor = ExecutionLayer(
                self.client, self.stage.get_params(), self.locks
            )

        # ── 止损撤销检测 ──
        if self.executor.check_stop_cancelled():
            self.locks.on_stop_cancelled()
            log.critical("!!! 检测到手动撤止损 → 锁定 7 天 !!!")

        # ── 锁检查 ──
        locked, reason = self.locks.is_locked()
        if locked:
            if now.second < 10:
                log.info(f"🔒 {reason}")
            return

        # ── 战略脑：今日作战指令 ──
        stats = recent_stats(10)
        regime = detect_regime(self._fetch_klines_1h("BTC_USDT"))

        if self.stage.stage == 1:
            # 阶段 1 用传入参数，阶段 2+ 用 stats
            briefing = self.strategic.decide(
                equity=equity,
                stage=self.stage.stage,
                stage_name=self.stage.stage_name(),
                distance_to_target=self.stage.distance_to_stage_target(),
                progress_pct=self.stage.progress_in_stage(),
                consecutive_losses=self.locks.consecutive_losses(),
                recent_win_rate=stats.get("win_rate"),
                recent_pnl_ratio=None,
                regime=regime,
            )
        else:
            win_rate = stats.get("win_rate")
            avg_w = stats.get("avg_win", 0)
            avg_l = abs(stats.get("avg_loss", 0))
            pnl_ratio = avg_w / avg_l if avg_l > 0 else None
            briefing = self.strategic.decide(
                equity=equity,
                stage=self.stage.stage,
                stage_name=self.stage.stage_name(),
                distance_to_target=self.stage.distance_to_stage_target(),
                progress_pct=self.stage.progress_in_stage(),
                consecutive_losses=self.locks.consecutive_losses(),
                recent_win_rate=win_rate,
                recent_pnl_ratio=pnl_ratio,
                regime=regime,
            )

        # 每日刷新简报输出
        if now.hour == SIGNAL["active_hour_start"] and now.minute < 2:
            self._print_briefing()
            self._generate_daily_briefing()

        if briefing.get("locked"):
            return

        # ── 检查持仓 ──
        positions = self._list_positions()

        if len(positions) == 0:
            # 无持仓 → 扫描信号
            self._scan_and_open()
        else:
            # 有持仓 → 管理出场
            klines_5m = self._fetch_klines_5m(positions[0].get("contract", "BTC_USDT"))
            result = self.executor.manage_positions(
                self._entry_price,
                self._entry_time or datetime.now() - timedelta(hours=1),
                self._entry_direction,
                klines_5m,
            )
            if result:
                equity_after = self._fetch_equity()
                log_trade(
                    symbol=positions[0].get("contract", ""),
                    direction=self._entry_direction,
                    entry_price=self._entry_price,
                    exit_price=self._fetch_mark_price(positions[0].get("contract", "")),
                    margin=self.stage.get_params().get("margin", 0),
                    nominal=self.stage.get_params().get("nominal", 0),
                    equity_before=equity,
                    equity_after=equity_after or equity,
                    pnl=(equity_after or equity) - equity,
                    pnl_pct=((equity_after or equity) - equity) / equity if equity > 0 else 0,
                    exit_reason=result,
                    stage=self.stage.stage,
                    stop_hit=result in ("trailing_exit", "time_exit"),
                    mode=self.strategic.mode(),
                )
                self.locks.on_trade_closed(
                    (equity_after or equity) - equity, equity_after or equity
                )
                self._entry_price = 0.0
                self._entry_direction = ""

        # ── 状态持久 ──
        self._save_state()

    # ═══════════════════════════════════════════
    #  信号扫描 → 开仓
    # ═══════════════════════════════════════════

    def _scan_and_open(self):
        params = self.stage.get_params()
        briefing = self.strategic.briefing()

        for symbol in params["symbols"]:
            klines = self._fetch_klines_15m(symbol)
            if not klines or len(klines) < 25:
                continue

            signal = self.tactical.generate(symbol, klines, briefing)
            if signal is None:
                continue

            log.info(
                f"信号: {signal.symbol} {signal.direction} "
                f"@{signal.entry_price:.2f} 质量:{signal.score}/10"
            )

            result = self.executor.open_position(signal)
            if result:
                self._entry_price = result["entry_price"]
                self._entry_time = datetime.now()
                self._entry_direction = signal.direction

                log.info(
                    f"开仓成功 | {signal.symbol} {signal.direction} "
                    f"保证金:{params['margin']:.2f}U 名义:{params['nominal']:.0f}U "
                    f"止损:{params['hard_stop_pct']:.1%} 模式:{self.strategic.mode()}"
                )
            break  # 单仓位 —— 扫到一个就停

    # ═══════════════════════════════════════════
    #  简报
    # ═══════════════════════════════════════════

    def _print_briefing(self):
        text = self.strategic.briefing_text()
        log.info("\n" + text)
        log.info(f"心态: {self.strategic.mindset_hint()}")

    def _generate_daily_briefing(self):
        """生成每日简报文件"""
        briefing = self.strategic.briefing()
        if not briefing:
            return
        path = os.path.join(
            os.path.dirname(__file__),
            "briefing_history",
            f"briefing_{briefing['date']}.json",
        )
        briefing["stats"] = recent_stats(10)
        briefing["stage_summary"] = self.stage.summary()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(briefing, f, indent=2, ensure_ascii=False)
        log.info(f"每日简报已保存: {path}")

    # ═══════════════════════════════════════════
    #  阶段切换
    # ═══════════════════════════════════════════

    def _on_stage_transition(self, direction: str):
        old_stage = self.stage.stage - 1 if direction == "up" else self.stage.stage + 1
        if direction == "up":
            log.info(f"🚀 阶段{old_stage} → 阶段{self.stage.stage} ({self.stage.stage_name()})")
        elif direction == "down":
            log.info(f"⚠️ 阶段{old_stage} → 阶段{self.stage.stage} ({self.stage.stage_name()})")
        log.info(f"首日半仓保护已激活")

    # ═══════════════════════════════════════════
    #  API 封装 — 公开数据用 gate_data，账户/下单用 client
    # ═══════════════════════════════════════════

    def _fetch_equity(self) -> Optional[float]:
        """优先返回真实 API 净值，失败回退到 state.json"""
        try:
            if self.client is not None:
                account = self.client.futures_list_accounts(settle="usdt")
                return float(account.total)
        except Exception:
            pass
        return self.state.get("equity", 100.0)

    def _fetch_mark_price(self, symbol: str) -> float:
        try:
            from gate_data import fetch_ticker
            ticker = fetch_ticker(symbol)
            if ticker and ticker.get("mark_price", 0) > 0:
                return ticker["mark_price"]
        except Exception:
            pass
        return 0.0

    def _list_positions(self) -> list:
        try:
            if self.client is not None:
                positions = self.client.futures_list_positions(settle="usdt")
                return [p for p in positions if float(p.size) != 0]
        except Exception:
            pass
        return []

    def _fetch_klines_15m(self, symbol: str) -> list:
        from gate_data import fetch_klines
        return fetch_klines(symbol, "15m", limit=30)

    def _fetch_klines_5m(self, symbol: str) -> list:
        from gate_data import fetch_klines
        return fetch_klines(symbol, "5m", limit=30)

    def _fetch_klines_1h(self, symbol: str) -> list:
        from gate_data import fetch_klines
        return fetch_klines(symbol, "1h", limit=30)

    # ═══════════════════════════════════════════
    #  状态持久
    # ═══════════════════════════════════════════

    def _load_or_init(self) -> dict:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
        return {"equity": 100.0, "created": datetime.now().isoformat()}

    def _save_state(self):
        self.state["equity"] = self.stage.equity
        self.state["stage"] = self.stage.stage
        self.state["updated"] = datetime.now().isoformat()
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _on_shutdown(self, signum, frame):
        log.info("收到退出信号...")
        self._shutdown = True


# ═══════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════

def main():
    # TODO: 替换为真实 Gate API client 初始化
    # from gate_api import ApiClient, Configuration, FuturesApi
    # config = Configuration(host="https://api.gateio.ws/api/v4")
    # config.key = os.getenv("GATE_API_KEY")
    # config.secret = os.getenv("GATE_API_SECRET")
    # client = FuturesApi(ApiClient(config))

    client = None  # 占位

    bot = Commander(client)
    bot.run()


if __name__ == "__main__":
    main()
