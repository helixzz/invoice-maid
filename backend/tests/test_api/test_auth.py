from __future__ import annotations


async def test_login_success(client) -> None:
    response = await client.post("/api/v1/auth/login", json={"password": "testpass"})

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


async def test_login_rejects_bad_password(client) -> None:
    response = await client.post("/api/v1/auth/login", json={"password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect password"


async def test_login_rate_limit_allows_first_ten_requests(client) -> None:
    responses = [await client.post("/api/v1/auth/login", json={"password": "testpass"}) for _ in range(10)]

    assert all(response.status_code == 200 for response in responses)


async def test_login_rate_limit_blocks_eleventh_request(client) -> None:
    for _ in range(10):
        response = await client.post("/api/v1/auth/login", json={"password": "testpass"})
        assert response.status_code == 200

    blocked = await client.post("/api/v1/auth/login", json={"password": "testpass"})

    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
