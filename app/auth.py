"""Supabase JWT verification. Identity comes ONLY from the signed token -
never from the request body - so a caller cannot act as another member.

Claims used (set them in Supabase on the user's app_metadata, which users
cannot edit themselves - unlike user_metadata):
  app_metadata.member_id  - the member's numeric ProfitConnect id
  app_metadata.role       - "admin" for dashboard/staff users
"""

from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from app.config import JWT_AUDIENCE, SUPABASE_JWT_SECRET, SUPABASE_URL

_jwks: PyJWKClient | None = None


@dataclass(frozen=True)
class Principal:
    user_id: str
    member_id: int | None
    is_admin: bool


def _decode(token: str) -> dict:
    global _jwks
    if SUPABASE_JWT_SECRET:
        return jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience=JWT_AUDIENCE)
    if not SUPABASE_URL:
        raise RuntimeError("Set SUPABASE_JWT_SECRET or SUPABASE_URL to verify tokens")
    if _jwks is None:
        _jwks = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True)
    key = _jwks.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=JWT_AUDIENCE)


def principal_from_token(token: str) -> Principal:
    claims = _decode(token)
    meta = claims.get("app_metadata") or {}
    raw_member = meta.get("member_id")
    try:
        member_id = int(raw_member) if raw_member is not None else None
    except (TypeError, ValueError):
        member_id = None
    return Principal(
        user_id=str(claims["sub"]),
        member_id=member_id,
        is_admin=meta.get("role") == "admin",
    )


def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        return principal_from_token(authorization[7:].strip())
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_member(authorization: str | None = Header(default=None)) -> Principal:
    p = get_principal(authorization)
    if p.member_id is None:
        raise HTTPException(status_code=403, detail="Account is not linked to a member")
    return p


def require_admin(authorization: str | None = Header(default=None)) -> Principal:
    p = get_principal(authorization)
    if not p.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return p
