"""
Hourly P&L Leaderboard → WeChat Work Webhook
Reads all rt_paper_v2_state*.json, aggregates by coin, saves markdown report,
and sends it only when a webhook is configured.
"""
import json, os, glob, urllib.request
from datetime import datetime, timezone, timedelta

# --- config ---
BASE = os.path.dirname(os.path.abspath(__file__))
WEBHOOK_ENV_VAR = "WECHAT_WORK_WEBHOOK_URL"
WEBHOOK_URL = os.environ.get(WEBHOOK_ENV_VAR, "").strip()
BEIJING = timezone(timedelta(hours=8))

# Expected initial equity per coin (from deploy_config)
CFG_PATH = os.path.join(BASE, "deploy_config.json")
with open(CFG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

EXPECTED = {}
for tier in CFG["tiers"].values():
    for coin in tier["coins"]:
        EXPECTED[coin.upper()] = {
            "strats": tier["strats_per_coin"],
            "eq_per_strat": tier["equity_per_strat"],
            "init_equity": round(tier["strats_per_coin"] * tier["equity_per_strat"], 2),
        }

# --- read all state files ---
def load_all_states():
    coins = {}
    state_files = sorted(glob.glob(os.path.join(BASE, "rt_paper_v2_state*.json")))
    for sf in state_files:
        fn = os.path.basename(sf)
        if "leaderboard" in fn or "bak" in fn:
            continue
        # Extract coin name: rt_paper_v2_state_btc.json → BTC
        coin = "SOL" if fn == "rt_paper_v2_state.json" else fn[len("rt_paper_v2_state_"):-5]
        coin = coin.upper()
        try:
            with open(sf, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        if not data:
            continue

        total_eq = 0.0
        total_pnl = 0.0
        trades = 0
        wins = 0
        has_position = False
        for track in data.values():
            eq = float(track.get("equity", 0))
            total_eq += eq
            # trades can be list (from _save_state) or int (from to_dict)
            t = track.get("trades", 0)
            if isinstance(t, list):
                n_trades = len(t)
                # compute wins from actual trade P&L data
                for td in t:
                    pnl = float(td.get("pnl", 0))
                    if pnl > 0:
                        wins += 1
            else:
                n_trades = int(t)
            trades += n_trades
            if track.get("position"):
                has_position = True

        exp = EXPECTED.get(coin, {"init_equity": round(total_eq, 2), "strats": 0})
        init = exp["init_equity"]
        pnl = total_eq - init
        pnl_pct = (pnl / init * 100) if init > 0 else 0

        coins[coin] = {
            "equity": round(total_eq, 2),
            "init": init,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "trades": trades,
            "wins": wins,
            "win_rate": round(wins / trades * 100, 1) if trades > 0 else 0,
            "strats": exp["strats"],
            "has_position": has_position,
        }

    return coins


def format_leaderboard(coins: dict) -> str:
    now = datetime.now(BEIJING)
    ts = now.strftime("%m-%d %H:%M")

    total_eq = sum(c["equity"] for c in coins.values())
    total_init = sum(c["init"] for c in coins.values())
    total_pnl = total_eq - total_init
    total_pnl_pct = (total_pnl / total_init * 100) if total_init > 0 else 0
    total_trades = sum(c["trades"] for c in coins.values())
    online = len(coins)
    total_coins = len(EXPECTED)
    total_strats = sum(c["strats"] for c in coins.values())

    # Sort by P&L descending
    ranked = sorted(coins.items(), key=lambda x: x[1]["pnl"], reverse=True)

    lines = []
    lines.append(f"# Gate.io 15m 策略排行榜")
    lines.append(f"> 更新时间: {ts} CST")
    lines.append(f"> 在线: **{online}/{total_coins}** 币种 | **{total_strats}** 策略 | **{total_trades}** 笔交易")
    lines.append(f"> 总净值: **{total_eq:.2f}U** | 总收益: **{total_pnl:+.2f}U ({total_pnl_pct:+.1f}%)**")
    lines.append("")

    # Top gainers
    top_gainers = [c for c in ranked if c[1]["pnl"] > 0][:5]
    if top_gainers:
        lines.append("### 收益 TOP")
        for i, (coin, c) in enumerate(top_gainers, 1):
            pos = "📈" if c["has_position"] else "  "
            lines.append(f"`{i:>2}` {pos} **{coin}** {c['pnl_pct']:+.1f}% "
                         f"({c['pnl']:+.2f}U) | 交易{c['trades']}笔 胜率{c['win_rate']}%")

    # Top losers
    top_losers = [c for c in ranked if c[1]["pnl"] < 0][:5]
    if top_losers:
        lines.append("")
        lines.append("### 回撤")
        for i, (coin, c) in enumerate(top_losers, 1):
            pos = "📉" if c["has_position"] else "  "
            lines.append(f"`{i:>2}` {pos} **{coin}** {c['pnl_pct']:+.1f}% "
                         f"({c['pnl']:+.2f}U) | 交易{c['trades']}笔")

    # Flat / no trades
    flat = [c for c in ranked if c[1]["pnl"] == 0]
    if flat and len(flat) <= 5:
        lines.append("")
        lines.append("### 观望 (无交易)")
        for coin, c in flat[:5]:
            lines.append(f"  · **{coin}** | {c['strats']}策略")

    # Summary table (compact text)
    lines.append("")
    lines.append("---")
    lines.append("### 全币种明细")
    for coin, c in ranked:
        pos_mark = "·" if not c["has_position"] else "●"
        lines.append(f"{pos_mark} **{coin:<6}** {c['init']:>5.2f}→{c['equity']:>6.2f} "
                     f"{c['pnl_pct']:>+6.1f}% | 交易{c['trades']:>2}")

    lines.append("")
    lines.append(f"> 每1小时自动推送 | {ts}")

    return "\n".join(lines)


def send_webhook(content: str):
    if not WEBHOOK_URL:
        print(f"  跳过推送: 未设置环境变量 {WEBHOOK_ENV_VAR}")
        return None

    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        }
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
    return result


if __name__ == "__main__":
    print(f"[{datetime.now(BEIJING).strftime('%H:%M:%S')}] 生成排行榜...")
    coins = load_all_states()
    if not coins:
        print("  无状态文件，跳过")
        exit(0)

    md = format_leaderboard(coins)

    # Save local copy
    report_path = os.path.join(BASE, "reports", "latest_hourly.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)

    try:
        r = send_webhook(md)
        if r is not None:
            print(f"  推送成功: {r}")
    except Exception as e:
        print(f"  推送失败: {e}")
        # Also save to log for diagnosis
        with open(os.path.join(BASE, "logs", "webhook_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M:%S')}] FAIL: {e}\n")

    print(f"  报告已保存: {report_path}")
