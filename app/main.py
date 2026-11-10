import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app import db
from app.agents.tribe import build_graph
from app.config import CORS_ORIGINS
from app.routers.chat import router as chat_router
from app.tools import http

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("reset_fitness.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await db.open_pool()
    # Tables are created by scripts/setup_checkpointer.py (direct
    # connection), not here - startup must not race across instances.
    checkpointer = AsyncPostgresSaver(pool)
    app.state.tribe_graph = build_graph(checkpointer)
    yield
    await http.aclose()
    await db.close_pool()


app = FastAPI(title="Reset Fitness Agents", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(chat_router)


@app.get("/healthz")
async def healthz():
    """Real DB check through the pool. 503 on failure so uptime monitors alert."""
    try:
        async with db.pool().connection() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception:
        logger.exception("Health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "error"})


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("%s %s failed after %.0fms", request.method, request.url.path,
                         (time.monotonic() - start) * 1000)
        raise
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    logger.log(level, "%s %s -> %s (%.0fms)", request.method, request.url.path,
               response.status_code, (time.monotonic() - start) * 1000)
    return response
