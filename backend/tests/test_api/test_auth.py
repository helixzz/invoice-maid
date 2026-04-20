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
