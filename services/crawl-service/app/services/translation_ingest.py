"""1.데이터 다국어 번역 데이터 → Qdrant RAG 인제스트."""
import asyncio
import hashlib
import json
import os
import zipfile
from typing import AsyncIterator

from app.config import settings
from app.lib.ollama import OllamaClient

TRANSLATION_COLLECTION = "translation_docs"
SENTS_PER_CHUNK = 10       # 문장 N개를 하나의 청크로 묶음
EMBED_BATCH = 32           # Qdrant upsert 배치 크기

DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "1.데이터")
)

CATEGORY_MAP = {
    "1. 학술논문": "academic",
    "2. 규제정보": "regulation",
    "3. 보고서": "report",
    "4. 뉴스기사": "news",
    "5. 공시정보": "disclosure",
}

LANG_MAP = {
    "1. 영어": "en",
    "2. 중국어간체": "zh",
    "3. 일본어": "ja",
    "4. 베트남어": "vi",
    "5. 인도네시아어": "id",
}


def _parse_zip_name(fname: str) -> dict:
    """파일명에서 카테고리·언어 추출. e.g. TL_4. 뉴스기사_3. 일본어.zip"""
    stem = os.path.splitext(fname)[0]
    parts = stem.split("_", 1)
    prefix = parts[0]  # TS / TL
    rest = parts[1] if len(parts) > 1 else ""
    cat_raw, lang_raw = (rest.split("_", 1) + [""])[:2]

    category = next((v for k, v in CATEGORY_MAP.items() if k in cat_raw), cat_raw.strip())
    target_lang = next((v for k, v in LANG_MAP.items() if k in lang_raw), "")
    data_type = "labeled" if prefix == "TL" else "source"
    return {"category": category, "target_language": target_lang, "data_type": data_type}


def _doc_chunks(doc: dict, zip_meta: dict) -> list[dict]:
    """JSON 문서 하나 → 청크 리스트."""
    meta = doc.get("meta", {})
    sents = doc.get("sents", [])
    if not sents:
        return []

    chunks = []
    for i in range(0, len(sents), SENTS_PER_CHUNK):
        batch = sents[i: i + SENTS_PER_CHUNK]
        src_text = " ".join(
            s.get("source_cleaned") or s.get("source_original", "") for s in batch
        ).strip()
        if not src_text:
            continue

        tgt_text = ""
        if zip_meta["data_type"] == "labeled":
            tgt_text = " ".join(s.get("mtpe") or s.get("mt", "") for s in batch).strip()

        chunks.append({
            "text": src_text,
            "translation": tgt_text,
            "doc_no": meta.get("doc_no", ""),
            "domain": meta.get("domain", ""),
            "category": zip_meta["category"],
            "source_language": meta.get("source_language", "ko"),
            "target_language": zip_meta["target_language"],
            "data_type": zip_meta["data_type"],
            "chunk_index": i // SENTS_PER_CHUNK,
        })
    return chunks


def _chunk_id(doc_no: str, chunk_index: int, zip_name: str) -> int:
    raw = f"{zip_name}::{doc_no}::{chunk_index}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:15], 16)


async def _ensure_collection(client, dim: int):
    from qdrant_client.http.models import Distance, VectorParams
    try:
        await client.get_collection(TRANSLATION_COLLECTION)
    except Exception:
        await client.create_collection(
            TRANSLATION_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )


async def _upsert_batch(client, points: list):
    from qdrant_client.http.models import PointStruct
    structs = [
        PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
        for p in points
    ]
    await client.upsert(collection_name=TRANSLATION_COLLECTION, points=structs)


async def _iter_zips(data_type_filter: str, categories: list[str], languages: list[str]):
    """필터 조건에 맞는 zip 파일 경로를 순회."""
    for split in ("Training", "Validation"):
        for sub in ("01.원천데이터", "02.라벨링데이터"):
            dirpath = os.path.join(DATA_DIR, split, sub)
            if not os.path.isdir(dirpath):
                continue
            for fname in sorted(os.listdir(dirpath)):
                if not fname.endswith(".zip"):
                    continue
                zm = _parse_zip_name(fname)
                if data_type_filter != "all" and zm["data_type"] != data_type_filter:
                    continue
                if categories and zm["category"] not in categories:
                    continue
                if languages and zm["target_language"] and zm["target_language"] not in languages:
                    continue
                yield os.path.join(dirpath, fname), fname, zm


