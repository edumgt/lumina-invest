"""브로커 클라이언트 — 한국투자증권(KIS) Open API 직접 호출 + 로그 전용 Mock.

app/services/brokers/kis.py 를 동기(urllib) 버전으로 옮겼다. 게이트웨이(stock-coin-trade)·
Secrets Manager 경유 경로는 서버 전용이라 빼고, .env 의 키로 직접 호출한다.

  모의투자: https://openapivts.koreainvestment.com:29443  (KIS_PAPER=true)
  실전투자: https://openapi.koreainvestment.com:9443       (KIS_PAPER=false)

접근토큰은 data/kis_token.json 에 캐시한다 — KIS 는 토큰 발급 호출 수를 제한하므로
프로세스를 다시 띄워도 유효기간(기본 24h) 안에는 재사용한다.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .market_data import MarketDataError, http_json
from .universe import code_of, name_of

logger = logging.getLogger(__name__)

REAL_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"
KST = timezone(timedelta(hours=9))

_PERIOD_DAYS = {"1mo": 31, "3mo": 93, "6mo": 186, "1y": 400, "2y": 800, "5y": 1900, "10y": 3800}


class BrokerError(Exception):
    pass


class KISClient:
    """KIS Open API 동기 클라이언트(현재가 · 일봉 · 잔고 · 주문)."""

    def __init__(self, app_key: str, app_secret: str, account_no: str,
                 paper: bool = True, token_path: Path | None = None, timeout: float = 15.0):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = (account_no or "").replace("-", "")
        self.paper = paper
        self.base_url = PAPER_URL if paper else REAL_URL
        self.timeout = timeout
        self.token_path = token_path
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ── 토큰 ────────────────────────────────────────────────────────────
    def _load_cached_token(self) -> None:
        if not self.token_path or not self.token_path.exists():
            return
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if data.get("base_url") == self.base_url and float(data.get("expires_at", 0)) > time.time() + 60:
            self._token = data.get("access_token")
            self._token_exp = float(data["expires_at"])

    def _store_token(self, token: str, expires_in: int) -> None:
        self._token = token
        self._token_exp = time.time() + max(60, int(expires_in) - 300)   # 5분 여유
        if not self.token_path:
            return
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"access_token": token, "expires_at": self._token_exp,
                                   "base_url": self.base_url}), encoding="utf-8")
        tmp.replace(self.token_path)
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass

    def token(self) -> str:
        if self._token and self._token_exp > time.time():
            return self._token
        self._load_cached_token()
        if self._token and self._token_exp > time.time():
            return self._token
        try:
            data = http_json(f"{self.base_url}/oauth2/tokenP", method="POST", timeout=self.timeout,
                             headers={"content-type": "application/json; charset=utf-8"},
                             body={"grant_type": "client_credentials", "appkey": self.app_key,
                                   "appsecret": self.app_secret})
        except MarketDataError as exc:
            raise BrokerError(f"KIS 토큰 발급 실패: {exc}") from exc
        if not data.get("access_token"):
            raise BrokerError(f"KIS 토큰 응답에 access_token 없음: {str(data)[:200]}")
        self._store_token(data["access_token"], data.get("expires_in", 86_400))
        return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    @property
    def cano(self) -> str:
        return self.account_no[:8]

    @property
    def acnt_prdt_cd(self) -> str:
        return self.account_no[8:] or "01"

    # ── 시세 ────────────────────────────────────────────────────────────
    def quote(self, symbol: str) -> dict:
        data = http_json(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code_of(symbol)},
                         headers=self._headers("FHKST01010100"), timeout=self.timeout)
        out = data.get("output") or {}
        if not out.get("stck_prpr"):
            raise BrokerError(f"KIS 현재가 응답 비정상 {symbol}: {str(data)[:200]}")
        return {"symbol": symbol, "name": out.get("hts_kor_isnm") or name_of(symbol),
                "price": float(out["stck_prpr"]), "open": float(out.get("stck_oprc") or 0),
                "high": float(out.get("stck_hgpr") or 0), "low": float(out.get("stck_lwpr") or 0),
                "volume": int(out.get("acml_vol") or 0),
                "change_pct": float(out.get("prdy_ctrt") or 0), "source": "kis"}

    def daily_candles(self, symbol: str, period: str = "2y") -> dict:
        """일봉. KIS 는 한 번에 최대 100영업일을 주므로 기간만큼 나눠 받아 과거→현재 순으로 합친다."""
        days = _PERIOD_DAYS.get(period, 400)
        end = datetime.now(KST).date()
        start_limit = end - timedelta(days=days)
        rows: dict[str, dict] = {}
        cursor = end
        for _ in range(20):                       # 100영업일 × 20 ≈ 8년
            if cursor < start_limit:
                break
            window_start = max(start_limit, cursor - timedelta(days=140))
            data = http_json(f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                             {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code_of(symbol),
                              "FID_INPUT_DATE_1": window_start.strftime("%Y%m%d"),
                              "FID_INPUT_DATE_2": cursor.strftime("%Y%m%d"),
                              "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
                             headers=self._headers("FHKST03010100"), timeout=self.timeout)
            chunk = [r for r in (data.get("output2") or []) if r.get("stck_bsop_date") and r.get("stck_clpr")]
            if not chunk:
                break
            for r in chunk:
                date_str = r["stck_bsop_date"]
                ts = int(datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=KST).timestamp())
                rows[date_str] = {"time": ts, "open": float(r.get("stck_oprc") or 0),
                                  "high": float(r.get("stck_hgpr") or 0), "low": float(r.get("stck_lwpr") or 0),
                                  "close": float(r["stck_clpr"]), "volume": int(float(r.get("acml_vol") or 0))}
            oldest = min(chunk, key=lambda r: r["stck_bsop_date"])["stck_bsop_date"]
            cursor = datetime.strptime(oldest, "%Y%m%d").date() - timedelta(days=1)
        candles = [rows[k] for k in sorted(rows)]
        return {"symbol": symbol, "interval": "1d", "period": period, "candles": candles, "source": "kis"}

    # ── 잔고 ────────────────────────────────────────────────────────────
    def balance(self) -> dict:
        tr_id = "VTTC8434R" if self.paper else "TTTC8434R"
        data = http_json(f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "AFHR_FLPR_YN": "N",
                          "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                          "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
                          "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
                         headers=self._headers(tr_id), timeout=self.timeout)
        holdings = []
        for h in data.get("output1") or []:
            qty = int(float(h.get("hldg_qty") or 0))
            if qty <= 0:
                continue
            holdings.append({"symbol": h.get("pdno", ""), "name": h.get("prdt_name", ""), "quantity": qty,
                             "avg_price": float(h.get("pchs_avg_pric") or 0),
                             "current_price": float(h.get("prpr") or 0),
                             "eval_amount": float(h.get("evlu_amt") or 0),
                             "gain_loss": float(h.get("evlu_pfls_amt") or 0),
                             "gain_pct": float(h.get("evlu_pfls_rt") or 0)})
        summary = (data.get("output2") or [{}])[0]
        return {"total_eval": float(summary.get("tot_evlu_amt") or 0),
                "total_buy": float(summary.get("pchs_amt_smtl_amt") or 0),
                "total_gain": float(summary.get("evlu_pfls_smtl_amt") or 0),
                "cash": float(summary.get("dnca_tot_amt") or 0),
                "holdings": holdings, "environment": "paper" if self.paper else "real"}

    # ── 주문 ────────────────────────────────────────────────────────────
    def place_order(self, symbol: str, side: str, quantity: int, price: float,
                    order_type: str = "LIMIT") -> dict:
        """현금 매수/매도. order_type MARKET 이면 시장가(ORD_DVSN=01, 단가 0)로 보낸다."""
        if side not in ("buy", "sell"):
            raise BrokerError(f"알 수 없는 주문 방향: {side}")
        tr_id = ("VTTC0802U" if self.paper else "TTTC0802U") if side == "buy" else \
                ("VTTC0801U" if self.paper else "TTTC0801U")
        market = str(order_type).upper() == "MARKET"
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "PDNO": code_of(symbol),
                "ORD_DVSN": "01" if market else "00",
                "ORD_QTY": str(int(quantity)),
                "ORD_UNPR": "0" if market else str(int(price))}
        try:
            data = http_json(f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash", method="POST",
                             headers=self._headers(tr_id), body=body, timeout=self.timeout)
        except MarketDataError as exc:
            raise BrokerError(f"KIS 주문 전송 실패: {exc}") from exc
        if str(data.get("rt_cd")) != "0":
            raise BrokerError(f"KIS 주문 거부 [{data.get('msg_cd')}] {data.get('msg1')}")
        out = data.get("output") or {}
        return {"status": "submitted", "broker": "kis",
                "environment": "paper" if self.paper else "real",
                "order_no": out.get("ODNO") or out.get("ORNO"),
                "order_type": "MARKET" if market else "LIMIT",
                "message": data.get("msg1", ""), "response": data}


class MockBroker:
    """실주문 대신 로그만 남기는 브로커 — live 모드 동작을 키 없이 확인할 때 쓴다."""

    environment = "mock"

    def place_order(self, symbol: str, side: str, quantity: int, price: float,
                    order_type: str = "LIMIT") -> dict:
        logger.info("[MOCK 주문] %s %s %d주 @%s (%s)", side.upper(), symbol, quantity, f"{price:,.0f}", order_type)
        return {"status": "submitted", "broker": "mock", "environment": "mock",
                "order_no": f"MOCK{int(time.time())}", "order_type": order_type,
                "message": f"[MOCK] {side.upper()} {symbol} {quantity}주 @{price:,.0f}"}

    def balance(self) -> dict:
        return {"total_eval": 0.0, "total_buy": 0.0, "total_gain": 0.0, "cash": 0.0,
                "holdings": [], "environment": "mock"}


def build_broker(settings) -> KISClient | MockBroker | None:
    """live 모드에서 쓸 브로커. 키가 없으면 None(가상 장부만 기록)."""
    if settings.mode != "live":
        return None
    if settings.kis_configured():
        return KISClient(settings.kis_app_key, settings.kis_app_secret, settings.kis_account_no,
                         paper=settings.kis_paper, token_path=settings.data_dir / "kis_token.json",
                         timeout=settings.http_timeout)
    logger.warning("live 모드지만 KIS 키가 없습니다 — 실주문 대신 MOCK 브로커를 씁니다 (.env 의 KIS_APP_KEY 등 확인)")
    return MockBroker()


def build_market_client(settings) -> KISClient | None:
    """시세 소스가 kis 일 때 쓸 조회용 클라이언트(주문 권한과 무관)."""
    if settings.market_data_source != "kis" or not (settings.kis_app_key and settings.kis_app_secret):
        return None
    return KISClient(settings.kis_app_key, settings.kis_app_secret, settings.kis_account_no,
                     paper=settings.kis_paper, token_path=settings.data_dir / "kis_token.json",
                     timeout=settings.http_timeout)
