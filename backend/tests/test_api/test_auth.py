from __future__ import annotations


async def test_login_success(client) -> None:
    response = await client.post("/api/v1/auth/login", json={"password": "testpass"})

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


async def test_login_rejects_bad_password(client) -> None:
    response = await client.post("/api/v1/auth/login", json={"password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect password"
