"""
200x Commander — Gate.io API 认证客户端
=======================================
从环境变量读取 GATE_API_KEY / GATE_API_SECRET，
初始化 gate_api FuturesApi 实例。
"""
import os
import logging

from gate_api import ApiClient, Configuration, FuturesApi

log = logging.getLogger("commander.gate")

GATE_BASE_URL = "https://api.gateio.ws/api/v4"


def init_gate_client() -> FuturesApi:
    """初始化 Gate.io 合约 API 客户端。

    优先读环境变量，其次读 .env 文件。
    返回 FuturesApi 实例，若未配置 API Key 则返回 None。
    """
    key = os.environ.get("GATE_API_KEY", "").strip()
    secret = os.environ.get("GATE_API_SECRET", "").strip()

    # 尝试从 .env 加载
    if not key or not secret:
        _load_dotenv()
        key = os.environ.get("GATE_API_KEY", "").strip()
        secret = os.environ.get("GATE_API_SECRET", "").strip()

    if not key or not secret:
        log.warning("GATE_API_KEY / GATE_API_SECRET 未配置，API 客户端不可用")
        return None

    configuration = Configuration(host=GATE_BASE_URL, key=key, secret=secret)
    client = FuturesApi(ApiClient(configuration))
    log.info("Gate.io API 客户端已初始化")
    return client


def _load_dotenv():
    """加载 .env 文件到环境变量（不覆盖已有值）。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass  # .env 加载失败不影响主流程


def get_account_equity(client: FuturesApi, settle: str = "usdt") -> float:
    """通过 Gate.io API 查询合约账户总权益。

    返回 float，失败返回 0.0。
    """
    try:
        account = client.list_futures_accounts(settle=settle)
        return float(account.total)
    except Exception as e:
        log.warning("查询账户权益失败: %s", e)
        return 0.0


def get_account_state(client: FuturesApi, settle: str = "usdt") -> dict:
    """查询完整账户状态：equity, available, unrealised_pnl, margin, total。

    返回 dict，失败返回空 dict。
    """
    try:
        account = client.list_futures_accounts(settle=settle)
        return {
            "equity": float(account.total),
            "available": float(account.available),
            "unrealised_pnl": float(getattr(account, "unrealised_pnl", 0) or 0),
            "margin": float(getattr(account, "total_margin", 0) or 0),
            "total": float(account.total),
        }
    except Exception as e:
        log.warning("查询账户状态失败: %s", e)
        return {}
