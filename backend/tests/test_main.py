from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.main as main_module


async def test_health_and_spa_catch_all(client, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "missing")
    missing = await main_module.spa_catch_all("anything")
    assert missing == {"error": "Frontend not built"}

    built = tmp_path / "dist"
    built.mkdir()
    (built / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "FRONTEND_DIST", built)
    response = await main_module.spa_catch_all("anything")
    assert response.path.endswith("index.html")


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_scheduler(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    engine = type("Engine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(main_module, "create_engine_and_session", lambda database_url: (engine, object()))
    monkeypatch.setattr(main_module, "init_db", AsyncMock())
    start = []
    stop = []
    monkeypatch.setattr("app.tasks.scheduler.start_scheduler", lambda settings: start.append(settings))
    monkeypatch.setattr("app.tasks.scheduler.stop_scheduler", lambda: stop.append(True))

    async with main_module.lifespan(main_module.app):
        assert start == [settings]

    assert stop == [True]
    engine.dispose.assert_awaited_once()
