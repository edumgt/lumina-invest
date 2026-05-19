"""Neo4j 그래프 관련 API 라우터."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.services import graph_service
from app.services.graph_rag import graph_rag_search, graph_rag_answer

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/related/{symbol}")
async def related_stocks(symbol: str):
    """종목의 경쟁사·공급사·수요처·동일 섹터 종목을 반환한다."""
    try:
        result = await graph_service.get_related_stocks(symbol)
    except Exception as exc:
        raise HTTPException(503, f"Neo4j 조회 실패: {exc}") from exc
    if not result.get("found"):
        raise HTTPException(404, f"종목을 그래프에서 찾을 수 없습니다: {symbol}")
    return result


@router.get("/sector")
async def sector_stocks(name: str = Query(..., description="섹터명 (부분 일치)")):
    """섹터에 속한 종목 목록을 반환한다."""
    try:
        stocks = await graph_service.get_sector_stocks(name)
    except Exception as exc:
        raise HTTPException(503, f"Neo4j 조회 실패: {exc}") from exc
    return {"sector": name, "stocks": stocks, "count": len(stocks)}


@router.get("/path")
async def supply_chain_path(
    from_symbol: str = Query(...),
    to_symbol: str = Query(...),
):
    """두 종목 사이의 최단 공급망 경로를 반환한다."""
    try:
        chain = await graph_service.get_supply_chain_path(from_symbol, to_symbol)
    except Exception as exc:
        raise HTTPException(503, f"Neo4j 조회 실패: {exc}") from exc
    if not chain:
        return {"found": False, "from": from_symbol, "to": to_symbol, "chain": []}
    return {"found": True, "from": from_symbol, "to": to_symbol, "chain": chain}


@router.get("/documents/{symbol}")
async def documents_for_symbol(symbol: str):
    """특정 종목을 언급한 문서 목록을 반환한다."""
    try:
        docs = await graph_service.get_documents_for_symbol(symbol)
    except Exception as exc:
        raise HTTPException(503, f"Neo4j 조회 실패: {exc}") from exc
    return {"symbol": symbol, "documents": docs, "count": len(docs)}


@router.post("/seed")
async def seed_graph():
    """종목 관계 그래프 초기 데이터를 Neo4j에 삽입한다."""
    try:
        await graph_service.seed_graph()
    except Exception as exc:
        raise HTTPException(503, f"Neo4j 시드 실패: {exc}") from exc
    return {"ok": True, "message": "그래프 시드 완료"}


class GraphRagRequest(BaseModel):
    query: str
    top_k: int = 5
    answer: bool = False


@router.post("/rag")
async def graph_rag(body: GraphRagRequest):
    """GraphRAG: 벡터 검색 + 그래프 컨텍스트를 결합해 검색하거나 LLM 답변을 생성한다."""
    try:
        if body.answer:
            text = await graph_rag_answer(body.query, top_k=body.top_k)
            return {"answer": text}
        result = await graph_rag_search(body.query, top_k=body.top_k)
        return result
    except Exception as exc:
        raise HTTPException(503, f"GraphRAG 실패: {exc}") from exc
