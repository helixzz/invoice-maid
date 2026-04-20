from __future__ import annotations


async def test_login_success(client, admin_user) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "testpass"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_rejects_bad_password(client, admin_user) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


async def test_login_rejects_unknown_email(client, admin_user) -> None:
    del admin_user
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@nowhere.example", "password": "testpass"},
    )
    assert response.status_code == 401


async def test_login_accepts_unqualified_hostname_email(
    client, db, settings, monkeypatch
) -> None:
    """Self-hosted deployments use bare hostnames like ``admin@local`` as
    the bootstrap email. pydantic's ``EmailStr`` rejects these because the
    domain has no period; the login schema must accept them so operators
    running on an intranet or VM can log in."""
    from app.models import User as _User

    del monkeypatch, settings
    user = _User(
        email="admin@local",
        hashed_password="hashed:testpass",
        is_active=True,
        is_admin=True,
    )
    db.add(user)
    await db.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@local", "password": "testpass"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_login_requires_email(client) -> None:
    """Backward-compat callers that send only ``password`` must get a
    clear 422, not be silently accepted as 'admin'."""
    response = await client.post("/api/v1/auth/login", json={"password": "testpass"})
    assert response.status_code == 422


async def test_login_rejects_deactivated_user(client, admin_user, db) -> None:
    admin_user.is_active = False
    await db.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "testpass"},
    )
    assert response.status_code == 401


async def test_login_rate_limit_allows_first_ten_requests(client, admin_user) -> None:
    responses = [
        await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": "testpass"},
        )
        for _ in range(10)
    ]
    for i, r in enumerate(responses):
        assert r.status_code == 200, f"request {i}: {r.status_code} {r.text}"


async def test_login_rate_limit_blocks_eleventh_request(client, admin_user) -> None:
    for _ in range(10):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_user.email, "password": "testpass"},
        )
        assert r.status_code == 200

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "testpass"},
    )
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


async def test_me_returns_current_user(client, auth_headers, admin_user) -> None:
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == admin_user.id
    assert body["email"] == admin_user.email
    assert body["is_admin"] is True


async def test_me_requires_auth(client) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_logout_revokes_current_session(client, auth_headers) -> None:
    assert (await client.get("/api/v1/auth/me", headers=auth_headers)).status_code == 200

    logout_resp = await client.post("/api/v1/auth/logout", headers=auth_headers)
    assert logout_resp.status_code == 200
    assert logout_resp.json()["success"] is True

    assert (await client.get("/api/v1/auth/me", headers=auth_headers)).status_code == 401


async def test_logout_all_revokes_every_session(client, admin_user, db) -> None:
    """Creating two separate sessions, then /logout-all from one of them,
    must invalidate both. This is the ``revoke all devices'' feature."""
    from app.services.auth_service import create_access_token, create_user_session

    token1 = create_access_token({"sub": str(admin_user.id)})
    await create_user_session(db, admin_user, token1)
    token2 = create_access_token({"sub": str(admin_user.id)})
    await create_user_session(db, admin_user, token2)

    h1 = {"Authorization": f"Bearer {token1}"}
    h2 = {"Authorization": f"Bearer {token2}"}

    assert (await client.get("/api/v1/auth/me", headers=h1)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=h2)).status_code == 200

    resp = await client.post("/api/v1/auth/logout-all", headers=h1)
    assert resp.status_code == 200

    assert (await client.get("/api/v1/auth/me", headers=h1)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=h2)).status_code == 401


async def test_list_sessions_returns_active_sessions(
    client, auth_headers, admin_user, db
) -> None:
    from app.services.auth_service import create_access_token, create_user_session

    extra_token = create_access_token({"sub": str(admin_user.id)})
    await create_user_session(
        db, admin_user, extra_token, user_agent="Mozilla/5.0", ip_address="127.0.0.1"
    )

    resp = await client.get("/api/v1/auth/sessions", headers=auth_headers)
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) >= 2
    assert all("expires_at" in s for s in sessions)
    assert any(s.get("user_agent") == "Mozilla/5.0" for s in sessions)


async def test_list_sessions_excludes_revoked(
    client, auth_headers, admin_user, db
) -> None:
    from app.models import UserSession
    from sqlalchemy import select
    from datetime import datetime, timezone

    result = await db.execute(
        select(UserSession).where(UserSession.user_id == admin_user.id)
    )
    sessions = list(result.scalars().all())
    if len(sessions) > 0:
        sessions[0].revoked_at = datetime.now(timezone.utc)
        await db.commit()

    from app.services.auth_service import create_access_token, create_user_session
    token = create_access_token({"sub": str(admin_user.id)})
    await create_user_session(db, admin_user, token)
    fresh_headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/auth/sessions", headers=fresh_headers)
    assert resp.status_code == 200
    session_ids = [s["id"] for s in resp.json()]
    if sessions:
        assert sessions[0].id not in session_ids


