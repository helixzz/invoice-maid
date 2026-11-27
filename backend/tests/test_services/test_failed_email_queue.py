from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.failed_email import FailedEmail
from app.services.email_scanner import RawEmail
from app.services.failed_email_queue import (
    count_pending_failed_emails,
    enqueue_failed_email,
    fetch_due_failed_emails,
    mark_failed_email_resolved,
)


def _raw(uid: str = "uid-1", **overrides) -> RawEmail:
    defaults = {
        "uid": uid,
        "subject": "您的京东订单电子发票已开具",
        "body_text": "invoice body",
        "body_html": "",
        "from_addr": "service@jd.com",
        "received_at": datetime.now(timezone.utc),
        "attachments": [],
        "body_links": ["https://jd.com/inv.pdf"],
        "headers": {"_graph_id": "g1", "_hydrate_error": "429"},
        "is_hydrated": False,
    }
    defaults.update(overrides)
    return RawEmail(**defaults)


@pytest.mark.asyncio
async def test_failed_email_queue_lifecycle(db, create_email_account) -> None:
    account = await create_email_account()
    email = _raw()

    entry = await enqueue_failed_email(
        db,
        user_id=account.user_id,
        email_account_id=account.id,
        email_data=email,
        error_message="503 LLM unavailable",
        max_retries=2,
    )
    assert entry.retry_count == 1
    assert entry.status == "pending"
    assert await count_pending_failed_emails(db) == 1

    # Not due yet
    assert await fetch_due_failed_emails(db, email_account_id=account.id) == []

    # Force due and fetch
    entry.next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    due = await fetch_due_failed_emails(db, email_account_id=account.id)
    assert len(due) == 1
    assert due[0].uid == "uid-1"
    assert due[0].headers["_from_retry_queue"] == "True"
    assert "_hydrate_error" not in due[0].headers
    assert due[0].headers["_graph_id"] == "g1"
    assert due[0].is_hydrated is False

    # Mark resolved
    assert await mark_failed_email_resolved(db, email_account_id=account.id, email_uid="uid-1") is True
    assert await mark_failed_email_resolved(db, email_account_id=account.id, email_uid="uid-1") is False
    assert await count_pending_failed_emails(db) == 0


@pytest.mark.asyncio
async def test_failed_email_queue_exhaustion_and_malformed_json(db, create_email_account) -> None:
    account = await create_email_account()
    email = _raw(uid="uid-2", headers=None)

    entry = await enqueue_failed_email(
        db,
        user_id=account.user_id,
        email_account_id=account.id,
        email_data=email,
        error_message="err1",
        max_retries=3,
    )
    # 2nd failure stays pending
    entry = await enqueue_failed_email(
        db,
        user_id=account.user_id,
        email_account_id=account.id,
        email_data=email,
        error_message="err2",
        max_retries=3,
    )
    assert entry.retry_count == 2
    assert entry.status == "pending"

    # Corrupt json fields to verify defensive deserialization
    entry.body_links_json = '{"not": "a list"}'
    entry.headers_json = '["not", "a dict"]'
    entry.next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    due = await fetch_due_failed_emails(db, email_account_id=account.id)
    assert len(due) == 1
    assert due[0].body_links == []

    # Invalid JSON strings
    entry.body_links_json = "{bad"
    entry.headers_json = "{bad"
    await db.commit()
    due2 = await fetch_due_failed_emails(db, email_account_id=account.id)
    assert len(due2) == 1
    assert due2[0].body_links == []

    # 3rd failure hits max_retries -> exhausted
    entry = await enqueue_failed_email(
        db,
        user_id=account.user_id,
        email_account_id=account.id,
        email_data=email,
        error_message="err3",
        max_retries=3,
    )
    assert entry.status == "exhausted"
    assert await count_pending_failed_emails(db) == 0
