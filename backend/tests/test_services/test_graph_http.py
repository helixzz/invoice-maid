from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.services.graph_http as graph_http_mod
from app.services.graph_http import graph_get_with_retry, parse_retry_after


def test_parse_retry_after_handles_header_and_fallback() -> None:
    assert parse_retry_after({"Retry-After": "5"}, 0) == 5.0
    assert parse_retry_after({"retry-after": "120"}, 0) == 30.0
    assert parse_retry_after({"Retry-After": "-3"}, 1) == 2.0
    assert parse_retry_after({"Retry-After": "not-a-number"}, 2) == 4.0
    assert parse_retry_after(None, 0) == 1.0


@pytest.mark.asyncio
async def test_graph_get_with_retry_retries_on_429_and_503(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(graph_http_mod.asyncio, "sleep", fake_sleep)

    responses = [
        SimpleNamespace(status_code=429, headers={"Retry-After": "2"}),
        SimpleNamespace(status_code=503, headers={}),
        SimpleNamespace(status_code=200, headers={}),
    ]
    calls: list[dict[str, Any]] = []

    class FakeClient:
        async def get(self, url: str, headers: dict[str, str], params: dict[str, Any] | None = None):
            calls.append({"url": url, "headers": headers, "params": params})
            return responses.pop(0)

    resp = await graph_get_with_retry(
        FakeClient(),
        "https://graph.microsoft.com/v1.0/me/messages/1?$select=body",
        headers={"Authorization": "Bearer x"},
        params={"$select": "body"},
        max_retries=3,
    )
    assert resp.status_code == 200
    assert len(calls) == 3
    assert sleeps == [2.0, 2.0]


@pytest.mark.asyncio
async def test_graph_get_with_retry_returns_last_response_when_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(graph_http_mod.asyncio, "sleep", fake_sleep)

    class FakeClient:
        async def get(self, url: str, headers: dict[str, str]):
            del url, headers
            return SimpleNamespace(status_code=429, headers={"Retry-After": "1"})

    resp = await graph_get_with_retry(
        FakeClient(),
        "https://graph.microsoft.com/v1.0/me/mailFolders",
        headers={"Authorization": "Bearer x"},
        max_retries=1,
    )
    assert resp.status_code == 429
