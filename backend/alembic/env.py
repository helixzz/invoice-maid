from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base


def _load_dotenv_if_present() -> None:
    """Ensure ``STORAGE_PATH``, ``DATABASE_URL``, and any other env vars
    that the application reads from ``backend/.env`` are available to
    migrations. The application loads this file through pydantic
    settings at import time; the alembic CLI does not, so a data
    migration like 0013 (which moves invoice files on disk based on
    ``STORAGE_PATH``) would otherwise fall back to its URL-derived
    default and target the wrong directory in deployments where the
    env file is the source of truth (systemd-managed hosts, for
    example). Silent no-op if ``.env`` is missing or python-dotenv is
    unavailable — the caller either set the vars explicitly or the
    migration will raise loudly itself.

    Honors ``ALEMBIC_SKIP_DOTENV=1`` as a test-only escape hatch so
    tests that need to exercise missing-env fallback branches (e.g.
    ``test_0012_refuses_to_run_without_admin_seed_inputs``) can
    neutralize the auto-load without deleting ``.env`` from the
    developer's workspace."""
    if os.getenv("ALEMBIC_SKIP_DOTENV", "").lower() in {"1", "true", "yes"}:
        return
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dep
        return
    load_dotenv(env_path, override=False)


_load_dotenv_if_present()


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    try:
        from app.config import get_settings
    except Exception as exc:  # pragma: no cover - config bootstrap fallback
        raise RuntimeError("DATABASE_URL is not configured for Alembic migrations.") from exc

    return get_settings().DATABASE_URL


def include_object(object_: object, name: str | None, type_: str, reflected: bool, compare_to: object | None) -> bool:
    del object_, reflected, compare_to
    if type_ == "table" and name is not None:
        if name.startswith(("vec", "fts")) or name.endswith("_fts"):
            return False
    return True


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
