"""Celery 애플리케이션 설정.

브로커/백엔드: Redis (이미 스택에 존재)
워커 실행: celery -A app.celery_app worker --loglevel=info --concurrency=2
Beat  실행: celery -A app.celery_app beat  --loglevel=info
"""
from celery import Celery
from app.config import settings

celery_app = Celery("lumina-invest")

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
            "task": "app.tasks.sync_tasks.sync_market_data",
            "schedule": 3600.0,           # 1시간
            "options": {"expires": 3500},
        },
        "sync-candles-daily": {
            "task": "app.tasks.sync_tasks.sync_stock_candles",
            "schedule": 86400.0,          # 24시간
            "options": {"expires": 82800},
        },
    },
)

# tasks 패키지 자동 탐색
celery_app.autodiscover_tasks(["app.tasks"])
