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
        total_emails=5,
        current_email_idx=3,
    )

    assert progress.account_pct == 0.5
    assert progress.email_pct == 0.6
    assert progress.overall_pct == 0.32
    assert progress.to_dict()["account_pct"] == 50.0
    assert progress.to_dict()["email_pct"] == 60.0
    assert progress.to_dict()["overall_pct"] == 32.0
    assert progress.to_dict()["phase"] == "scanning"
    assert json.loads(progress.to_json())["phase"] == "scanning"


def test_scan_progress_properties_return_zero_without_totals() -> None:
    progress = sp.ScanProgress()

    assert progress.account_pct == 0.0
    assert progress.email_pct == 0.0
    assert progress.overall_pct == 0.0


def test_reset_update_and_finish_progress() -> None:
    sp.reset_progress(total_accounts=3)
    initial_updated_at = sp.get_progress().updated_at

    sp.update_progress(current_account_idx=1, current_account_name="Inbox", invoices_found=2)
    progress = sp.get_progress()
    assert progress.phase is sp.ScanPhase.SCANNING
    assert progress.total_accounts == 3
    assert progress.current_account_idx == 1
    assert progress.current_account_name == "Inbox"
    assert progress.invoices_found == 2
    assert progress.updated_at >= initial_updated_at

    sp.finish_progress()
    assert sp.get_progress().phase is sp.ScanPhase.DONE

    sp.finish_progress(error="boom")
    assert sp.get_progress().phase is sp.ScanPhase.ERROR


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
