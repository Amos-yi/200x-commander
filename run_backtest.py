"""
回测运行脚本
用法: python run_backtest.py [symbol] [days]
"""

import sys
import os
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gate_data import fetch_klines
from backtest import BacktestEngine


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC_USDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    
    print(f"[200x Commander] 回测")
    print(f"币种: {symbol} | 周期: {days} 天")
    print(f"下载 K 线数据...")
    
    # 15m K线, 每天 96 根, 按 limit=1000 一批拉
    needed = days * 96
    all_klines = []
    batch_limit = 500
    batches = (needed // batch_limit) + 3
    
    for batch_num in range(batches):
        klines = fetch_klines(symbol, "15m", limit=batch_limit)
        if not klines:
            break
        all_klines.extend(klines)
        # 去重后用最早时间判断是否已覆盖所需天数
        seen = set()
        unique_all = []
        for k in all_klines:
            if k["time"] not in seen:
                seen.add(k["time"])
                unique_all.append(k)
        if unique_all:
            earliest = min(k["time"] for k in unique_all)
            if earliest < (datetime.now() - timedelta(days=days)).timestamp():
                break
    
    if not all_klines:
        print("ERROR: 未能获取 K 线数据")
        return
    
    # 按时间排序去重
    seen = set()
    unique = []
    for k in all_klines:
        if k["time"] not in seen:
            seen.add(k["time"])
            unique.append(k)
    unique.sort(key=lambda x: x["time"])
    
    # 截取指定天数
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    unique = [k for k in unique if k["time"] >= cutoff]
    
    print(f"已获取 {len(unique)} 根 K 线")
    if len(unique) < 2:
        print("ERROR: K 线数据不足")
        return
    print(f"时间范围: {datetime.fromtimestamp(unique[0]['time'])} -> {datetime.fromtimestamp(unique[-1]['time'])}")
    print(f"运行回测...")
    
    engine = BacktestEngine(unique, initial_equity=100.0, symbol=symbol)
    report = engine.run()
    
    print()
    print("=" * 60)
    print("                    回 测 结 果")
    print("=" * 60)
    
    if "error" in report:
        print(f"  {report['error']}")
        return
    
    print(f"  初始净值:      100.00 U")
    print(f"  最终净值:      {report['final_equity']} U")
    print(f"  总收益率:      {report['total_return_pct']:+.2f}%")
    print(f"  " + "-" * 42)
    print(f"  总交易笔数:    {report['total_trades']}")
    print(f"  胜率:          {report['win_rate']*100:.1f}% ({report['wins']}/{report['total_trades']})")
    print(f"  平均盈利:      {report['avg_win']:+.2f} U")
    print(f"  平均亏损:      {report['avg_loss']:+.2f} U")
    if report.get('avg_loss', 0) > 0:
        print(f"  盈亏比:        {report['avg_win']/report['avg_loss']:.2f}")
    print(f"  " + "-" * 42)
    print(f"  夏普比率:      {report['sharpe_ratio']}")
    print(f"  利润因子:      {report['profit_factor']}")
    print(f"  最大回撤:      {report['max_drawdown_pct']}%")
    print(f"  " + "-" * 42)
    print(f"  最佳交易:      {report['best_trade']:+.2f} U")
    print(f"  最差交易:      {report['worst_trade']:+.2f} U")
    print(f"  平均信号质量:  {report['avg_score']}/10")
    print(f"  " + "-" * 42)
    print(f"  退出原因分布:")
    for reason, count in report.get("exit_reasons", {}).items():
        bar = "#" * count
        print(f"    {reason:20s} {count:2d} {bar}")
    print("=" * 60)
    
    # 保存报告
    report_path = os.path.join(
        os.path.dirname(__file__), "backtest_results",
        f"backtest_{symbol}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    )
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {report_path}")
    
    # 判定
    print()
    if report.get("profit_factor", 0) > 1.2 and report.get("win_rate", 0) > 0.35:
        print("[PASS] 策略具有正期望，可以进入 Paper 验证。")
    elif report.get("profit_factor", 0) > 1.0:
        print("[WARN] 策略收益边缘，建议优化参数后再看。")
    else:
        print("[FAIL] 策略亏损，不建议继续。需要重新审视信号逻辑。")


if __name__ == "__main__":
    main()
