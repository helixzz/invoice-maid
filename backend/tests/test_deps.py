from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from jose import JWTError

import app.deps as deps
from app.models import User
from app.services.auth_service import (
    create_access_token,
    create_user_session,
    revoke_session,
)


@pytest.fixture
async def active_user(db):
    user = User(
        email="deps-test@example.com",
        hashed_password="hashed:testpass",
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_requires_token(db) -> None:
    with pytest.raises(HTTPException, match="Not authenticated") as exc_info:
        await deps.get_current_user_and_session(db=db)
    assert exc_info.value.status_code == 401


async def test_rejects_expired_token(db, active_user, settings) -> None:
    token = create_access_token({"sub": str(active_user.id)}, expires_delta=timedelta(seconds=-1))
    with pytest.raises(HTTPException, match="Token has expired") as exc_info:
        await deps.get_current_user_and_session(authorization=f"Bearer {token}", db=db)
    assert exc_info.value.status_code == 401


async def test_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch, db) -> None:
    monkeypatch.setattr(deps, "decode_access_token", lambda token: (_ for _ in ()).throw(JWTError("bad")))
    with pytest.raises(HTTPException, match="Invalid authentication credentials"):
        await deps.get_current_user_and_session(authorization="Bearer bad-token", db=db)


async def test_rejects_token_without_matching_session(db, active_user) -> None:
    """A JWT that verifies correctly but has no backing ``user_sessions`` row
    must be rejected. This is the core of the session-revocation contract:
    signing a valid JWT isn't sufficient to grant access — there must be an
    un-revoked session row matching the token hash."""
    token = create_access_token({"sub": str(active_user.id)})
    with pytest.raises(HTTPException, match="Invalid authentication credentials"):
        await deps.get_current_user_and_session(authorization=f"Bearer {token}", db=db)


async def test_accepts_valid_session_and_returns_user(db, active_user, settings) -> None:
    token = create_access_token({"sub": str(active_user.id)})
    await create_user_session(db, active_user, token, settings=settings)

    user, session = await deps.get_current_user_and_session(
        authorization=f"Bearer {token}", db=db
    )
    assert user.id == active_user.id
    assert session.user_id == active_user.id
    assert session.revoked_at is None


async def test_prefers_query_token_over_header(db, active_user, settings) -> None:
    token = create_access_token({"sub": str(active_user.id)})
    await create_user_session(db, active_user, token, settings=settings)

    user, _ = await deps.get_current_user_and_session(
        authorization="Bearer ignored", token=token, db=db
    )
    assert user.id == active_user.id


async def test_rejects_revoked_session(db, active_user, settings) -> None:
    token = create_access_token({"sub": str(active_user.id)})
    session = await create_user_session(db, active_user, token, settings=settings)
    await revoke_session(db, session)

    with pytest.raises(HTTPException, match="Invalid authentication credentials"):
        await deps.get_current_user_and_session(authorization=f"Bearer {token}", db=db)


async def test_rejects_session_for_deactivated_user(db, active_user, settings) -> None:
    from app.services.auth_service import resolve_active_session

    token = create_access_token({"sub": str(active_user.id)})
    await create_user_session(db, active_user, token, settings=settings)

    active_user.is_active = False
    await db.commit()

    with pytest.raises(HTTPException, match="Invalid authentication credentials"):
        await deps.get_current_user_and_session(authorization=f"Bearer {token}", db=db)

    assert await resolve_active_session(db, token) is None


async def test_resolve_session_returns_none_when_session_row_expired(
    db, active_user
) -> None:
    """When the ``user_sessions.expires_at`` column is in the past — regardless
    of the JWT exp claim — ``resolve_active_session`` must return None.
    The two expiration clocks are independent: JWT exp is checked first by
    the decode step, and session expires_at is a second gate that the cleanup
    job uses to evict stale rows. Covers the line 106 ``return None`` branch."""
    from datetime import datetime, timedelta, timezone
    from app.models import UserSession
    from app.services.auth_service import (
        create_access_token,
        hash_token,
        resolve_active_session,
    )

    token = create_access_token({"sub": str(active_user.id)})
    session = UserSession(
        user_id=active_user.id,
        token_hash=hash_token(token),
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(session)
    await db.commit()

    assert await resolve_active_session(db, token) is None


async def test_get_current_user_returns_user_object(db, active_user, settings) -> None:
    token = create_access_token({"sub": str(active_user.id)})
    await create_user_session(db, active_user, token, settings=settings)

    resolved = await deps.get_current_user_and_session(authorization=f"Bearer {token}", db=db)
    user = await deps.get_current_user(resolved=resolved)
    assert user.id == active_user.id
    assert user.email == active_user.email


async def test_resolve_session_handles_naive_expires_at(
    db, active_user, settings
) -> None:
    """SQLite round-trips timezone-aware DATETIME columns as naive Python
    datetimes. ``resolve_active_session`` must coerce those back to UTC
    before comparing against ``datetime.now(timezone.utc)`` — otherwise
    the comparison would raise TypeError on every authenticated request."""
    from datetime import datetime, timedelta, timezone
    from app.models import UserSession
    from app.services.auth_service import (
        create_access_token,
        hash_token,
        resolve_active_session,
    )

    token = create_access_token({"sub": str(active_user.id)})
    session = UserSession(
        user_id=active_user.id,
        token_hash=hash_token(token),
        created_at=datetime.now(timezone.utc),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(session)
    await db.commit()

    resolved = await resolve_active_session(db, token)
    assert resolved is not None
    user, _ = resolved
    assert user.id == active_user.id


async def test_resolve_session_returns_none_for_unknown_token(db) -> None:
    """Raw JWT that was never stored as a UserSession row (e.g. forged token
    that passes JWT signature check but has no session side) must return
    None, not raise."""
    from app.services.auth_service import resolve_active_session

    assert await resolve_active_session(db, "totally-made-up-token") is None


async def test_resolve_session_returns_none_for_orphaned_session(
    db, active_user, settings
) -> None:
    """A session whose user was deactivated while the session was active —
    ``resolve_active_session`` must return None so the request gets 401
    even though the session row itself is still unrevoked.

    Uses is_active=False because CASCADE deletion of the user would also
    drop the session, exercising line 103 instead of line 106."""
    from datetime import datetime, timedelta, timezone
    from app.models import UserSession
    from app.services.auth_service import (
        create_access_token,
        hash_token,
        resolve_active_session,
    )

    token = create_access_token({"sub": str(active_user.id)})
    session = UserSession(
        user_id=active_user.id,
        token_hash=hash_token(token),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(session)
    await db.commit()

    active_user.is_active = False
    await db.commit()

    assert await resolve_active_session(db, token) is None
