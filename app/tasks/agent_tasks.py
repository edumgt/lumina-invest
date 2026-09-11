"""AI 에이전트 Celery 태스크.

LangGraph 에이전트(최대 6회 LLM 호출)를 HTTP 요청 사이클 밖에서 실행한다.
Celery 워커는 동기 환경이므로 asyncio.run() 으로 비동기 코드를 실행한다.
각 태스크는 독립적인 이벤트 루프에서 DB 커넥션을 새로 만들고 완료 후 정리한다.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@celery_app.task(
    bind=True,
    name="agent.run",
    max_retries=1,
    soft_time_limit=240,  # 4분 – SoftTimeLimitExceeded 발생
    time_limit=300,        # 5분 – 강제 종료
)
def run_agent_task(
    self,
    user_id: str,
    conversation_id: str,
    question: str,
    history: list,
    llm_model: str,
    rag_context: str = "",
) -> dict:
    """LangGraph ReAct 에이전트를 워커에서 실행하고 채팅 기록을 PostgreSQL에 저장한다."""

    async def _async() -> dict:
        import uuid
        from sqlalchemy import select
        from app.config import settings
        from app.database.postgres import connect_postgres, close_postgres, get_session_factory
        from app.lib.redis_cache import connect_redis, close_redis
        from app.lib.llm_client import get_llm_client
        from app.models import Chat, Conversation
        from app.services.langgraph_agent import run_agent

        await connect_redis()
        await connect_postgres()
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                ollama = get_llm_client()

                result = await run_agent(
                    db, ollama, llm_model, question, history, rag_context
                )

                # 채팅 기록 + 스레드 통계 저장
                try:
                    db.add(Chat(
                        user_id=uuid.UUID(user_id),
                        conversation_id=uuid.UUID(conversation_id),
                        question=question,
                        answer=result["answer"],
                        steps=result.get("steps", []),
                        citations=result.get("citations", []),
                    ))
                    conv_result = await db.execute(
                        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
                    )
                    conv = conv_result.scalar_one_or_none()
                    if conv:
                        conv.message_count += 1
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.warning("채팅 기록 저장 실패: %s", e)

            return {**result, "conversation_id": conversation_id}
        finally:
            await close_redis()
            await close_postgres()

    try:
        return asyncio.run(_async())
    except Exception as exc:
        logger.exception("에이전트 태스크 실패 (task_id=%s): %s", self.request.id, exc)
        raise self.retry(exc=exc, countdown=5)
