"""LangChain LCEL 기반 RAG 파이프라인.

구성:
  OllamaEmbeddings  →  QdrantVectorStore  →  similarity_search
  LCEL 체인: retriever | format_docs | prompt | llm | StrOutputParser
"""
from __future__ import annotations
from typing import Any

from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_ollama import ChatOllama
from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from app.config import settings


class CompatibleQdrantStore(QdrantVectorStore):
    @classmethod
    def _document_from_point(cls, point, collection_name, content_payload_key, metadata_payload_key):
        payload = point.payload or {}
        metadata = dict(payload.get(metadata_payload_key) or {k:v for k,v in payload.items() if k not in ('text','page_content')})
        metadata.update({'_id':point.id,'_collection_name':collection_name})
        return Document(page_content=payload.get(content_payload_key) or payload.get('text') or payload.get('page_content') or '', metadata=metadata)


# ── 내부 팩토리 ───────────────────────────────────────────────────────────────

def _make_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBED_MODEL,
    )


async def _get_or_create_collection(client: AsyncQdrantClient, collection: str) -> None:
    """Qdrant 컬렉션이 없으면 nomic-embed-text 기준 dim=768로 생성한다."""
    try:
        await client.get_collection(collection)
    except Exception:
        await client.create_collection(
            collection,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


# ── 공개 함수 ─────────────────────────────────────────────────────────────────

async def rag_search(
    query:      str,
    top_k:      int  = 5,
    collection: str | None = None,
    filter_source: str | None = None,
) -> list[dict]:
    """
    LangChain QdrantVectorStore를 통해 유사 문서를 검색한다.

    Args:
        query:         검색 쿼리
        top_k:         반환할 최대 문서 수
        collection:    Qdrant 컬렉션명 (None이면 settings.QDRANT_COLLECTION 사용)
        filter_source: 특정 source만 필터링 (예: "upload", "github:...")

    Returns:
        [{"text": ..., "url": ..., "title": ..., "source": ..., "score": ...}, ...]
    """
    coll = collection or settings.QDRANT_COLLECTION
    try:
        vector = await _make_embeddings().aembed_query(query)
        from qdrant_client.http.models import Filter, FieldCondition, MatchValue
        qdrant_filter = Filter(must=[FieldCondition(key="source",match=MatchValue(value=filter_source))]) if filter_source else None
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        try:
            await _get_or_create_collection(client, coll)
            results = await client.query_points(collection_name=coll, query=vector,
                limit=top_k, query_filter=qdrant_filter, with_payload=True)
        finally:
            await client.close()
        docs = []
        for point in results.points:
            payload = point.payload or {}
            metadata = payload.get('metadata') or payload
            text = payload.get('text') or payload.get('page_content') or ''
            if text:
                docs.append({'text':text,'url':metadata.get('url',''),
                    'title':metadata.get('title',''),'source':metadata.get('source',''),'score':float(point.score)})
        return docs
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Qdrant RAG 검색 실패')
        return []


async def store_chunks(
    chunks:     list[str],
    metadata:   dict,
    collection: str | None = None,
) -> int:
    """
    텍스트 청크 목록을 OllamaEmbeddings로 임베딩하여 Qdrant에 저장한다.

    Returns:
        실제 저장된 청크 수
    """
    if not chunks:
        return 0

    coll = collection or settings.QDRANT_COLLECTION
    try:
        import uuid
        from qdrant_client.http.models import PointStruct
        vectors = await _make_embeddings().aembed_documents(chunks)
        points = [PointStruct(id=str(uuid.uuid4()),vector=vector,
            payload={**metadata,'text':chunk,'metadata':dict(metadata),'chunk_index':index})
            for index,(chunk,vector) in enumerate(zip(chunks,vectors))]
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        try:
            await _get_or_create_collection(client, coll)
            await client.upsert(collection_name=coll,points=points,wait=True)
        finally:
            await client.close()
        return len(points)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Qdrant RAG 문서 저장 실패')
        return 0


def build_rag_chain(collection: str | None = None):
    """
    LCEL 기반 RAG 체인을 반환한다.

    사용 예:
        chain = build_rag_chain()
        answer = await chain.ainvoke({"question": "..."})
    """
    coll = collection or settings.QDRANT_COLLECTION

    # 동기 Qdrant 클라이언트 (LCEL retriever는 sync 인터페이스 사용)
    from qdrant_client import QdrantClient
    sync_client = QdrantClient(url=settings.QDRANT_URL)

    vector_store = CompatibleQdrantStore(
        client=sync_client,
        collection_name=coll,
        embedding=_make_embeddings(),
        content_payload_key="text",
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": settings.TOP_K})

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "너는 금융 AI 어시스턴트다. 아래 참고 문서를 바탕으로 질문에 한국어로 답하라.\n\n"
            "[참고 문서]\n{context}",
        ),
        ("human", "{question}"),
    ])

    llm = ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=0.2,
        num_predict=256,
        num_ctx=2048,
    )

    def format_docs(docs: list[Document]) -> str:
        return "\n\n".join(
            f"[{d.metadata.get('title', '문서')}]\n{d.page_content}" for d in docs
        )

    chain = (
        {"context": retriever | RunnableLambda(format_docs), "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain


async def delete_chunks_by_source(source: str, collection: str | None = None) -> int:
    """
    특정 source 메타데이터를 가진 모든 벡터를 Qdrant에서 삭제한다.

    Returns:
        삭제 요청이 성공하면 1, 실패하면 0
    """
    coll = collection or settings.QDRANT_COLLECTION
    try:
        from qdrant_client.http.models import Filter, FieldCondition, MatchValue
        client = AsyncQdrantClient(url=settings.QDRANT_URL)
        await client.delete(
            collection_name=coll,
            points_selector=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=source))]
            ),
        )
        await client.close()
        return 1
    except Exception:
        return 0
