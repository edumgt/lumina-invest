"""Neo4j 기반 종목 관계 그래프 서비스.

그래프 구조:
  노드: Company, Sector
  관계: BELONGS_TO (Company→Sector), COMPETES_WITH, SUPPLIES_TO
"""
from __future__ import annotations
from app.database.neo4j import get_neo4j

# ── 시드 데이터 ────────────────────────────────────────────────────────────────

_COMPANIES = [
    {"symbol": "005930.KS", "name": "삼성전자",   "market": "KOSPI", "sector": "IT/반도체"},
    {"symbol": "000660.KS", "name": "SK하이닉스", "market": "KOSPI", "sector": "IT/반도체"},
    {"symbol": "035420.KS", "name": "NAVER",      "market": "KOSPI", "sector": "IT/인터넷"},
    {"symbol": "035720.KS", "name": "카카오",      "market": "KOSPI", "sector": "IT/인터넷"},
    {"symbol": "005380.KS", "name": "현대자동차", "market": "KOSPI", "sector": "자동차"},
    {"symbol": "000270.KS", "name": "기아",        "market": "KOSPI", "sector": "자동차"},
    {"symbol": "012330.KS", "name": "현대모비스", "market": "KOSPI", "sector": "자동차부품"},
    {"symbol": "051910.KS", "name": "LG화학",      "market": "KOSPI", "sector": "화학/배터리"},
    {"symbol": "006400.KS", "name": "삼성SDI",     "market": "KOSPI", "sector": "화학/배터리"},
    {"symbol": "373220.KS", "name": "LG에너지솔루션", "market": "KOSPI", "sector": "화학/배터리"},
    {"symbol": "005490.KS", "name": "POSCO홀딩스", "market": "KOSPI", "sector": "철강/소재"},
    {"symbol": "068270.KS", "name": "셀트리온",   "market": "KOSPI", "sector": "바이오/헬스케어"},
    {"symbol": "207940.KS", "name": "삼성바이오로직스", "market": "KOSPI", "sector": "바이오/헬스케어"},
    {"symbol": "034730.KS", "name": "SK",          "market": "KOSPI", "sector": "지주/에너지"},
    {"symbol": "096770.KS", "name": "SK이노베이션", "market": "KOSPI", "sector": "화학/배터리"},
]

# (source_symbol, target_symbol, relation_type)
_RELATIONSHIPS: list[tuple[str, str, str]] = [
    # 경쟁 관계
    ("005930.KS", "000660.KS", "COMPETES_WITH"),
    ("035420.KS", "035720.KS", "COMPETES_WITH"),
    ("005380.KS", "000270.KS", "COMPETES_WITH"),
    ("051910.KS", "006400.KS", "COMPETES_WITH"),
    ("051910.KS", "373220.KS", "COMPETES_WITH"),
    ("006400.KS", "373220.KS", "COMPETES_WITH"),
    ("068270.KS", "207940.KS", "COMPETES_WITH"),
    # 공급망 관계
    ("000660.KS", "005930.KS", "SUPPLIES_TO"),   # SK하이닉스 → 삼성전자
    ("012330.KS", "005380.KS", "SUPPLIES_TO"),   # 현대모비스 → 현대자동차
    ("012330.KS", "000270.KS", "SUPPLIES_TO"),   # 현대모비스 → 기아
    ("051910.KS", "373220.KS", "SUPPLIES_TO"),   # LG화학 → LG에너지솔루션
    ("096770.KS", "006400.KS", "SUPPLIES_TO"),   # SK이노베이션 → 삼성SDI
    ("005490.KS", "005380.KS", "SUPPLIES_TO"),   # POSCO → 현대자동차
    ("005490.KS", "000270.KS", "SUPPLIES_TO"),   # POSCO → 기아
]


# ── 그래프 초기화 ──────────────────────────────────────────────────────────────

async def seed_graph() -> None:
    """Company·Sector 노드와 관계를 Neo4j에 삽입한다. 이미 존재하면 MERGE로 건너뛴다."""
    driver = get_neo4j()
    async with driver.session() as session:
        # Sector 노드
        sectors = {c["sector"] for c in _COMPANIES}
        for sector in sectors:
            await session.run(
                "MERGE (s:Sector {name: $name})",
                name=sector,
            )

        # Company 노드 + BELONGS_TO 관계
        for c in _COMPANIES:
            await session.run(
                """
                MERGE (co:Company {symbol: $symbol})
                SET co.name = $name, co.market = $market
                WITH co
                MATCH (s:Sector {name: $sector})
                MERGE (co)-[:BELONGS_TO]->(s)
                """,
                symbol=c["symbol"],
                name=c["name"],
                market=c["market"],
                sector=c["sector"],
            )

        # 종목 간 관계
        for src, dst, rel in _RELATIONSHIPS:
            await session.run(
                f"""
                MATCH (a:Company {{symbol: $src}})
                MATCH (b:Company {{symbol: $dst}})
                MERGE (a)-[:{rel}]->(b)
                """,
                src=src,
                dst=dst,
            )


# ── 쿼리 함수 ─────────────────────────────────────────────────────────────────

