"""Fixed-window rate limiter backed by Postgres, so the count is shared by
every instance (the old in-memory one was per-process). Keyed by the
authenticated user, not IP.
"""

import time

from fastapi import Depends, HTTPException

from app.auth import Principal, require_member
from app.config import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_SECONDS
from app.db import pool


async def enforce_rate_limit(principal: Principal = Depends(require_member)) -> Principal:
    bucket = int(time.time() // RATE_LIMIT_WINDOW_SECONDS)
    async with pool().connection() as conn:
        cur = await conn.execute(
            "INSERT INTO rate_limits (key, bucket, count) VALUES (%s, %s, 1) "
            "ON CONFLICT (key, bucket) DO UPDATE SET count = rate_limits.count + 1 "
            "RETURNING count",
            (f"user:{principal.user_id}", bucket),
        )
        row = await cur.fetchone()
        # Old buckets are dead weight; cheap opportunistic cleanup.
        await conn.execute("DELETE FROM rate_limits WHERE bucket < %s", (bucket - 5,))
    if row["count"] > RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many messages, please slow down.",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )
    return principal
