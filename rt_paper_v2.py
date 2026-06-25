"""
Real-Time Paper Trading v2 — 141 OKX Strategies on Gate.io 15m ETH_USDT
======================================================================
WebSocket 实时推送 → 141策略独立决策 → 实时排行榜
"""

import os
os.environ['LANG'] = 'en_US.UTF-8'

# ── 必须：在任何第三方库导入前移除 CWD，防止 _macro_calendar.py / config.py 等影蔽标准库 ──
import json, sys, time, statistics, threading, atexit, glob as _glob, shutil as _shutil, os as _os_sys
sys.path = [p for p in sys.path if p not in ('', '.')]
_base_gate = os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() and __file__ else os.getcwd(), '..', 'gate_bot')
# fallback: if not relative, try known location relative to this script's dir or CWD
_gate_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() and __file__ else os.getcwd(), '..', 'gate_bot'))
if os.path.isdir(_gate_path):
    sys.path.insert(0, _gate_path)
else:
    sys.path.insert(0, r"C:\Users\Administrator\gate_bot")  # legacy fallback

from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.request import Request, urlopen

import pandas as pd
import websocket

# ── 加载 141 策略 ──
from core.okx_strategies import STRATEGIES as CORE_STRATEGIES, Signal as OKXSignal
import core.okx_strategies_advanced

STRATEGIES = CORE_STRATEGIES

# ── Deploy Config Filter (PLAN C) ──
_DEPLOY_CONFIG = None
_DEPLOY_EQUITY = {}  # per-strategy equity from config (USDT)
_deploy_cfg_path = os.path.join(os.path.dirname(os.path.abspath(
    __file__ if '__file__' in dir() and __file__ else
    os.environ.get('RT_STATE_FILE', os.getcwd())
)), "deploy_config.json")
print(f"[deploy_config] looking at: {_deploy_cfg_path}  (exists={os.path.exists(_deploy_cfg_path)})  __file__={'YES' if '__file__' in dir() and __file__ else 'NO'}")
if os.path.exists(_deploy_cfg_path):
    try:
        with open(_deploy_cfg_path, "r", encoding="utf-8") as _dcf:
            _DEPLOY_CONFIG = json.load(_dcf)
        _coin = os.environ.get("RT_SYMBOL", "").split("_")[0]
        _whitelist = _DEPLOY_CONFIG.get("strategies", {}).get(_coin, [])
        print(f"[deploy_config] coin={_coin} whitelist={_whitelist}")
        _tiers = _DEPLOY_CONFIG.get("tiers", {})
        _equity_per_strat = 2.33  # default fallback
        for _tier_name, _tier_data in _tiers.items():
            if _coin in _tier_data.get("coins", []):
                _equity_per_strat = _tier_data.get("equity_per_strat", 2.33)
                print(f"[deploy_config] tier={_tier_name} equity_per_strat={_equity_per_strat}")
                break
        if _whitelist:
            _whitelist_upper = [s.upper() for s in _whitelist]
            _final = {}
            for _k, _v in STRATEGIES.items():
                try:
                    _obj = _v()
                    _n = _obj.name.upper()
                    # Exact match only (no prefix matching to avoid catching variants)
                    if _n in _whitelist_upper:
                        _final[_k] = _v
                        _DEPLOY_EQUITY[_k] = _equity_per_strat
                        print(f"[deploy_config] matched: {_k} -> {_obj.name} (equity={_equity_per_strat})")
                except Exception:
                    pass
            if _final:
                STRATEGIES = _final
                print(f"[deploy_config] {os.environ.get('RT_SYMBOL','?')}: filtered {len(CORE_STRATEGIES)}->{len(STRATEGIES)} strategies: {_whitelist}")
                print(f"[deploy_config] per-strat equity: {_equity_per_strat} USDT")
            else:
                print(f"[deploy_config] WARNING: whitelist={_whitelist} but no strategies matched!")
    except Exception as _e:
        import traceback
        print(f"[deploy_config] load failed: {_e}")
        traceback.print_exc()

# ── 常量 ──
import os as _os_const
if '__file__' in dir() and __file__:
    _BASE_DIR = _os_const.path.dirname(_os_const.path.abspath(__file__))
