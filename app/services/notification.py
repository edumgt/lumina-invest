"""텔레그램 알림 서비스.

사용법:
  1. BotFather 에서 봇 토큰 발급: /newbot
  2. 채팅 ID 확인: https://api.telegram.org/bot<TOKEN>/getUpdates
  3. .env 에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 설정
"""
import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(message: str, chat_id: str | None = None) -> bool:
    """텔레그램 메시지 전송.

    Args:
        message: 전송할 메시지 (HTML 태그 사용 가능)
        chat_id: 대상 채팅 ID (기본값: settings.TELEGRAM_CHAT_ID)

    Returns:
        전송 성공 여부
    """
    token = settings.TELEGRAM_BOT_TOKEN
    cid   = chat_id or settings.TELEGRAM_CHAT_ID

    if not token or not cid:
        logger.debug("텔레그램 설정 없음 – 알림 건너뜀 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정)")
        return False

    url = _BASE.format(token=token)
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(url, json={
                "chat_id":    cid,
                "text":       message,
                "parse_mode": "HTML",
            })
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("텔레그램 알림 전송 실패: %s", exc)
        return False


# ── 알림 헬퍼 ────────────────────────────────────────────────────────────────

async def notify_insufficient_funds(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    required: float,
    available: float,
) -> None:
    """예수금 부족으로 주문 실패 알림."""
    msg = (
        "🚨 <b>주문 실패 – 예수금 부족</b>\n\n"
        f"종목: <code>{symbol}</code>\n"
        f"구분: {'매수' if side == 'buy' else '매도'}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"필요 금액: <b>{required:,.0f}원</b>\n"
        f"가용 예수금: <b>{available:,.0f}원</b>\n"
        f"부족분: {required - available:,.0f}원"
    )
    await send_telegram(msg)


async def notify_order_placed(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    broker: str = "",
) -> None:
    """주문 체결 알림."""
    emoji = "📈" if side == "buy" else "📉"
    label = "매수" if side == "buy" else "매도"
    msg = (
        f"{emoji} <b>주문 접수</b>\n\n"
        f"종목: <code>{symbol}</code>\n"
        f"구분: {label}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"총액: {quantity * price:,.0f}원"
        + (f"\n증권사: {broker}" if broker else "")
    )
    await send_telegram(msg)


async def notify_order_error(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    error: str,
) -> None:
    """주문 오류 알림."""
    label = "매수" if side == "buy" else "매도"
    msg = (
        "❌ <b>주문 오류</b>\n\n"
        f"종목: <code>{symbol}</code>\n"
        f"구분: {label}\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"오류: {error}"
    )
    await send_telegram(msg)


async def notify_auto_trade_started() -> None:
    """자동매매 시작 알림."""
    await send_telegram("🤖 <b>자동매매 시작</b>\n\n10분 주기로 퀀트 신호를 분석합니다.")


async def notify_auto_trade_stopped() -> None:
    """자동매매 중지 알림."""
    await send_telegram("⏹ <b>자동매매 중지</b>\n\n자동매매가 정지되었습니다.")


async def notify_auto_trade_executed(
    symbol: str,
    name: str,
    action: str,
    quantity: int,
    price: float,
    reason: str,
) -> None:
    """자동매매 가상 체결 알림."""
    emoji = "📈" if action == "buy" else "📉"
    label = "매수" if action == "buy" else "매도"
    msg = (
        f"{emoji} <b>[자동매매] {label} 체결</b>\n\n"
        f"종목: {name} (<code>{symbol}</code>)\n"
        f"수량: {quantity:,}주 × {price:,.0f}원\n"
        f"사유: {reason}"
    )
    await send_telegram(msg)
