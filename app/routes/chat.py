import logging
"""채팅 API – 대화 스레드 + Redis 사용자 상태 연동.

변경 사항:
- conversation_id 필드 추가: 없으면 자동으로 새 스레드 생성
- Redis user_state 에 활성 conversation_id 기록
- 메시지 저장 시 conversation_id 포함
- 대화 스레드 updated_at / message_count 갱신
- JWT Bearer 또는 쿠키 세션 모두 허용 (get_current_user_any)
- llm_mode 로 답변 생성 방식을 고른다 (채팅 화면 우측 상단 선택):
    ollama : 서버 설정(LLM_PROVIDER)의 LLM 으로 LangGraph 에이전트 실행 (기본)
    openai : 요청에 담긴 OpenAI API 키로 OpenAI Chat Completions 호출 (키는 저장·로그하지 않음)
    rag    : LLM 없이 Qdrant 에서 검색한 청크만 그대로 반환 (순수 RAG)
"""
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.models import Chat, Conversation
from app.lib.jwt_auth import get_current_user_any
from app.lib.llm_client import OpenAIClient, get_llm_client
from app.lib.user_state import get_active_conversation, set_active_conversation
from app.services.langgraph_agent import run_agent
from app.services.rag_pipeline import rag_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


LlmMode = Literal["ollama", "openai", "rag"]


class ChatBody(BaseModel):
    question: str
    history: list[dict] = []
    use_rag: bool = True
    conversation_id: Optional[str] = None  # 없으면 자동 생성
    llm_mode: LlmMode = "ollama"
    openai_api_key: Optional[str] = None   # llm_mode=openai 일 때. 서버에 저장하지 않는다.
    openai_model: Optional[str] = None     # 비우면 settings.OPENAI_MODEL


def _rag_only_answer(question: str, docs: list[dict]) -> dict:
    """순수 RAG 모드: 검색된 청크를 LLM 없이 그대로 답변으로 구성한다."""
    if not docs:
        return {
            "answer": "검색된 참고 문서(청크)가 없습니다. 크롤링/문서 업로드로 지식 베이스를 먼저 채워 주세요.",
            "steps": [], "citations": [], "chunks": [], "mode": "rag",
        }
    lines = [f"'{question}' 관련 검색 결과 {len(docs)}개 (LLM 미사용, 유사도 순)\n"]
    citations = []
    for i, d in enumerate(docs, 1):
        title = d.get("title") or d.get("source") or "제목 없음"
        score = d.get("score")
        score_txt = f" · 유사도 {score:.3f}" if isinstance(score, (int, float)) else ""
        lines.append(f"[{i}] {title}{score_txt}")
        if d.get("url"):
            lines.append(f"    {d['url']}")
        lines.append(f"    {d.get('text', '').strip()[:800]}")
        lines.append("")
        citations.append({"title": title, "url": d.get("url", ""), "source": d.get("source", "")})
    return {
        "answer": "\n".join(lines).rstrip(),
        "steps": [], "citations": citations, "chunks": docs, "mode": "rag",
    }


def _resolve_llm(body: "ChatBody"):
    """llm_mode 에 맞는 (클라이언트, 모델명, 표시용 이름) 을 돌려준다."""
    if body.llm_mode == "openai":
        key = (body.openai_api_key or settings.OPENAI_API_KEY or "").strip()
        if not key:
            raise HTTPException(400, "OpenAI API 키를 입력해 주세요.")
        client = OpenAIClient(key, model=(body.openai_model or "").strip())
        return client, client.model, "OpenAI"
    return get_llm_client(), settings.LLM_MODEL, f"Ollama({settings.LLM_PROVIDER})"


