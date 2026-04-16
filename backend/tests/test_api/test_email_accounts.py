from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.api.email_accounts as accounts_api


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


async def test_email_account_test_connection_success(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account()
    scanner = SimpleNamespace(test_connection=AsyncMock(return_value=True))
    monkeypatch.setattr(accounts_api.ScannerFactory, "get_scanner", lambda account_type: scanner)

    response = await client.post(f"/api/v1/accounts/{account.id}/test-connection", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "detail": None}
    scanner.test_connection.assert_awaited_once_with(account)


async def test_email_account_test_connection_false_result(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="pop3")
    scanner = SimpleNamespace(test_connection=AsyncMock(return_value=False))
    monkeypatch.setattr(accounts_api.ScannerFactory, "get_scanner", lambda account_type: scanner)

    response = await client.post(f"/api/v1/accounts/{account.id}/test-connection", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Connection test failed"}
    scanner.test_connection.assert_awaited_once_with(account)


async def test_email_account_test_connection_exception(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    scanner = SimpleNamespace(test_connection=AsyncMock(side_effect=RuntimeError("secret failure details")))
    monkeypatch.setattr(accounts_api.ScannerFactory, "get_scanner", lambda account_type: scanner)

    response = await client.post(f"/api/v1/accounts/{account.id}/test-connection", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Connection test failed"}
    scanner.test_connection.assert_awaited_once_with(account)


async def test_email_account_test_connection_missing_account(client, auth_headers) -> None:
    response = await client.post("/api/v1/accounts/999/test-connection", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Account not found"}
