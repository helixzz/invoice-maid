from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.failed_email import FailedEmail
from app.services.email_scanner import RawEmail

RETRY_BACKOFF_MINUTES = (5, 15, 45, 120, 360)


def _compute_next_retry(retry_count: int, now: datetime) -> datetime:
    idx = min(max(retry_count - 1, 0), len(RETRY_BACKOFF_MINUTES) - 1)
    return now + timedelta(minutes=RETRY_BACKOFF_MINUTES[idx])


def _safe_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {
        str(k): str(v)
        for k, v in headers.items()
        if str(k) != "_hydrate_error"
    }


async def enqueue_failed_email(
    db: AsyncSession,
    *,
    user_id: int,
    email_account_id: int,
    email_data: RawEmail,
    error_message: str,
    max_retries: int = 5,
) -> FailedEmail:
    """Persist or update a failed email in the retry queue with exponential backoff."""
    now = datetime.now(timezone.utc)
    existing = (
        await db.execute(
            select(FailedEmail).where(
                FailedEmail.email_account_id == email_account_id,
                FailedEmail.email_uid == email_data.uid,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        entry = FailedEmail(
            user_id=user_id,
            email_account_id=email_account_id,
            email_uid=email_data.uid,
            email_subject=(email_data.subject or "")[:500],
            from_addr=(email_data.from_addr or "")[:500],
            body_text=(email_data.body_text or "")[:10000],
            body_links_json=json.dumps(list(email_data.body_links or [])[:50], ensure_ascii=False),
            headers_json=json.dumps(_safe_headers(getattr(email_data, "headers", None)), ensure_ascii=False),
            is_hydrated=bool(getattr(email_data, "is_hydrated", True)),
            error_message=error_message[:2000],
            retry_count=1,
            max_retries=max_retries,
            next_retry_at=_compute_next_retry(1, now),
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.add(entry)
        await db.commit()
        return entry

    existing.retry_count += 1
    existing.error_message = error_message[:2000]
    existing.updated_at = now
    existing.is_hydrated = bool(getattr(email_data, "is_hydrated", existing.is_hydrated))
    if existing.retry_count >= existing.max_retries:
        existing.status = "exhausted"
    else:
        existing.status = "pending"
        existing.next_retry_at = _compute_next_retry(existing.retry_count, now)
    await db.commit()
    return existing


async def fetch_due_failed_emails(
    db: AsyncSession,
    *,
    email_account_id: int,
    limit: int = 50,
) -> list[RawEmail]:
    """Load pending failed emails whose retry backoff window has elapsed."""
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(FailedEmail)
            .where(
                FailedEmail.email_account_id == email_account_id,
                FailedEmail.status == "pending",
                FailedEmail.next_retry_at <= now,
            )
            .order_by(FailedEmail.next_retry_at.asc(), FailedEmail.id.asc())
            .limit(limit)
        )
    ).scalars().all()

    rebuilt: list[RawEmail] = []
    for row in rows:
        try:
            body_links = json.loads(row.body_links_json)
            if not isinstance(body_links, list):
                body_links = []
        except (TypeError, ValueError):
            body_links = []
        try:
            headers = json.loads(row.headers_json)
            if not isinstance(headers, dict):
                headers = {}
        except (TypeError, ValueError):
            headers = {}

        headers["_from_retry_queue"] = "True"
        rebuilt.append(
            RawEmail(
                uid=row.email_uid,
                subject=row.email_subject,
                body_text=row.body_text,
                body_html="",
                from_addr=row.from_addr,
                received_at=row.created_at or now,
                attachments=[],
                body_links=[str(u) for u in body_links],
                headers={str(k): str(v) for k, v in headers.items()},
                is_hydrated=bool(row.is_hydrated),
            )
        )
    return rebuilt


async def mark_failed_email_resolved(
    db: AsyncSession,
    *,
    email_account_id: int,
    email_uid: str,
) -> bool:
    existing = (
        await db.execute(
            select(FailedEmail).where(
                FailedEmail.email_account_id == email_account_id,
                FailedEmail.email_uid == email_uid,
                FailedEmail.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    existing.status = "resolved"
    existing.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return True


async def count_pending_failed_emails(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count(FailedEmail.id)).where(FailedEmail.status == "pending")
        )
    ) or 0
