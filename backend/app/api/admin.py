from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import AdminUser
from app.models import (
    CorrectionLog,
    EmailAccount,
    ExtractionLog,
    Invoice,
    SavedView,
    ScanLog,
    User,
    UserSession,
    WebhookLog,
)
from app.schemas.admin import AdminUserPatch, AdminUserSummary
from app.services.file_manager import FileManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserSummary])
async def list_users(
    _admin: AdminUser,
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserSummary]:
    """List all users with their invoice counts. Admin-only."""
    stmt = (
        select(
            User.id,
            User.email,
            User.is_active,
            User.is_admin,
            User.created_at,
            func.count(Invoice.id).label("invoice_count"),
        )
        .outerjoin(Invoice, Invoice.user_id == User.id)
        .group_by(User.id)
        .order_by(User.id.asc())
    )
    result = await db.execute(stmt)
    return [
        AdminUserSummary(
            id=row.id,
            email=row.email,
            is_active=row.is_active,
            is_admin=row.is_admin,
            created_at=row.created_at,
            invoice_count=int(row.invoice_count or 0),
        )
        for row in result.all()
    ]


@router.put("/users/{user_id}", response_model=AdminUserSummary)
async def update_user(
    user_id: int,
    payload: AdminUserPatch,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
) -> AdminUserSummary:
    """Admin edits: toggle is_active, toggle is_admin, rename email.

    Admin cannot:
    - Deactivate themselves (``is_active=False`` on self)
    - Demote themselves from admin if they are the last admin
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if target.id == admin.id and payload.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate yourself",
        )

    if (
        target.id == admin.id
        and payload.is_admin is False
        and target.is_admin is True
    ):
        admin_count = (
            await db.execute(
                select(func.count(User.id)).where(User.is_admin.is_(True))
            )
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last admin",
            )

    if payload.email is not None:
        new_email = payload.email.strip().lower()
        if "@" not in new_email or new_email.startswith("@") or new_email.endswith("@"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="email must contain '@'",
            )
        existing = await db.execute(
            select(User).where(User.email == new_email, User.id != target.id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email is already taken",
            )
        target.email = new_email

    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.is_admin is not None:
        target.is_admin = payload.is_admin

    await db.commit()
    await db.refresh(target)

    invoice_count = (
        await db.execute(
            select(func.count(Invoice.id)).where(Invoice.user_id == target.id)
        )
    ).scalar_one()

    return AdminUserSummary(
        id=target.id,
        email=target.email,
        is_active=target.is_active,
        is_admin=target.is_admin,
        created_at=target.created_at,
        invoice_count=int(invoice_count),
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a user and cascade-delete all their data.

    The tenant tables all CASCADE on users.id via the FK added in
    migration 0012, so the DB-side cleanup is automatic. This handler
    also removes the user's per-user file directory from disk via
    ``FileManager.delete_user_files``.

    Admin cannot delete themselves — would leave an orphan admin user
    slot and could lock out the instance."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Explicit per-table cleanup in FK-safe order. Production has
    # PRAGMA foreign_keys=ON (set via the app's connect hook) and the
    # migration 0012 FKs cascade, but we don't rely on that: an
    # operator running alembic via a path that skips the app's hook
    # (or a future DB engine) would silently leak rows. Explicit
    # deletion is defensive and matches what FileManager.delete_user_files
    # does for the disk side.
    for model in (
        CorrectionLog,
        ExtractionLog,
        WebhookLog,
        SavedView,
        Invoice,
        ScanLog,
        EmailAccount,
        UserSession,
    ):
        await db.execute(delete(model).where(model.user_id == user_id))

    await db.delete(target)
    await db.commit()

    file_manager = FileManager(get_settings().STORAGE_PATH)
    removed = await file_manager.delete_user_files(user_id)
    logger.info(
        "admin %s deleted user %d (%s); removed %d files",
        admin.email,
        user_id,
        target.email,
        removed,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
