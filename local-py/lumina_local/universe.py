"""퀀트 유니버스 — app/services/stock.py 의 QUANT_STOCKS 를 그대로 옮긴 것.

심볼은 Yahoo 표기(.KS=코스피, .KQ=코스닥). KIS 주문·조회에는 접미사를 떼고 6자리 코드로 보낸다.
종목을 바꾸려면 이 파일만 고치면 된다(로컬 전용이므로 DB 설정이 필요 없다).
"""
from __future__ import annotations

QUANT_SECTORS = ("반도체", "IT", "K뷰티")
QUANT_STOCKS = [
    # ── 반도체 (12) ──
    {"symbol": "005930.KS", "name": "삼성전자", "sector": "반도체"},
    {"symbol": "000660.KS", "name": "SK하이닉스", "sector": "반도체"},
    {"symbol": "042700.KS", "name": "한미반도체", "sector": "반도체"},
    {"symbol": "000990.KS", "name": "DB하이텍", "sector": "반도체"},
    {"symbol": "108320.KS", "name": "LX세미콘", "sector": "반도체"},
    {"symbol": "014680.KS", "name": "한솔케미칼", "sector": "반도체"},
    {"symbol": "058470.KQ", "name": "리노공업", "sector": "반도체"},
    {"symbol": "039030.KQ", "name": "이오테크닉스", "sector": "반도체"},
    {"symbol": "403870.KQ", "name": "HPSP", "sector": "반도체"},
    {"symbol": "240810.KQ", "name": "원익IPS", "sector": "반도체"},
    {"symbol": "036930.KQ", "name": "주성엔지니어링", "sector": "반도체"},
    {"symbol": "357780.KQ", "name": "솔브레인", "sector": "반도체"},
    # ── IT (10) ──
    {"symbol": "035420.KS", "name": "NAVER", "sector": "IT"},
    {"symbol": "035720.KS", "name": "카카오", "sector": "IT"},
    {"symbol": "018260.KS", "name": "삼성에스디에스", "sector": "IT"},
    {"symbol": "064400.KS", "name": "LG CNS", "sector": "IT"},
    {"symbol": "259960.KS", "name": "크래프톤", "sector": "IT"},
    {"symbol": "036570.KS", "name": "엔씨소프트", "sector": "IT"},
    {"symbol": "307950.KS", "name": "현대오토에버", "sector": "IT"},
    {"symbol": "012510.KQ", "name": "더존비즈온", "sector": "IT"},
    {"symbol": "293490.KQ", "name": "카카오게임즈", "sector": "IT"},
    {"symbol": "263750.KQ", "name": "펄어비스", "sector": "IT"},
    # ── K뷰티 (9) ──
    {"symbol": "090430.KS", "name": "아모레퍼시픽", "sector": "K뷰티"},
    {"symbol": "051900.KS", "name": "LG생활건강", "sector": "K뷰티"},
    {"symbol": "192820.KS", "name": "코스맥스", "sector": "K뷰티"},
    {"symbol": "161890.KS", "name": "한국콜마", "sector": "K뷰티"},
    {"symbol": "278470.KS", "name": "에이피알", "sector": "K뷰티"},
    {"symbol": "257720.KQ", "name": "실리콘투", "sector": "K뷰티"},
    {"symbol": "237880.KQ", "name": "클리오", "sector": "K뷰티"},
    {"symbol": "018290.KQ", "name": "브이티", "sector": "K뷰티"},
    {"symbol": "241710.KQ", "name": "코스메카코리아", "sector": "K뷰티"},
]

MARKET_INDICES = [
    {"symbol": "^KS11", "name": "KOSPI"},
    {"symbol": "^KQ11", "name": "KOSDAQ"},
    {"symbol": "KRW=X", "name": "USD/KRW"},
]

STOCK_MAP = {s["symbol"]: s for s in QUANT_STOCKS}


def code_of(symbol: str) -> str:
    """Yahoo 심볼 → KRX 6자리 코드."""
    s = str(symbol).upper()
    for suffix in (".KS", ".KQ"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def name_of(symbol: str) -> str:
    return (STOCK_MAP.get(symbol) or {}).get("name") or symbol


def is_krx(symbol: str) -> bool:
    s = str(symbol or "").upper()
    return s.endswith(".KS") or s.endswith(".KQ") or (len(s) == 6 and s.isdigit())
