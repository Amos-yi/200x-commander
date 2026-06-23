"""Unit tests for LeadTrader auto-enable copy trading in PLAN C."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import Mock, patch, call, ANY

from lead_trader import LeadTrader, load_copy_trading_config

# ── 辅助 ──────────────────────────────────────────

def _fake_config(auto_threshold=300.0):
    return {
        "enabled": False,
        "auto_enable_equity_threshold": auto_threshold,
        "profit_share_ratio": 0.10,
        "min_copy_amount": 10.0,
        "max_copy_amount": 10000.0,
        "max_daily_trades": 3,
        "avoid_high_freq": True,
        "min_interval_seconds": 300,
    }

def _mock_client():
    """返回一个 mock gate_api FuturesApi 客户端。"""
    return Mock()


# ── 不达标时不触发 ────────────────────────────────────

def test_equity_below_threshold_does_nothing():
    lt = LeadTrader(None, _fake_config(300))
    lt._configured = False
    lt.get_account_equity = Mock(return_value=150.0)

    result = lt.check_and_auto_enable()
    assert result is None
    assert lt.enabled is False
    assert lt.auto_already_fired is False


# ── 达标触发 ─────────────────────────────────────────

def test_equity_above_threshold_auto_enables():
    client = _mock_client()
    lt = LeadTrader(client, _fake_config(300))
    lt._configured = True
    lt.get_account_equity = Mock(return_value=320.0)
    lt._try_register_leader = Mock(return_value=(True, "OK"))
    lt._persist_enabled = Mock(return_value=True)

    result = lt.check_and_auto_enable()
    assert result is not None
    assert "320" in result
    assert lt.enabled is True
    assert lt.auto_already_fired is True
    assert lt._try_register_leader.called


# ── 不重复触发 ────────────────────────────────────────

def test_does_not_fire_twice():
    client = _mock_client()
    lt = LeadTrader(client, _fake_config(300))
    lt._configured = True
    lt.get_account_equity = Mock(return_value=500.0)
    lt._try_register_leader = Mock(return_value=(True, "OK"))
    lt._persist_enabled = Mock(return_value=True)

    result1 = lt.check_and_auto_enable()
    assert result1 is not None
    assert lt.auto_already_fired is True

    result2 = lt.check_and_auto_enable()
    assert result2 is None
    assert lt._try_register_leader.call_count == 1


# ── API 未配置时不触发 ────────────────────────────────

def test_no_api_key_skips_check():
    lt = LeadTrader(None, _fake_config(300))
    lt._configured = False
    lt.get_account_equity = Mock(return_value=500.0)

    result = lt.check_and_auto_enable()
    assert result is None
    assert lt.enabled is False


# ── 阈值可配置 ────────────────────────────────────────

def test_custom_threshold_respected():
    client = _mock_client()
    lt = LeadTrader(client, _fake_config(500))
    lt._configured = True
    lt.get_account_equity = Mock(return_value=400.0)

    result = lt.check_and_auto_enable()
    assert result is None  # 400 < 500

    lt.get_account_equity = Mock(return_value=510.0)
    lt._try_register_leader = Mock(return_value=(True, "OK"))
    lt._persist_enabled = Mock(return_value=True)
    result = lt.check_and_auto_enable()
    assert result is not None
    assert "510" in result


# ── 权益查询缓存 ─────────────────────────────────────

def test_equity_cache_used_within_60s():
    client = _mock_client()
    client.list_futures_accounts = Mock()
    client.list_futures_accounts.return_value.total = 350.5

    lt = LeadTrader(client, _fake_config(300))
    lt._configured = True

    eq1 = lt.get_account_equity()
    assert eq1 == 350.5
    assert client.list_futures_accounts.call_count == 1

    # Second call within 60s should use cache
    eq2 = lt.get_account_equity()
    assert eq2 == 350.5
    assert client.list_futures_accounts.call_count == 1

    # Force refresh
    eq3 = lt.get_account_equity(force=True)
    assert eq3 == 350.5
    assert client.list_futures_accounts.call_count == 2


# ── 通知注入 ─────────────────────────────────────────

def test_set_notifier_stores_reference():
    lt = LeadTrader(None, _fake_config(300))
    assert lt._notifier is None

    mock_notifier = Mock()
    lt.set_notifier(mock_notifier)
    assert lt._notifier is mock_notifier


# ── 通知触发时发送 PushPlus ─────────────────────────

def test_notifier_called_on_auto_enable():
    client = _mock_client()
    lt = LeadTrader(client, _fake_config(300))
    lt._configured = True
    lt.get_account_equity = Mock(return_value=350.0)
    lt._try_register_leader = Mock(return_value=(True, "OK"))
    lt._persist_enabled = Mock(return_value=True)

    mock_notifier = Mock()
    lt.set_notifier(mock_notifier)

    result = lt.check_and_auto_enable()
    assert result is not None
    mock_notifier.assert_called_once()
    call_title = mock_notifier.call_args[0][0]
    assert "350" in call_title