async def test_login_records_user_agent_and_ip(
    client, admin_user
) -> None:
    """Login must persist client fingerprint on the created session row.
    These fields are surfaced by GET /auth/sessions for the session-revocation
    UI in Phase 5."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "testpass"},
        headers={"User-Agent": "test-agent/1.0"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    list_resp = await client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    sessions = list_resp.json()
    assert any(s.get("user_agent") == "test-agent/1.0" for s in sessions)


async def test_login_handles_missing_client_ip(
    client, admin_user, monkeypatch
) -> None:
    """If the ASGI scope lacks a .client attribute (rare but possible behind
    some reverse-proxy configs), _client_ip must return None gracefully
    rather than raising."""
    from app.api import auth as auth_module

    class _FakeRequest:
        headers = {"User-Agent": "probe"}
        client = None

    class _FakeRequestNoAttr:
        headers = {"User-Agent": "probe"}

    assert auth_module._client_ip(_FakeRequest()) is None
    assert auth_module._client_ip(_FakeRequestNoAttr()) is None

    class _BlankClient:
        host = ""

    class _FakeRequestBlankHost:
        headers = {"User-Agent": "probe"}
        client = _BlankClient()

    assert auth_module._client_ip(_FakeRequestBlankHost()) is None


async def test_register_disabled_by_default(client, settings) -> None:
    settings.ALLOW_REGISTRATION = False

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@example.com",
            "password": "newpass123",
            "password_confirm": "newpass123",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Registration is disabled on this instance"}


async def test_register_creates_user_when_allowed(client, settings, db) -> None:
    settings.ALLOW_REGISTRATION = True

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@example.com",
            "password": "newpass123",
            "password_confirm": "newpass123",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"

    from app.models import User
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.email == "new@example.com"))
    user = result.scalar_one()
    assert user.is_admin is False
    assert user.is_active is True
    assert user.hashed_password == "hashed:newpass123"


async def test_register_rejects_duplicate_email(client, settings, admin_user) -> None:
    settings.ALLOW_REGISTRATION = True

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": admin_user.email,
            "password": "newpass123",
            "password_confirm": "newpass123",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}


async def test_register_rejects_password_mismatch(client, settings) -> None:
    settings.ALLOW_REGISTRATION = True

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "mismatch@example.com",
            "password": "password123",
            "password_confirm": "differentpass",
        },
    )

    assert response.status_code == 422


async def test_register_rejects_bare_username_without_at_sign(
    client, settings
) -> None:
    settings.ALLOW_REGISTRATION = True

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "no-at-sign",
            "password": "newpass123",
            "password_confirm": "newpass123",
        },
    )

    assert response.status_code == 422


async def test_register_rejects_leading_trailing_at_sign(client, settings) -> None:
    settings.ALLOW_REGISTRATION = True

    for bad_email in ("@domain.com", "local@"):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": bad_email,
                "password": "newpass123",
                "password_confirm": "newpass123",
            },
        )
        assert response.status_code == 422, f"expected 422 for {bad_email!r}"


async def test_register_rejects_short_password(client, settings) -> None:
    settings.ALLOW_REGISTRATION = True

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "short@example.com",
            "password": "short",
            "password_confirm": "short",
        },
    )

    assert response.status_code == 422


async def test_change_password_success(
    client, auth_headers, admin_user, db, settings
) -> None:
    """Change-password revokes OTHER sessions but preserves the caller's
    current session so this device stays logged in. We seed a second
    session for the SAME user (simulating the user being logged in on
    another device) and verify it gets revoked."""
    from app.services.auth_service import create_access_token, create_user_session

    other_token = create_access_token({"sub": str(admin_user.id)})
    await create_user_session(db, admin_user, other_token, settings=settings)
    other_headers = {"Authorization": f"Bearer {other_token}"}

    pre_check = await client.get("/api/v1/auth/me", headers=other_headers)
    assert pre_check.status_code == 200

    response = await client.put(
        "/api/v1/auth/me/password",
        headers=auth_headers,
        json={
            "current_password": "testpass",
            "new_password": "brand-new-password",
            "new_password_confirm": "brand-new-password",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}

    from sqlalchemy import select
    from app.models import User

    refreshed = (
        await db.execute(select(User).where(User.id == admin_user.id))
    ).scalar_one()
    assert refreshed.hashed_password == "hashed:brand-new-password"

    still_valid = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert still_valid.status_code == 200

    other_now_revoked = await client.get("/api/v1/auth/me", headers=other_headers)
    assert other_now_revoked.status_code == 401


async def test_change_password_only_revokes_other_users_not_peers(
    client, auth_headers, admin_user, second_auth_headers
) -> None:
    """Changing admin_user's password must NOT affect sessions belonging
    to other users. This is the tenant-isolation invariant for the
    session-revocation side-effect."""
    del admin_user

    response = await client.put(
        "/api/v1/auth/me/password",
        headers=auth_headers,
        json={
            "current_password": "testpass",
            "new_password": "another-new-password",
            "new_password_confirm": "another-new-password",
        },
    )

    assert response.status_code == 200

    second_still_works = await client.get(
        "/api/v1/auth/me", headers=second_auth_headers
    )
    assert second_still_works.status_code == 200


async def test_change_password_wrong_current(client, auth_headers) -> None:
    response = await client.put(
        "/api/v1/auth/me/password",
        headers=auth_headers,
        json={
            "current_password": "wrong",
            "new_password": "brand-new-password",
            "new_password_confirm": "brand-new-password",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Current password is incorrect"}


async def test_change_password_mismatch(client, auth_headers) -> None:
    response = await client.put(
        "/api/v1/auth/me/password",
        headers=auth_headers,
        json={
            "current_password": "testpass",
            "new_password": "brand-new-password",
            "new_password_confirm": "different-password-here",
        },
    )

    assert response.status_code == 422


async def test_change_password_must_differ_from_current(
    client, auth_headers
) -> None:
    response = await client.put(
        "/api/v1/auth/me/password",
        headers=auth_headers,
        json={
            "current_password": "testpass",
            "new_password": "testpass",
            "new_password_confirm": "testpass",
        },
    )

    assert response.status_code == 422


async def test_change_password_requires_auth(client) -> None:
    response = await client.put(
        "/api/v1/auth/me/password",
        json={
            "current_password": "x",
            "new_password": "brand-new-password",
            "new_password_confirm": "brand-new-password",
        },
    )

    assert response.status_code == 401
