"""
复盘模块：每日 / 每周 / 每月
机器自动整理，人做决策
"""

import json
import os
from datetime import datetime, timedelta
from trade_logger import load_history, recent_stats

REVIEW_DIR = os.path.join(os.path.dirname(__file__), "reviews")


def ensure_review_dir():
    os.makedirs(REVIEW_DIR, exist_ok=True)


# ═══════════════════════════════════════════
#  每日复盘（Bot 自动生成，不需要人看）
# ═══════════════════════════════════════════

def daily_review(date_str: str = None) -> str:
    """生成每日简短复盘。返回文本。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    history = load_history()
    today_trades = [r for r in history if r["time"].startswith(date_str)]

    lines = [f"# 每日复盘 {date_str}", ""]

    if not today_trades:
        lines.append("今日无交易。")
        return "\n".join(lines)

    lines.append(f"交易笔数: {len(today_trades)}")
    for i, t in enumerate(today_trades, 1):
        emoji = "✅" if t["pnl"] > 0 else "❌"
        lines.append(
            f"  {i}. {emoji} {t['symbol']} {t['direction']} "
            f"入场:{t['entry']} 出场:{t['exit']} "
            f"盈亏:{t['pnl']:.2f}U ({t['pnl_pct']:.2%}) "
            f"原因:{t['exit_reason']} "
            f"质量:{t.get('score','?')}/10"
        )

    total_pnl = sum(t["pnl"] for t in today_trades)
    lines.append("")
    lines.append(f"今日总盈亏: {total_pnl:+.2f}U")
    lines.append(f"胜率: {sum(1 for t in today_trades if t['pnl']>0)}/{len(today_trades)}")

    # 检查低分强开
    low_score = [t for t in today_trades if t.get("score", 0) < 5]
    if low_score:
        lines.append(f"⚠️ 低分强开: {len(low_score)} 笔")

    return "\n".join(lines)


# ═══════════════════════════════════════════
#  每周复盘（Bot 自动生成，人应阅读）
# ═══════════════════════════════════════════

def weekly_review(end_date: str = None) -> str:
    """
    生成周度复盘。
    end_date: 周的结束日期，默认今天。
    """
    ensure_review_dir()
    if end_date is None:
        end = datetime.now()
    else:
        end = datetime.fromisoformat(end_date)
    start = end - timedelta(days=7)

    history = load_history()
    week_trades = [
        r for r in history
        if start <= datetime.fromisoformat(r["time"]) <= end
    ]

    lines = [
        f"# 周度复盘 {start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}",
        "",
    ]

    if not week_trades:
        lines.append("本周无交易。")
        return "\n".join(lines)

    wins = [t for t in week_trades if t["pnl"] > 0]
    losses = [t for t in week_trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in week_trades)

    equity_start = week_trades[0]["equity_before"]
    equity_end = week_trades[-1]["equity_after"]

    lines.append("## 核心指标")
    lines.append(f"- 周初净值: {equity_start:.2f}U")
    lines.append(f"- 周末净值: {equity_end:.2f}U")
    lines.append(f"- 周收益: {total_pnl:+.2f}U ({(equity_end/equity_start-1)*100:+.1f}%)")
    lines.append(f"- 交易笔数: {len(week_trades)}")
    lines.append(f"- 胜率: {len(wins)}/{len(week_trades)} ({len(wins)/len(week_trades)*100:.0f}%)")
    lines.append(f"- 平均盈: {sum(t['pnl'] for t in wins)/len(wins):.2f}U" if wins else "- 平均盈: N/A")
    lines.append(f"- 平均亏: {sum(t['pnl'] for t in losses)/len(losses):.2f}U" if losses else "- 平均亏: N/A")
    lines.append("")

    # 模式分布
    lines.append("## 模式分布")
    modes = {}
    for t in week_trades:
        m = t.get("mode", "unknown")
        modes[m] = modes.get(m, 0) + 1
    for mode, count in modes.items():
        lines.append(f"- {mode}: {count} 笔")
    lines.append("")

    # 低分交易
    low_score = [t for t in week_trades if t.get("score", 0) < 5]
    if low_score:
        lines.append("## 低分交易预警")
        lines.append(f"共 {len(low_score)} 笔质量分 < 5 的交易:")
        for t in low_score:
            lines.append(f"- {t['symbol']} 质量:{t['score']}, 盈亏:{t['pnl']:.2f}U")
        lines.append("")

    # 止损命中率
    stop_hits = [t for t in week_trades if t.get("stop_hit")]
    if stop_hits:
        lines.append(f"## 止损命中: {len(stop_hits)}/{len(week_trades)} 笔")

    # 结论
    lines.append("## 结论")
    if total_pnl > 0 and len(wins) > len(losses):
        lines.append("✅ 本周正向，按当前节奏继续。")
    elif total_pnl > 0:
        lines.append("⚠️ 本周盈利但胜率不足，检查是否有扛单倾向。")
    elif total_pnl < 0 and equity_end < equity_start * 0.85:
        lines.append("🔴 本周亏损超 15%，建议下周降仓、严格信号门槛。")
    else:
        lines.append("🟡 本周略亏，正常波动范围内，观察下周。")

    # 写入文件
    review_text = "\n".join(lines)
    path = os.path.join(REVIEW_DIR, f"weekly_{end.strftime('%Y%m%d')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(review_text)

    return review_text


# ═══════════════════════════════════════════
#  月度深度复盘（Bot 准备所有数据，人必须来读并做决策）
# ═══════════════════════════════════════════

def monthly_review(end_date: str = None) -> str:
    """
    月度深度复盘。
    这是整个系统最重要的输出——人必须亲自读并做出决策。
    """
    ensure_review_dir()
    if end_date is None:
        end = datetime.now()
    else:
        end = datetime.fromisoformat(end_date)
    start = end - timedelta(days=30)

    history = load_history()
    month_trades = [
        r for r in history
        if start <= datetime.fromisoformat(r["time"]) <= end
    ]

    lines = [
        "# 月度深度复盘",
        f"周期: {start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}",
        "",
        "> ⚠️ 此报告必须由你亲自阅读，并做出月度决策。",
        "",
    ]

    if not month_trades:
        lines.append("本月无交易记录。")
        lines.append("")
        lines.append("## 决策：检查为什么没有交易。是信号太少？还是门槛太高？")
        return "\n".join(lines)

    wins = [t for t in month_trades if t["pnl"] > 0]
    losses = [t for t in month_trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in month_trades)
    equity_start = month_trades[0]["equity_before"]
    equity_end = month_trades[-1]["equity_after"]
    monthly_return = (equity_end / equity_start - 1)

    lines.append("## 1. 核心数据")
    lines.append(f"|- 月初净值：{equity_start:.2f}U")
    lines.append(f"|- 月末净值：{equity_end:.2f}U")
    lines.append(f"|- 月收益：{total_pnl:+.2f}U ({monthly_return*100:+.1f}%)")
    lines.append(f"|- 交易笔数：{len(month_trades)}")
    lines.append(f"|- 胜率：{len(wins)}/{len(month_trades)} ({len(wins)/len(month_trades)*100:.0f}%)")
    lines.append(f"|- 平均盈：{sum(t['pnl'] for t in wins)/len(wins):.2f}U" if wins else "|- 平均盈：N/A")
    lines.append(f"|- 平均亏：{sum(t['pnl'] for t in losses)/len(losses):.2f}U" if losses else "|- 平均亏：N/A")

    # 计算最大连续亏损
    max_consec = 0
    curr_consec = 0
    for t in month_trades:
        if t["pnl"] <= 0:
            curr_consec += 1
            max_consec = max(max_consec, curr_consec)
        else:
            curr_consec = 0
    lines.append(f"|- 最大连续亏损：{max_consec} 笔")
    lines.append("")

    # 阶段变化
    lines.append("## 2. 阶段进度")
    stages_seen = sorted(set(t["stage"] for t in month_trades))
    lines.append(f"本月跨越阶段: {stages_seen}")
    from config import STAGES
    current_stage = month_trades[-1]["stage"]
    target = STAGES[current_stage]["target"]
    lines.append(f"当前阶段目标: {target}U | 距目标: {target - equity_end:.0f}U")
    lines.append("")

    # 退出原因分布
    lines.append("## 3. 退出原因分布")
    exit_reasons = {}
    for t in month_trades:
        r = t.get("exit_reason", "unknown")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    for reason, count in exit_reasons.items():
        lines.append(f"- {reason}: {count} 笔")
    lines.append("")

    # 质量分析
    lines.append("## 4. 信号质量分析")
    avg_score = sum(t.get("score", 0) for t in month_trades) / len(month_trades)
    low_quality = [t for t in month_trades if t.get("score", 0) < 5]
    lines.append(f"平均质量分: {avg_score:.1f}/10")
    lines.append(f"低分交易 ({len(low_quality)} 笔):")
    for t in low_quality:
        lines.append(f"  - {t['symbol']} 质量:{t['score']} 盈亏:{t['pnl']:.2f}U")
    lines.append("")

    # 月度决策问题 —— 人必须回答
    lines.append("## 5. 月度决策（你必须回答）")
    lines.append("")
    lines.append("### Q1: 策略是否成立？")
    lines.append(f"月化收益 {monthly_return*100:+.1f}%，胜率 {len(wins)/len(month_trades)*100:.0f}%。")
    lines.append("基于这两个数字，你认为当前策略逻辑是否成立？ [ ] 是 [ ] 否 [ ] 需要更多数据")
    lines.append("")
    lines.append("### Q2: 参数是否要调？")
    lines.append("是否有参数明显不合适？（比如止损太紧频繁止损 / 止损太松单笔亏损太大 / 信号门槛太高错过好机会）")
    lines.append("[ ] 不调 [ ] 调（请在下文说明调什么）")
    lines.append("")
    lines.append("### Q3: 下一阶段预期")
    lines.append("按当前月化，下一个阶段切换预计需要多久？这个时间你能接受吗？")
    lines.append("[ ] 能接受 [ ] 需要提速（考虑的策略调整：______）")
    lines.append("")
    lines.append("### Q4: 违规检查")
    lines.append("本月是否出现过以下情况：")
    lines.append("[ ] 手动撤止损 [ ] 扛单不止损 [ ] 超今日额度开仓 [ ] 情绪化报复开仓")
    lines.append("")

    # 结论
    lines.append("## 6. 月度决策")
    lines.append("")
    lines.append("请在以下选项中选择一项：")
    lines.append("- [ ] **继续** — 策略成立，按现有规则继续下一个月")
    lines.append("- [ ] **调整** — 策略方向对，但参数需要微调（请记录调了什么）")
    lines.append("- [ ] **暂停** — 策略问题或纪律问题，暂停实盘 30 天复盘")

    review_text = "\n".join(lines)
    path = os.path.join(REVIEW_DIR, f"monthly_{end.strftime('%Y%m')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(review_text)

    return review_text
