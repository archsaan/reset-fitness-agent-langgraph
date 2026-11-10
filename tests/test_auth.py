import time

import jwt
import pytest
from fastapi import HTTPException

from app import auth

SECRET = "x" * 40


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", SECRET)


def _token(meta=None, **over):
    claims = {"sub": "u1", "aud": "authenticated", "exp": int(time.time()) + 60,
              "app_metadata": meta or {}} | over
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_member_id_comes_from_app_metadata():
    p = auth.principal_from_token(_token({"member_id": "55"}))
    assert (p.member_id, p.is_admin) == (55, False)


def test_user_metadata_cannot_grant_identity_or_admin():
    # user_metadata is user-editable in Supabase, so it must be ignored.
    t = _token({}, user_metadata={"member_id": 1, "role": "admin"})
    p = auth.principal_from_token(t)
    assert p.member_id is None and not p.is_admin


def test_admin_role():
    assert auth.principal_from_token(_token({"role": "admin"})).is_admin


def test_rejects_bad_signature_expired_and_missing():
    bad = jwt.encode({"sub": "u", "aud": "authenticated"}, "y" * 40, algorithm="HS256")
    for header in (f"Bearer {bad}", f"Bearer {_token(exp=int(time.time()) - 5)}", None, "Basic abc"):
        with pytest.raises(HTTPException) as e:
            auth.get_principal(header)
        assert e.value.status_code == 401


def test_non_member_account_is_forbidden_from_chat():
    with pytest.raises(HTTPException) as e:
        auth.require_member(f"Bearer {_token({})}")
    assert e.value.status_code == 403


def test_non_admin_is_forbidden_from_admin():
    with pytest.raises(HTTPException) as e:
        auth.require_admin(f"Bearer {_token({'member_id': 1})}")
    assert e.value.status_code == 403
