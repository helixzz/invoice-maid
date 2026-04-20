from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

import app.main as main_module
from app.models import ScanLog


async def _async_gen(value):
    yield value


async def test_health_and_spa_catch_all(
    client, db, create_email_account, create_invoice, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    await create_invoice(email_account=account)
    scan_log = ScanLog(user_id=account.user_id, email_account_id=account.id, finished_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    db.add(scan_log)
    await db.commit()
    monkeypatch.setattr(main_module, "get_scheduler", lambda: type("Scheduler", (), {"running": True})())

    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "version": main_module._APP_VERSION,
        "db": "ok",
        "scheduler": "running",
        "sqlite_vec": False,
        "invoice_count": 1,
        "last_scan_at": "2026-01-02T00:00:00+00:00",
    }

    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "missing")
    missing = await main_module.spa_catch_all("anything")
    assert missing == {"error": "Frontend not built"}

    built = tmp_path / "dist"
    built.mkdir()
    (built / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "FRONTEND_DIST", built)
    response = await main_module.spa_catch_all("anything")
    assert response.path.endswith("index.html")


async def test_favicon_route(client, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "favicon.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(main_module, "FRONTEND_DIST", dist)

    response = await client.get("/favicon.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"

    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "missing")
    missing = await client.get("/favicon.png")
    assert missing.status_code == 404


async def test_health_reports_degraded_on_db_failure(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "get_scheduler", lambda: type("Scheduler", (), {"running": True})())
    monkeypatch.setattr(
        main_module.AsyncSession,
        "execute",
        AsyncMock(side_effect=SQLAlchemyError("database unavailable")),
    )

    health = await client.get("/api/v1/health")

    assert health.status_code == 200
    assert health.json() == {
        "status": "degraded",
        "version": main_module._APP_VERSION,
        "db": "error",
        "scheduler": "running",
        "sqlite_vec": False,
        "invoice_count": 0,
        "last_scan_at": None,
    }


async def test_health_normalizes_naive_last_scan_datetime(
    client, db, create_email_account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    db.add(ScanLog(user_id=account.user_id, email_account_id=account.id, finished_at=datetime(2026, 1, 2)))
    await db.commit()
    monkeypatch.setattr(main_module, "get_scheduler", lambda: type("Scheduler", (), {"running": True})())

    health = await client.get("/api/v1/health")

    assert health.status_code == 200
    assert health.json()["last_scan_at"] == "2026-01-02T00:00:00+00:00"


async def test_health_ignores_non_datetime_last_scan_value(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "get_scheduler", lambda: type("Scheduler", (), {"running": True})())

    async def fake_scalar(statement):
        rendered = str(statement)
        if "count(*)" in rendered:
            return 0
        return "not-a-datetime"

    monkeypatch.setattr(main_module.AsyncSession, "scalar", AsyncMock(side_effect=fake_scalar))

    health = await client.get("/api/v1/health")

    assert health.status_code == 200
    assert health.json()["last_scan_at"] is None


async def test_health_reports_degraded_when_scheduler_stopped(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "get_scheduler", lambda: None)

    health = await client.get("/api/v1/health")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["scheduler"] == "stopped"


def test_configured_worker_count_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    assert main_module._configured_worker_count() is None

    monkeypatch.setenv("WEB_CONCURRENCY", "bad")
    assert main_module._configured_worker_count() is None

    monkeypatch.setenv("WEB_CONCURRENCY", "0")
    assert main_module._configured_worker_count() is None

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert main_module._configured_worker_count() == 2


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_scheduler(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    engine = type("Engine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(main_module, "create_engine_and_session", lambda database_url: (engine, object()))
    monkeypatch.setattr(main_module, "init_db", AsyncMock())
    mock_db = AsyncMock()
    monkeypatch.setattr(main_module, "get_db", lambda: _async_gen(mock_db))
    start = []
    stop = []
    monkeypatch.setattr("app.tasks.scheduler.start_scheduler", lambda settings: start.append(settings))
    monkeypatch.setattr("app.tasks.scheduler.stop_scheduler", lambda: stop.append(True))
    monkeypatch.setattr("app.services.bootstrap.bootstrap_admin_user", AsyncMock(return_value=None))

    async with main_module.lifespan(main_module.app):
        assert start == [settings]

    assert stop == [True]
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_skips_scheduler_for_multiple_workers(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    del settings
    engine = type("Engine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(main_module, "create_engine_and_session", lambda database_url: (engine, object()))
    monkeypatch.setattr(main_module, "init_db", AsyncMock())
    mock_db = AsyncMock()
    monkeypatch.setattr(main_module, "get_db", lambda: _async_gen(mock_db))
    start = []
    stop = []
    warnings: list[str] = []
    monkeypatch.setattr("app.tasks.scheduler.start_scheduler", lambda settings: start.append(settings))
    monkeypatch.setattr("app.tasks.scheduler.stop_scheduler", lambda: stop.append(True))
    monkeypatch.setattr(main_module.logger, "warning", lambda message, *args: warnings.append(message % args))
    monkeypatch.setattr("app.services.bootstrap.bootstrap_admin_user", AsyncMock(return_value=None))
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    async with main_module.lifespan(main_module.app):
        assert start == []

    assert stop == []
    assert warnings == ["Multiple workers detected (2). Scheduler disabled to prevent duplicate jobs."]
    engine.dispose.assert_awaited_once()


async def test_scan_orphan_user_directories_logs_orphan(
    db, settings, admin_user, tmp_path, caplog
) -> None:
    """Orphan detection: a users/{id}/ dir on disk with no matching
    users.id row must generate a WARNING in the service log.
    Operators rely on this for disk-usage hygiene."""
    import logging

    from app import main as main_module

    settings.STORAGE_PATH = str(tmp_path)

    (tmp_path / "users" / "99" / "invoices").mkdir(parents=True)
    (tmp_path / "users" / str(admin_user.id) / "invoices").mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await main_module._scan_orphan_user_directories(db, settings)

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("orphan user storage directory" in m and "99" in m for m in messages)
    assert not any(
        "orphan user storage directory" in m and f"id={admin_user.id}" in m
        for m in messages
    )


async def test_scan_orphan_user_directories_noop_when_users_dir_absent(
    db, settings, tmp_path
) -> None:
    from app import main as main_module

    settings.STORAGE_PATH = str(tmp_path)

    await main_module._scan_orphan_user_directories(db, settings)


async def test_scan_orphan_user_directories_skips_non_numeric_entries(
    db, settings, tmp_path, caplog
) -> None:
    """Entries under users/ that don't parse as integer user ids (stray
    files, operator debris) must be ignored silently — we only scan
    the well-formed ``users/{id}/`` shape."""
    import logging

    from app import main as main_module

    settings.STORAGE_PATH = str(tmp_path)

    users_dir = tmp_path / "users"
    users_dir.mkdir()
    (users_dir / "README").write_text("not a user id")
    (users_dir / "not-a-number").mkdir()

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await main_module._scan_orphan_user_directories(db, settings)

    assert not any("orphan" in rec.getMessage() for rec in caplog.records)
