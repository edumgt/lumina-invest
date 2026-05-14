"""메리츠증권 클라이언트 (REST 출시 예정 안내용)."""

from .base import BrokerClient


class MeritzClient(BrokerClient):
    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret

    def _raise_not_ready(self):
        raise RuntimeError(
            "메리츠증권 REST API는 현재 출시 준비 중입니다. "
            "공식 API 오픈 이후 연동을 활성화하세요."
        )

    async def get_token(self):
        self._raise_not_ready()

    async def get_price(self, symbol: str):
        self._raise_not_ready()

    async def get_balance(self, account_no: str):
        self._raise_not_ready()

    async def place_order(self, account_no: str, symbol: str, side: str, quantity: int, price: float):
        self._raise_not_ready()

    async def get_daily_ohlcv(self, symbol: str, start: str, end: str):
        self._raise_not_ready()

