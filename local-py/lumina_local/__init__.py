"""lumina-local — 로컬 전용 자동매매 패키지.

app/ 의 자동매매 로직(지표 → 시그널 → 전략 스펙 → 위험관리 → 체결)을 그대로 옮기되
PostgreSQL · Redis · Celery · FastAPI 의존을 전부 제거하고 표준 라이브러리만 쓴다.
상태는 JSON 파일(data/state.json), 시세 캐시는 data/cache/ 에 둔다.
"""

__all__ = [
    "config", "universe", "indicators", "market_data", "store",
    "risk_guard", "strategy", "aggressive", "broker", "auto_trade", "cli",
]
__version__ = "1.0.0"
