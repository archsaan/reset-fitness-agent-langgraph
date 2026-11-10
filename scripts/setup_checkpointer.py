"""One-time (and after upgrading langgraph-checkpoint-postgres): create the
LangGraph checkpoint tables. Uses DIRECT_DATABASE_URL (session mode, port
5432) because DDL/migrations don't work through the transaction pooler.

    python -m scripts.setup_checkpointer
"""

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import DIRECT_DATABASE_URL


async def main() -> None:
    async with AsyncPostgresSaver.from_conn_string(DIRECT_DATABASE_URL) as saver:
        await saver.setup()
    print("Checkpoint tables ready.")


if __name__ == "__main__":
    asyncio.run(main())
