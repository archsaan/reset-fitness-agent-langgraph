"""One shared async connection pool for the whole app (and the LangGraph
checkpointer). Opened in main.py's lifespan.

Supavisor transaction mode (port 6543) does not support prepared
statements, so prepare_threshold=None is required.
"""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pgvector.psycopg import register_vector_async

from app.config import DATABASE_URL

_pool: AsyncConnectionPool | None = None


async def _configure(conn) -> None:
    await register_vector_async(conn)
    await conn.commit()


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    _pool = AsyncConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        open=False,
        configure=_configure,
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
    )
    await _pool.open(wait=True)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not open")
    return _pool
