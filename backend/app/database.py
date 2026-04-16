from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def load_sqlite_vec(connection: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        return True
    except Exception as exc:  # pragma: no cover - depends on local SQLite build
        logger.warning("sqlite-vec not available: %s. Semantic search disabled.", exc)
        return False


def _get_sqlite_connection(dbapi_connection: Any) -> sqlite3.Connection | None:
    driver_connection = getattr(dbapi_connection, "driver_connection", None)
    if driver_connection is None:
        return dbapi_connection if isinstance(dbapi_connection, sqlite3.Connection) else None

    if isinstance(driver_connection, sqlite3.Connection):
        return driver_connection

    raw_connection = getattr(driver_connection, "_conn", None)
    return raw_connection if isinstance(raw_connection, sqlite3.Connection) else None


def _install_sqlite_hooks(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn: Any, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

        sqlite_connection = _get_sqlite_connection(dbapi_conn)
        if sqlite_connection is not None:
            _ = load_sqlite_vec(sqlite_connection)


def create_engine_and_session(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )
    _install_sqlite_hooks(engine)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    global _engine, _session_factory
    _engine = engine
    _session_factory = session_factory

    return engine, session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Database session factory has not been initialized.")

    async with _session_factory() as session:
        yield session


async def create_fts5_objects(engine: AsyncEngine) -> None:
    statements = (
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS invoices_fts
        USING fts5(
            invoice_no, buyer, seller, invoice_type, item_summary, raw_text,
            content='invoices', content_rowid='id'
        )
        """,
        """
        CREATE TRIGGER IF NOT EXISTS invoices_ai AFTER INSERT ON invoices BEGIN
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS invoices_ad AFTER DELETE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS invoices_au AFTER UPDATE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END
        """,
        "INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')",
    )

    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


async def init_db(database_url: str | None = None) -> None:
    engine = _engine
    if database_url is not None:
        engine, _ = create_engine_and_session(database_url)
    elif engine is None:
        env_database_url = os.getenv("DATABASE_URL")
        if env_database_url is None:
            raise RuntimeError("DATABASE_URL is required to initialize the database.")
        engine, _ = create_engine_and_session(env_database_url)

    assert engine is not None

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    await create_fts5_objects(engine)
