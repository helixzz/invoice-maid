from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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