async def _get_or_create_conversation(db: AsyncSession, user_id: str, cid: Optional[str]) -> str:
    """conversation_id 가 주어지면 검증, 없으면 Redis 활성 스레드 또는 신규 생성."""
    uid = uuid.UUID(user_id)
    if cid:
        try:
            result = await db.execute(
                select(Conversation).where(Conversation.id == uuid.UUID(cid), Conversation.user_id == uid)
            )
            conv = result.scalar_one_or_none()
        except Exception:
            conv = None
        if not conv:
            raise HTTPException(404, "대화 스레드를 찾을 수 없습니다.")
        return cid

    # Redis 에서 활성 스레드 확인
    active = await get_active_conversation(user_id)
    if active:
        result = await db.execute(
            select(Conversation).where(Conversation.id == uuid.UUID(active), Conversation.user_id == uid)
        )
        if result.scalar_one_or_none():
            return active

    # 새 스레드 생성
    conv = Conversation(user_id=uid, title=f"대화 {_now()[:10]}", message_count=0)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    new_cid = str(conv.id)
    await set_active_conversation(user_id, new_cid)
    return new_cid


async def _build_history_from_db(db: AsyncSession, conversation_id: str, user_id: str, limit: int = 10) -> list[dict]:
    """PostgreSQL 에서 최근 대화 이력을 LangGraph 포맷으로 변환합니다."""
    result = await db.execute(
        select(Chat)
        .where(Chat.conversation_id == uuid.UUID(conversation_id), Chat.user_id == uuid.UUID(user_id))
        .order_by(Chat.created_at.desc())
        .limit(limit)
    )
    docs = list(result.scalars().all())
    docs.reverse()

    history = []
    for doc in docs:
        history.append({"role": "user", "content": doc.question})
        history.append({"role": "assistant", "content": doc.answer})
    return history


