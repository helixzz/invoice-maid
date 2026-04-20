"""Admin user management endpoints.

Every test follows one of two shapes:

1. **Non-admin caller**: request must return 403 with
   ``{"detail": "Admin privileges required"}``. This is the
   ``AdminUser`` dep boundary. 403 is correct — not 404 — because the
   endpoint's existence is public in the OpenAPI schema; we're
   refusing the *action*, not hiding the URL.

2. **Admin caller**: request performs the action, subject to the
   anti-lockout guardrails (cannot deactivate/demote/delete self,
   cannot demote the last admin).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailAccount, Invoice, User


async def test_admin_list_users_as_admin(
    client, auth_headers, admin_user, second_user, create_invoice
) -> None:
    await create_invoice(invoice_no="ADMIN-A")
    await create_invoice(invoice_no="ADMIN-B")

    response = await client.get("/api/v1/admin/users", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    by_email = {row["email"]: row for row in body}
    assert by_email[admin_user.email]["is_admin"] is True
    assert by_email[admin_user.email]["invoice_count"] == 2
    assert by_email[second_user.email]["is_admin"] is False
    assert by_email[second_user.email]["invoice_count"] == 0


async def test_admin_list_users_as_non_admin_returns_403(
    client, second_auth_headers
) -> None:
    response = await client.get("/api/v1/admin/users", headers=second_auth_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin privileges required"}


async def test_admin_list_users_unauthenticated_returns_401(client) -> None:
    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 401


async def test_admin_deactivate_user(
    client, auth_headers, second_user, db
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{second_user.id}",
        headers=auth_headers,
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    refreshed = await db.get(User, second_user.id)
    assert refreshed.is_active is False


async def test_admin_cannot_deactivate_self(
    client, auth_headers, admin_user
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{admin_user.id}",
        headers=auth_headers,
        json={"is_active": False},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot deactivate yourself"}


async def test_admin_cannot_demote_last_admin_self(
    client, auth_headers, admin_user
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{admin_user.id}",
        headers=auth_headers,
        json={"is_admin": False},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot demote the last admin"}


async def test_admin_can_demote_self_if_another_admin_exists(
    client, auth_headers, admin_user, second_user, db
) -> None:
    """When a second admin exists, the first admin may demote themselves
    — the instance still has an admin after the change."""
    second_user.is_admin = True
    await db.commit()

    response = await client.put(
        f"/api/v1/admin/users/{admin_user.id}",
        headers=auth_headers,
        json={"is_admin": False},
    )
    assert response.status_code == 200
    assert response.json()["is_admin"] is False


async def test_admin_promote_user_to_admin(
    client, auth_headers, second_user, db
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{second_user.id}",
        headers=auth_headers,
        json={"is_admin": True},
    )
    assert response.status_code == 200
    assert response.json()["is_admin"] is True

    refreshed = await db.get(User, second_user.id)
    assert refreshed.is_admin is True


async def test_admin_update_user_email(
    client, auth_headers, second_user, db
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{second_user.id}",
        headers=auth_headers,
        json={"email": "Renamed@Example.COM"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "renamed@example.com"

    refreshed = await db.get(User, second_user.id)
    assert refreshed.email == "renamed@example.com"


async def test_admin_update_user_rejects_duplicate_email(
    client, auth_headers, admin_user, second_user
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{second_user.id}",
        headers=auth_headers,
        json={"email": admin_user.email},
    )
    assert response.status_code == 409


async def test_admin_update_user_rejects_malformed_email(
    client, auth_headers, second_user
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{second_user.id}",
        headers=auth_headers,
        json={"email": "no-at-sign"},
    )
    assert response.status_code == 422


async def test_admin_update_missing_user_returns_404(
    client, auth_headers
) -> None:
    response = await client.put(
        "/api/v1/admin/users/99999",
        headers=auth_headers,
        json={"is_active": False},
    )
    assert response.status_code == 404


async def test_admin_update_user_as_non_admin_returns_403(
    client, second_auth_headers, admin_user
) -> None:
    response = await client.put(
        f"/api/v1/admin/users/{admin_user.id}",
        headers=second_auth_headers,
        json={"is_active": False},
    )
    assert response.status_code == 403


async def test_admin_delete_user_cascades(
    client, auth_headers, second_user, db, create_invoice, create_email_account
) -> None:
    """Deleting user 2 cascades the FK to wipe their email_accounts,
    invoices, scan_logs, extraction_logs, correction_logs, saved_views,
    webhook_logs. The per-user file directory is removed separately via
    ``FileManager.delete_user_files``."""
    other_account = await create_email_account(
        name="Second's Account", username="two@example.com", user_id=second_user.id
    )

    invoice = await create_invoice(
        email_account=other_account, invoice_no="OWNED-BY-2",
        user_id=second_user.id,
    )

    invoice_id = invoice.id
    user_id = second_user.id

    response = await client.delete(
        f"/api/v1/admin/users/{user_id}", headers=auth_headers
    )
    assert response.status_code == 204

    db.expire_all()

    remaining_user = await db.get(User, user_id)
    assert remaining_user is None
    remaining_invoice = await db.get(Invoice, invoice_id)
    assert remaining_invoice is None


async def test_admin_cannot_delete_self(
    client, auth_headers, admin_user
) -> None:
    response = await client.delete(
        f"/api/v1/admin/users/{admin_user.id}", headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Cannot delete yourself"}


async def test_admin_delete_missing_user_returns_404(
    client, auth_headers
) -> None:
    response = await client.delete(
        "/api/v1/admin/users/99999", headers=auth_headers
    )
    assert response.status_code == 404


async def test_admin_delete_user_as_non_admin_returns_403(
    client, second_auth_headers, admin_user
) -> None:
    response = await client.delete(
        f"/api/v1/admin/users/{admin_user.id}", headers=second_auth_headers
    )
    assert response.status_code == 403


async def test_admin_delete_removes_user_files_from_disk(
    client, auth_headers, second_user, db, settings, tmp_path
) -> None:
    """The DELETE /admin/users/{id} handler invokes
    FileManager.delete_user_files after the DB cascade to wipe the
    per-user subdirectory on disk. If the directory exists, it's gone
    after the 204."""
    from app.services.file_manager import FileManager

    fm = FileManager(settings.STORAGE_PATH)
    user_dir = fm.storage_path / "users" / str(second_user.id) / "invoices"
    user_dir.mkdir(parents=True)
    (user_dir / "orphan.pdf").write_bytes(b"before-delete")

    response = await client.delete(
        f"/api/v1/admin/users/{second_user.id}", headers=auth_headers
    )
    assert response.status_code == 204

    assert not (fm.storage_path / "users" / str(second_user.id)).exists()
