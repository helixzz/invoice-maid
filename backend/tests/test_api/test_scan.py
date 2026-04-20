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
        user_id=account.user_id, email_account_id=account.id,
        started_at=datetime(2024, 1, 1),
        finished_at=datetime(2024, 1, 1, 0, 1),
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
    assert "+00:00" in payload["items"][0]["started_at"]
    assert "+00:00" in payload["items"][0]["finished_at"]


async def test_scan_logs_preserve_timezone_aware_datetimes(client, auth_headers, db, create_email_account) -> None:
    account = await create_email_account()
    log = ScanLog(
        user_id=account.user_id, email_account_id=account.id,
        started_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2024, 6, 15, 12, 5, 0, tzinfo=timezone.utc),
        emails_scanned=50,
        invoices_found=3,
    )
    db.add(log)
    await db.commit()

    response = await client.get("/api/v1/scan/logs?page=1&size=10", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "2024-06-15T12:00:00" in item["started_at"]
    assert "2024-06-15T12:05:00" in item["finished_at"]


async def test_trigger_scan_returns_409_when_already_running(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(scan_api.sp, "is_scanning", lambda: True)

    response = await client.post("/api/v1/scan/trigger", headers=auth_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Scan already in progress"}


async def test_trigger_full_rescan_resets_last_scan_uid(client, auth_headers, db, create_email_account, monkeypatch) -> None:
    task_calls: list[object] = []
    monkeypatch.setattr("app.api.scan.asyncio", SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task"))

    account = await create_email_account()
    account.last_scan_uid = "12345"
    await db.commit()

    response = await client.post("/api/v1/scan/trigger?full=true", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "triggered"}

    await db.refresh(account)
    assert account.last_scan_uid is None
    for coro in task_calls:
        coro.close()


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
    assert response.json() == {"detail": "Not found"}


async def test_list_extraction_logs_returns_new_parse_metadata_fields(client, auth_headers, create_scan_log, create_extraction_log) -> None:
    scan_log = await create_scan_log()
    await create_extraction_log(
        scan_log=scan_log,
        outcome="saved",
        classification_tier=3,
        parse_method="qr",
        parse_format="pdf",
        download_outcome="downloaded",
    )
    response = await client.get(f"/api/v1/scan/logs/{scan_log.id}/extractions", headers=auth_headers)

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["classification_tier"] == 3
    assert item["parse_method"] == "qr"
    assert item["parse_format"] == "pdf"
    assert item["download_outcome"] == "downloaded"


async def test_scan_log_summary_aggregates_outcomes_methods_and_tiers(client, auth_headers, create_scan_log, create_extraction_log) -> None:
    scan_log = await create_scan_log()
    await create_extraction_log(scan_log=scan_log, outcome="saved", classification_tier=3, parse_method="qr")
    await create_extraction_log(scan_log=scan_log, outcome="saved", classification_tier=1, parse_method="xml_xpath")
    await create_extraction_log(scan_log=scan_log, outcome="not_invoice", classification_tier=1, parse_method=None)
    await create_extraction_log(scan_log=scan_log, outcome="low_confidence", classification_tier=3, parse_method="regex")
    await create_extraction_log(scan_log=scan_log, outcome="duplicate", classification_tier=3, parse_method="qr")

    response = await client.get(f"/api/v1/scan/logs/{scan_log.id}/summary", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["scan_log_id"] == scan_log.id
    assert body["total"] == 5
    assert body["outcomes"] == {"saved": 2, "not_invoice": 1, "low_confidence": 1, "duplicate": 1}
    assert body["parse_methods"] == {"qr": 2, "xml_xpath": 1, "regex": 1}
    assert body["classification_tiers"] == {"tier1": 2, "tier3": 3}


async def test_scan_log_summary_missing_scan_returns_404(client, auth_headers) -> None:
    response = await client.get("/api/v1/scan/logs/999/summary", headers=auth_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


async def test_scan_log_summary_empty_scan_has_zero_total(client, auth_headers, create_scan_log) -> None:
    scan_log = await create_scan_log()
    response = await client.get(f"/api/v1/scan/logs/{scan_log.id}/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["outcomes"] == {}
    assert body["parse_methods"] == {}
    assert body["classification_tiers"] == {}


async def test_trigger_scan_with_body_options_passes_to_scheduler(client, auth_headers, db, create_email_account, monkeypatch) -> None:
    task_calls: list[object] = []
    monkeypatch.setattr("app.api.scan.asyncio", SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task"))

    captured_options: list = []

    async def fake_scan_all_accounts(options=None):
        captured_options.append(options)

    monkeypatch.setattr("app.api.scan.scan_all_accounts", fake_scan_all_accounts)
    await create_email_account()

    response = await client.post(
        "/api/v1/scan/trigger",
        headers=auth_headers,
        json={"full": False, "unread_only": True, "since": "2024-06-15T00:00:00Z"},
    )
    assert response.status_code == 200
    for coro in task_calls:
        await coro


async def test_trigger_scan_body_full_overrides_query(client, auth_headers, db, create_email_account, monkeypatch) -> None:
    task_calls: list[object] = []
    monkeypatch.setattr("app.api.scan.asyncio", SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task"))

    async def fake_scan_all_accounts(options=None):
        del options

    monkeypatch.setattr("app.api.scan.scan_all_accounts", fake_scan_all_accounts)
    account = await create_email_account()
    account.last_scan_uid = "old-state"
    await db.commit()

    response = await client.post(
        "/api/v1/scan/trigger",
        headers=auth_headers,
        json={"full": True, "unread_only": False, "since": None},
    )
    assert response.status_code == 200
    await db.refresh(account)
    assert account.last_scan_uid is None
    for coro in task_calls:
        await coro


async def test_trigger_scan_body_absent_still_works(client, auth_headers, db, create_email_account, monkeypatch) -> None:
    task_calls: list[object] = []
    monkeypatch.setattr("app.api.scan.asyncio", SimpleNamespace(create_task=lambda coro: task_calls.append(coro) or "task"))

    async def fake_scan_all_accounts(options=None):
        del options

    monkeypatch.setattr("app.api.scan.scan_all_accounts", fake_scan_all_accounts)
    await create_email_account()

    response = await client.post("/api/v1/scan/trigger", headers=auth_headers)
    assert response.status_code == 200
    for coro in task_calls:
        await coro
