"""증권사 클라이언트 팩토리."""
from .base import BrokerClient
from .kis import KISClient
from .ebest import EBestClient
from .kiwoom import KiwoomClient
from .daishin import DaishinClient
from .meritz import MeritzClient
from .shinhan import ShinhanClient
from .mock import MockBrokerClient


def get_broker_client(
    broker: str,
    app_key: str = "",
    app_secret: str = "",
    paper: bool = True,
) -> BrokerClient:
    """
    broker: "kis" | "kiwoom" | "daishin" | "meritz" | "shinhan" | "ebest" | "mock"
    key/secret이 없으면 자동으로 MockBrokerClient 반환.
    """
    broker = (broker or "mock").lower()
    if not app_key or not app_secret:
        return MockBrokerClient()
    if broker == "kis":
        return KISClient(app_key, app_secret, paper=paper)
    if broker == "kiwoom":
        return KiwoomClient(app_key, app_secret)
    if broker == "daishin":
        return DaishinClient(app_key, app_secret)
    if broker == "meritz":
        return MeritzClient(app_key, app_secret)
    if broker == "shinhan":
        return ShinhanClient(app_key, app_secret)
    if broker == "ebest":
        return EBestClient(app_key, app_secret)
    return MockBrokerClient()
