"""Celery 애플리케이션 설정.

브로커/백엔드: Redis (이미 스택에 존재)
워커 실행: celery -A app.celery_app worker --loglevel=info --concurrency=2
Beat  실행: celery -A app.celery_app beat  --loglevel=info
"""
from celery import Celery
from app.config import settings

celery_app = Celery(
    "lumina-invest",
    # autodiscover_tasks(["app.tasks"])는 app.tasks.tasks 모듈을 찾기 때문에 실제로는 아무 태스크도
    # 등록되지 않았다(워커 [tasks] 목록이 비어 Beat 스케줄·/async 엔드포인트가 모두 대기 상태).
    # 태스크 모듈을 명시적으로 include 한다.
    include=["app.tasks.sync_tasks", "app.tasks.ingest_tasks", "app.tasks.agent_tasks"],
)

celery_app.conf.update(
    # ── 브로커 / 백엔드 ─────────────────────────────────────────────────────────
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,

    # ── 직렬화 ──────────────────────────────────────────────────────────────────
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # ── 시간대 ──────────────────────────────────────────────────────────────────
    timezone="Asia/Seoul",
    enable_utc=True,

    # ── 태스크 동작 ─────────────────────────────────────────────────────────────
    task_track_started=True,       # STARTED 상태 기록 (폴링용)
    task_acks_late=True,           # 완료 후 ACK → 워커 crash 시 재큐
    worker_prefetch_multiplier=1,  # LLM 태스크가 무거우므로 1:1 처리
    result_expires=3600,           # 결과 1시간 보관

    # ── Celery Beat 주기 스케줄 ────────────────────────────────────────────────
    beat_schedule={
        "sync-market-data-hourly": {
            "task": "sync.market_data",
            "schedule": 3600.0,           # 1시간
            "options": {"expires": 3500},
        },
        "sync-candles-daily": {
            "task": "sync.stock_candles",
            "schedule": 86400.0,          # 24시간
            "options": {"expires": 82800},
        },
        "quant-reconcile": {
            "task": "quant.reconcile",
            "schedule": float(settings.RECONCILE_INTERVAL_SEC),   # 기본 10분 — 로그 vs KIS 실거래 정합성
            "options": {"expires": max(60, int(settings.RECONCILE_INTERVAL_SEC) - 30)},
        },
        "beat-heartbeat-1min": {
            "task": "beat.heartbeat",
            "schedule": 60.0,             # 1분 — beat/worker 생존 신호 (healthcheck → autoheal 재시작)
            "options": {"expires": 50},
        },
        "quant-auto-trade-cycle": {
            "task": "quant.auto_trade_cycle",
            "schedule": float(settings.QUANT_CYCLE_SEC),                       # 기본 3분 — 자동매매 활성 사용자 사이클 (2026-10-07, 5분→3분)
            "options": {"expires": max(30, int(settings.QUANT_CYCLE_SEC) - 20)},  # 다음 주기 전에 만료 — 지연된 사이클이 겹쳐 실행되지 않게
        },
        "quant-confirm-fills-2min": {
            "task": "quant.confirm_fills",
            "schedule": 120.0,            # 2분 — 게이트웨이 경유 KIS 실주문의 체결 확인 (live_orders)
            "options": {"expires": 110},
        },
        "rebalance-check-hourly": {
            "task": "rebalance.check_triggers",
            "schedule": 3600.0,           # 1시간 — 시간·이탈률 리밸런싱 트리거 점검
            "options": {"expires": 3500},
        },
    },
)

# tasks 패키지 자동 탐색
celery_app.autodiscover_tasks(["app.tasks"])
