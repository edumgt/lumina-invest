"""KIS 자격증명 서버 관리: Secrets Manager 조회·캐시·폴백, 화면용 status 에 키 원문이 없는지, 라우트가 DB 에 키를 남기지 않는지."""
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import settings
from app.services import kis_credentials as kc


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in ("KIS_SECRETS_NAME", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"):
        monkeypatch.setattr(settings, k, "")
    monkeypatch.setattr(settings, "KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "KIS_SECRETS_CACHE_TTL", 600)
    kc.invalidate()
    yield
    kc.invalidate()


def test_not_configured_returns_none_and_status_without_keys():
    assert kc.is_configured() is False
    assert kc.resolve() is None
    st = asyncio.run(kc.get_status())
    assert st["configured"] is False and st["managed"] is True and st["source"] is None
    assert "app_key" not in st and "app_secret" not in st


def test_secrets_manager_json_is_parsed_and_cached(monkeypatch):
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "lumina-invest/prod/kis")
    calls = []

    def fake_fetch(name):
        calls.append(name)
        return {"KIS_APP_KEY": "PSabc", "app_secret": "sec", "account_no": "5012345601", "environment": "paper"}

    with patch.object(kc, "_fetch_secret_json", side_effect=fake_fetch):
        c1 = kc.resolve()
        c2 = kc.resolve()
    assert c1 == c2 and c1.app_key == "PSabc" and c1.paper is True and c1.source == "secrets-manager"
    assert calls == ["lumina-invest/prod/kis"]          # 두 번째는 캐시
    st = kc.status(c1)
    assert st["configured"] and st["account_masked"] == "5012****01" and st["environment"] == "paper"
    assert "PSabc" not in json.dumps(st) and "sec" not in st.values()


def test_secret_environment_real_sets_paper_false(monkeypatch):
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "x")
    with patch.object(kc, "_fetch_secret_json", return_value={"app_key": "a", "app_secret": "b", "environment": "real"}):
        c = kc.resolve()
    assert c.paper is False and c.environment == "real" and c.account_no == ""


def test_secrets_failure_falls_back_to_env_and_reports_error(monkeypatch):
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "missing")
    monkeypatch.setattr(settings, "KIS_APP_KEY", "envkey")
    monkeypatch.setattr(settings, "KIS_APP_SECRET", "envsec")
    monkeypatch.setattr(settings, "KIS_ACCOUNT_NO", "1234567890")
    with patch.object(kc, "_fetch_secret_json", side_effect=RuntimeError("AccessDeniedException")):
        c = kc.resolve()
    assert c is not None and c.source == "env" and c.app_key == "envkey"
    st = kc.status(c)
    assert st["configured"] and st["source"] == "env" and "AccessDeniedException" in st["error"]


def test_secrets_failure_without_fallback_is_negative_cached(monkeypatch):
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "missing")
    with patch.object(kc, "_fetch_secret_json", side_effect=RuntimeError("boom")) as m:
        assert kc.resolve() is None
        assert kc.resolve() is None
        assert m.call_count == 1                         # 60초 네거티브 캐시
        assert kc.resolve(force=True) is None and m.call_count == 2


def test_mask_account():
    assert kc.mask_account("") == ""
    assert kc.mask_account("123456") == "******"
    assert kc.mask_account("5012345601") == "5012****01"


# ── 라우트: KIS 는 사용자가 보낸 키를 저장하지 않고 Secrets Manager 값을 쓴다 ─────────
from app.routes import stocks as stocks_routes  # noqa: E402


def test_apply_credentials_drops_user_keys_for_kis_and_keeps_for_others():
    row = SimpleNamespace(app_key="old", app_secret="old", account_no="old")
    stocks_routes._apply_credentials(row, "kis", "userkey", "usersec", "999")
    assert (row.app_key, row.app_secret, row.account_no) == ("", "", "")
    stocks_routes._apply_credentials(row, "kb", "userkey", "usersec", "999")
    assert (row.app_key, row.app_secret, row.account_no) == ("userkey", "usersec", "999")


def test_resolve_credentials_uses_secrets_manager_for_kis(monkeypatch):
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "x")
    row = SimpleNamespace(broker="kis", app_key="", app_secret="", account_no="", paper=True)
    with patch.object(kc, "_fetch_secret_json", return_value={"app_key": "smk", "app_secret": "sms", "account_no": "5012345601"}):
        broker, k, s, acct, paper = asyncio.run(stocks_routes._resolve_credentials(row))
        view = asyncio.run(stocks_routes._connection_view(row))
    assert (broker, k, s, acct, paper) == ("kis", "smk", "sms", "5012345601", True)
    assert view["connected"] is True and view["app_key"] == "" and view["account_no"] == "5012****01"
    assert view["kis_managed"]["source"] == "secrets-manager"


def test_resolve_credentials_kis_unconfigured_yields_mock_client():
    row = SimpleNamespace(broker="kis", app_key="leftover", app_secret="leftover", account_no="1", paper=True)
    broker, k, s, acct, paper = asyncio.run(stocks_routes._resolve_credentials(row))
    assert broker == "kis" and k == "" and s == ""                   # DB 잔존 키는 쓰지 않는다
    view = asyncio.run(stocks_routes._connection_view(row))
    assert view["connected"] is False and view["kis_managed"]["configured"] is False


def test_resolve_credentials_non_managed_uses_row():
    row = SimpleNamespace(broker="kb", app_key="k", app_secret="s", account_no="a", paper=False)
    assert asyncio.run(stocks_routes._resolve_credentials(row)) == ("kb", "k", "s", "a", False)
    assert asyncio.run(stocks_routes._resolve_credentials(None)) == ("mock", "", "", "", True)
