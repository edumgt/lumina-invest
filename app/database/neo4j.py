"""Neo4j 비동기 드라이버 연결 관리."""
from __future__ import annotations
from neo4j import AsyncGraphDatabase, AsyncDriver
from app.config import settings

_driver: AsyncDriver | None = None


async def connect_neo4j() -> None:
    global _driver
    _driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    await _driver.verify_connectivity()


async def close_neo4j() -> None:
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


def get_neo4j() -> AsyncDriver:
    if _driver is None:
        raise RuntimeError("Neo4j not connected")
    return _driver


async def ensure_graph_schema() -> None:
    """제약 조건과 인덱스를 생성한다."""
    driver = get_neo4j()
    async with driver.session() as session:
        await session.run(
            "CREATE CONSTRAINT company_symbol IF NOT EXISTS "
            "FOR (c:Company) REQUIRE c.symbol IS UNIQUE"
        )
        await session.run(
            "CREATE CONSTRAINT sector_name IF NOT EXISTS "
            "FOR (s:Sector) REQUIRE s.name IS UNIQUE"
        )
        await session.run(
            "CREATE INDEX company_name IF NOT EXISTS "
            "FOR (c:Company) ON (c.name)"
        )
        await session.run(
            "CREATE INDEX document_source IF NOT EXISTS "
            "FOR (d:Document) ON (d.source)"
        )
