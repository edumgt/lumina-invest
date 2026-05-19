"""GraphRAG: Qdrant 벡터 검색 + Neo4j 그래프 컨텍스트를 결합한 RAG 파이프라인."""
from __future__ import annotations
import asyncio
from app.services.rag_pipeline import rag_search
from app.services.graph_service import (
    get_related_stocks,
    get_documents_for_symbol,
    extract_mentioned_symbols,
)


async def graph_rag_search(
    query: str,
    top_k: int = 5,
    collection: str | None = None,
) -> dict:
    """
    벡터 검색 결과에 그래프 컨텍스트를 추가해 반환한다.

    Returns:
        {
            "vector_results": [...],   # Qdrant 유사 문서
            "graph_context": {...},    # Neo4j 관계 정보
            "combined_context": str,   # LLM 프롬프트용 합성 텍스트
        }
    """
    # 1) 벡터 검색 (Qdrant)
    vector_results = await rag_search(query, top_k=top_k, collection=collection)

    # 2) 쿼리 + 벡터 결과에서 종목 심볼 추출
    all_text = query + " " + " ".join(r.get("text", "") for r in vector_results)
    symbols = extract_mentioned_symbols(all_text)

    # 3) 그래프 컨텍스트 병렬 조회
    graph_context: dict[str, dict] = {}
    if symbols:
        tasks = [get_related_stocks(sym) for sym in symbols[:3]]  # 최대 3종목
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, res in zip(symbols[:3], results):
            if isinstance(res, dict) and res.get("found"):
                graph_context[sym] = res

    # 4) 그래프 컨텍스트를 텍스트로 직렬화
    graph_text_parts: list[str] = []
    for sym, ctx in graph_context.items():
        parts = [f"[{ctx['name']} ({sym}) — {ctx['sector']}]"]
        if ctx.get("competitors"):
            names = ", ".join(c["name"] for c in ctx["competitors"])
            parts.append(f"  경쟁사: {names}")
        if ctx.get("suppliers"):
            names = ", ".join(c["name"] for c in ctx["suppliers"])
            parts.append(f"  주요 공급사: {names}")
        if ctx.get("customers"):
            names = ", ".join(c["name"] for c in ctx["customers"])
            parts.append(f"  주요 납품처: {names}")
        if ctx.get("sector_peers"):
            names = ", ".join(c["name"] for c in ctx["sector_peers"])
            parts.append(f"  동일 섹터: {names}")
        graph_text_parts.append("\n".join(parts))

    graph_text = "\n\n".join(graph_text_parts)

    # 5) 벡터 결과 텍스트 직렬화
    vector_text = "\n\n".join(
        f"[{r.get('title', '문서')}]\n{r['text']}"
        for r in vector_results
    )

    combined_context = ""
    if vector_text:
        combined_context += f"=== 관련 문서 ===\n{vector_text}\n\n"
    if graph_text:
        combined_context += f"=== 종목 관계 그래프 ===\n{graph_text}"

    return {
        "vector_results": vector_results,
        "graph_context": graph_context,
        "combined_context": combined_context.strip(),
        "detected_symbols": symbols,
    }


async def graph_rag_answer(query: str, top_k: int = 5, collection: str | None = None) -> str:
    """GraphRAG 컨텍스트로 LLM 답변을 생성한다."""
    from langchain_ollama import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from app.config import settings

    result = await graph_rag_search(query, top_k=top_k, collection=collection)
    context = result["combined_context"]

    if not context:
        context = "관련 데이터가 없습니다."

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "너는 금융 AI 어시스턴트다. 아래 참고 정보(문서 + 종목 관계 그래프)를 바탕으로 "
            "질문에 한국어로 답하라. 그래프 정보는 기업 간 관계를 나타낸다.\n\n"
            "[참고 정보]\n{context}",
        ),
        ("human", "{question}"),
    ])

    llm = ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=0.2,
        num_predict=2048,
    )

    chain = prompt | llm | StrOutputParser()
    return await chain.ainvoke({"context": context, "question": query})
