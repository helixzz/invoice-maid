from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

import app.api.scan as scan_api
import app.api.test_helpers as test_helpers_api
from app.models import EmailAccount, Invoice, ScanLog


async def test_reset_smoke_data_guarded_when_disabled(client, settings) -> None:
    settings.ENABLE_TEST_HELPERS = False

    response = await client.post("/api/v1/test-helpers/reset-smoke")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


async def test_reset_smoke_data_seeds_deterministic_records(client, settings, db, create_email_account) -> None:
    settings.ENABLE_TEST_HELPERS = True
    await create_email_account(name="Old Account", username="old@example.com")

    first = await client.post("/api/v1/test-helpers/reset-smoke")
    second = await client.post("/api/v1/test-helpers/reset-smoke")

    assert first.status_code == 200
    assert second.status_code == 200
    payload = second.json()
    assert payload == {"account_id": 1, "invoice_id": 1, "scan_log_id": 1}

    account_count = (await db.execute(select(func.count(EmailAccount.id)))).scalar_one()
    invoice_count = (await db.execute(select(func.count(Invoice.id)))).scalar_one()
    log_count = (await db.execute(select(func.count(ScanLog.id)))).scalar_one()
    assert account_count == 1
    assert invoice_count == 1
    assert log_count == 1

    invoice = (await db.execute(select(Invoice))).scalar_one()
    assert invoice.invoice_no == "SMOKE-INV-001"


async def test_smoke_helper_connection_and_trigger_paths(client, auth_headers, settings, db) -> None:
    settings.ENABLE_TEST_HELPERS = True
    seed = await client.post("/api/v1/test-helpers/reset-smoke")
    account_id = seed.json()["account_id"]

    task_calls = []
    original_asyncio = scan_api.asyncio
    scan_api.asyncio = SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task")

    try:
        connection = await client.post(f"/api/v1/accounts/{account_id}/test-connection", headers=auth_headers)
        assert connection.status_code == 200
        assert connection.json() == {"ok": True, "detail": None}

        trigger = await client.post("/api/v1/scan/trigger", headers=auth_headers)
        assert trigger.status_code == 200
        assert trigger.json() == {"status": "triggered"}
        assert task_calls
        task_calls[0].close()

        await test_helpers_api._run_smoke_scan_in_session(db)

        log_count = (await db.execute(select(func.count(ScanLog.id)))).scalar_one()
        assert log_count == 2

        latest_log = (
            await db.execute(select(ScanLog).order_by(ScanLog.started_at.desc(), ScanLog.id.desc()))
        ).scalars().first()
        assert latest_log.invoices_found == 1
    finally:
        scan_api.asyncio = original_asyncio


async def test_run_smoke_scan_returns_when_disabled(settings) -> None:
    settings.ENABLE_TEST_HELPERS = False

    assert await test_helpers_api.run_smoke_scan() is None


async def test_run_smoke_scan_returns_when_smoke_account_missing(settings, db) -> None:
    settings.ENABLE_TEST_HELPERS = True

    assert await test_helpers_api._run_smoke_scan_in_session(db) is None


async def test_run_smoke_scan_uses_get_db_generator(settings, db, monkeypatch) -> None:
    settings.ENABLE_TEST_HELPERS = True
    await test_helpers_api.seed_smoke_data(db, settings)

    async def fake_get_db():
        yield db

    monkeypatch.setattr(test_helpers_api, "get_db", fake_get_db)

    assert await test_helpers_api.run_smoke_scan() is None

    log_count = (await db.execute(select(func.count(ScanLog.id)))).scalar_one()
    assert log_count == 2


async def test_run_smoke_scan_returns_when_get_db_yields_nothing(settings, monkeypatch) -> None:
    settings.ENABLE_TEST_HELPERS = True

    async def empty_get_db():
        if False:
            yield None

    monkeypatch.setattr(test_helpers_api, "get_db", empty_get_db)

    assert await test_helpers_api.run_smoke_scan() is None
