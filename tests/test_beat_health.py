"""beat heartbeat / healthcheck 판정."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.celery_app import celery_app
from app.tasks import beat_health as bh


def test_check_logic():
    assert bh.check(None, uptime_sec=10)[0] is True            # 기동 직후 키 없음 → 유예
    assert bh.check(None, uptime_sec=1000)[0] is False         # 오래 떠 있는데 키 없음 → 비정상
    assert bh.check(30, uptime_sec=1000)[0] is True
    assert bh.check(bh.MAX_AGE_SEC + 1, uptime_sec=1000)[0] is False


def test_write_and_age_roundtrip():
    store = {}
    fake = MagicMock()
    fake.set.side_effect = lambda k, v, ex=None: store.__setitem__(k, v.encode())
    fake.get.side_effect = lambda k: store.get(k)
    with patch("redis.Redis.from_url", return_value=fake):
        t0 = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
        bh.write_heartbeat(t0)
        assert bh.heartbeat_age_sec(t0 + timedelta(seconds=90)) == 90
        store.clear()
        assert bh.heartbeat_age_sec() is None
    assert fake.set.call_args.kwargs["ex"] == bh.MAX_AGE_SEC * 4


def test_heartbeat_task_registered_and_scheduled():
    import app.tasks.sync_tasks  # noqa: F401
    assert "beat.heartbeat" in celery_app.tasks
    entry = celery_app.conf.beat_schedule["beat-heartbeat-1min"]
    assert entry["task"] == "beat.heartbeat" and entry["schedule"] == 60.0 and entry["options"]["expires"] < 60


def test_main_exit_codes():
    with patch.object(bh, "heartbeat_age_sec", return_value=10), patch.object(bh, "_uptime_sec", return_value=999):
        assert bh.main() == 0
    with patch.object(bh, "heartbeat_age_sec", side_effect=RuntimeError("down")):
        assert bh.main() == 1
