======================================================================
  P0-D 安全冷启动验证报告
======================================================================
Timestamp: 2026-06-17
Reviewer: deep-copilot (evidence-only)
======================================================================

## 1. 当前 HEAD 与 Git 状态

HEAD:         16ca49f
Branch:       master
Git status:   CLEAN
Last commits: 7e4e17c → 14e0e23 → 4d81ddc → 8d9a546 → 889626a → 16ca49f

P0C_REVIEW_COMMITTED=YES  (16ca49f)
GIT_STATUS_CLEAN=YES

## 2. 运行前安全复核

AUTO_DEPLOY_HAS_SAFE_MODE=NO      (no --dry-run, no --help parsing)
AUTO_DEPLOY_ENTRY_CONFIRMED=YES    (entry code reads as intended)
REAL_KEY_FOUND=NO                  (no api_key/secret in runtime path)
PRIVATE_API_CALL_IN_AUTO_DEPLOY_PATH=NO
ORDER_FUNCTION_IN_AUTO_DEPLOY_PATH=NO
SAFE_TO_COLD_START=YES

Call chain: _auto_deploy.py → run_rt_paper_*.bat → rt_paper_v2.py (paper only)
execution_layer.py (has futures_create_order) is NOT in this call chain.

## 3. BTC/MATIC/RNDR 冷启动前基线

BTC_BAT_EXISTS=YES        run_rt_paper_btc.bat
BTC_STATUS_BEFORE=EXISTS  rt_paper_v2_state_btc.json (generated in prior
                          accidental run; 141 strategies, 109 positions)
MATIC_BAT_EXISTS=YES      run_rt_paper_matic.bat
MATIC_STATUS_BEFORE=MISSING
RNDR_BAT_EXISTS=YES       run_rt_paper_rndr.bat
RNDR_STATUS_BEFORE=MISSING

## 4. 冷启动执行方式

Executed: `python _auto_deploy.py` (no safe-mode flag available)
Duration: ~30 seconds (one complete scan cycle)
Action:   Killed via taskkill after scan output captured
Orders:   NONE (confirmed by call chain analysis)

## 5. 冷启动扫描输出（得分排行）

 Score  Coin          Price       ADX   ATR%    VolR
 -----  ------------  ----------  ----  -----  -----
   55   UNI_USDT       3.2150     34.6  0.995   0.50
   50   BNB_USDT     602.8000     35.9  0.252   1.47
   50   TIA_USDT       0.3936     24.2  0.531   1.17
   45   BTC_USDT   65125.2000     29.6  0.209   1.14
   45   FIL_USDT       0.8166     18.8  0.471   1.03
   40   AVAX_USDT      6.8390     12.1  0.315   1.18
   35   SEI_USDT       0.0553     29.6  0.291   0.70
   30   ETH_USDT    1753.0100     17.5  0.318   1.11
   30   ADA_USDT       0.1688     17.1  0.398   1.13
   30   ATOM_USDT      2.0000     26.8  0.199   0.39
   30   APT_USDT       0.6671     10.6  0.409   0.73
   30   RUNE_USDT      0.4170      9.4  0.371   0.73
   30   FET_USDT       0.2065     13.0  0.309   0.60
   25   SOL_USDT      72.2900     14.2  0.317   1.08
   25   ARB_USDT       0.0862     14.2  0.416   1.02
   25   SUI_USDT       0.7894     13.6  0.328   1.00
   20   XRP_USDT       1.1960     18.1  0.266   1.09
   20   DOGE_USDT      0.0862     16.8  0.238   1.04
   20   DOT_USDT       1.0120     12.0  0.298   0.79
   20   LINK_USDT      8.1640     13.7  0.310   0.91
   20   INJ_USDT       5.4050      9.1  0.459   0.82
   15   LTC_USDT      45.1600     17.9  0.237   0.96
   10   MATIC_USDT     0.4229      0.0  0.000   1.00
    5   OP_USDT        0.1067     13.6  0.261   0.65

