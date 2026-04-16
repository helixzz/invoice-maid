from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

import app.database as database


def test_load_sqlite_vec_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeConnection:
        def enable_load_extension(self, flag: bool) -> None:
            calls.append(f"enable:{flag}")

    monkeypatch.setitem(__import__("sys").modules, "sqlite_vec", SimpleNamespace(load=lambda conn: calls.append("load")))

    assert database.load_sqlite_vec(FakeConnection()) is True
    assert calls == ["enable:True", "load", "enable:False"]


def test_load_sqlite_vec_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "sqlite_vec", SimpleNamespace(load=lambda conn: (_ for _ in ()).throw(RuntimeError("x"))))

    connection = sqlite3.connect(":memory:")
    try:
        assert database.load_sqlite_vec(connection) is False
    finally:
        connection.close()


def test_get_sqlite_connection_variants() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert database._get_sqlite_connection(connection) is connection
        wrapped = SimpleNamespace(driver_connection=connection)
        assert database._get_sqlite_connection(wrapped) is connection
        nested = SimpleNamespace(driver_connection=SimpleNamespace(_conn=connection))
        assert database._get_sqlite_connection(nested) is connection
        assert database._get_sqlite_connection(object()) is None
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_create_engine_and_session_and_get_db_error() -> None:
    database._engine = None
    database._session_factory = None

    with pytest.raises(RuntimeError, match="session factory"):
        async for _ in database.get_db():
            pass

    engine, factory = database.create_engine_and_session("sqlite+aiosqlite:///:memory:")
    assert database._engine is engine
    assert database._session_factory is factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_uses_env_database_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    database._engine = None
    database._session_factory = None
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'env.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    await database.init_db()

    assert database._engine is not None
    assert database._session_factory is not None
    await database._engine.dispose()


@pytest.mark.asyncio
async def test_init_db_requires_database_url_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    database._engine = None
    database._session_factory = None
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await database.init_db()


@pytest.mark.asyncio
async def test_get_db_yields_session_and_init_db_explicit_url(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'explicit.db'}"
    await database.init_db(db_url)
    sessions = []
    async for session in database.get_db():
        sessions.append(session)
    assert sessions
    await database._engine.dispose()


@pytest.mark.asyncio
async def test_init_db_uses_existing_engine_path(tmp_path) -> None:
    engine, _ = database.create_engine_and_session(f"sqlite+aiosqlite:///{tmp_path / 'existing.db'}")
    await database.init_db()
    await engine.dispose()


def test_install_sqlite_hooks_non_sqlite_connection() -> None:
    engine, _ = database.create_engine_and_session("sqlite+aiosqlite:///:memory:")
    try:
        class Cursor:
            def execute(self, statement: str) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeDbapiConnection:
            def cursor(self) -> Cursor:
                return Cursor()

        assert database._get_sqlite_connection(FakeDbapiConnection()) is None
    finally:
        __import__("asyncio").run(engine.dispose())


def test_get_sqlite_connection_with_nested_driver_non_sqlite() -> None:
    from types import SimpleNamespace

    fake = SimpleNamespace(driver_connection=SimpleNamespace(_conn="not-a-connection"))
    assert database._get_sqlite_connection(fake) is None

    fake2 = SimpleNamespace(driver_connection="not-a-connection")
    assert database._get_sqlite_connection(fake2) is None
