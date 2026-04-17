from __future__ import annotations

import asyncio
import json

import pytest

from app.services import scan_progress as sp


def test_scan_progress_properties_to_dict_and_to_json() -> None:
    progress = sp.ScanProgress(
        phase=sp.ScanPhase.SCANNING,
        total_accounts=4,
        current_account_idx=2,
        total_emails=10,
        emails_processed=3,
    )

    assert progress.account_pct == 0.5
    assert progress.email_pct == 0.3
    # overall: completed=(2-1)/4=0.25, current_weight=0.25, email_frac=3/10=0.3
    # = 0.25 + 0.25 * 0.3 = 0.325
    assert progress.overall_pct == 0.325
    assert progress.to_dict()["account_pct"] == 50.0
    assert progress.to_dict()["email_pct"] == 30.0
    assert progress.to_dict()["overall_pct"] == 32.5
    assert progress.to_dict()["phase"] == "scanning"
    assert json.loads(progress.to_json())["phase"] == "scanning"


def test_scan_progress_properties_return_zero_without_totals() -> None:
    progress = sp.ScanProgress()

    assert progress.account_pct == 0.0
    assert progress.email_pct == 0.0
    assert progress.overall_pct == 0.0


@pytest.mark.asyncio
async def test_reset_update_and_finish_progress() -> None:
    sp.reset_progress(total_accounts=3)
    initial_updated_at = sp.get_progress().updated_at

    await sp.update_progress(
        current_account_idx=1,
        current_account_name="Inbox",
        invoices_found=2,
        total_emails=5,
    )
    progress = sp.get_progress()
    assert progress.phase is sp.ScanPhase.SCANNING
    assert progress.total_accounts == 3
    assert progress.current_account_idx == 1
    assert progress.current_account_name == "Inbox"
    assert progress.invoices_found == 2
    assert progress.updated_at >= initial_updated_at

    await sp.finish_progress()
    progress = sp.get_progress()
    assert progress.phase is sp.ScanPhase.DONE
    assert progress.current_account_idx == 3
    assert progress.current_email_idx == 5
    assert progress.emails_processed == 5

    await sp.finish_progress(error="boom")
    assert sp.get_progress().phase is sp.ScanPhase.ERROR


@pytest.mark.asyncio
async def test_progress_counters_increment_atomically() -> None:
    sp.reset_progress(total_accounts=1)

    await asyncio.gather(
        *(sp.inc_emails_processed() for _ in range(25)),
        *(sp.inc_invoices_found() for _ in range(7)),
        *(sp.inc_errors() for _ in range(3)),
    )

    progress = sp.get_progress()
    assert progress.emails_processed == 25
    assert progress.invoices_found == 7
    assert progress.errors == 3


@pytest.mark.asyncio
async def test_is_scanning_reflects_lock_state() -> None:
    assert sp.is_scanning() is False

    await sp._scan_lock.acquire()
    try:
        assert sp.is_scanning() is True
    finally:
        sp._scan_lock.release()

    assert sp.is_scanning() is False


def test_subscribe_unsubscribe_and_broadcast() -> None:
    queue = sp.subscribe()
    try:
        sp._broadcast("payload")
        assert queue.get_nowait() == "payload"
    finally:
        sp.unsubscribe(queue)

    assert queue not in sp._subscribers
    sp.unsubscribe(queue)


def test_broadcast_evicts_full_queues() -> None:
    full_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    full_queue.put_nowait("filled")
    live_queue = sp.subscribe()
    sp._subscribers.insert(0, full_queue)

    try:
        sp._broadcast("payload")
        assert full_queue not in sp._subscribers
        assert live_queue.get_nowait() == "payload"
    finally:
        sp.unsubscribe(live_queue)
