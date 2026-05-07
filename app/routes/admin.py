from fastapi import APIRouter, Depends, HTTPException
from app.database.mongo import get_mdb
from app.lib.session import get_current_user

router = APIRouter(prefix="/api/admin")

FINANCIAL_COLS = ["personal_cb_stats", "corporate_cb_stats", "bank_products", "fund_products"]
USER_COLS = ["chats", "portfolio", "orders", "broker_settings", "crawled_docs", "audit_events"]


def _require_admin(user=Depends(get_current_user)):
    if "admin" not in user.get("roles", []):
        raise HTTPException(403, "관리자 권한이 필요합니다.")
    return user


@router.post("/reset")
async def reset_db(
    user=Depends(_require_admin),
    mdb=Depends(get_mdb),
):
    for col in FINANCIAL_COLS + USER_COLS:
        await mdb[col].delete_many({})

    return {"ok": True, "message": f"MongoDB {len(FINANCIAL_COLS)}개 금융컬렉션 + {len(USER_COLS)}개 사용자컬렉션 초기화 완료"}


@router.get("/stats")
async def db_stats(
    user=Depends(_require_admin),
    mdb=Depends(get_mdb),
):
    stats: dict = {}

    for col in FINANCIAL_COLS:
        stats[f"mongo.{col}"] = await mdb[col].count_documents({})

    for col in USER_COLS:
        stats[f"mongo.{col}"] = await mdb[col].count_documents({})

    return {"stats": stats}
