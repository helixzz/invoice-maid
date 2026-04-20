from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.api.email_accounts as accounts_api
from sqlalchemy import select

from app.models import EmailAccount
from app.services.email_scanner import OAuthFlowState, oauth_registry


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
    assert response.json() == {"detail": "Not found"}


async def test_outlook_account_creation_auto_assigns_oauth_token_path(client, auth_headers, db, settings) -> None:
    response = await client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={
            "name": "Outlook",
            "type": "outlook",
            "username": "azure-client-id",
            "password": None,
            "is_active": True,
        },
    )

    assert response.status_code == 201
    account_id = response.json()["id"]
    account = await db.scalar(select(EmailAccount).where(EmailAccount.id == account_id))
    assert account is not None
    assert account.oauth_token_path == str((Path(settings.STORAGE_PATH).parent / "oauth" / f"account_{account_id}_token.json").resolve())


async def test_create_outlook_account_auto_detects_personal(client, auth_headers) -> None:
    response = await client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={
            "name": "Personal Outlook",
            "type": "outlook",
            "username": "person@outlook.com",
        },
    )

    assert response.status_code == 201
    assert response.json()["outlook_account_type"] == "personal"


async def test_create_outlook_account_auto_detects_organizational(client, auth_headers) -> None:
    response = await client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={
            "name": "Work Outlook",
            "type": "outlook",
            "username": "person@company.com",
        },
    )

    assert response.status_code == 201
    assert response.json()["outlook_account_type"] == "organizational"


async def test_create_outlook_account_respects_explicit_account_type(client, auth_headers) -> None:
    response = await client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={
            "name": "Forced Work Outlook",
            "type": "outlook",
            "username": "person@outlook.com",
            "outlook_account_type": "organizational",
        },
    )

    assert response.status_code == 201
    assert response.json()["outlook_account_type"] == "organizational"


async def test_update_outlook_account_type(client, auth_headers, create_email_account) -> None:
    account = await create_email_account(type="outlook", username="person@outlook.com", outlook_account_type="personal")

    response = await client.put(
        f"/api/v1/accounts/{account.id}",
        headers=auth_headers,
        json={"outlook_account_type": "organizational"},
    )

    assert response.status_code == 200
    assert response.json()["outlook_account_type"] == "organizational"


async def test_email_account_response_includes_outlook_account_type(client, auth_headers, create_email_account) -> None:
    await create_email_account(type="outlook", username="person@company.com", outlook_account_type="organizational")

    response = await client.get("/api/v1/accounts", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["outlook_account_type"] == "organizational"


async def test_email_account_test_connection_outlook_requires_auth_message(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    scanner = SimpleNamespace(
        test_connection=AsyncMock(side_effect=RuntimeError("Outlook authorization required. Use the Settings page to authenticate."))
    )
    monkeypatch.setattr(accounts_api.ScannerFactory, "get_scanner", lambda account_type: scanner)

    response = await client.post(f"/api/v1/accounts/{account.id}/test-connection", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "detail": "Outlook authorization required. Use the Authenticate button.",
    }


async def test_oauth_initiate_rejects_non_outlook_account(client, auth_headers, create_email_account) -> None:
    account = await create_email_account(type="imap")

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "OAuth is only supported for Outlook accounts"}


async def test_oauth_initiate_missing_account(client, auth_headers) -> None:
    response = await client.post("/api/v1/accounts/999/oauth/initiate", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


async def test_oauth_initiate_returns_authorized_when_cached_token_exists(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=True))

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "status": "authorized",
        "verification_uri": None,
        "user_code": None,
        "expires_at": None,
    }


async def test_oauth_initiate_starts_pending_flow_and_is_idempotent(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        ),
    )
    monkeypatch.setattr(accounts_api, "_attach_flow_task", lambda account, scanner, flow, state: None)

    first = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)
    second = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)

    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    assert first.json()["verification_uri"] == "https://microsoft.com/devicelogin"
    assert first.json()["user_code"] == "ABCD-EFGH"
    assert second.status_code == 200
    assert second.json() == first.json()


async def test_oauth_status_returns_current_state(client, auth_headers, create_email_account) -> None:
    account = await create_email_account(type="outlook")
    oauth_registry.set(
        account.id,
        OAuthFlowState(
            status="pending",
            verification_uri="https://microsoft.com/devicelogin",
            user_code="ABCD-EFGH",
            expires_at=datetime(2099, 4, 17, 12, 34, 56, tzinfo=timezone.utc),
        ),
    )

    response = await client.get(f"/api/v1/accounts/{account.id}/oauth/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "status": "pending",
        "verification_uri": "https://microsoft.com/devicelogin",
        "user_code": "ABCD-EFGH",
        "expires_at": "2099-04-17T12:34:56Z",
        "detail": None,
    }


async def test_oauth_status_marks_expired_flow(client, auth_headers, create_email_account) -> None:
    account = await create_email_account(type="outlook")
    oauth_registry.set(
        account.id,
        OAuthFlowState(
            status="pending",
            verification_uri="https://microsoft.com/devicelogin",
            user_code="ABCD-EFGH",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ),
    )

    response = await client.get(f"/api/v1/accounts/{account.id}/oauth/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "expired"
    assert response.json()["detail"] == "Device code expired"


async def test_oauth_status_without_flow_or_token(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))

    response = await client.get(f"/api/v1/accounts/{account.id}/oauth/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "status": "none",
        "verification_uri": None,
        "user_code": None,
        "expires_at": None,
        "detail": "Authorization not started",
    }


