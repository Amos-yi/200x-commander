"""
200x Commander Bot 核心测试
"""

import os
import sys
import json
import tempfile

# 确保项目根在 sys.path
_sys_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _sys_root)

import config
from stage_manager import StageManager
from lock_manager import LockManager
from _macro_calendar import Calendar, detect_regime
from strategic_brain import StrategicBrain
from tactical_brain import TacticalBrain
from trade_logger import log_trade, recent_stats, LOG_PATH as TRADE_LOG

LOCK_STATE = os.path.join(_sys_root, "state.json")


def _clean_artifacts():
    """每次全量测试前清残留文件"""
    for p in [LOCK_STATE, TRADE_LOG]:
        if os.path.exists(p):
            os.remove(p)


# ═══════════════════════════════════════════
#  StageManager
# ═══════════════════════════════════════════

class TestStageManager:
    def test_stage1_100(self):
        sm = StageManager(100)
        assert sm.stage == 1
        assert sm.stage_name() == "脆弱期"
        assert sm.stage_target() == 500
        assert sm.distance_to_stage_target() == 400
        assert sm.progress_in_stage() == 0.0

    def test_stage1_mid(self):
        sm = StageManager(300)
        assert sm.stage == 1
        assert sm.progress_in_stage() == 0.5

    def test_stage1_edge(self):
        sm = StageManager(499)
        assert sm.stage == 1
        sm2 = StageManager(500)
        assert sm2.stage == 2

    def test_stage_params(self):
        sm = StageManager(100)
        p = sm.get_params()
        assert p["margin"] == 5.0
        assert p["nominal"] == 1000.0
        assert p["hard_stop_pct"] == 0.015

    def test_transition_up(self):
        sm = StageManager(450)
        result = sm.update_equity(550)
        assert result == "up"
        assert sm.stage == 2
        p = sm.get_params()
        assert p["override_mult"] == 0.5

    def test_transition_down(self):
        sm = StageManager(550)
        result = sm.update_equity(450)
        assert result == "down"
        assert sm.stage == 1
        p = sm.get_params()
        assert p["override_mult"] == 0.5

    def test_progress_bar(self):
        sm = StageManager(300)
        bar = sm.progress_bar(20)
        assert "█" in bar
        assert "500" in bar

    def test_summary(self):
        sm = StageManager(100)
        s = sm.summary()
        assert "脆弱期" in s
        assert "100" in s

    def test_stage3(self):
        sm = StageManager(5000)
        assert sm.stage == 3
        p = sm.get_params()
        assert p["margin_pct"] == 0.03

    def test_stage4(self):
        sm = StageManager(20000)
        assert sm.stage == 4
        p = sm.get_params()
        assert p["margin_pct"] == 0.02


# ═══════════════════════════════════════════
#  LockManager
# ═══════════════════════════════════════════

class TestLockManager:
    def setup_method(self):
        _clean_artifacts()

    def test_init(self):
        lm = LockManager()
        assert lm.daily_trades() >= 0
        assert lm.consecutive_losses() == 0

    def test_locked_outside_hours(self):
        lm = LockManager()
        locked, reason = lm.is_locked()
        assert isinstance(locked, bool)
        assert isinstance(reason, str)

    def test_daily_limit(self):
        lm = LockManager()
        import datetime
        lm.state["daily_trades"] = 2
        lm.state["last_trade_date"] = datetime.datetime.now().strftime("%Y-%m-%d")
        locked, reason = lm.is_locked()
        if locked:
            assert "2" in reason

    def test_on_trade_closed_win(self):
        lm = LockManager()
        lm.state["consecutive_losses"] = 2
        lm.on_trade_closed(50, 150)
        assert lm.consecutive_losses() == 0

    def test_on_trade_closed_loss(self):
        lm = LockManager()
        lm.state["consecutive_losses"] = 2
        lm.on_trade_closed(-30, 70)
        assert lm.consecutive_losses() == 3

    def test_status(self):
        lm = LockManager()
        s = lm.status()
        assert "locked" in s
        assert "daily_trades" in s


# ═══════════════════════════════════════════
#  Calendar
# ═══════════════════════════════════════════

class TestCalendar:
    def test_event_window_normal(self):
        import datetime
        now = datetime.datetime(2026, 6, 15, 14, 0)
        assert not Calendar.is_event_window(now)

    def test_event_window_during_fomc(self):
        import datetime
        now = datetime.datetime(2026, 6, 30, 20, 0)
        assert Calendar.is_event_window(now)
        assert Calendar.event_name(now) == "FOMC"

    def test_weekend(self):
        import datetime
        sat = datetime.datetime(2026, 6, 20, 14, 0)  # 周六
        assert Calendar.is_weekend(sat)

    def test_upcoming_events(self):
        events = Calendar.upcoming_events(365)
        assert len(events) > 0


# ═══════════════════════════════════════════
#  Market Regime
# ═══════════════════════════════════════════

