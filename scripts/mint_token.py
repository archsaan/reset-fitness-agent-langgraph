"""Mint a short-lived test token for local/Render testing (HS256, signed
with JWT_SECRET). Real member tokens must be minted by your auth service
after it verifies who the member is - never expose this to clients.

    python -m scripts.mint_token --member 6
    python -m scripts.mint_token --admin
"""

import argparse
import time

import jwt

from app.config import JWT_AUDIENCE, SUPABASE_JWT_SECRET


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--member", type=int)
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--minutes", type=int, default=60)
    a = ap.parse_args()
    if not SUPABASE_JWT_SECRET:
        raise SystemExit("Set JWT_SECRET first")
    meta = {}
    if a.member is not None:
        meta["member_id"] = a.member
    if a.admin:
        meta["role"] = "admin"
    print(jwt.encode(
        {"sub": f"test-{a.member or 'admin'}", "aud": JWT_AUDIENCE,
         "exp": int(time.time()) + a.minutes * 60, "app_metadata": meta},
        SUPABASE_JWT_SECRET, algorithm="HS256"))


if __name__ == "__main__":
    main()