async def get_related_stocks(symbol: str) -> dict:
    """한 종목의 경쟁사, 공급사, 수요처, 동일 섹터 종목을 반환한다."""
    driver = get_neo4j()
    async with driver.session() as session:
        # 기본 정보 + 섹터
        base = await session.run(
            """
            MATCH (c:Company {symbol: $symbol})-[:BELONGS_TO]->(s:Sector)
            RETURN c.name AS name, c.symbol AS symbol, s.name AS sector
            """,
            symbol=symbol,
        )
        base_rec = await base.single()
        if not base_rec:
            return {"symbol": symbol, "found": False}

        # 경쟁사
        comp = await session.run(
            """
            MATCH (c:Company {symbol: $symbol})-[:COMPETES_WITH]-(peer:Company)
            RETURN peer.symbol AS symbol, peer.name AS name
            """,
            symbol=symbol,
        )
        competitors = [{"symbol": r["symbol"], "name": r["name"]} async for r in comp]

        # 공급사 (나에게 공급)
        sup = await session.run(
            """
            MATCH (supplier:Company)-[:SUPPLIES_TO]->(c:Company {symbol: $symbol})
            RETURN supplier.symbol AS symbol, supplier.name AS name
            """,
            symbol=symbol,
        )
        suppliers = [{"symbol": r["symbol"], "name": r["name"]} async for r in sup]

        # 수요처 (내가 공급)
        cust = await session.run(
            """
            MATCH (c:Company {symbol: $symbol})-[:SUPPLIES_TO]->(customer:Company)
            RETURN customer.symbol AS symbol, customer.name AS name
            """,
            symbol=symbol,
        )
        customers = [{"symbol": r["symbol"], "name": r["name"]} async for r in cust]

        # 동일 섹터 종목
        peers = await session.run(
            """
            MATCH (c:Company {symbol: $symbol})-[:BELONGS_TO]->(s:Sector)
                  <-[:BELONGS_TO]-(peer:Company)
            WHERE peer.symbol <> $symbol
            RETURN peer.symbol AS symbol, peer.name AS name
            """,
            symbol=symbol,
        )
        sector_peers = [{"symbol": r["symbol"], "name": r["name"]} async for r in peers]

    return {
        "symbol": symbol,
        "name": base_rec["name"],
        "sector": base_rec["sector"],
        "found": True,
        "competitors": competitors,
        "suppliers": suppliers,
        "customers": customers,
        "sector_peers": sector_peers,
    }


async def get_sector_stocks(sector: str) -> list[dict]:
    """섹터에 속한 종목 목록을 반환한다."""
    driver = get_neo4j()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Company)-[:BELONGS_TO]->(s:Sector)
            WHERE s.name CONTAINS $sector
            RETURN c.symbol AS symbol, c.name AS name, s.name AS sector
            ORDER BY c.name
            """,
            sector=sector,
        )
        return [{"symbol": r["symbol"], "name": r["name"], "sector": r["sector"]}
                async for r in result]


async def get_supply_chain_path(from_symbol: str, to_symbol: str) -> list[dict]:
    """두 종목 사이의 최단 공급망 경로를 반환한다 (최대 4홉)."""
    driver = get_neo4j()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH path = shortestPath(
                (a:Company {symbol: $from_sym})-[:SUPPLIES_TO*..4]->(b:Company {symbol: $to_sym})
            )
            RETURN [node IN nodes(path) | {symbol: node.symbol, name: node.name}] AS chain
            """,
            from_sym=from_symbol,
            to_sym=to_symbol,
        )
        record = await result.single()
        return record["chain"] if record else []


async def link_document_to_companies(
    doc_id: str,
    title: str,
    source: str,
    url: str,
    mentioned_symbols: list[str],
) -> None:
    """문서 노드를 생성하고 언급된 종목과 MENTIONS 관계를 연결한다."""
    driver = get_neo4j()
    async with driver.session() as session:
        await session.run(
            """
            MERGE (d:Document {doc_id: $doc_id})
            SET d.title = $title, d.source = $source, d.url = $url
            """,
            doc_id=doc_id,
            title=title,
            source=source,
            url=url,
        )
        for symbol in mentioned_symbols:
            await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})
                MATCH (c:Company {symbol: $symbol})
                MERGE (d)-[:MENTIONS]->(c)
                """,
                doc_id=doc_id,
                symbol=symbol,
            )


async def get_documents_for_symbol(symbol: str) -> list[dict]:
    """특정 종목을 언급한 문서 목록을 반환한다."""
    driver = get_neo4j()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (d:Document)-[:MENTIONS]->(c:Company {symbol: $symbol})
            RETURN d.doc_id AS doc_id, d.title AS title,
                   d.source AS source, d.url AS url
            ORDER BY d.title
            LIMIT 20
            """,
            symbol=symbol,
        )
        return [
            {"doc_id": r["doc_id"], "title": r["title"],
             "source": r["source"], "url": r["url"]}
            async for r in result
        ]


def extract_mentioned_symbols(text: str) -> list[str]:
    """텍스트에서 알려진 회사명을 매칭해 심볼 목록을 반환한다."""
    name_to_symbol = {c["name"]: c["symbol"] for c in _COMPANIES}
    found = []
    for name, symbol in name_to_symbol.items():
        if name in text:
            found.append(symbol)
    return found