elif _os_const.environ.get("RT_STATE_FILE"):
    # -c exec 模式下 __file__ 不可用，从 RT_STATE_FILE 反推目录
    _BASE_DIR = _os_const.path.dirname(_os_const.path.abspath(
        _os_const.environ["RT_STATE_FILE"]))
else:
    _BASE_DIR = _os_const.path.abspath(_os_const.getcwd())
_SYMBOL = _os_const.environ.get("RT_SYMBOL", "SOL_USDT")
_STATE_DEFAULT = _os_const.path.join(_BASE_DIR, "rt_paper_v2_state.json")
_STATE = _os_const.environ.get("RT_STATE_FILE", _STATE_DEFAULT)
WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
SYMBOL = _SYMBOL
INTERVAL = "5m"
STATE_FILE = _STATE
LEADERBOARD_FILE = _os_const.path.join(_BASE_DIR, "rt_paper_v2_leaderboard.json")
REPORT_DIR = _os_const.path.join(_BASE_DIR, "reports")
LOG_DIR = _os_const.path.join(_BASE_DIR, "logs")

INITIAL_EQUITY = 100.0
MARGIN_PCT = 0.05
LEVERAGE = 200
HARD_STOP_PCT = 0.015
TP1_PCT = 0.03
TP2_PCT = 0.05
TAKER_FEE = 0.0005   # 0.05% 吃单
MAKER_FEE = 0.0002   # 0.02% 挂单
SLIPPAGE = 0.0003    # 0.03% 滑点（吃单成交）
MIN_BARS = 50
LEADERBOARD_INTERVAL = 60
TIME_STOP_HOURS = 8
GATE_API = "https://api.gateio.ws/api/v4"
WEBHOOK_ENV_VAR = "WECHAT_WORK_WEBHOOK_URL"
WEBHOOK_URL = _os_const.environ.get(WEBHOOK_ENV_VAR, "").strip()


class StrategyTrack:
    __slots__ = (
        'key', 'name', 'strategy_obj', 'equity', 'peak_equity',
        'position', 'trades', 'consecutive_losses', 'daily_trades',
        'last_trade_date', 'locked_until', 'last_close_time', 'locked_reason',
    )

    def __init__(self, key: str, name: str, strategy_obj, equity: float = INITIAL_EQUITY):
        self.key = key
        self.name = name
        self.strategy_obj = strategy_obj
        self.equity = equity
        self.peak_equity = equity
        self.position: Optional[dict] = None
        self.trades: List[dict] = []
        self.consecutive_losses = 0
        self.daily_trades = 0
        self.last_trade_date = ""
        self.locked_until: Optional[datetime] = None
        self.last_close_time: Optional[datetime] = None
        self.locked_reason = ""

    @property
    def locked(self) -> bool:
        return self.locked_until is not None and datetime.now() < self.locked_until

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t["pnl"] > 0) / len(self.trades) * 100

    @property
    def avg_r(self) -> float:
        if not self.trades:
            return 0.0
        return statistics.mean(t["r_multiple"] for t in self.trades)

    @property
    def profit_factor(self) -> float:
        gw = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in self.trades if t["pnl"] <= 0))
        if gl > 0:
            return gw / gl
        return 1.0 if gw > 0 else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t["pnl"] for t in self.trades)

    @property
    def return_pct(self) -> float:
        return self.total_pnl / INITIAL_EQUITY * 100

    @property
    def max_dd(self) -> float:
        if not self.trades:
            return 0.0
        eq = [INITIAL_EQUITY]
        for t in self.trades:
            eq.append(eq[-1] + t["pnl"])
        peak = eq[0]
        dd = 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                dd = max(dd, (peak - v) / peak)
        return dd * 100

    def lock(self, reason: str, hours: int = 24):
        self.locked_until = datetime.now() + timedelta(hours=hours)
        self.locked_reason = reason

    def reset_daily(self, dt_str: str):
        if dt_str != self.last_trade_date:
            self.daily_trades = 0
            self.last_trade_date = dt_str

    def to_dict(self) -> dict:
        pos = self.position
        return {
            "key": self.key,
            "name": self.name,
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "trades": self.trade_count,
            "win_rate": round(self.win_rate, 1),
            "avg_r": round(self.avg_r, 2),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_pnl, 2),
            "return_pct": round(self.return_pct, 1),
            "max_dd": round(self.max_dd, 1),
            "position": {
                "side": pos["side"],
                "entry_price": pos["entry_price"],
                "stop_price": pos["stop_price"],
                "tp1": pos["tp1"],
                "tp2": pos["tp2"],
                "entry_time": pos["entry_time"].isoformat()
                    if isinstance(pos["entry_time"], datetime) else str(pos["entry_time"]),
            } if pos else None,
            "locked": self.locked,
        }


