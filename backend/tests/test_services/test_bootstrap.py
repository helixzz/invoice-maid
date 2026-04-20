from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import User
from app.services.bootstrap import bootstrap_admin_user


async def test_bootstrap_creates_admin_when_users_table_empty(db, settings) -> None:
    result = await bootstrap_admin_user(db, settings)
    assert result is not None
    assert result.id == 1
    assert result.email == settings.ADMIN_EMAIL.strip().lower()
    assert result.hashed_password == settings.ADMIN_PASSWORD_HASH
    assert result.is_admin is True
    assert result.is_active is True


async def test_bootstrap_is_idempotent_when_users_exist(db, settings) -> None:
    """Running the bootstrap a second time must not create a duplicate.
    This matters because ``lifespan`` runs on every uvicorn restart —
    the DB already has a user after the first boot."""
    first = await bootstrap_admin_user(db, settings)
    assert first is not None

    second = await bootstrap_admin_user(db, settings)
    assert second is None

    count = len((await db.execute(select(User))).scalars().all())
    assert count == 1


async def test_bootstrap_skips_when_admin_email_blank(db, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "")

    result = await bootstrap_admin_user(db, settings)
    assert result is None
    assert (await db.execute(select(User))).scalar_one_or_none() is None


async def test_bootstrap_skips_when_admin_password_hash_blank(
    db, settings, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", "")

    result = await bootstrap_admin_user(db, settings)
    assert result is None
    assert (await db.execute(select(User))).scalar_one_or_none() is None


async def test_bootstrap_normalises_email_lowercase(db, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "  Admin@Example.COM  ")

    result = await bootstrap_admin_user(db, settings)
    assert result is not None
    assert result.email == "admin@example.com"
