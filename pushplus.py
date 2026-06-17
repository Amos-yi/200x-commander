"""
PushPlus 推送模块 — 交易通知 + 日报
"""

import json
import logging
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError

log = logging.getLogger("pushplus")

PUSHPLUS_URL = "https://www.pushplus.plus/send"
TIMEOUT = 10


class PushPlus:
    """单例推送器"""

    def __init__(self, token: str = ""):
        self.token = token or ""
        self.sent_messages = set()  # 简单去重
        self.enabled = bool(token)

    def send(self, title: str, content: str, template: str = "markdown") -> bool:
        """
        发送 PushPlus 消息。
        返回 True/False。
        """
        if not self.enabled:
            log.info(f"[PushPlus] 未启用，跳过: {title}")
            return False

        # 去重（30 分钟内相同标题）
        dedupe_key = f"{title}:{datetime.now().strftime('%Y%m%d_%H%M')}"
        if dedupe_key in self.sent_messages:
            return True
        self.sent_messages.add(dedupe_key)

        payload = json.dumps({
            "token": self.token,
            "title": title,
            "content": content,
            "template": template,
        }).encode()

        try:
            req = Request(
                PUSHPLUS_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode())
                if body.get("code") == 200:
                    log.info(f"[PushPlus] 发送成功: {title}")
                    return True
                else:
                    log.warning(f"[PushPlus] 发送失败: {body}")
                    return False
        except URLError as e:
            log.error(f"[PushPlus] 网络错误: {e}")
            return False
        except Exception as e:
            log.error(f"[PushPlus] 未知错误: {e}")
            return False

    # ── 预置模板 ──

    def notify_open(self, symbol: str, direction: str, entry_price: float,
                    margin: float, nominal: float, stop: float,
                    mode: str, score: int, stage_name: str) -> bool:
        """开仓通知"""
        dir_cn = "做多" if direction == "long" else "做空"
        title = f"200x Commander 开仓 | {symbol} {dir_cn}"
        content = (
            f"# ✅ 开仓通知\n\n"
            f"| 项目 | 值 |\n|------|----|\n"
            f"| 币种 | **{symbol}** |\n"
            f"| 方向 | {dir_cn} |\n"
            f"| 入场价 | {entry_price:.2f} |\n"
            f"| 保证金 | {margin:.2f} U |\n"
            f"| 名义仓位 | {nominal:.0f} U |\n"
            f"| 止损价 | {stop:.2f} |\n"
            f"| 信号质量 | {score}/10 |\n"
            f"| 作战模式 | {mode} |\n"
            f"| 阶段 | {stage_name} |\n"
        )
        return self.send(title, content)

    def notify_close(self, symbol: str, direction: str, pnl: float,
                     pnl_pct: float, exit_reason: str, equity: float) -> bool:
        """平仓通知"""
        emoji = "✅" if pnl > 0 else "❌"
        dir_cn = "做多" if direction == "long" else "做空"
        title = f"200x Commander 平仓 {emoji} | {symbol} {dir_cn} | {pnl:+.2f}U"
        content = (
            f"# {emoji} 平仓通知\n\n"
            f"| 项目 | 值 |\n|------|----|\n"
            f"| 币种 | **{symbol}** |\n"
            f"| 方向 | {dir_cn} |\n"
            f"| 盈亏 | **{pnl:+.2f} U** ({pnl_pct:+.1%}) |\n"
            f"| 出场原因 | {exit_reason} |\n"
            f"| 当前净值 | {equity:.2f} U |\n"
        )
        return self.send(title, content)

    def daily_report(self, text: str) -> bool:
        """日报推送"""
        title = f"200x Commander 日报 | {datetime.now().strftime('%Y-%m-%d')}"
        return self.send(title, text)

    def alert(self, level: str, msg: str) -> bool:
        """告警推送"""
        emoji = {"info": "ℹ️", "warn": "⚠️", "error": "🔴"}.get(level, "ℹ️")
        title = f"{emoji} 200x Commander 告警 | {level.upper()}"
        return self.send(title, msg)