class RealTimePaperTraderV2:

    def __init__(self):
        print(f"正在加载 {len(STRATEGIES)} 个策略 ...")
        self.tracks: Dict[str, StrategyTrack] = {}
        name_counts: Dict[str, int] = {}

        for skey, scls in STRATEGIES.items():
            try:
                obj = scls()
                name = obj.name
                if name in name_counts:
                    name_counts[name] += 1
                    display_name = f"{name}#{name_counts[name]}"
                else:
                    name_counts[name] = 1
                    display_name = name
                uniq_key = f"{skey}_{name_counts[name]}" if name_counts[name] > 1 else skey
                _eq = _DEPLOY_EQUITY.get(skey, 100.0)
                self.tracks[uniq_key] = StrategyTrack(uniq_key, display_name, obj, equity=_eq)
            except Exception as e:
                print(f"  加载失败 [{skey}]: {e}")

        print(f"已加载 {len(self.tracks)} 个策略变体")
        self.klines: Dict[int, dict] = {}
        self._last_leaderboard_print = 0.0
        self._last_hourly_report = time.time()
        self._last_state_save = time.time()
        self._last_heartbeat = 0.0
        self._ws = None
        self._lock = threading.Lock()
        atexit.register(self._safe_shutdown)
        self._load_state()

        # 写 PID 文件，供 STOP 脚本精确关停
        _coin = SYMBOL.split('_')[0].lower()
        _pid_path = _os_sys.path.join(LOG_DIR, f"pid_{_coin}.txt")
        try:
            with open(_pid_path, "w") as _pf:
                _pf.write(str(_os_sys.getpid()))
        except Exception:
            pass

    def _fetch_klines_rest(self, limit: int = 200) -> List[dict]:
        endpoint = f"/futures/usdt/candlesticks?contract={SYMBOL}&interval={INTERVAL}&limit={limit}"
        url = f"{GATE_API}{endpoint}"
        req = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            print(f"REST 历史数据拉取失败: {e}")
            return []
        result = []
        for candle in data:
            result.append({
                "time": int(candle.get("t", 0)),
                "open": float(candle.get("o", 0)),
                "high": float(candle.get("h", 0)),
                "low": float(candle.get("l", 0)),
                "close": float(candle.get("c", 0)),
                "volume": float(candle.get("v", 0)),
            })
        return result

    def _preload_history(self):
        candles = self._fetch_klines_rest(200)
        for c in candles:
            self.klines[c["time"]] = c
        print(f"历史K线已加载: {len(self.klines)} 根 (15m {SYMBOL})")

    def _build_df(self) -> Optional[pd.DataFrame]:
        if len(self.klines) < MIN_BARS:
            return None
        sorted_klines = sorted(self.klines.values(), key=lambda x: x["time"])
        df = pd.DataFrame(sorted_klines)
        df.index = pd.to_datetime(df["time"], unit="s")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.astype(float)
        return df

    def connect_ws(self):
        print(f"连接 Gate.io WebSocket: {WS_URL}")
        self._preload_history()
        reconnect_delay = 1
        max_delay = 60
        while True:
            try:
                self._ws = websocket.WebSocketApp(
                    WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"WebSocket 异常: {e}，{reconnect_delay}s 后重连...")
            self._save_state()
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_delay)

    def _on_open(self, ws):
        print("WebSocket 已连接")
        print(f"  → {SYMBOL} {INTERVAL} 实时数据流启动")
        sub = {
            "time": int(time.time()),
            "channel": "futures.candlesticks",
            "event": "subscribe",
            "payload": [INTERVAL, SYMBOL],
        }
        ws.send(json.dumps(sub))
        # 订阅成功不再刷日志

    def _on_message(self, ws, raw):
        self._update_heartbeat()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        channel = msg.get("channel", "")
        event = msg.get("event", "")
        result = msg.get("result", {})
        if channel != "futures.candlesticks" or event != "update":
            return
        if isinstance(result, list):
            if not result:
                return
            result = result[0]
        candle = {
            "time": int(result.get("t", 0)),
            "open": float(result.get("o", 0)),
            "high": float(result.get("h", 0)),
            "low": float(result.get("l", 0)),
            "close": float(result.get("c", 0)),
            "volume": float(result.get("v", 0)),
        }
        if candle["time"] == 0:
            return
        with self._lock:
            self.klines[candle["time"]] = candle
            # 裁剪保留最近 500 根 K 线，防止无限增长
            if len(self.klines) > 500:
                keys = sorted(self.klines.keys())
                for old_key in keys[:-500]:
                    del self.klines[old_key]
            df = self._build_df()
            if df is None:
                return
            now = datetime.now()
            self._on_tick(df, candle, now)

    def _on_error(self, ws, error):
        print(f"WebSocket 错误: {error}")

    def _on_close(self, ws, code, msg):
        self._save_state()
        if code or msg:
            print(f"WebSocket 关闭: code={code} msg={msg}")

    def _on_tick(self, df: pd.DataFrame, current_candle: dict, now: datetime):
        dt_str = now.strftime("%Y-%m-%d")
        active_positions = 0
        for key, track in self.tracks.items():
            try:
                self._process_track(key, track, df, current_candle, now, dt_str)
                if track.position:
                    active_positions += 1
            except Exception as e:
                print(f"[{SYMBOL}] 策略异常 {track.name}: {e}")
                import traceback
                traceback.print_exc()

        elapsed = time.time() - self._last_leaderboard_print
        if elapsed >= LEADERBOARD_INTERVAL:
            self._print_leaderboard(active_positions)
            self._save_leaderboard()
            self._last_leaderboard_print = time.time()

        # 每小时存档报表
        if time.time() - self._last_hourly_report >= 3600:
            self._save_hourly_report()
            self._last_hourly_report = time.time()

        # 每1分钟存档状态（防止崩了丢持仓数据）
        if time.time() - self._last_state_save >= 60:
            self._save_state()
            self._last_state_save = time.time()

        # 健康心跳（同时走 _on_message 和 _on_tick 双保险）
        self._update_heartbeat()

    def _update_heartbeat(self):
        """每 15 秒写心跳文件，守护进程据此判断进程存活"""
        _now = time.time()
        if _now - self._last_heartbeat >= 15:
            _coin = SYMBOL.split('_')[0].lower()
            _hb_path = _os_sys.path.join(LOG_DIR, f"heartbeat_{_coin}.txt")
            try:
                with open(_hb_path, "w", encoding="utf-8") as _hb:
                    _hb.write(datetime.now().isoformat())
            except Exception:
                pass  # 任何原因都静默忽略，不阻塞交易
            self._last_heartbeat = _now

    def _process_track(self, key, track, df, current_candle, now, dt_str):
        if track.locked_until and now >= track.locked_until:
            track.locked_until = None
        track.reset_daily(dt_str)

        if track.position:
            exit_price, exit_reason = self._check_exit(track, df, current_candle, now)
            if exit_price is not None:
                self._close_position(track, exit_price, exit_reason, now)
            return

        # 反转冷却：刚平仓不久不急于新开
        if track.last_close_time:
            cooldown_seconds = 15 * 60
            elapsed_since_close = (now - track.last_close_time).total_seconds()
            if elapsed_since_close < cooldown_seconds:
                return

        if track.locked:
            return
        if track.daily_trades >= 2:
            return
        if track.consecutive_losses >= 3:
            track.lock(f"连亏{track.consecutive_losses}笔", 24)
            return

        sig = self._get_entry_signal(track, df)
        if sig is None:
            return
        self._enter_position(track, sig, current_candle, now)
        track.daily_trades += 1

    def _get_entry_signal(self, track, df) -> Optional[OKXSignal]:
        try:
            signals = track.strategy_obj.generate_signals(df)
        except Exception:
            return None
        if not signals:
            return None
        sig = signals[-1]
        if sig is None:
            return None
        if sig.signal_type not in ("BUY", "SELL"):
            return None
        return sig

    def _check_exit(self, track, df, current_candle, now):
        pos = track.position
        high = current_candle["high"]
        low = current_candle["low"]
        close = current_candle["close"]
        side = pos["side"]

        if side == "long" and low <= pos["stop_price"]:
            return (pos["stop_price"], "止损")
        if side == "short" and high >= pos["stop_price"]:
            return (pos["stop_price"], "止损")

        tp1_hit = (high >= pos["tp1"]) if side == "long" else (low <= pos["tp1"])
        tp2_hit = (high >= pos["tp2"]) if side == "long" else (low <= pos["tp2"])

        if tp1_hit and not pos.get("tp1_hit"):
            pos["tp1_hit"] = True
            pos["breakeven_activated"] = True
            pos["stop_price"] = pos["entry_price"]

        if tp2_hit and not pos.get("tp2_hit"):
            pos["tp2_hit"] = True
            return (pos["tp2"], "TP2目标达成")

        rev_sig = self._get_entry_signal(track, df)
        if rev_sig is not None:
            if side == "long" and rev_sig.signal_type == "SELL":
                return (close, "策略反转")
            if side == "short" and rev_sig.signal_type == "BUY":
                return (close, "策略反转")

        hours_held = (now - pos["entry_time"]).total_seconds() / 3600
        if hours_held >= TIME_STOP_HOURS:
            ep = pos["entry_price"]
            if ep and ep > 0:
                price_change = abs(close - ep) / ep
                if price_change < 0.01:
                    return (close, "时间止损")

        return (None, None)

    def _enter_position(self, track, signal, current_candle, now):
        entry_price = signal.price
        if not entry_price or entry_price <= 0:
            return
        direction = "long" if signal.signal_type == "BUY" else "short"
        margin = track.equity * MARGIN_PCT
        size = margin * LEVERAGE / entry_price

        if direction == "long":
            stop_price = entry_price * (1 - HARD_STOP_PCT)
            tp1 = entry_price * (1 + TP1_PCT)
            tp2 = entry_price * (1 + TP2_PCT)
        else:
            stop_price = entry_price * (1 + HARD_STOP_PCT)
            tp1 = entry_price * (1 - TP1_PCT)
            tp2 = entry_price * (1 - TP2_PCT)

        entry_notional = size * entry_price
        track.position = {
            "side": direction,
            "entry_price": entry_price,
            "entry_time": now,
            "size": size,
            "margin": round(margin, 2),
            "stop_price": stop_price,
            "tp1": tp1,
            "tp2": tp2,
            "breakeven_activated": False,
            "tp1_hit": False,
            "tp2_hit": False,
            "entry_notional": entry_notional,
        }
        dir_cn = "多" if direction == "long" else "空"
        print(f"[{track.name}] 开仓 {direction.upper()} @ {entry_price:.2f} "
              f"止损:{stop_price:.2f} TP1:{tp1:.2f} TP2:{tp2:.2f}")
        self._notify_trade(
            f"📈 {track.name} 开{dir_cn}",
            f"入场: **{entry_price:.4f}**\n"
            f"止损: {stop_price:.4f} | 止盈1: {tp1:.4f} | 止盈2: {tp2:.4f}\n"
            f"保证金: {margin:.2f}U")


    def _close_position(self, track, exit_price, exit_reason, now):
        pos = track.position
        entry = pos["entry_price"]
        direction = pos["side"]

        if direction == "long":
            pnl = (exit_price - entry) * pos["size"]
        else:
            pnl = (entry - exit_price) * pos["size"]

        # 手续费：入场吃单 0.05% + 出场（止损/反转=吃单，止盈=挂单）
        entry_notional = pos.get("entry_notional", pos["size"] * entry)
        entry_fee = entry_notional * TAKER_FEE
        if exit_reason in ("TP1目标达成", "TP2目标达成"):
            exit_fee = entry_notional * MAKER_FEE
        else:
            exit_fee = entry_notional * TAKER_FEE
        total_fee = entry_fee + exit_fee
        pnl -= total_fee

        # 滑点：入场必吃单(滑点0.03%) + 出场止盈挂单(无滑点)，止损/反转吃单(滑点0.03%)
        entry_slippage = entry_notional * SLIPPAGE
        if exit_reason in ("TP1目标达成", "TP2目标达成"):
            exit_slippage = 0.0
        else:
            exit_slippage = entry_notional * SLIPPAGE
        pnl -= (entry_slippage + exit_slippage)

        track.equity += pnl
        if track.equity > track.peak_equity:
            track.peak_equity = track.equity

        risk_amount = pos["margin"] * HARD_STOP_PCT * LEVERAGE
        r_mult = pnl / risk_amount if risk_amount > 0 else 0.0
        track.consecutive_losses = 0 if pnl > 0 else track.consecutive_losses + 1

        trade = {
            "strategy": track.name,
            "direction": direction,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "r_multiple": round(r_mult, 2),
            "margin": pos["margin"],
            "exit_reason": exit_reason,
            "entry_time": pos["entry_time"].isoformat()
                if isinstance(pos["entry_time"], datetime) else str(pos["entry_time"]),
            "exit_time": now.isoformat(),
        }
        track.trades.append(trade)

        emoji = "+" if pnl > 0 else "-"
        print(f"[{track.name}] {emoji}{pnl:+.2f}U ({r_mult:+.2f}R) "
              f"原因:{exit_reason} 净值:{track.equity:.2f}")

        pnl_sign = "+" if pnl >= 0 else ""
        dir_cn = "多" if direction == "long" else "空"

        # 中文原因
        reason_map = {
            "止损": "止损",
            "TP1目标达成": "止盈1达成",
            "TP2目标达成": "止盈2达成",
            "信号反转": "信号反转",
            "时间止损": "时间止损",
        }
        reason_cn = reason_map.get(exit_reason, exit_reason)

        self._notify_trade(
            f"💰 {track.name} 平{dir_cn}",
            f"入场: {entry:.4f} → 出场: **{exit_price:.4f}**\n"
            f"盈亏: {pnl_sign}{pnl:+.4f}U ({r_mult:+.2f}R) | {reason_cn}\n"
            f"净值: {track.equity:.2f}U")

        track.position = None
        track.last_close_time = now
        self._save_state()

    def _notify_trade(self, title: str, body: str):
        """Send trade notification to WeChat webhook (fire-and-forget)."""
        import json as _json, urllib.request as _ur
        if not WEBHOOK_URL:
            print(f"[Webhook] 跳过发送: 未设置环境变量 {WEBHOOK_ENV_VAR}")
            return

        coin = SYMBOL.split("_")[0] if "_" in SYMBOL else SYMBOL
        content = f"**{coin}** | {title}\n{body}"
        payload = _json.dumps({
            "msgtype": "markdown",
            "markdown": {"content": content},
        }, ensure_ascii=False).encode("utf-8")
        def _send():
            try:
                req = _ur.Request(WEBHOOK_URL, data=payload,
                                  headers={"Content-Type": "application/json; charset=utf-8"})
                with _ur.urlopen(req, timeout=5) as resp:
                    result = _json.loads(resp.read().decode())
                    if result.get("errcode") != 0:
                        print(f"[Webhook] 推送失败: {result}")
            except Exception as e:
                print(f"[Webhook] 推送异常: {e}")
        threading.Thread(target=_send, daemon=True).start()

    def _print_leaderboard(self, active_positions: int):
        ranked = sorted(self.tracks.values(), key=lambda t: t.equity, reverse=True)
        lines = ["\n" + "=" * 85]
        lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] 实时排行榜")
        lines.append(f"{'策略名称':<22s} {'净值':>8s} {'盈亏%':>8s} {'交易':>5s} "
                     f"{'胜率':>6s} {'均R':>7s} {'PF':>6s} {'持仓':>6s}")
        lines.append("-" * 85)

        shown = set()
        for t in ranked[:20]:
            shown.add(t.key)
            pos_marker = ""
            if t.position:
                pos_marker = "LONG" if t.position["side"] == "long" else "SHORT"
            lines.append(
                f"{t.name:<22s} {t.equity:>8.2f} {t.return_pct:>+7.1f}% "
                f"{t.trade_count:>5d} {t.win_rate:>5.1f}% {t.avg_r:>+6.2f}R "
                f"{t.profit_factor:>5.2f}  {pos_marker:>6s}"
            )

        extra_pos = [t for t in ranked if t.position and t.key not in shown]
        if extra_pos:
            lines.append("-" * 85)
            lines.append("  以下策略有持仓:")
            for t in extra_pos:
                pos_marker = "LONG" if t.position["side"] == "long" else "SHORT"
                lines.append(
                    f"{t.name:<22s} {t.equity:>8.2f} {t.return_pct:>+7.1f}% "
                    f"{t.trade_count:>5d} {t.win_rate:>5.1f}% {t.avg_r:>+6.2f}R "
                    f"{t.profit_factor:>5.2f}  {pos_marker:>6s}"
                )
        lines.append("=" * 85)
        lines.append(f"共 {len(self.tracks)} 策略 | {active_positions} 持仓 "
                     f"| 总净值: {sum(t.equity for t in self.tracks.values()):.0f}U")
        print("\n".join(lines))

    def _save_leaderboard(self):
        data = {key: track.to_dict() for key, track in self.tracks.items()}
        try:
            with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            print(f"保存排行榜失败: {e}")

    def _save_hourly_report(self):
        import os as _os
        _os.makedirs(REPORT_DIR, exist_ok=True)
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M")
        filename = f"report_{SYMBOL}_{INTERVAL}_{date_str}_{time_str}.json"

        ranked = sorted(self.tracks.values(), key=lambda t: t.equity, reverse=True)
        total_equity = sum(t.equity for t in ranked)
        active_positions = sum(1 for t in ranked if t.position)
        traded = sum(1 for t in ranked if t.trade_count > 0)

        report = {
            "updated_at": now.isoformat(),
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "total_tracks": len(ranked),
            "active_positions": active_positions,
            "traded": traded,
            "total_equity": round(total_equity, 2),
            "top_10": [],
            "bottom_5": [],
        }

        for t in ranked[:10]:
            report["top_10"].append({
                "name": t.name,
                "equity": round(t.equity, 2),
                "return_pct": round(t.return_pct, 1),
                "trades": t.trade_count,
                "win_rate": round(t.win_rate, 1),
                "avg_r": round(t.avg_r, 2),
                "profit_factor": round(t.profit_factor, 2),
                "position": t.position["side"] if t.position else None,
            })

        for t in ranked[-5:]:
            report["bottom_5"].append({
                "name": t.name,
                "equity": round(t.equity, 2),
                "return_pct": round(t.return_pct, 1),
                "trades": t.trade_count,
            })

        path = _os.path.join(REPORT_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n[报表] {filename} 已保存 (总净值:{total_equity:.0f}U | 交易:{traded} | 持仓:{active_positions})")

    def _save_state(self):
        import shutil
        data = {}
        for key, track in self.tracks.items():
            pos = track.position
            entry = {}
            if pos:
                entry = {
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "entry_time": pos["entry_time"].isoformat()
                        if isinstance(pos["entry_time"], datetime) else str(pos["entry_time"]),
                    "size": pos["size"],
                    "margin": pos["margin"],
                    "stop_price": pos["stop_price"],
                    "tp1": pos["tp1"],
                    "tp2": pos["tp2"],
                    "breakeven_activated": pos.get("breakeven_activated", False),
                    "tp1_hit": pos.get("tp1_hit", False),
                    "tp2_hit": pos.get("tp2_hit", False),
                }
            data[key] = {
                "equity": track.equity,
                "peak_equity": track.peak_equity,
                "trades": track.trades,
                "consecutive_losses": track.consecutive_losses,
                "daily_trades": track.daily_trades,
                "last_trade_date": track.last_trade_date,
                "last_close_time": track.last_close_time.isoformat()
                    if track.last_close_time else None,
                "locked_until": track.locked_until.isoformat()
                    if track.locked_until else None,
                "locked_reason": track.locked_reason,
                "position": entry if pos else None,
            }
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            # 原子替换：先备份旧文件，再 rename
            if os.path.exists(STATE_FILE):
                _shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
            os.replace(tmp, STATE_FILE)
            # 清理旧 .bak：只保留最近5个
            _bak_list = sorted(_glob.glob(STATE_FILE + ".bak*"))
            for _old_bak in _bak_list[:-5]:
                try:
                    os.remove(_old_bak)
                except OSError:
                    pass
        except Exception as e:
            print(f"保存状态失败: {e}")

    def _safe_shutdown(self):
        """atexit 兜底：进程无论怎么死都保存状态"""
        try:
            self._save_state()
        except Exception:
            pass  # 静默失败，不阻塞退出

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, d in data.items():
                if key in self.tracks:
                    t = self.tracks[key]
                    t.equity = d.get("equity", INITIAL_EQUITY)
                    t.peak_equity = d.get("peak_equity", INITIAL_EQUITY)
                    t.trades = d.get("trades", [])
                    t.consecutive_losses = d.get("consecutive_losses", 0)
                    t.daily_trades = d.get("daily_trades", 0)
                    t.last_trade_date = d.get("last_trade_date", "")
                    if d.get("locked_until"):
                        try:
                            t.locked_until = datetime.fromisoformat(d["locked_until"])
                        except (ValueError, TypeError):
                            t.locked_until = None
                        t.locked_reason = d.get("locked_reason", "")
                    pd_ = d.get("position")
                    if pd_:
                        try:
                            et = datetime.fromisoformat(pd_["entry_time"])
                        except (ValueError, TypeError, KeyError):
                            et = datetime.now()
                        t.position = {
                            "side": pd_["side"],
                            "entry_price": pd_["entry_price"],
                            "entry_time": et,
                            "size": pd_.get("size", 0),
                            "margin": pd_.get("margin", 0),
                            "stop_price": pd_.get("stop_price", 0),
                            "tp1": pd_.get("tp1", 0),
                            "tp2": pd_.get("tp2", 0),
                            "breakeven_activated": pd_.get("breakeven_activated", False),
                            "tp1_hit": pd_.get("tp1_hit", False),
                            "tp2_hit": pd_.get("tp2_hit", False),
                        }
            print(f"状态已恢复: {len(data)} 策略")
            active_pos = sum(1 for t in self.tracks.values() if t.position)
            if active_pos:
                print(f"  其中 {active_pos} 个策略有持仓")
        except Exception as e:
            print(f"状态恢复失败: {e}")


def rotate_log(log_path, max_size_mb=5, keep=3):
    """Rotate log file if it exceeds max_size_mb."""
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > max_size_mb * 1024 * 1024:
            for i in range(keep - 1, 0, -1):
                src = f"{log_path}.{i}"
                dst = f"{log_path}.{i + 1}"
                if os.path.exists(src):
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(src, dst)
            if os.path.exists(log_path):
                os.rename(log_path, f"{log_path}.1")
            print(f"[日志轮转] {os.path.basename(log_path)} > {max_size_mb}MB")
    except Exception as e:
        print(f"[日志轮转] 失败: {e}")


def main():
    # 启动时轮转日志
    _log_name = f"rt_{SYMBOL.split('_')[0].lower()}.log"
    _log_path = os.path.join(LOG_DIR, _log_name)
    rotate_log(_log_path)

    print("=" * 60)
    print("  Real-Time Paper Trading v2")
    print(f"  {len(STRATEGIES)} 策略并联 | {SYMBOL} {INTERVAL}")
    print(f"  初始: {INITIAL_EQUITY}U/策略 | {LEVERAGE}x | 保证金{MARGIN_PCT*100}%")
    print(f"  止损{HARD_STOP_PCT*100}% | TP1:{TP1_PCT*100}% | TP2:{TP2_PCT*100}%")
    print("=" * 60)

    trader = RealTimePaperTraderV2()
    try:
        trader.connect_ws()
    except KeyboardInterrupt:
        print("\n保存状态中...")
        trader._save_state()
        print("已退出")
    except Exception as e:
        print(f"致命错误: {e}")
        import traceback
        traceback.print_exc()
        trader._save_state()


if __name__ == "__main__":
    main()
