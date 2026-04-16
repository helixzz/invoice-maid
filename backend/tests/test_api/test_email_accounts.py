from __future__ import annotations


async def test_email_accounts_crud(client, auth_headers) -> None:
    create_response = await client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={
            "name": "Mailbox",
            "type": "imap",
            "host": "imap.example.com",
            "port": 993,
            "username": "user@example.com",
            "password": "secret",
            "is_active": True,
        },
    )
    assert create_response.status_code == 201
    account_id = create_response.json()["id"]

    list_response = await client.get("/api/v1/accounts", headers=auth_headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    update_response = await client.put(
        f"/api/v1/accounts/{account_id}",
        headers=auth_headers,
        json={"name": "Updated", "password": "new-secret", "is_active": False},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated"
    assert update_response.json()["is_active"] is False

    delete_response = await client.delete(f"/api/v1/accounts/{account_id}", headers=auth_headers)
    assert delete_response.status_code == 204


async def test_email_accounts_not_found_and_auth_required(client, auth_headers) -> None:
    unauthorized = await client.get("/api/v1/accounts")
    assert unauthorized.status_code == 401

    update_response = await client.put("/api/v1/accounts/999", headers=auth_headers, json={"name": "missing"})
    assert update_response.status_code == 404

    delete_response = await client.delete("/api/v1/accounts/999", headers=auth_headers)
    assert delete_response.status_code == 404


async def test_email_account_update_all_optional_fields(client, auth_headers) -> None:
    created = await client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={
            "name": "Mailbox",
            "type": "imap",
            "host": "old.example.com",
            "port": 993,
            "username": "old@example.com",
            "password": "secret",
        },
    )
    account_id = created.json()["id"]

    updated = await client.put(
        f"/api/v1/accounts/{account_id}",
        headers=auth_headers,
        json={"host": "new.example.com", "port": 995, "username": "new@example.com"},
    )

    assert updated.status_code == 200
    assert updated.json()["host"] == "new.example.com"
    assert updated.json()["port"] == 995
    assert updated.json()["username"] == "new@example.com"
