"""Admin-editable persona/model per agent, with a short in-process TTL cache
so a normal turn no longer costs a database round-trip. An edit is visible
on every instance within AGENT_CONFIG_TTL_SECONDS. The safety RULES stay in
code (app/prompts.py) and are always appended after the persona.
"""

import time

from app.config import AGENT_CONFIG_TTL_SECONDS
from app.db import pool

_cache: dict[str, tuple[float, dict]] = {}


async def _row(agent_slug: str) -> dict:
    hit = _cache.get(agent_slug)
    if hit and time.monotonic() - hit[0] < AGENT_CONFIG_TTL_SECONDS:
        return hit[1]
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT system_prompt, model FROM agent_configs WHERE agent_slug = %s", (agent_slug,))
        row = await cur.fetchone() or {"system_prompt": None, "model": None}
    _cache[agent_slug] = (time.monotonic(), row)
    return row


async def get_system_prompt(agent_slug: str, default: str) -> str:
    return (await _row(agent_slug))["system_prompt"] or default


async def get_agent_model(agent_slug: str, default: str) -> str:
    return (await _row(agent_slug))["model"] or default


async def set_system_prompt(agent_slug: str, system_prompt: str) -> None:
    async with pool().connection() as conn:
        await conn.execute(
            "INSERT INTO agent_configs (agent_slug, system_prompt, updated_at) VALUES (%s, %s, NOW()) "
            "ON CONFLICT (agent_slug) DO UPDATE SET system_prompt = EXCLUDED.system_prompt, updated_at = NOW()",
            (agent_slug, system_prompt))
    _cache.pop(agent_slug, None)


async def reset_system_prompt(agent_slug: str) -> None:
    async with pool().connection() as conn:
        await conn.execute(
            "UPDATE agent_configs SET system_prompt = NULL, updated_at = NOW() WHERE agent_slug = %s",
            (agent_slug,))
    _cache.pop(agent_slug, None)