async def test_oauth_status_without_flow_but_with_token(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=True))

    response = await client.get(f"/api/v1/accounts/{account.id}/oauth/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "status": "authorized",
        "verification_uri": None,
        "user_code": None,
        "expires_at": None,
        "detail": None,
    }


async def test_oauth_status_rejects_non_outlook_account(client, auth_headers, create_email_account) -> None:
    account = await create_email_account(type="imap")

    response = await client.get(f"/api/v1/accounts/{account.id}/oauth/status", headers=auth_headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "OAuth is only supported for Outlook accounts"}


async def test_oauth_status_missing_account(client, auth_headers) -> None:
    response = await client.get("/api/v1/accounts/999/oauth/status", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


async def test_oauth_initiate_with_expired_existing_state_restarts_flow(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    oauth_registry.set(
        account.id,
        OAuthFlowState(
            status="pending",
            verification_uri="https://old",
            user_code="OLD",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ),
    )
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "NEW-CODE",
                "expires_in": 900,
            }
        ),
    )
    monkeypatch.setattr(accounts_api, "_attach_flow_task", lambda account, scanner, flow, state: None)

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["user_code"] == "NEW-CODE"


async def test_oauth_initiate_background_task_marks_error_on_failure(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        ),
    )

    async def failing_complete(_self, flow, token_path, outlook_type):
        del flow, token_path, outlook_type
        raise RuntimeError("device flow failed")

    monkeypatch.setattr(accounts_api.OutlookScanner, "complete_device_flow_async_with_path", failing_complete)

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)
    assert response.status_code == 200

    await asyncio.sleep(0)
    state = oauth_registry.get(account.id)
    assert state is not None
    assert state.status == "error"
    assert state.detail == "device flow failed"


async def test_oauth_initiate_background_task_marks_authorized_on_success(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        ),
    )

    async def complete(_self, flow, token_path, outlook_type):
        del flow, token_path, outlook_type
        return {"access_token": "token"}

    monkeypatch.setattr(accounts_api.OutlookScanner, "complete_device_flow_async_with_path", complete)

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)
    assert response.status_code == 200

    await asyncio.sleep(0)
    state = oauth_registry.get(account.id)
    assert state is not None
    assert state.status == "authorized"
    assert state.detail is None


async def test_oauth_initiate_background_task_marks_error_from_result(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        ),
    )

    async def complete(_self, flow, token_path, outlook_type):
        del flow, token_path, outlook_type
        return {"error": "authorization_pending", "error_description": "still waiting"}

    monkeypatch.setattr(accounts_api.OutlookScanner, "complete_device_flow_async_with_path", complete)

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)
    assert response.status_code == 200

    await asyncio.sleep(0)
    state = oauth_registry.get(account.id)
    assert state is not None
    assert state.status == "error"
    assert state.detail == "still waiting"


async def test_oauth_initiate_background_task_marks_expired_from_result(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "expires_in": -1,
            }
        ),
    )

    async def complete(_self, flow, token_path, outlook_type):
        del flow, token_path, outlook_type
        return {"error": "expired_token"}

    monkeypatch.setattr(accounts_api.OutlookScanner, "complete_device_flow_async_with_path", complete)

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)
    assert response.status_code == 200

    await asyncio.sleep(0)
    state = oauth_registry.get(account.id)
    assert state is not None
    assert state.status == "expired"
    assert state.detail == "Device code expired"


async def test_oauth_initiate_background_task_handles_cancellation(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    monkeypatch.setattr(accounts_api.OutlookScanner, "has_cached_token_async", AsyncMock(return_value=False))
    monkeypatch.setattr(
        accounts_api.OutlookScanner,
        "initiate_device_flow_async",
        AsyncMock(
            return_value={
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        ),
    )

    async def cancelled(_self, flow, token_path, outlook_type):
        del flow, token_path, outlook_type
        raise asyncio.CancelledError()

    monkeypatch.setattr(accounts_api.OutlookScanner, "complete_device_flow_async_with_path", cancelled)

    response = await client.post(f"/api/v1/accounts/{account.id}/oauth/initiate", headers=auth_headers)
    assert response.status_code == 200

    await asyncio.sleep(0)
    state = oauth_registry.get(account.id)
    assert state is not None
    assert state.status == "pending"
    assert state.detail is None


async def test_email_account_test_connection_generic_runtime_error(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="outlook")
    scanner = SimpleNamespace(test_connection=AsyncMock(side_effect=RuntimeError("other runtime failure")))
    monkeypatch.setattr(accounts_api.ScannerFactory, "get_scanner", lambda account_type: scanner)

    response = await client.post(f"/api/v1/accounts/{account.id}/test-connection", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Connection test failed"}


async def test_email_account_test_connection_generic_exception(client, auth_headers, create_email_account, monkeypatch) -> None:
    account = await create_email_account(type="imap")
    scanner = SimpleNamespace(test_connection=AsyncMock(side_effect=ValueError("boom")))
    monkeypatch.setattr(accounts_api.ScannerFactory, "get_scanner", lambda account_type: scanner)

    response = await client.post(f"/api/v1/accounts/{account.id}/test-connection", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Connection test failed"}
