"""KIS(한국투자증권) 자격증명 — 서버가 AWS Secrets Manager 에서 읽어 관리한다.

사용자는 종목 선정 화면에서 증권사로 KIS 를 고르기만 하고 App Key / App Secret / 계좌번호를
입력하지 않는다. 화면에는 연동 여부(configured)·계좌 마스킹·환경(paper/real)만 내려간다.

우선순위
  1. Secrets Manager  : KIS_SECRETS_NAME 이 설정되어 있으면 GetSecretValue (JSON 문자열)
  2. 환경변수 폴백    : KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO (로컬 개발·장애 시)
  3. 둘 다 없으면 None → 호출부는 MockBrokerClient 로 동작

Secret JSON 예
  {"app_key": "...", "app_secret": "...", "account_no": "5012345601", "environment": "paper"}
  (environment 생략 시 KIS_ENVIRONMENT 설정값. 키 이름은 대소문자/접두사 KIS_ 를 허용한다)

성공값은 KIS_SECRETS_CACHE_TTL 초, 실패는 _NEGATIVE_TTL 초 캐시해 매 요청마다 AWS 를 호출하지 않는다.
boto3 미설치·IAM 권한 없음·시크릿 없음은 예외를 삼키고 status() 의 error 에 요약만 남긴다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

MANAGED_BROKERS = frozenset({"kis"})
_NEGATIVE_TTL = 60.0
_ALIASES = {
    "app_key": ("app_key", "appkey", "kis_app_key", "APP_KEY", "KIS_APP_KEY", "appKey"),
    "app_secret": ("app_secret", "appsecret", "kis_app_secret", "APP_SECRET", "KIS_APP_SECRET", "appSecret"),
    "account_no": ("account_no", "account", "cano", "kis_account_no", "ACCOUNT_NO", "KIS_ACCOUNT_NO", "accountNo"),
    "environment": ("environment", "env", "kis_environment", "ENVIRONMENT", "KIS_ENVIRONMENT"),
}


@dataclass(frozen=True)
class KISCredentials:
    app_key: str
    app_secret: str
    account_no: str
    paper: bool
    source: str  # "secrets-manager" | "env"

    @property
    def environment(self) -> str:
        return "paper" if self.paper else "real"


_lock = threading.Lock()
_cached: KISCredentials | None = None
_cached_at: float = 0.0
_cached_error: str = ""
_cached_error_at: float = 0.0


def is_managed(broker: str | None) -> bool:
    """이 증권사의 자격증명을 서버(Secrets Manager)가 관리하는가."""
    return (broker or "").strip().lower() in MANAGED_BROKERS


def is_configured() -> bool:
    """자격증명 소스가 설정되어 있는가 (실제 조회 성공 여부는 resolve 로 확인)."""
    return bool(settings.KIS_SECRETS_NAME or (settings.KIS_APP_KEY and settings.KIS_APP_SECRET))


def invalidate() -> None:
    global _cached, _cached_at, _cached_error, _cached_error_at
    with _lock:
        _cached = None
        _cached_at = 0.0
        _cached_error = ""
        _cached_error_at = 0.0


def _pick(data: dict, field: str) -> str:
    for key in _ALIASES[field]:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _paper_from(env_value: str) -> bool:
    env = (env_value or settings.KIS_ENVIRONMENT or "paper").strip().lower()
    return env not in ("real", "live", "prod", "production")


def _fetch_secret_json(name: str) -> dict:
    import boto3  # 지연 import: 로컬 개발 환경에 boto3 가 없어도 앱은 뜬다

    client = boto3.client("secretsmanager", region_name=settings.AWS_REGION)
    resp = client.get_secret_value(SecretId=name)
    raw = resp.get("SecretString")
    if not raw and resp.get("SecretBinary"):
        raw = base64.b64decode(resp["SecretBinary"]).decode("utf-8")
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("secret 값이 JSON 객체가 아닙니다")
    return data


def _from_env() -> KISCredentials | None:
    if settings.KIS_APP_KEY and settings.KIS_APP_SECRET:
        return KISCredentials(
            app_key=settings.KIS_APP_KEY, app_secret=settings.KIS_APP_SECRET,
            account_no=settings.KIS_ACCOUNT_NO or "", paper=_paper_from(""), source="env",
        )
    return None


def _load() -> tuple[KISCredentials | None, str]:
    name = (settings.KIS_SECRETS_NAME or "").strip()
    if name:
        try:
            data = _fetch_secret_json(name)
            app_key, app_secret = _pick(data, "app_key"), _pick(data, "app_secret")
            if not app_key or not app_secret:
                raise ValueError("secret 에 app_key/app_secret 이 없습니다")
            return KISCredentials(
                app_key=app_key, app_secret=app_secret, account_no=_pick(data, "account_no"),
                paper=_paper_from(_pick(data, "environment")), source="secrets-manager",
            ), ""
        except Exception as e:  # noqa: BLE001 — 자격증명 문제는 종류가 많고 모두 "미연동"으로 취급
            err = f"{type(e).__name__}: {str(e)[:160]}"
            logger.warning("KIS Secrets Manager 조회 실패 (%s): %s", name, err)
            fallback = _from_env()
            if fallback:
                return fallback, err
            return None, err
    fallback = _from_env()
    return fallback, ("" if fallback else "KIS_SECRETS_NAME / KIS_APP_KEY 미설정")


def resolve(force: bool = False) -> KISCredentials | None:
    """캐시된 자격증명을 돌려준다. 동기(blocking) — 비동기 경로는 get_credentials() 사용."""
    global _cached, _cached_at, _cached_error, _cached_error_at
    now = time.monotonic()
    with _lock:
        if not force:
            if _cached and now - _cached_at < settings.KIS_SECRETS_CACHE_TTL:
                return _cached
            if _cached is None and _cached_error and now - _cached_error_at < _NEGATIVE_TTL:
                return None
        creds, err = _load()
        _cached, _cached_at = creds, now
        _cached_error, _cached_error_at = err, now
        return creds


async def get_credentials(force: bool = False) -> KISCredentials | None:
    return await asyncio.to_thread(resolve, force)


def mask_account(account_no: str) -> str:
    """앞 4자리·뒤 2자리만 남긴다 (예: 5012****01). 짧으면 전부 가린다."""
    if not account_no:
        return ""
    if len(account_no) <= 6:
        return "*" * len(account_no)
    return account_no[:4] + "*" * (len(account_no) - 6) + account_no[-2:]


def status(creds: KISCredentials | None, *, resolved: bool = True) -> dict:
    """화면 표시용. 키 원문은 절대 포함하지 않는다."""
    return {
        "managed": True,
        "configured": creds is not None,
        "source": creds.source if creds else ("secrets-manager" if settings.KIS_SECRETS_NAME else ("env" if settings.KIS_APP_KEY else None)),
        "secret_name": settings.KIS_SECRETS_NAME or "",
        "account_masked": mask_account(creds.account_no) if creds else "",
        "has_account": bool(creds and creds.account_no),
        "environment": creds.environment if creds else _paper_from(""),
        "error": "" if (creds and creds.source == "secrets-manager") else _cached_error,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if resolved else None,
    }


async def get_status() -> dict:
    if not is_configured():
        return status(None, resolved=False)
    return status(await get_credentials())
