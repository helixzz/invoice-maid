from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.api.scan as scan_api
from app.services import scan_progress as sp
from app.models import ScanLog


async def test_trigger_scan_and_list_logs(client, auth_headers, db, create_email_account, monkeypatch) -> None:
    task_calls = []
    monkeypatch.setattr("app.api.scan.asyncio", SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task"))

    trigger = await client.post("/api/v1/scan/trigger", headers=auth_headers)
    assert trigger.status_code == 200
    assert trigger.json() == {"status": "triggered"}
    assert task_calls
    task_calls[0].close()

    account = await create_email_account()
    log = ScanLog(
        email_account_id=account.id,
        started_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
        emails_scanned=2,
        invoices_found=1,
        error_message=None,
    )
    db.add(log)
    await db.commit()

    response = await client.get("/api/v1/scan/logs?page=1&size=10", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["emails_scanned"] == 2


async def test_trigger_scan_returns_409_when_already_running(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(scan_api.sp, "is_scanning", lambda: True)

    response = await client.post("/api/v1/scan/trigger", headers=auth_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Scan already in progress"}


async def test_progress_snapshot_accepts_query_token_and_returns_progress(client, auth_token) -> None:
    sp.reset_progress(total_accounts=2)
    await sp.update_progress(current_account_idx=1, current_account_name="Inbox")

    response = await client.get(f"/api/v1/scan/progress?token={auth_token}")

    assert response.status_code == 200
    assert response.json()["phase"] == "scanning"
    assert response.json()["current_account_name"] == "Inbox"


@pytest.mark.asyncio
async def test_progress_stream_emits_progress_and_done_event(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEventSourceResponse:
        def __init__(self, content, headers):
            self.body_iterator = content
            self.headers = headers

    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr(scan_api, "EventSourceResponse", FakeEventSourceResponse)
    sp.reset_progress(total_accounts=1)

    response = await scan_api.progress_stream(FakeRequest(), "admin")
    stream = response.body_iterator
    first = await anext(stream)
    assert first["event"] == "progress"
    assert '"phase": "scanning"' in first["data"]

    await sp.finish_progress()
    second = await anext(stream)
    assert second["event"] == "progress"
    assert '"phase": "done"' in second["data"]
    assert response.headers == {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert sp._subscribers == []


@pytest.mark.asyncio
async def test_progress_stream_emits_ping_on_timeout_and_unsubscribes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEventSourceResponse:
        def __init__(self, content, headers):
            self.body_iterator = content
            self.headers = headers

    class FakeRequest:
        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 1

    async def fake_wait_for(awaitable, timeout):
        del timeout
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(scan_api, "EventSourceResponse", FakeEventSourceResponse)
    monkeypatch.setattr(scan_api.asyncio, "wait_for", fake_wait_for)

    response = await scan_api.progress_stream(FakeRequest(), "admin")
    stream = response.body_iterator

    assert (await anext(stream))["event"] == "progress"
    assert (await anext(stream)) == {"data": "", "event": "ping"}

    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert sp._subscribers == []


@pytest.mark.asyncio
async def test_progress_stream_continues_after_scanning_event_until_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEventSourceResponse:
        def __init__(self, content, headers):
            self.body_iterator = content
            self.headers = headers

    class FakeRequest:
        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 1

    monkeypatch.setattr(scan_api, "EventSourceResponse", FakeEventSourceResponse)
    sp.reset_progress(total_accounts=1)

    response = await scan_api.progress_stream(FakeRequest(), "admin")
    stream = response.body_iterator
    assert (await anext(stream))["event"] == "progress"

    await sp.update_progress(current_account_name="Mailbox")
    second = await anext(stream)
    assert second["event"] == "progress"
    assert '"phase": "scanning"' in second["data"]

    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert sp._subscribers == []


async def test_list_extraction_logs(client, auth_headers, create_scan_log, create_extraction_log) -> None:
    scan_log = await create_scan_log(emails_scanned=1, invoices_found=1)
    await create_extraction_log(
        scan_log=scan_log,
        email_uid="uid-42",
        email_subject="Invoice Subject",
        attachment_filename="invoice.xml",
        outcome="saved",
        invoice_no="INV-42",
        confidence=0.93,
    )
    await create_extraction_log(
        scan_log=scan_log,
        email_uid="uid-42",
        email_subject="Invoice Subject",
        attachment_filename="duplicate.xml",
        outcome="duplicate",
        invoice_no="INV-42",
        confidence=0.93,
    )

    response = await client.get(f"/api/v1/scan/logs/{scan_log.id}/extractions", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert [item["outcome"] for item in payload["items"]] == ["saved", "duplicate"]
    assert payload["items"][0]["attachment_filename"] == "invoice.xml"
    assert payload["items"][1]["invoice_no"] == "INV-42"


async def test_list_extraction_logs_missing_scan_log_returns_404(client, auth_headers) -> None:
    response = await client.get("/api/v1/scan/logs/999/extractions", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Scan log not found"}
