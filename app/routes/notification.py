"""알림 설정 API.

GET  /api/notification/settings  – 현재 사용자의 알림 설정 조회
POST /api/notification/settings  – 알림 설정 저장
POST /api/notification/test      – 테스트 알림 전송
"""
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models import NotificationLog, NotificationSettings
from app.services import notification

router = APIRouter(prefix="/api/notification")

_SUPPORTED_CHANNELS = {"telegram", "slack", "email", "kakao", "sms"}


class NotificationSettingsBody(BaseModel):
    """알림 설정 저장 요청 모델."""

    channels: list[str] = Field(default_factory=list, description="활성화할 채널 목록")

    # 텔레그램
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Slack
    slack_webhook_url: str = ""

    # 이메일
    email_to: str = ""
    email_host: str = ""
    email_port: int = Field(default=587, ge=1, le=65535)
    email_user: str = ""
    email_password: str = ""
    email_from: str = ""

    # 카카오 알림톡
    kakao_api_key: str = ""
    kakao_api_secret: str = ""
    kakao_sender_key: str = ""
    kakao_phone: str = ""

    # SMS
    sms_api_key: str = ""
    sms_api_secret: str = ""
    sms_from: str = ""
    sms_to: str = ""


async def _get_settings_row(db: AsyncSession, user_id: uuid.UUID) -> NotificationSettings | None:
    result = await db.execute(select(NotificationSettings).where(NotificationSettings.user_id == user_id))
    return result.scalar_one_or_none()


@router.get("/settings")
async def get_notification_settings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """현재 사용자의 알림 설정을 반환한다. 비밀값(password/secret)은 마스킹."""
    row = await _get_settings_row(db, uuid.UUID(user["id"]))

    def mask(v: str) -> str:
        """보안을 위해 비밀값은 설정 여부만 알 수 있도록 고정 마스크로 반환."""
        return "****" if v else ""

    if not row:
        return {
            "channels": [], "telegram_token": "", "telegram_chat_id": "",
            "slack_webhook_url": "", "email_to": "", "email_host": "",
            "email_port": 587, "email_user": "", "email_password": "", "email_from": "",
            "kakao_api_key": "", "kakao_api_secret": "", "kakao_sender_key": "", "kakao_phone": "",
            "sms_api_key": "", "sms_api_secret": "", "sms_from": "", "sms_to": "",
        }

    return {
        "channels":          row.channels,
        "telegram_token":    mask(row.telegram_token),
        "telegram_chat_id":  row.telegram_chat_id,
        "slack_webhook_url": row.slack_webhook_url,
        "email_to":          row.email_to,
        "email_host":        row.email_host,
        "email_port":        row.email_port,
        "email_user":        row.email_user,
        "email_password":    mask(row.email_password),
        "email_from":        row.email_from,
        "kakao_api_key":     mask(row.kakao_api_key),
        "kakao_api_secret":  mask(row.kakao_api_secret),
        "kakao_sender_key":  row.kakao_sender_key,
        "kakao_phone":       row.kakao_phone,
        "sms_api_key":       mask(row.sms_api_key),
        "sms_api_secret":    mask(row.sms_api_secret),
        "sms_from":          row.sms_from,
        "sms_to":            row.sms_to,
    }


@router.post("/settings")
async def save_notification_settings(
    body: NotificationSettingsBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """알림 설정을 저장한다. 빈 문자열 비밀값은 기존 값을 유지한다."""
    channels = [c for c in body.channels if c in _SUPPORTED_CHANNELS]
    uid = uuid.UUID(user["id"])

    row = await _get_settings_row(db, uid)
    if row is None:
        row = NotificationSettings(user_id=uid)
        db.add(row)

    def keep_secret(new_val: str, existing_val: str) -> str:
        """값이 마스크 플레이스홀더("****")이거나 빈 문자열이면 기존 저장값을 유지한다."""
        if not new_val or new_val == "****":
            return existing_val
        return new_val

    row.channels = channels
    row.telegram_token = keep_secret(body.telegram_token, row.telegram_token)
    row.telegram_chat_id = body.telegram_chat_id
    row.slack_webhook_url = body.slack_webhook_url
    row.email_to = body.email_to
    row.email_host = body.email_host
    row.email_port = body.email_port
    row.email_user = body.email_user
    row.email_password = keep_secret(body.email_password, row.email_password)
    row.email_from = body.email_from
    row.kakao_api_key = keep_secret(body.kakao_api_key, row.kakao_api_key)
    row.kakao_api_secret = keep_secret(body.kakao_api_secret, row.kakao_api_secret)
    row.kakao_sender_key = body.kakao_sender_key
    row.kakao_phone = body.kakao_phone
    row.sms_api_key = keep_secret(body.sms_api_key, row.sms_api_key)
    row.sms_api_secret = keep_secret(body.sms_api_secret, row.sms_api_secret)
    row.sms_from = body.sms_from
    row.sms_to = body.sms_to

    await db.commit()
    return {"ok": True}


@router.post("/test")
async def test_notification(
    user=Depends(get_current_user),
):
    """현재 설정된 모든 채널로 테스트 알림을 전송한다."""
    html = (
        " <b>[알림 테스트]</b>\n\n"
        "매매 알림이 정상적으로 설정되었습니다.\n"
        "이 메시지가 수신되면 해당 채널이 활성화된 것입니다."
    )
    plain = (
        "[알림 테스트] 매매 알림이 정상적으로 설정되었습니다.\n"
        "이 메시지가 수신되면 해당 채널이 활성화된 것입니다."
    )
    await notification.dispatch(
        plain,
        html_message=html,
        subject="[매매 알림] 테스트",
        user_id=user["id"],
    )
    return {"ok": True, "message": "테스트 알림을 전송했습니다."}


@router.get("/history")
async def notification_history(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """현재 사용자의 최근 알림 발송 이력을 조회한다."""
    result = await db.execute(
        select(NotificationLog)
        .where(NotificationLog.user_id == uuid.UUID(user["id"]))
        .order_by(NotificationLog.created_at.desc())
        .limit(limit)
    )
    events = [{
        "subject": ev.subject, "message": ev.message, "channels": ev.channels,
        "created_at": ev.created_at.isoformat(),
    } for ev in result.scalars().all()]
    return {"events": events, "count": len(events)}
