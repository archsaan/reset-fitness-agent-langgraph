"""Knowledge base (RAG) + usage logging, async. Same tables and chunking
algorithm as the ADK project, so existing data carries over."""

import logging

import voyageai
from pgvector import Vector

from app.config import EMBEDDING_MODEL, KB_RETRIEVAL_TOP_K, VOYAGE_API_KEY
from app.db import pool

logger = logging.getLogger("reset_fitness.kb")

CHUNK_SIZE_CHARS = 800
CHUNK_OVERLAP_CHARS = 150

_voyage = voyageai.AsyncClient(api_key=VOYAGE_API_KEY) if VOYAGE_API_KEY else None


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = current[-overlap:] if overlap < len(current) else current

    for para in paragraphs:
        if len(para) > chunk_size:
            flush()
            current = ""
            for i in range(0, len(para), chunk_size - overlap):
                piece = para[i:i + chunk_size]
                if piece:
                    chunks.append(piece)
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            flush()
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


async def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    if not texts or _voyage is None:
        return []
    result = await _voyage.embed(texts, model=EMBEDDING_MODEL, input_type=input_type)
    return result.embeddings


# ---- admin CRUD ----------------------------------------------------------

async def list_kb_docs(agent_slug: str) -> list[dict]:
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT id, filename, active, uploaded_at FROM adk_kb_docs "
            "WHERE agent_slug = %s ORDER BY uploaded_at DESC", (agent_slug,))
        return await cur.fetchall()


async def insert_kb_doc(agent_slug: str, filename: str, content: str) -> int:
    async with pool().connection() as conn:
        cur = await conn.execute(
            "INSERT INTO adk_kb_docs (agent_slug, filename, content, active) "
            "VALUES (%s, %s, %s, TRUE) RETURNING id", (agent_slug, filename, content))
        return (await cur.fetchone())["id"]


async def toggle_kb_doc(doc_id: int) -> bool | None:
    async with pool().connection() as conn:
        cur = await conn.execute(
            "UPDATE adk_kb_docs SET active = NOT active WHERE id = %s RETURNING active", (doc_id,))
        row = await cur.fetchone()
        return row["active"] if row else None


async def delete_kb_doc(doc_id: int) -> None:
    async with pool().connection() as conn:
        await conn.execute("DELETE FROM adk_kb_docs WHERE id = %s", (doc_id,))


async def index_kb_doc(doc_id: int, agent_slug: str, content: str) -> int:
    """Chunk + embed + store. Silent no-op without VOYAGE_API_KEY (the doc
    is then served by the full-dump fallback)."""
    chunks = chunk_text(content)
    embeddings = await _embed(chunks, "document") if chunks else []
    if not embeddings:
        return 0
    async with pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM adk_kb_chunks WHERE doc_id = %s", (doc_id,))
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO adk_kb_chunks (doc_id, agent_slug, chunk_text, embedding) "
                    "VALUES (%s, %s, %s, %s)",
                    [(doc_id, agent_slug, c, Vector(e)) for c, e in zip(chunks, embeddings)],
                )
    return len(chunks)


# ---- retrieval -----------------------------------------------------------

async def _active_text(agent_slug: str) -> str:
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT content FROM adk_kb_docs WHERE agent_slug = %s AND active = TRUE", (agent_slug,))
        rows = await cur.fetchall()
    return "\n\n---\n\n".join(r["content"] for r in rows)


async def _retrieve(agent_slug: str, question: str) -> str | None:
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM adk_kb_chunks WHERE agent_slug = %s) AS e", (agent_slug,))
        if not (await cur.fetchone())["e"]:
            return None
    vectors = await _embed([question], "query")
    if not vectors:
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT c.chunk_text FROM adk_kb_chunks c JOIN adk_kb_docs d ON d.id = c.doc_id "
            "WHERE d.active = TRUE AND c.agent_slug = %s "
            "ORDER BY c.embedding <=> %s LIMIT %s",
            (agent_slug, Vector(vectors[0]), KB_RETRIEVAL_TOP_K))
        rows = await cur.fetchall()
    if not rows:
        return "(no matching knowledge base content found for this question)"
    return "\n\n---\n\n".join(r["chunk_text"] for r in rows)


async def get_kb_context(agent_slug: str, question: str | None) -> str:
    """RAG when indexed, full dump otherwise. Retrieval failures degrade,
    they never break the chat."""
    if question:
        try:
            retrieved = await _retrieve(agent_slug, question)
            if retrieved is not None:
                return retrieved
        except Exception:
            logger.exception("KB retrieval failed; falling back to full dump")
    return (await _active_text(agent_slug)) or "(no active knowledge base documents)"


# ---- usage ---------------------------------------------------------------

async def log_usage(agent_slug: str, session_id: str, model: str, input_tokens: int, output_tokens: int) -> None:
    try:
        async with pool().connection() as conn:
            await conn.execute(
                "INSERT INTO usage_logs (agent_slug, session_id, model, input_tokens, output_tokens) "
                "VALUES (%s, %s, %s, %s, %s)",
                (agent_slug, session_id, model, input_tokens, output_tokens))
    except Exception:
        logger.exception("Failed to log usage")
