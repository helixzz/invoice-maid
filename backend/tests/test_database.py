from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import app.database as database
import app.config as config_module


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
    settings = config_module.get_settings()
    settings.SQLITE_VEC_ENABLED = False

    await database.init_db()

    assert database._engine is not None
    assert database._session_factory is not None
    assert settings.sqlite_vec_available is False

    async with database._engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(invoice_embeddings)"))
        columns = [row[1] for row in result.fetchall()]
        assert columns == ["rowid", "embedding"]
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
    settings = config_module.get_settings()
    settings.SQLITE_VEC_ENABLED = False
    await database.init_db(db_url)
    sessions = []
    async for session in database.get_db():
        sessions.append(session)
    assert sessions
    await database._engine.dispose()


@pytest.mark.asyncio
async def test_init_db_uses_existing_engine_path(tmp_path) -> None:
    engine, _ = database.create_engine_and_session(f"sqlite+aiosqlite:///{tmp_path / 'existing.db'}")
    settings = config_module.get_settings()
    settings.SQLITE_VEC_ENABLED = False
    await database.init_db()
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_embedding_objects_falls_back_when_vec_table_fails(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = database.create_engine_and_session(f"sqlite+aiosqlite:///{tmp_path / 'fallback.db'}")

    original_sql_builder = database._invoice_embeddings_table_sql

    def fake_sql_builder(embed_dim: int, sqlite_vec_enabled: bool) -> str:
        if sqlite_vec_enabled:
            return "CREATE VIRTUAL TABLE invoice_embeddings USING definitely_missing_module(embedding FLOAT[3])"
        return original_sql_builder(embed_dim, sqlite_vec_enabled)

    monkeypatch.setattr(database, "_invoice_embeddings_table_sql", fake_sql_builder)

    try:
        available = await database.create_embedding_objects(engine, embed_dim=3, sqlite_vec_requested=True)
        assert available is False
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(invoice_embeddings)"))
            columns = [row[1] for row in result.fetchall()]
            assert columns == ["rowid", "embedding"]
    finally:
        await engine.dispose()


def test_install_sqlite_hooks_non_sqlite_connection() -> None:
    engine, _ = database.create_engine_and_session("sqlite+aiosqlite:///:memory:")
    try:
        class Cursor:
            def execute(self, statement: str) -> None:
                del statement

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


def test_invoice_embeddings_table_sql_uses_vec0_when_enabled() -> None:
    sql = database._invoice_embeddings_table_sql(1536, True)

    assert "CREATE VIRTUAL TABLE IF NOT EXISTS invoice_embeddings" in sql
    assert "USING vec0" in sql
    assert "embedding FLOAT[1536]" in sql


@pytest.mark.asyncio
async def test_create_embedding_objects_returns_true_when_requested_table_succeeds(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = database.create_engine_and_session(f"sqlite+aiosqlite:///{tmp_path / 'vec-success.db'}")

    original_sql_builder = database._invoice_embeddings_table_sql

    def fake_sql_builder(embed_dim: int, sqlite_vec_enabled: bool) -> str:
        if sqlite_vec_enabled:
            return """
            CREATE TABLE IF NOT EXISTS invoice_embeddings (
                rowid INTEGER PRIMARY KEY,
                embedding BLOB NOT NULL
            )
            """
        return original_sql_builder(embed_dim, sqlite_vec_enabled)

    monkeypatch.setattr(database, "_invoice_embeddings_table_sql", fake_sql_builder)

    try:
        available = await database.create_embedding_objects(engine, embed_dim=8, sqlite_vec_requested=True)
        assert available is True
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(invoice_embeddings)"))
            columns = [row[1] for row in result.fetchall()]
            assert columns == ["rowid", "embedding"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_embedding_objects_returns_false_when_sqlite_vec_not_requested(tmp_path) -> None:
    engine, _ = database.create_engine_and_session(f"sqlite+aiosqlite:///{tmp_path / 'blob-only.db'}")

    try:
        available = await database.create_embedding_objects(engine, embed_dim=8, sqlite_vec_requested=False)
        assert available is False
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(invoice_embeddings)"))
            columns = [row[1] for row in result.fetchall()]
            assert columns == ["rowid", "embedding"]
    finally:
        await engine.dispose()
