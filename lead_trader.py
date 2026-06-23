"""
200x Commander — 带单交易员模块
===============================
300U 自动开通 Gate.io copy-trading leader 权限。
每 tick 检测账户权益，达标后自动注册 + 持久化 + PushPlus 通知。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from gate_api.exceptions import GateApiException

log = logging.getLogger("commander.lead")

GATE_COPYTRADING_BASE = "/api/v4/copytrading"


class LeadTrader:
    """带单交易员管理器。

    职责：
    1. 每 tick 查 Gate.io 合约账户权益
    2. 权益 >= auto_enable_equity_threshold 时自动注册带单
    3. 持久化 enabled 状态到 config.py
    4. PushPlus 通知
    """

    def __init__(
        self,
        gate_client,
        copy_trading_config: dict,
        settle: str = "usdt",
    ):
        self._client = gate_client
        self._configured = gate_client is not None
        self._settle = settle

        self.enabled = bool(copy_trading_config.get("enabled", False))
        self.auto_enable_equity_threshold = float(
            copy_trading_config.get("auto_enable_equity_threshold", 300.0)
        )
        self.profit_share_ratio = float(
            copy_trading_config.get("profit_share_ratio", 0.10)
        )
        self.min_copy_amount = float(
            copy_trading_config.get("min_copy_amount", 10.0)
        )
        self.max_copy_amount = float(
            copy_trading_config.get("max_copy_amount", 10000.0)
        )
        self.max_daily_trades = int(
            copy_trading_config.get("max_daily_trades", 3)
        )
        self.avoid_high_freq = bool(
            copy_trading_config.get("avoid_high_freq", True)
        )
        self.min_interval_seconds = int(
            copy_trading_config.get("min_interval_seconds", 300)
        )

        self.auto_already_fired = False
        self._cached_equity: float = 0.0
        self._cached_equity_ts: float = 0.0
        self._last_trade_ts: float = 0.0
        self._daily_trade_count = 0
        self._trade_day = self._today_key()
        self._notifier = None  # type: ignore

        log.info(
            "带单模块初始化 | enabled=%s | API=%s | 阈值=%.0f USDT | "
            "分润=%.0f%% | 跟单=%.0f~%.0fU | 日上限=%d | 防高频=%s",
            "是" if self.enabled else "否",
            "是" if self._configured else "否",
            self.auto_enable_equity_threshold,
            self.profit_share_ratio * 100,
            self.min_copy_amount,
            self.max_copy_amount,
            self.max_daily_trades,
            "是" if self.avoid_high_freq else "否",
        )

    # ── 辅助 ────────────────────────────────────────

    @staticmethod
    def _today_key() -> str:
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    def _reset_daily_if_needed(self) -> None:
        today = self._today_key()
        if today != self._trade_day:
            self._trade_day = today
            self._daily_trade_count = 0
            if not self.enabled:
                self.auto_already_fired = False

    # ── 通知注入 ─────────────────────────────────────

    def set_notifier(self, notifier) -> None:
        """注入 PushPlus 通知器。"""
        self._notifier = notifier

    # ── 权益查询 ────────────────────────────────────

    def get_account_equity(self, force: bool = False) -> float:
        """查询 Gate.io 合约账户总权益。默认缓存 60 秒。"""
        if not self._configured:
            return 0.0

        now = time.time()
        if (not force) and self._cached_equity and now - self._cached_equity_ts < 60:
            return self._cached_equity

        try:
            account = self._client.list_futures_accounts(settle=self._settle)
            equity = float(account.total)
            self._cached_equity = equity
            self._cached_equity_ts = now
            return equity
        except Exception as e:
            log.warning("查询账户权益失败: %s", e)
            return self._cached_equity

    # ── API 注册 ────────────────────────────────────

    def _try_register_leader(self) -> Tuple[bool, str]:
        """尝试通过 Gate.io API 注册为带单员。

        返回 (success, message)。
        """
        if not self._configured:
            return False, "API Key 未配置"

        try:
            body = {
                "profit_share_ratio": str(self.profit_share_ratio),
                "min_copy_amount": str(self.min_copy_amount),
                "max_copy_amount": str(self.max_copy_amount),
            }
            # 使用 gate_api 底层的 call_api 调用 copytrading 端点
            api_client = self._client.api_client
            api_client.call_api(
                "POST",
                f"{GATE_COPYTRADING_BASE}/leader/apply",
                body=body,
                header_params={"Content-Type": "application/json"},
                response_type="dict",
            )
            return True, "API 注册请求已提交"
        except GateApiException as e:
            if e.label in (
                "ALREADY_LEADER",
                "LEADER_ALREADY_EXISTS",
                "ALREADY_APPLIED",
            ):
                return True, "已是带单员或已申请"
            return False, f"GateAPI[{e.label}]: {e.message}"
        except Exception as e:
            msg = str(e)
            if "404" in msg or "Not Found" in msg:
                return False, "API 端点不可用（需 Gate.io 网页手动开通）"
            return False, f"注册异常: {e}"

    # ── 持久化 ──────────────────────────────────────

    def _persist_enabled(self) -> bool:
        """将 enabled=True 写回 deploy_config.json。"""
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "deploy_config.json",
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.setdefault("copy_trading", {})["enabled"] = True
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            log.info("deploy_config.json 已更新: copy_trading.enabled=true")
            return True
        except Exception as e:
            log.error("写 deploy_config.json 失败: %s", e)
            return False

    # ── 主检测 ──────────────────────────────────────

    def check_and_auto_enable(self) -> Optional[str]:
        """每 tick 调用 — 检测权益是否达标并自动开启带单。

        返回 None 表示无变化；返回 str 表示触发了动作的描述。
        """
        self._reset_daily_if_needed()

        if self.enabled:
            return None
        if self.auto_already_fired:
            return None
        if not self._configured:
            return None

        equity = self.get_account_equity()
        if equity < self.auto_enable_equity_threshold:
            return None

        log.info(
            "权益 %.2f USDT >= 阈值 %.0f USDT — 尝试自动开通带单",
            equity,
            self.auto_enable_equity_threshold,
        )

        success, msg = self._try_register_leader()
        self.enabled = True
        self.auto_already_fired = True

        self._persist_enabled()

        # PushPlus 通知
        title = f"🎯 带单已自动开通（权益: {equity:.0f}U）"
        content = (
            f"200x Commander 检测到账户权益达到 {equity:.2f} USDT，"
            f"已自动开启带单模式。\n\n"
            f"API 注册: {'成功' if success else '需手动审核'} ({msg})"
        )
        if self._notifier:
            try:
                self._notifier(title, content)
            except Exception as e:
                log.warning("发送通知失败: %s", e)

        return f"权益{equity:.0f}U达标 → 带单{'已开通' if success else '待审核'}: {msg}"


def load_copy_trading_config() -> dict:
    """从 config.py COPY_TRADING 和 deploy_config.json 合并加载配置。"""
    cfg = {}
    # 1. config.py 的默认值
    try:
        from config import COPY_TRADING
        cfg.update(COPY_TRADING)
    except ImportError:
        pass

    # 2. deploy_config.json 覆盖
    deploy_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "deploy_config.json",
    )
    try:
        with open(deploy_path, "r", encoding="utf-8") as f:
            deploy = json.load(f)
        ct = deploy.get("copy_trading", {})
        if ct:
            cfg.update(ct)
    except Exception:
        pass

    return cfg