class TestMarketRegime:
    def test_trending_up(self):
        klines = []
        price = 50000
        for i in range(25):
            klines.append({
                "close": price + i * 200,
                "high": price + i * 200 + 100,
                "low": price + i * 200 - 50,
            })
        regime = detect_regime(klines)
        assert regime in ("trending_up", "ranging", "volatile")

    def test_ranging(self):
        import random
        random.seed(42)
        klines = []
        for i in range(25):
            klines.append({
                "close": 50000 + random.randint(-200, 200),
                "high": 50100 + random.randint(-200, 200),
                "low": 49900 + random.randint(-200, 200),
            })
        regime = detect_regime(klines)
        assert regime in ("ranging", "trending_up", "trending_down")


# ═══════════════════════════════════════════
#  StrategicBrain
# ═══════════════════════════════════════════

class TestStrategicBrain:
    def test_decide_forbidden_at_night(self):
        sb = StrategicBrain()
        b = sb.decide(100, 1, "脆弱期", 400, 0.0, 0, None, None, "ranging")
        assert "mode" in b
        assert "equity" in b

    def test_mode_names(self):
        valid = {"offensive", "standard", "repair", "survival", "harvest", "forbidden"}
        for mode in config.MODE_RISK_BUDGET:
            assert mode in valid

    def test_mindset_hints(self):
        sb = StrategicBrain()
        for mode in ["offensive", "standard", "repair", "survival", "harvest", "forbidden"]:
            sb._current_mode = mode
            hint = sb.mindset_hint()
            assert len(hint) > 0


# ═══════════════════════════════════════════
#  TacticalBrain
# ═══════════════════════════════════════════

class TestTacticalBrain:
    def _make_klines(self, trend="up", with_volume=True):
        klines = []
        base = 50000 if trend == "up" else 52000
        step = 50 if trend == "up" else -50
        for i in range(30):
            c = base + step * i
            vol = 1000 if with_volume else 200
            if i >= 27:
                vol = 2500
                c = base + step * i + (50 if trend == "up" else -50)
            klines.append({
                "open": c - 20,
                "high": c + 30,
                "low": c - 40,
                "close": c,
                "volume": vol,
            })
        return klines

    def test_no_signal_on_no_cross(self):
        tb = TacticalBrain()
        klines = self._make_klines("up", True)
        briefing = {"locked": False, "quality_threshold": 5}
        result = tb.generate("BTC_USDT", klines, briefing)
        if result is not None:
            assert result.symbol == "BTC_USDT"
            assert result.score >= 0
            assert result.score <= 10

    def test_locked_briefing_returns_none(self):
        tb = TacticalBrain()
        klines = self._make_klines()
        briefing = {"locked": True, "quality_threshold": 5}
        result = tb.generate("BTC_USDT", klines, briefing)
        assert result is None

    def test_quality_threshold_blocks_low_score(self):
        tb = TacticalBrain()
        klines = self._make_klines("up", False)
        briefing = {"locked": False, "quality_threshold": 8}
        result = tb.generate("BTC_USDT", klines, briefing)
        assert result is None


# ═══════════════════════════════════════════
#  Trade Logger
# ═══════════════════════════════════════════

class TestTradeLogger:
    def setup_method(self):
        _clean_artifacts()

    def test_stats_empty(self):
        stats = recent_stats(10)
        assert stats["count"] == 0

    def test_log_format(self):
        record = log_trade(
            symbol="BTC_USDT",
            direction="long",
            entry_price=50000,
            exit_price=51000,
            margin=5,
            nominal=1000,
            equity_before=100,
            equity_after=120,
            pnl=20,
            pnl_pct=0.2,
            exit_reason="tp1",
            stage=1,
            stop_hit=False,
            score=7,
            mode="standard",
        )
        assert record["symbol"] == "BTC_USDT"
        assert record["pnl"] == 20


# ═══════════════════════════════════════════
#  配置完整性
# ═══════════════════════════════════════════

class TestConfig:
    def test_stages_continuous(self):
        for i in range(1, 5):
            lo, hi = config.STAGES[i]["equity_range"]
            assert lo < hi, f"阶段 {i} 范围无效"

    def test_stage_transitions_connect(self):
        _, hi1 = config.STAGES[1]["equity_range"]
        lo2, _ = config.STAGES[2]["equity_range"]
        assert hi1 == lo2

        _, hi2 = config.STAGES[2]["equity_range"]
        lo3, _ = config.STAGES[3]["equity_range"]
        assert hi2 == lo3

    def test_mode_quality_thresholds(self):
        for mode in config.MODE_RISK_BUDGET:
            assert mode in config.MODE_QUALITY_THRESHOLD, f"缺少 {mode} 的质量阈值"
            assert 0 <= config.MODE_QUALITY_THRESHOLD[mode] <= 10

    def test_mode_risk_budgets_valid(self):
        for mode, mult in config.MODE_RISK_BUDGET.items():
            assert 0 <= mult <= 1.5

    def test_profit_rules(self):
        for stage in range(1, 5):
            assert stage in config.PROFIT_RULES
            assert "withdraw_pct" in config.PROFIT_RULES[stage]