async def run_translation_ingest(
    ollama: OllamaClient,
    log: list[str],
    data_type: str = "labeled",
    categories: list[str] | None = None,
    languages: list[str] | None = None,
    max_docs: int = 0,
) -> dict:
    """
    data_type: "labeled" | "source" | "all"
    categories: ["news","report",...] or None (전체)
    languages:  ["en","ja","zh","vi","id"] or None (전체)
    max_docs:   0 = 무제한
    """
    from qdrant_client import AsyncQdrantClient

    cats = categories or []
    langs = languages or []

    client = AsyncQdrantClient(url=settings.QDRANT_URL)

    # 차원 확인
    test_emb = await ollama.embed(settings.EMBED_MODEL, "test")
    if not test_emb:
        log.append("[ERROR] 임베딩 모델 응답 없음")
        await client.close()
        return {"error": "no embedding"}
    dim = len(test_emb)
    await _ensure_collection(client, dim)

    total_docs = 0
    total_chunks = 0
    total_points = 0
    pending: list[dict] = []

    async def flush():
        nonlocal total_points
        if pending:
            await _upsert_batch(client, pending)
            total_points += len(pending)
            pending.clear()

    async for zip_path, zip_name, zm in _iter_zips(data_type, cats, langs):
        log.append(f"[ZIP] {zip_name} ({zm['data_type']}, {zm['category']}, {zm['target_language']})")
        try:
            zf = zipfile.ZipFile(zip_path)
        except Exception as e:
            log.append(f"  [SKIP] {e}")
            continue

        zip_docs = 0
        zip_chunks = 0
        for member in zf.namelist():
            if not member.endswith(".json"):
                continue
            if max_docs and total_docs >= max_docs:
                break
            try:
                doc = json.loads(zf.read(member))
            except Exception:
                continue

            chunks = _doc_chunks(doc, zm)
            for ch in chunks:
                emb = await ollama.embed(settings.EMBED_MODEL, ch["text"])
                if not emb:
                    continue
                pending.append({
                    "id": _chunk_id(ch["doc_no"], ch["chunk_index"], zip_name),
                    "vector": emb,
                    "payload": ch,
                })
                if len(pending) >= EMBED_BATCH:
                    await flush()

            zip_docs += 1
            zip_chunks += len(chunks)
            total_docs += 1
            total_chunks += len(chunks)

        await flush()
        log.append(f"  → {zip_docs}문서 / {zip_chunks}청크")

    await flush()
    await client.close()

    summary = {
        "total_docs": total_docs,
        "total_chunks": total_chunks,
        "total_points": total_points,
        "collection": TRANSLATION_COLLECTION,
    }
    log.append(f"\n완료: {total_docs}문서, {total_chunks}청크, Qdrant {total_points}건")
    return summary


async def translation_search(
    query: str,
    ollama: OllamaClient,
    top_k: int = 5,
    category: str | None = None,
    target_language: str | None = None,
) -> list[dict]:
    """번역 데이터 벡터 검색."""
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http.models import Filter, FieldCondition, MatchValue

        qemb = await ollama.embed(settings.EMBED_MODEL, query)
        if not qemb:
            return []

        client = AsyncQdrantClient(url=settings.QDRANT_URL)

        conditions = []
        if category:
            conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
        if target_language:
            conditions.append(FieldCondition(key="target_language", match=MatchValue(value=target_language)))

        flt = Filter(must=conditions) if conditions else None

        results = await client.search(
            collection_name=TRANSLATION_COLLECTION,
            query_vector=qemb,
            query_filter=flt,
            limit=top_k,
        )
        await client.close()
        return [
            {
                "score": r.score,
                "text": r.payload.get("text", ""),
                "translation": r.payload.get("translation", ""),
                "doc_no": r.payload.get("doc_no", ""),
                "category": r.payload.get("category", ""),
                "target_language": r.payload.get("target_language", ""),
                "domain": r.payload.get("domain", ""),
            }
            for r in results
        ]
    except Exception:
        return []
