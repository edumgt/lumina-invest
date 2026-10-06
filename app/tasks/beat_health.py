"""celery-beat / worker 생존 확인.

beat 가 60초마다 ``beat.heartbeat`` 를 보내고 worker 가 실행하면 Redis 키 ``celery:beat:heartbeat`` 에 UTC 시각이 적힌다.
키가 ``MAX_AGE_SEC`` 보다 오래됐으면 beat 가 보내지 않거나 worker 가 받지 않는 것이다.
``python -m app.tasks.beat_health`` 는 Docker healthcheck 용 CLI: 정상 0, 비정상 1.
2026-10-06 운영에서 beat 가 첫 사이클 뒤 30분 이상 due task 를 보내지 않은 사고(수동 restart 로 복구) 뒤 추가.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

HEARTBEAT_KEY = "celery:beat:heartbeat"
MAX_AGE_SEC = 180          # heartbeat 주기 60초의 3배
GRACE_SEC = 240            # 컨테이너 기동 직후 키가 없을 수 있는 유예


def _redis_url() -> str:
    from app.config import settings
    return settings.REDIS_URL


def write_heartbeat(now: datetime | None = None) -> str:
    import redis
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    r = redis.Redis.from_url(_redis_url(), socket_timeout=5)
    r.set(HEARTBEAT_KEY, stamp, ex=MAX_AGE_SEC * 4)
    return stamp


def heartbeat_age_sec(now: datetime | None = None) -> float | None:
    """마지막 heartbeat 이후 경과 초. 키가 없으면 None."""
    import redis
    r = redis.Redis.from_url(_redis_url(), socket_timeout=5)
    raw = r.get(HEARTBEAT_KEY)
    if not raw:
        return None
    last = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - last).total_seconds()


def check(age: float | None, uptime_sec: float, max_age: float = MAX_AGE_SEC, grace: float = GRACE_SEC) -> tuple[bool, str]:
    """healthcheck 판정. 기동 직후(uptime < grace)에는 키가 없어도 정상."""
    if age is None:
        return (uptime_sec < grace, f"heartbeat 없음 (uptime {uptime_sec:.0f}s)")
    return (age <= max_age, f"heartbeat {age:.0f}s 전")


def _uptime_sec() -> float:
    try:
        with open("/proc/1/stat") as f:
            start_ticks = int(f.read().split()[21])
        import os
        with open("/proc/uptime") as f:
            sys_uptime = float(f.read().split()[0])
        return max(0.0, sys_uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except Exception:
        return GRACE_SEC + 1   # 알 수 없으면 유예 없이 판정


def main() -> int:
    try:
        ok, msg = check(heartbeat_age_sec(), _uptime_sec())
    except Exception as exc:
        ok, msg = False, f"redis 조회 실패: {exc}"
    print(("OK " if ok else "UNHEALTHY ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
