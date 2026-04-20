from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import User

logger = logging.getLogger(__name__)


async def bootstrap_admin_user(db: AsyncSession, settings: Settings) -> User | None:
    existing = (await db.execute(select(User).limit(1))).scalar_one_or_none()
    if existing is not None:
        return None

    email = settings.ADMIN_EMAIL.strip().lower()
    if not email or not settings.ADMIN_PASSWORD_HASH:
        logger.warning(
            "Bootstrap admin skipped: ADMIN_EMAIL or ADMIN_PASSWORD_HASH empty"
        )
        return None

    admin = User(
        email=email,
        hashed_password=settings.ADMIN_PASSWORD_HASH,
        is_active=True,
        is_admin=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    logger.info(
        "Bootstrap admin user created: id=%d email=%s (from ADMIN_EMAIL + ADMIN_PASSWORD_HASH)",
        admin.id,
        admin.email,
    )
    return admin
