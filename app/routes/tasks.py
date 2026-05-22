"""Celery 태스크 상태 조회 API.

클라이언트는 /api/chat/async 등 비동기 엔드포인트에서 task_id 를 받은 뒤
이 엔드포인트를 폴링하여 진행 상태와 최종 결과를 확인한다.

상태 흐름: pending → running → success | error
"""
from fastapi import APIRouter
from celery.result import AsyncResult

from app.celery_app import celery_app

router = APIRouter(prefix="/api")

_STATE_MAP = {
    "PENDING":  "pending",
    "RECEIVED": "pending",
    "STARTED":  "running",
    "RETRY":    "running",
    "SUCCESS":  "success",
    "FAILURE":  "error",
    "REVOKED":  "error",
}


@router.get("/tasks/{task_id}", summary="태스크 상태 조회")
async def get_task_status(task_id: str):
    """
    task_id 로 Celery 태스크의 진행 상태와 결과를 반환한다.

    - **pending** : 큐 대기 중
    - **running** : 워커 실행 중
    - **success** : 완료 – result 필드에 반환값 포함
    - **error**   : 실패 – error 필드에 예외 메시지 포함
    """
    ar = AsyncResult(task_id, app=celery_app)
    state = _STATE_MAP.get(ar.state, ar.state.lower())

    base = {"task_id": task_id, "state": state}

    if ar.state == "SUCCESS":
        return {**base, "result": ar.result}

    if ar.state == "FAILURE":
        return {**base, "result": None, "error": str(ar.result)}

    return {**base, "result": None}


@router.delete("/tasks/{task_id}", summary="태스크 취소")
async def revoke_task(task_id: str, terminate: bool = False):
    """실행 중이거나 대기 중인 태스크를 취소한다.

    - **terminate=false** (기본): 워커가 태스크를 시작하기 전에만 취소
    - **terminate=true**: 실행 중인 워커 프로세스도 강제 종료 (SIGTERM)
    """
    celery_app.control.revoke(task_id, terminate=terminate)
    return {"task_id": task_id, "revoked": True, "terminate": terminate}
