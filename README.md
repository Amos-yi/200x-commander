# 200x Commander — 多币种纸盘交易系统

141 策略 × 25 币种的实时纸盘交易引擎，含自动调度器、仪表盘和单币种报告。

## 安装

```bash
pip install -r requirements.txt
```

依赖：`pandas`、`websocket-client`、`gate-api`。

## Webhook 配置

企业微信 webhook 不再写在代码里。需要推送时，在启动进程前设置环境变量：

```powershell
$env:WECHAT_WORK_WEBHOOK_URL="<your WeChat Work bot webhook URL>"
```

未设置 `WECHAT_WORK_WEBHOOK_URL` 时，`rt_paper_v2.py` 的交易通知和 `hourly_report.py` 的小时推送会安全跳过并打印原因；`hourly_report.py` 仍会生成本地 `reports/latest_hourly.md`。

## 快速启动

```bash
# 一键启动（自动扫描25币种，进场/离场全自动）
双击 START.bat

# 一键关停
双击 STOP.bat
```

## 工具脚本

| 命令 | 用途 |
|---|---|
| `START.bat` | 启动调度器，5分钟扫描，自动进出场 |
| `STOP.bat` | 关停所有python进程 |
| `python _dash.py` | 全币种仪表盘（净值/交易/策略排名） |
| `python _report.py ETH_USDT` | 单币种交易明细报告 |
| `python _scan_market.py` | 一次性行情扫描（不启动交易） |

## 架构

```
START.bat
  └─ _auto_deploy.py      ← 调度守护：每5分钟扫描，≥60分进场/<40分离场
       ├─ run_rt_paper_btc.bat   ← 崩溃自重启包装
       ├─ run_rt_paper_eth.bat
       ├─ ...（25个币种各一个）
       └─ run_rt_paper_rndr.bat
            └─ rt_paper_v2.py    ← 141策略实时纸盘引擎（WebSocket驱动）
```

## 状态文件

每个币种一个状态文件：`rt_paper_v2_state_{coin}.json`

- 包含 141 个策略的净值、持仓、交易历史
- 每 5 分钟自动存盘
- 异常退出可通过 `.bak` 文件恢复

## 日志

`logs/rt_{coin}.log` — 每个币种独立日志文件。

常见日志事件：
- `Starting XXX_USDT` — 交易进程启动
- `Process exited` — 进程意外退出，10秒后自动重启
- `ping/pong timed out` — WebSocket断连，自动重连

## 仪表盘示例

```
  币种     净值     交易   Top策略
  UNI   24,959U    467   HAMMER +406%
  SOL   18,015U    651   HAMMER +406%
  INJ   16,442U    423   VIDYA +238%
  ...
  总计  338,226U  7,859   22币种
```

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `START.bat` 闪退 | Gate.io API 不可达 | 检查网络，重试 |
| 某币种无状态文件 | 该币种从未成功启动 | 检查 `logs/rt_{coin}.log` |
| WebSocket 反复断连 | Gate.io 限流 | 正常现象，batch 会自动重启 |
| 仪表盘显示 BROKEN | 状态文件损坏 | 删除该 `.json` 文件，重启该币种 |
| CPU/内存过高 | 25个进程同时运行 | 减少币种数量（修改 `_auto_deploy.py` 中 `COINS` 列表） |

## 注意事项

- 本系统为 **纸盘模拟**，不涉及真实资金
- `_macro_calendar.py` 含 2026 年宏观经济事件日历，过期后需手动更新
- 日志文件请定期清理（`del logs\*.log`）