@router.post("/chat")
async def chat(
    body: ChatBody,
    user=Depends(get_current_user_any),
    db: AsyncSession = Depends(get_pg_session),
):
    user_id = user["id"]
    # rag 모드는 공통 서버 LLM(Docker Ollama Qwen)으로 검색 근거를 설명한다. use_rag 은 "검색 근거를 붙일지" 이지
    # "어떤 LLM 을 쓸지" 가 아니므로 ollama/openai 모드의 LLM 선택(_resolve_llm)에는 영향을 주지 않는다.
    if body.llm_mode == 'rag':
        llm, llm_model, llm_label = get_llm_client(), settings.LLM_MODEL, f'Docker Ollama {settings.LLM_MODEL}'
    else:
        llm, llm_model, llm_label = _resolve_llm(body)

    # 대화 스레드 확보
    conversation_id = await _get_or_create_conversation(db, user_id, body.conversation_id)

    # 클라이언트가 history 를 보내지 않았으면 DB 에서 최근 이력 로드
    history = body.history
    if not history:
        history = await _build_history_from_db(db, conversation_id, user_id, limit=10)

    # LangChain RAG 검색 (Qdrant) – rag 모드는 항상 검색
    rag_context = ""
    docs: list[dict] = []
    if body.use_rag or body.llm_mode == "rag":
        try:
            docs = await rag_search(body.question, top_k=settings.TOP_K)
            if docs:
                rag_context = "\n\n".join(
                    f"[{d['title']}] {d['text'][:500]}" for d in docs
                )
        except Exception:
            docs = []

    if body.llm_mode == "rag":
        # RAG 모드도 공통 Qwen으로 검색 근거를 설명한다.
        result = _rag_only_answer(body.question, docs)
        result["llm_used"] = False
        if docs:
            # 검색 근거가 있으면 LLM(기본 Qwen, openai 모드면 사용자 키)으로 짧게 설명한다. LLM 이 없거나 실패하면
            # 규칙 기반 답변("LLM 미사용")을 그대로 둔다 — RAG 모드는 LLM 없이도 동작해야 한다.
            try:
                summary = await llm.chat(llm_model, [
                    {'role': 'system', 'content': '검색 근거만 바탕으로 한국어로 간결히 답하세요. 출처 제목을 언급하고 근거가 부족하면 밝히세요. 매매를 단정하지 마세요.'},
                    {'role': 'user', 'content': f'질문: {body.question}\n근거:\n{rag_context}'}],
                    options={'num_ctx': 2048, 'num_predict': 160, 'temperature': 0.2})
                if isinstance(summary, str) and summary.strip():
                    result['answer'] = summary.strip()
                    result['model'] = llm_model
                    result['llm_used'] = True
            except Exception as exc:  # LLM 미연결·타임아웃·모의 클라이언트 등
                logger.warning("RAG 모드 LLM 요약 실패 — 규칙 기반 답변 유지: %s", exc)
    else:
        # LangGraph 에이전트 실행 (ollama: 서버 LLM / openai: 사용자 키)
        try:
            result = await run_agent(
                db, llm, llm_model,
                body.question, history,
                rag_context=rag_context,
            )
            result["mode"] = body.llm_mode
            result["model"] = llm_model
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if body.llm_mode == "openai":
                if code == 401:
                    raise HTTPException(401, "OpenAI API 키가 유효하지 않습니다.")
                if code == 429:
                    raise HTTPException(429, "OpenAI 사용량 한도(rate limit/quota)를 초과했습니다.")
                if code == 404:
                    raise HTTPException(400, f"OpenAI 모델({llm_model})을 찾을 수 없습니다.")
                raise HTTPException(502, f"OpenAI 오류: HTTP {code}")
            if code == 404:
                raise HTTPException(
                    503,
                    f"LLM 모델({llm_model})을 찾을 수 없습니다. "
                    f"(ollama pull {llm_model})",
                )
            raise HTTPException(503, f"Ollama 오류: {code}")
        except httpx.ConnectError:
            target = settings.OPENAI_BASE_URL if body.llm_mode == "openai" else settings.OLLAMA_BASE_URL
            raise HTTPException(503, f"{llm_label} 서버({target})에 연결할 수 없습니다.")
        except httpx.TimeoutException:
            raise HTTPException(504, "LLM 응답 시간이 초과되었습니다.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"에이전트 오류 ({llm_label}): {str(e)[:200]}")

    # PostgreSQL – 메시지 저장 (conversation_id 포함)
    try:
        db.add(Chat(
            user_id=uuid.UUID(user_id),
            client_id=user.get("client_id", ""),
            conversation_id=uuid.UUID(conversation_id),
            question=body.question,
            answer=result["answer"],
            steps=result.get("steps", []),
            citations=result.get("citations", []),
        ))
        # 스레드 통계 갱신
        conv_result = await db.execute(select(Conversation).where(Conversation.id == uuid.UUID(conversation_id)))
        conv = conv_result.scalar_one_or_none()
        if conv:
            conv.message_count += 1
        await db.commit()
    except Exception:
        await db.rollback()

    # Redis 사용자 상태 갱신 (활성 대화 + 마지막 활동 시각)
    try:
        await set_active_conversation(user_id, conversation_id)
    except Exception:
        pass

    return {**result, "conversation_id": conversation_id}


# ── 비동기 채팅 (Celery) ──────────────────────────────────────────────────────

@router.post("/chat/async", summary="비동기 채팅 (태스크 큐)")
async def chat_async(
    body: ChatBody,
    user=Depends(get_current_user_any),
    db: AsyncSession = Depends(get_pg_session),
):
    """에이전트 실행을 Celery 워커에 위임하고 task_id 를 즉시 반환한다.

    클라이언트는 GET /api/tasks/{task_id} 를 폴링하여 결과를 확인한다.
    LLM 응답 대기(최대 수 분)가 HTTP 타임아웃을 유발하는 상황에 사용한다.
    """
    from app.tasks.agent_tasks import run_agent_task

    user_id = user["id"]
    conversation_id = await _get_or_create_conversation(db, user_id, body.conversation_id)

    history = body.history
    if not history:
        history = await _build_history_from_db(db, conversation_id, user_id, limit=10)

    rag_context = ""
    if body.use_rag:
        try:
            docs = await rag_search(body.question, top_k=settings.TOP_K)
            if docs:
                rag_context = "\n\n".join(
                    f"[{d['title']}] {d['text'][:500]}" for d in docs
                )
        except Exception:
            pass

    task = run_agent_task.delay(
        user_id=user_id,
        conversation_id=conversation_id,
        question=body.question,
        history=history,
        llm_model=settings.LLM_MODEL,
        rag_context=rag_context,
    )

    return {
        "task_id": task.id,
        "conversation_id": conversation_id,
        "poll_url": f"/api/tasks/{task.id}",
    }
