"""Async connection pool. Raw SQL by design — the schema is hand-tuned
(generated tsvector columns, partial indexes, window-function views) and an ORM
would only obscure it."""
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings

_pool: AsyncConnectionPool | None = None


async def open_pool() -> None:
    global _pool
    _pool = AsyncConnectionPool(
        settings.database_url, min_size=1, max_size=10, open=False,
        kwargs={"row_factory": dict_row},
    )
    await _pool.open(wait=True, timeout=30)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


@asynccontextmanager
async def connection():
    if _pool is None:
        raise RuntimeError("pool not initialised")
    async with _pool.connection() as conn:
        yield conn


async def fetch_all(sql: str, params: dict | None = None) -> list[dict]:
    async with connection() as conn:
        cur = await conn.execute(sql, params or {})
        return await cur.fetchall()


async def fetch_one(sql: str, params: dict | None = None) -> dict | None:
    async with connection() as conn:
        cur = await conn.execute(sql, params or {})
        return await cur.fetchone()


async def execute(sql: str, params: dict | None = None) -> None:
    async with connection() as conn:
        await conn.execute(sql, params or {})
