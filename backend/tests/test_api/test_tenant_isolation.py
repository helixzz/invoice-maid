"""Tenant isolation guarantees.

These tests seed every tenant-scoped resource (invoices, accounts,
scan logs, extraction logs, saved views) under ``admin_user`` (user 1)
and then attempt to access, modify, delete, list, search, and export
those resources via the API using ``second_auth_headers`` (user 2).

Every assertion is that the second user sees either:

* HTTP 404 for direct-lookup endpoints (``GET/PUT/DELETE /{id}``),
  ``similar``, ``download``, scan-log-scoped extraction/summary
* Empty list / zero counts for list + stats + export + semantic
  endpoints
* Batch operations: silently no-op on other-user ids (batch-delete,
  batch-download), rather than returning 403 or 404 listing which
  IDs are "forbidden" (that would leak existence)

The 404 response body must be ``{"detail": "Not found"}`` — NOT a
resource-specific string like ``"Invoice not found"`` — because a
distinct detail would leak whether the resource exists for another
user.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CorrectionLog,
    EmailAccount,
    ExtractionLog,
    Invoice,
    SavedView,
    ScanLog,
    User,
)


@pytest.fixture
async def seeded_admin_resources(
    db: AsyncSession, admin_user: User, create_email_account, create_invoice, create_scan_log
) -> dict[str, object]:
    account = await create_email_account(
        name="Admin Account", username="admin_only@example.com"
    )
    invoice = await create_invoice(
        email_account=account,
        invoice_no="ADMIN-001",
        buyer="AdminBuyer",
        seller="AdminSeller",
        raw_text="admin-only buyer seller",
    )
    scan_log = await create_scan_log(email_account=account)
    extraction = ExtractionLog(
        user_id=admin_user.id,
        scan_log_id=scan_log.id,
        email_uid="admin-uid",
        email_subject="admin subject",
        attachment_filename="admin.pdf",
        outcome="saved",
        invoice_no="ADMIN-001",
        confidence=0.9,
    )
    db.add(extraction)
    view = SavedView(
        user_id=admin_user.id,
        name="Admin View",
        filter_json='{"q":"admin"}',
    )
    db.add(view)
    await db.commit()
    await db.refresh(extraction)
    await db.refresh(view)
    return {
        "account_id": account.id,
        "invoice_id": invoice.id,
        "scan_log_id": scan_log.id,
        "extraction_id": extraction.id,
        "view_id": view.id,
    }


async def test_get_invoice_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.get(
        f"/api/v1/invoices/{invoice_id}", headers=second_auth_headers
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


async def test_get_similar_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.get(
        f"/api/v1/invoices/{invoice_id}/similar", headers=second_auth_headers
    )
    assert response.status_code == 404


async def test_update_invoice_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.put(
        f"/api/v1/invoices/{invoice_id}",
        headers=second_auth_headers,
        json={"buyer": "Hacked"},
    )
    assert response.status_code == 404


async def test_delete_invoice_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers, db
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.delete(
        f"/api/v1/invoices/{invoice_id}", headers=second_auth_headers
    )
    assert response.status_code == 404

    still_there = await db.get(Invoice, invoice_id)
    assert still_there is not None, "delete must not have touched admin's invoice"


async def test_download_invoice_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.get(
        f"/api/v1/invoices/{invoice_id}/download", headers=second_auth_headers
    )
    assert response.status_code == 404


async def test_list_invoices_other_user_sees_empty(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get("/api/v1/invoices", headers=second_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_invoices_with_query_other_user_sees_empty(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get(
        "/api/v1/invoices?q=AdminBuyer", headers=second_auth_headers
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_export_invoices_other_user_sees_empty_csv(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get(
        "/api/v1/invoices/export?format=csv", headers=second_auth_headers
    )
    assert response.status_code == 200
    body = response.content.decode("utf-8-sig")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 1, "CSV must contain only the header row for a user with no invoices"


async def test_semantic_search_other_user_sees_empty(
    client, seeded_admin_resources, second_auth_headers, monkeypatch
) -> None:
    del seeded_admin_resources
    from unittest.mock import AsyncMock

    import app.api.invoices as invoices_api

    monkeypatch.setattr(
        invoices_api.AIService,
        "embed_text",
        AsyncMock(return_value=[0.1, 0.2, 0.3]),
    )

    response = await client.post(
        "/api/v1/invoices/search",
        headers=second_auth_headers,
        json={"query": "AdminBuyer", "page": 1, "size": 20},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_batch_delete_other_user_ignores_foreign_ids(
    client, seeded_admin_resources, second_auth_headers, db
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.post(
        "/api/v1/invoices/batch-delete",
        headers=second_auth_headers,
        json={"ids": [invoice_id]},
    )
    assert response.status_code == 204

    still_there = await db.get(Invoice, invoice_id)
    assert still_there is not None


async def test_batch_download_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    invoice_id = seeded_admin_resources["invoice_id"]
    response = await client.post(
        "/api/v1/invoices/batch-download",
        headers=second_auth_headers,
        json={"ids": [invoice_id]},
    )
    assert response.status_code == 404


async def test_list_accounts_other_user_sees_empty(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get("/api/v1/accounts", headers=second_auth_headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_account_update_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    account_id = seeded_admin_resources["account_id"]
    response = await client.put(
        f"/api/v1/accounts/{account_id}",
        headers=second_auth_headers,
        json={"name": "Hacked"},
    )
    assert response.status_code == 404


async def test_account_delete_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers, db
) -> None:
    account_id = seeded_admin_resources["account_id"]
    response = await client.delete(
        f"/api/v1/accounts/{account_id}", headers=second_auth_headers
    )
    assert response.status_code == 404

    still_there = await db.get(EmailAccount, account_id)
    assert still_there is not None


async def test_account_test_connection_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    account_id = seeded_admin_resources["account_id"]
    response = await client.post(
        f"/api/v1/accounts/{account_id}/test-connection",
        headers=second_auth_headers,
    )
    assert response.status_code == 404


async def test_scan_logs_other_user_sees_empty(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get("/api/v1/scan/logs", headers=second_auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_scan_log_extractions_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    scan_log_id = seeded_admin_resources["scan_log_id"]
    response = await client.get(
        f"/api/v1/scan/logs/{scan_log_id}/extractions",
        headers=second_auth_headers,
    )
    assert response.status_code == 404


async def test_scan_log_summary_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    scan_log_id = seeded_admin_resources["scan_log_id"]
    response = await client.get(
        f"/api/v1/scan/logs/{scan_log_id}/summary",
        headers=second_auth_headers,
    )
    assert response.status_code == 404


async def test_stats_other_user_sees_zero(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get("/api/v1/stats", headers=second_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total_invoices"] == 0
    assert body["total_amount"] == 0
    assert body["active_accounts"] == 0
    assert body["last_scan_at"] is None
    assert body["last_scan_found"] is None
    assert body["monthly_spend"] == []
    assert body["top_sellers"] == []


async def test_saved_views_list_other_user_sees_empty(
    client, seeded_admin_resources, second_auth_headers
) -> None:
    del seeded_admin_resources
    response = await client.get("/api/v1/views", headers=second_auth_headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_saved_view_delete_other_user_sees_404(
    client, seeded_admin_resources, second_auth_headers, db
) -> None:
    view_id = seeded_admin_resources["view_id"]
    response = await client.delete(
        f"/api/v1/views/{view_id}", headers=second_auth_headers
    )
    assert response.status_code == 404

    still_there = await db.get(SavedView, view_id)
    assert still_there is not None


async def test_manual_upload_duplicate_check_is_scoped_to_user(
    db, settings, admin_user, second_user, manual_upload_account, monkeypatch
) -> None:
    """Two users may legitimately upload invoices with the same
    ``invoice_no`` — the pre-flush duplicate check must only consider
    invoices owned by the uploading user."""
    from unittest.mock import AsyncMock

    import app.services.manual_upload as mu
    from app.services.file_manager import FileManager
    from app.services.invoice_parser import ParsedInvoice

    del manual_upload_account

    shared_invoice_no = "SHARED-001"

    admin_invoice = Invoice(
        user_id=admin_user.id,
        invoice_no=shared_invoice_no,
        buyer="AdminBuyer",
        seller="AdminSeller",
        amount=Decimal("1.00"),
        invoice_date=date(2026, 1, 1),
        invoice_type="电子发票（普通发票）",
        item_summary="",
        file_path="existing-admin.pdf",
        raw_text="",
        email_uid="existing-admin",
        email_account_id=(
            await db.execute(
                select(EmailAccount.id).where(EmailAccount.user_id == admin_user.id)
            )
        ).scalar_one(),
        source_format="pdf",
        extraction_method="regex",
        confidence=1.0,
    )
    db.add(admin_invoice)
    await db.commit()

    parsed = ParsedInvoice(
        invoice_no=shared_invoice_no,
        buyer="SecondBuyer",
        seller="SecondSeller",
        amount=Decimal("99.00"),
        invoice_date=date(2026, 2, 2),
        invoice_type="电子发票（普通发票）",
        item_summary="shared invoice no across users",
        raw_text="A" * 200,
        source_format="pdf",
        extraction_method="regex",
        confidence=0.95,
    )
    monkeypatch.setattr(mu, "parse_invoice", lambda filename, payload: parsed)
    monkeypatch.setattr(mu, "is_scam_text", lambda text: (False, None))
    mock_ai = SimpleNamespace(
        extract_invoice_fields=AsyncMock(),
        embed_text=AsyncMock(return_value=[]),
    )

    result = await mu.process_uploaded_invoice(
        db=db,
        ai=mock_ai,
        file_mgr=FileManager(settings.STORAGE_PATH),
        settings=settings,
        filename="shared.pdf",
        payload=b"%PDF-1.4 stub",
        user_id=second_user.id,
    )
    assert result.outcome == "saved", (
        "Upload must succeed for user 2 even though user 1 already owns an "
        f"invoice with invoice_no={shared_invoice_no!r}"
    )

    total_shared = (
        await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.invoice_no == shared_invoice_no
            )
        )
    ).scalar_one()
    assert total_shared == 2
