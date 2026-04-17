from __future__ import annotations

from unittest.mock import ANY, AsyncMock

import pytest

import app.api.invoices as invoices_api


async def test_list_get_delete_and_search_invoices(
    client, auth_headers, create_invoice, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice = await create_invoice(invoice_no="INV-API", buyer="Alpha Buyer", raw_text="Alpha Buyer")
    semantic_search_calls: list[dict[str, object]] = []

    list_response = await client.get("/api/v1/invoices?q=Alpha", headers=auth_headers)
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1

    get_response = await client.get(f"/api/v1/invoices/{invoice.id}", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["invoice_no"] == "INV-API"

    class FakeAIService:
        def __init__(self, settings):
            del settings

        async def embed_text(self, text: str, db=None):
            del text, db
            return [0.1, 0.2, 0.3]

    class FakeSearchService:
        def __init__(self, settings):
            del settings

        async def search_fts(self, **kwargs):
            del kwargs
            return [invoice], 1

        async def search(self, **kwargs):
            semantic_search_calls.append(kwargs)
            return [invoice], 1

    monkeypatch.setattr(invoices_api, "AIService", FakeAIService)
    monkeypatch.setattr(invoices_api, "SearchService", FakeSearchService)
    monkeypatch.setattr(
        invoices_api.FileManager,
        "delete_invoice_file",
        AsyncMock(return_value=True),
    )

    semantic_response = await client.post(
        "/api/v1/invoices/search",
        headers=auth_headers,
        json={"query": "Alpha", "page": 3, "size": 5},
    )
    assert semantic_response.status_code == 200
    assert semantic_response.json()["items"][0]["invoice_no"] == "INV-API"
    assert semantic_response.json()["page"] == 3
    assert semantic_response.json()["size"] == 5
    assert semantic_search_calls == [
        {
            "db": ANY,
            "query": "Alpha",
            "query_embedding": [0.1, 0.2, 0.3],
            "page": 3,
            "size": 5,
        }
    ]

    delete_response = await client.delete(f"/api/v1/invoices/{invoice.id}", headers=auth_headers)
    assert delete_response.status_code == 204


async def test_invoice_not_found_paths(client, auth_headers) -> None:
    get_response = await client.get("/api/v1/invoices/999", headers=auth_headers)
    assert get_response.status_code == 404
    delete_response = await client.delete("/api/v1/invoices/999", headers=auth_headers)
    assert delete_response.status_code == 404


@pytest.mark.parametrize(
    ("page", "size"),
    [
        (0, 5),
        (-1, 5),
        (1, 0),
        (1, -1),
        (1, 101),
    ],
)
async def test_semantic_search_rejects_invalid_pagination(client, auth_headers, page: int, size: int) -> None:
    response = await client.post(
        "/api/v1/invoices/search",
        headers=auth_headers,
        json={"query": "Alpha", "page": page, "size": size},
    )

    assert response.status_code == 422


async def test_batch_delete_invoices(client, auth_headers, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    invoice_a = await create_invoice(invoice_no="INV-BATCH-A")
    invoice_b = await create_invoice(invoice_no="INV-BATCH-B")

    delete_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(invoices_api.FileManager, "delete_invoice_file", delete_mock)

    response = await client.post(
        "/api/v1/invoices/batch-delete",
        headers=auth_headers,
        json={"ids": [invoice_a.id, invoice_b.id]},
    )
    assert response.status_code == 204
    assert delete_mock.await_count == 2

    empty_response = await client.post(
        "/api/v1/invoices/batch-delete",
        headers=auth_headers,
        json={"ids": []},
    )
    assert empty_response.status_code == 204

    mixed_response = await client.post(
        "/api/v1/invoices/batch-delete",
        headers=auth_headers,
        json={"ids": [999999]},
    )
    assert mixed_response.status_code == 204