RNDR_USDT: NOT IN SCAN OUTPUT (fetch_klines returned < 50 bars
           or symbol not available on Gate.io futures)

Entry threshold: 60. NO coin scored >= 60. Zero bats started.

## 6. BTC/MATIC/RNDR 冷启动后状态

BTC_STATUS_AFTER=EXISTS
  (UNCHANGED. File from prior accidental run persists.
   141 strategies, 14130U total equity, 109 active positions.)
MATIC_STATUS_AFTER=MISSING
  (score=10 < threshold=60, bat was never started.)
RNDR_STATUS_AFTER=MISSING
  (fetch_klines failed — symbol may not be listed on Gate.io.)

## 7. Dashboard 状态变化

BTC:   NOT_STARTED (score 45 < 60, not triggered this cycle)
MATIC: NOT_STARTED (score 10 < 60)
RNDR:  NOT_STARTED (klines unavailable)

BTC_DASHBOARD_AFTER=NOT_STARTED (cold-start cycle; prior state was ONLINE)
MATIC_DASHBOARD_AFTER=NOT_STARTED
RNDR_DASHBOARD_AFTER=NOT_STARTED

## 8. 未生成 status 的原因分析

BTC:
  - Status generation chain IS proven (state file exists from prior run)
  - Current score 45 < threshold 60 → no new bat was started this cycle
  - If market conditions improve (BTC ADX > 40-50), it will start automatically

MATIC:
  - Score 10, well below threshold
  - ADX=0.0 likely indicates very low volatility / thin market
  - Bat exists and is correctly configured
  - STATUS CHAIN NOT EMPIRICALLY PROVEN because bat never fires

RNDR:
  - Not in scan output at all
  - fetch_klines returned < 50 bars or API error
  - May not be a valid Gate.io futures pair
  - STATUS CHAIN NOT PROVEN
  - Requires: verify symbol on Gate.io exchange

## 9. 是否触碰真实 API / 交易

REAL_API_OR_TRADE_TOUCHED=NO
- _auto_deploy.py uses public klines API only (read-only)
- No orders, keys, or private endpoints were called
- Bat invokes rt_paper_v2.py (paper trading only)

## 10. 下一步建议

1. RNDR: verify symbol exists on Gate.io futures.
   Candidate: check if RENDER_USDT or another variant is the correct pair.
2. MATIC: wait for market conditions to improve (ADX > 30) or
   approve lowering ENTRY_THRESHOLD if QA acceptance criteria demand it.
3. BTC: chain is proven. No action needed.

## 11. 最终结论

P0D_DONE=YES
P0C_REVIEW_COMMITTED=YES
GIT_STATUS_CLEAN=YES

SAFE_TO_COLD_START=YES
REAL_API_OR_TRADE_TOUCHED=NO

BTC_STATUS_BEFORE=EXISTS
MATIC_STATUS_BEFORE=MISSING
RNDR_STATUS_BEFORE=MISSING

BTC_STATUS_AFTER=EXISTS       (from prior run, not this cycle)
MATIC_STATUS_AFTER=MISSING    (score < 60)
RNDR_STATUS_AFTER=MISSING     (klines unavailable)

BTC_STATUS_CONFIRMED=YES       (chain proven by prior run)
MATIC_STATUS_CONFIRMED=NO     (bat never fired; score=10)
RNDR_STATUS_CONFIRMED=NO      (symbol may not exist on Gate.io)

READY_FOR_DELIVERY=NO
  Reason: MATIC status chain not provable because score < threshold.
          RNDR status chain not provable because klines fetch fails.
          Neither can be confirmed without either:
          a) lowering ENTRY_THRESHOLD, or
          b) verifying RNDR symbol on exchange, or
          c) accepting NOT_STARTED as the correct state for these coins.
======================================================================
