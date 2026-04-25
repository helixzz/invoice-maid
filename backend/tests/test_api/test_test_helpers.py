from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import app.api.scan as scan_api
import app.api.test_helpers as test_helpers_api
from app.models import EmailAccount, ExtractionLog, Invoice, ScanLog, User


async def test_reset_smoke_data_guarded_when_disabled(client, settings, auth_headers) -> None:
    settings.ENABLE_TEST_HELPERS = False

    response = await client.post("/api/v1/test-helpers/reset-smoke", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


async def test_reset_smoke_data_seeds_deterministic_records(client, settings, db, create_email_account, auth_headers) -> None:
    settings.ENABLE_TEST_HELPERS = True
    await create_email_account(name="Old Account", username="old@example.com")

    first = await client.post("/api/v1/test-helpers/reset-smoke", headers=auth_headers)
    second = await client.post("/api/v1/test-helpers/reset-smoke", headers=auth_headers)

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
    seed = await client.post("/api/v1/test-helpers/reset-smoke", headers=auth_headers)
    account_id = seed.json()["account_id"]

    task_calls = []
    original_asyncio = scan_api.asyncio
    scan_api.asyncio = SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task")

    try:
        connection = await client.post(f"/api/v1/accounts/{account_id}/test-connection", headers=auth_headers)
        assert connection.status_code == 200
        assert connection.json() == {"ok": False, "detail": "Connection test failed"}

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


async def test_run_smoke_scan_uses_get_db_generator(settings, db, monkeypatch, admin_user) -> None:
    del admin_user
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


async def test_seed_smoke_data_raises_when_no_admin_user(db, settings) -> None:
    """The seeder reuses the bootstrap admin to satisfy NOT NULL user_id
    on the seeded EmailAccount/Invoice/ScanLog. If no User row exists
    (bootstrap hook never ran), the seeder must fail loudly rather than
    silently insert rows with no owner."""
    with pytest.raises(HTTPException) as excinfo:
        await test_helpers_api.seed_smoke_data(db, settings)

    assert excinfo.value.status_code == 500
    assert "bootstrap hook" in excinfo.value.detail


async def test_seed_fix8_scenario_guarded_when_disabled(client, settings, auth_headers) -> None:
    settings.ENABLE_TEST_HELPERS = False

    response = await client.post(
        "/api/v1/test-helpers/seed-fix8-scenario", headers=auth_headers
    )

    assert response.status_code == 404


async def test_seed_fix8_scenario_requires_preceding_scan_log(
    client, settings, auth_headers
) -> None:
    settings.ENABLE_TEST_HELPERS = True

    response = await client.post(
        "/api/v1/test-helpers/seed-fix8-scenario", headers=auth_headers
    )

    assert response.status_code == 400
    assert "reset-smoke first" in response.json()["detail"]


async def test_seed_fix8_scenario_creates_2_email_6_row_shape(
    client, settings, auth_headers, db
) -> None:
    settings.ENABLE_TEST_HELPERS = True
    await client.post("/api/v1/test-helpers/reset-smoke", headers=auth_headers)

    response = await client.post(
        "/api/v1/test-helpers/seed-fix8-scenario", headers=auth_headers
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["extraction_log_ids"]) == 6

    rows = (
        await db.execute(
            select(ExtractionLog).where(ExtractionLog.scan_log_id == payload["scan_log_id"])
        )
    ).scalars().all()
    assert len(rows) == 6

    email_uids = {r.email_uid for r in rows}
    assert email_uids == {"fix8-email-first", "fix8-email-second"}

    saved = [r for r in rows if r.outcome == "saved"]
    duplicates = [r for r in rows if r.outcome == "duplicate"]
    low_conf = [r for r in rows if r.outcome == "low_confidence"]
    assert len(saved) == 1
    assert len(duplicates) == 3
    assert len(low_conf) == 2

    assert saved[0].email_uid == "fix8-email-first"
    assert saved[0].invoice_no == test_helpers_api.FIX8_SAMS_CLUB_INVOICE_NO
    for dup in duplicates:
        assert dup.email_uid == "fix8-email-second"
        assert dup.invoice_no == test_helpers_api.FIX8_SAMS_CLUB_INVOICE_NO


async def test_reset_users_to_admin_only_guarded_when_disabled(
    client, settings, auth_headers
) -> None:
    settings.ENABLE_TEST_HELPERS = False

    response = await client.post(
        "/api/v1/test-helpers/reset-users-to-admin-only", headers=auth_headers
    )

    assert response.status_code == 404


async def test_reset_users_to_admin_only_preserves_admin_deletes_others(
    client, settings, auth_headers, db, admin_user
) -> None:
    del admin_user
    settings.ENABLE_TEST_HELPERS = True

    extra = User(
        email="extra-user@smoke.invalid",
        hashed_password="$2b$12$unusableunusableunusableunusableunusableunusableunusableunu",
        is_admin=False,
        is_active=True,
    )
    db.add(extra)
    await db.commit()

    pre_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    assert pre_count == 2

    response = await client.post(
        "/api/v1/test-helpers/reset-users-to-admin-only", headers=auth_headers
    )

    assert response.status_code == 200
    post_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    assert post_count == 1

    remaining = (await db.execute(select(User))).scalar_one()
    assert remaining.is_admin is True


async def test_reset_users_to_admin_only_is_noop_with_no_users(db) -> None:
    await db.execute(User.__table__.delete())
    await db.commit()

    await test_helpers_api._reset_users_to_admin_only(db)

    count = (await db.execute(select(func.count(User.id)))).scalar_one()
    assert count == 0


async def test_seed_second_user_guarded_when_disabled(client, settings, auth_headers) -> None:
    settings.ENABLE_TEST_HELPERS = False

    response = await client.post(
        "/api/v1/test-helpers/seed-second-user", headers=auth_headers
    )

    assert response.status_code == 404


async def test_seed_second_user_creates_and_is_idempotent(
    client, settings, auth_headers, db, admin_user
) -> None:
    del admin_user
    settings.ENABLE_TEST_HELPERS = True

    first = await client.post(
        "/api/v1/test-helpers/seed-second-user", headers=auth_headers
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["second_user_email"] == "second-user@smoke.invalid"
    assert first_payload["second_user_password"] == "smoke-second-user-password"

    second = await client.post(
        "/api/v1/test-helpers/seed-second-user", headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json() == first_payload

    count = (
        await db.execute(
            select(func.count(User.id)).where(User.email == "second-user@smoke.invalid")
        )
    ).scalar_one()
    assert count == 1


async def test_reset_smoke_restores_rotated_admin_password(
    client, settings, auth_headers, db, admin_user
) -> None:
    """Drop-the-afterEach contract: /reset-smoke must restore the admin's
    hashed_password to the bootstrap value so Playwright specs can
    rotate the password mid-test without needing their own rollback
    logic. If this invariant regresses, spec-to-spec bleed returns
    and the whole Playwright suite starts 401-ing."""
    del admin_user
    settings.ENABLE_TEST_HELPERS = True

    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one()
    admin.hashed_password = "$2b$12$nothingremotelyvalidnothingremotelyvalidnothingrem"
    await db.commit()

    response = await client.post("/api/v1/test-helpers/reset-smoke", headers=auth_headers)
    assert response.status_code == 200

    await db.refresh(admin)
    assert admin.hashed_password == settings.ADMIN_PASSWORD_HASH


async def test_restore_admin_repairs_flipped_is_admin_and_is_active_flags(
    db, settings, admin_user
) -> None:
    """Separate test for the flag-repair path (is_admin / is_active)
    because setting is_active=False would cause /reset-smoke's auth
    dependency to 401 before reaching the restore logic; we exercise
    the helper directly."""
    del admin_user
    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one()
    admin.is_admin = False
    admin.is_active = False
    await db.commit()

    await test_helpers_api._restore_admin_from_bootstrap(db, settings)

    await db.refresh(admin)
    assert admin.is_admin is True
    assert admin.is_active is True


async def test_restore_admin_from_bootstrap_is_noop_with_no_users(db, settings) -> None:
    await db.execute(User.__table__.delete())
    await db.commit()

    await test_helpers_api._restore_admin_from_bootstrap(db, settings)

    count = (await db.execute(select(func.count(User.id)))).scalar_one()
    assert count == 0


async def test_restore_admin_from_bootstrap_noop_when_already_correct(
    db, settings, admin_user
) -> None:
    del admin_user
    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one()
    original_hash = admin.hashed_password

    await test_helpers_api._restore_admin_from_bootstrap(db, settings)

    await db.refresh(admin)
    assert admin.hashed_password == original_hash
