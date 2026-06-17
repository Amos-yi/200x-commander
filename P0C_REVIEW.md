======================================================================
  P0-C 复验证据报告
======================================================================
Timestamp: 2026-06-17
Reviewer: deep-copilot (evidence-only, no code changes)
======================================================================

## 一、Git 状态

HEAD:         889626a
Branch:       master
Dirty:        CLEAN (0 unstaged, 0 untracked excluding _p0c* scratch)
5 commits:    7e4e17c → 14e0e23 → 4d81ddc → 8d9a546 → 889626a

GIT_STATUS_CLEAN=YES

======================================================================

## 二、BTC / MATIC / RNDR 专项证据

BTC_BAT_EXISTS=YES        (run_rt_paper_btc.bat, 460 bytes)
BTC_STATUS_EXISTS=NO      (rt_paper_v2_state_btc.json does not exist on disk)
BTC_DASHBOARD_STATE=NOT_STARTED

MATIC_BAT_EXISTS=YES      (run_rt_paper_matic.bat, 472 bytes)
MATIC_STATUS_EXISTS=NO    (rt_paper_v2_state_matic.json does not exist on disk)
MATIC_DASHBOARD_STATE=NOT_STARTED

RNDR_BAT_EXISTS=YES       (run_rt_paper_rndr.bat, 466 bytes)
RNDR_STATUS_EXISTS=NO     (rt_paper_v2_state_rndr.json does not exist on disk)
RNDR_DASHBOARD_STATE=NOT_STARTED

结论: batch 文件已生成且格式正确。状态文件从未被创建 —
      这意味着 process 从未被 _auto_deploy 触发过
      进场（score >= 60），或者启动后立即崩溃（P0-A
      calendar.py 修复前可能发生）。
      NOT_STARTED 仅代表 dashboard 可见化占位完成，
      不代表 status 生成链路已实证跑通。

BTC_STATUS_CONFIRMED=NO
MATIC_STATUS_CONFIRMED=NO
RNDR_STATUS_CONFIRMED=NO

======================================================================

## 三、裸 except 复验

Scan target: *.py in workspace (excl _p0c* temp files)

Findings:
  _read_eth.py:8: except:
    → 临时调试脚本，已被 .gitignore 忽略，非运行时代码

Project code (rt_paper_v2, _auto_deploy, _dash, _report, etc.): 0 bare except.

BARE_EXCEPT_CLEAN=YES

======================================================================

## 四、硬编码路径复验

Scan target: *.py + *.bat in workspace (excl _p0c* temp)

Findings:
  11 hits, ALL in _single-use debug/scratch scripts
  with underscore prefix:
    _check_reasons.py, _check_trade.py, _fix_bats.py,
    _full_report.py, _full_report_eth.py, _full_report_rune.py,
    _read_eth.py, _read_lb.py, _read_rune.py, _read_state.py,
    _top20.py, _run_test.bat

  0 hits in runtime code:
    rt_paper_v2.py → Path(__file__).resolve() or env vars
    _auto_deploy.py    → Path(__file__).resolve().parent
    _dash.py           → Path(__file__).resolve().parent
    _report.py         → Path(__file__).resolve().parent
    25 run_rt_paper_*.bat → %~dp0

HARDCODED_PATH_CLEAN=YES (runtime code only)

======================================================================

## 五、真实 API / 交易风险复验

Scan keywords: api_key, secret, create_order, cancel_order,
close_position, set_leverage, place_order, private

Findings:
  execution_layer.py:66    → futures_create_order (REAL ORDER FUNCTION)
  execution_layer.py:241   → futures_create_order (REAL ORDER FUNCTION)
  main.py:345-346          → # commented-out API key references
  multi_paper.py:195       → _close_position (PAPER TRADING internal, NOT exchange)
  multi_paper.py:277       → def _close_position (PAPER TRADING internal)
  rt_paper_v2.py:358       → _close_position (PAPER TRADING internal)
  rt_paper_v2.py:468       → def _close_position (PAPER TRADING internal)

REAL_KEY_FOUND=NO           (commented-out, no live secrets)
ORDER_FUNCTION_FOUND=YES    (execution_layer.py has futures_create_order)
CANCEL_FUNCTION_FOUND=NO
LEVERAGE_FUNCTION_FOUND=NO
PRIVATE_API_CALL_FOUND=NO   (execution_layer.py is imported but caller uses
                              paper trade path rt_paper_v2.py exclusively)
REAL_API_OR_TRADE_TOUCHED=NO (runtime path: rt_paper_v2.py paper only)

======================================================================

## 六、Dashboard 损坏 JSON 可见化

Evidence from _dash.py source:
  L35: except json.JSONDecodeError as e:
  L36:     logging.warning("Corrupted JSON in %s: %s", ...)
  L37:     return None, "CORRUPTED"
  L39:     return None, "NOT_STARTED"
  L41:     logging.warning("Failed to load %s: %s", ...)

Display logic (line ~50-52):
  status_icon = {"NOT_STARTED": "...", "CORRUPTED": "... BROKEN"}.get(err, ...)
  print(f"  {status_icon} {sym:<14}  -- {err} --")

结论: 损坏 JSON 不会被静默跳过。
      CORRUPTED 标记会在终端输出中显示，且通过 logging.warning 记录。

DASHBOARD_CORRUPTED_VISIBLE=YES

======================================================================

## 七、最终结论

P0C_REVIEW_DONE=YES
GIT_STATUS_CLEAN=YES
CRITICAL_RECHECK_PASS=YES
HIGH_RECHECK_PASS=YES
MEDIUM_13_14_RECHECK_PASS=YES

BTC_STATUS_CONFIRMED=NO
MATIC_STATUS_CONFIRMED=NO
RNDR_STATUS_CONFIRMED=NO

DASHBOARD_CORRUPTED_VISIBLE=YES
REAL_API_OR_TRADE_TOUCHED=NO
READY_FOR_COLD_START_TEST=YES
READY_FOR_DELIVERY=NO

======================================================================
  NOTE: BTC/MATIC/RNDR batch 存在，dashboard 可见化已修复。
        但 status 文件从未生成，生成链路未完成实证。
        建议: 启动 _auto_deploy.py 后观察 BTC/MATIC/RNDR
        是否因 score < 60 而长期不进场，还是启动后崩溃。
        仅 NOT_STARTED 不能声称已验证。
======================================================================
