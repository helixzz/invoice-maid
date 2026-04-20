from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import app.api.stats as stats_api
from app.models import ScanLog


def _previous_month_day(today: date) -> date:
    current_month_start = today.replace(day=1)
    return current_month_start.fromordinal(current_month_start.toordinal() - 1)


async def test_get_stats_empty_db(client, auth_headers) -> None:
    response = await client.get("/api/v1/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "total_invoices": 0,
        "total_amount": 0.0,
        "invoices_this_month": 0,
        "amount_this_month": 0.0,
        "active_accounts": 0,
        "last_scan_at": None,
        "last_scan_found": None,
        "monthly_spend": [],
        "top_sellers": [],
        "by_type": [],
        "by_method": [],
        "avg_confidence": 0.0,
    }


async def test_get_stats_populated_db(client, auth_headers, db, create_email_account, create_invoice) -> None:
    today = date.today()
    active_account = await create_email_account(name="Active Account", is_active=True)
    await create_email_account(name="Inactive Account", username="inactive@example.com", is_active=False)

    await create_invoice(
        email_account=active_account,
        invoice_no="INV-CURRENT-1",
        amount=Decimal("100.50"),
        invoice_date=today,
        email_uid="uid-current-1",
    )
    await create_invoice(
        email_account=active_account,
        invoice_no="INV-CURRENT-2",
        amount=Decimal("49.50"),
        invoice_date=today.replace(day=1),
        email_uid="uid-current-2",
    )
    await create_invoice(
        email_account=active_account,
        invoice_no="INV-PREVIOUS",
        amount=Decimal("25.00"),
        invoice_date=_previous_month_day(today),
        email_uid="uid-previous",
    )

    older_log = ScanLog(
        user_id=active_account.user_id, email_account_id=active_account.id,
        started_at=datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
        finished_at=datetime(2024, 1, 1, 8, 5, tzinfo=timezone.utc),
        emails_scanned=5,
        invoices_found=1,
        error_message=None,
    )
    latest_log = ScanLog(
        user_id=active_account.user_id, email_account_id=active_account.id,
        started_at=datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc),
        finished_at=datetime(2024, 1, 2, 9, 10, tzinfo=timezone.utc),
        emails_scanned=8,
        invoices_found=3,
        error_message=None,
    )
    db.add_all([older_log, latest_log])
    await db.commit()

    response = await client.get("/api/v1/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "total_invoices": 3,
        "total_amount": 175.0,
        "invoices_this_month": 2,
        "amount_this_month": 150.0,
        "active_accounts": 1,
        "last_scan_at": "2024-01-02T09:00:00+00:00",
        "last_scan_found": 3,
        "monthly_spend": [
            {"month": _previous_month_day(today).strftime("%Y-%m"), "total": 25.0, "count": 1},
            {"month": today.strftime("%Y-%m"), "total": 150.0, "count": 2},
        ],
        "top_sellers": [{"seller": "Beta Seller", "total": 175.0, "count": 3}],
        "by_type": [{"type": "增值税电子普通发票", "count": 3}],
        "by_method": [{"method": "regex", "count": 3}],
        "avg_confidence": 0.8,
    }


async def test_get_stats_analytics_aggregations(client, auth_headers, create_email_account, create_invoice) -> None:
    account = await create_email_account(name="Analytics Account")
    await create_invoice(
        email_account=account,
        invoice_no="INV-ANA-1",
        seller="Gamma Seller",
        amount=Decimal("10.00"),
        invoice_date=date(2026, 3, 5),
        invoice_type="专用发票",
        extraction_method="ocr",
        confidence=0.5,
        email_uid="uid-ana-1",
    )
    await create_invoice(
        email_account=account,
        invoice_no="INV-ANA-2",
        seller="Alpha Seller",
        amount=Decimal("50.00"),
        invoice_date=date(2026, 4, 6),
        invoice_type="普通发票",
        extraction_method="ocr",
        confidence=0.9,
        email_uid="uid-ana-2",
    )
    await create_invoice(
        email_account=account,
        invoice_no="INV-ANA-3",
        seller="Alpha Seller",
        amount=Decimal("70.00"),
        invoice_date=date(2026, 4, 10),
        invoice_type="普通发票",
        extraction_method="llm",
        confidence=0.7,
        email_uid="uid-ana-3",
    )
    await create_invoice(
        email_account=account,
        invoice_no="INV-ANA-4",
        seller="Beta Seller",
        amount=Decimal("20.00"),
        invoice_date=date(2026, 5, 1),
        invoice_type="专用发票",
        extraction_method="llm",
        confidence=1.0,
        email_uid="uid-ana-4",
    )

    response = await client.get("/api/v1/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["monthly_spend"] == [
        {"month": "2026-03", "total": 10.0, "count": 1},
        {"month": "2026-04", "total": 120.0, "count": 2},
        {"month": "2026-05", "total": 20.0, "count": 1},
    ]
    assert response.json()["top_sellers"] == [
        {"seller": "Alpha Seller", "total": 120.0, "count": 2},
        {"seller": "Beta Seller", "total": 20.0, "count": 1},
        {"seller": "Gamma Seller", "total": 10.0, "count": 1},
    ]
    assert sorted(response.json()["by_type"], key=lambda item: item["type"]) == [
        {"type": "专用发票", "count": 2},
        {"type": "普通发票", "count": 2},
    ]
    assert sorted(response.json()["by_method"], key=lambda item: item["method"]) == [
        {"method": "llm", "count": 2},
        {"method": "ocr", "count": 2},
    ]
    assert response.json()["avg_confidence"] == 0.775


def test_stats_helpers_cover_edge_cases() -> None:
    assert stats_api._to_float(None) == 0.0
    assert stats_api._month_bounds(date(2024, 12, 15)) == (date(2024, 12, 1), date(2025, 1, 1))
