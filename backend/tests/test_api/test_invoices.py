from __future__ import annotations

import csv
from io import StringIO
from unittest.mock import ANY, AsyncMock

import pytest
from sqlalchemy import select

import app.api.invoices as invoices_api
from app.models import CorrectionLog


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
    assert get_response.json()["extraction_method"] == "regex"
    assert get_response.json()["confidence"] == 0.8
    assert get_response.json()["is_manually_corrected"] is False

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


async def test_update_invoice_creates_correction_logs_and_sets_manual_flag(
    client, auth_headers, create_invoice, db
) -> None:
    invoice = await create_invoice(invoice_no="INV-EDIT-1", buyer="Before Buyer", amount=12.34)

    response = await client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=auth_headers,
        json={
            "buyer": "After Buyer",
            "amount": "99.88",
            "invoice_type": "数电专票",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["buyer"] == "After Buyer"
    assert payload["amount"] == 99.88
    assert payload["invoice_type"] == "数电专票"
    assert payload["is_manually_corrected"] is True

    correction_logs = (await db.execute(select(CorrectionLog).order_by(CorrectionLog.id.asc()))).scalars().all()
    assert [(item.field_name, item.old_value, item.new_value) for item in correction_logs] == [
        ("buyer", "Before Buyer", "After Buyer"),
        ("amount", "12.34", "99.88"),
        ("invoice_type", "电子普通发票", "数电专票"),
    ]

    get_response = await client.get(f"/api/v1/invoices/{invoice.id}", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["is_manually_corrected"] is True


async def test_update_invoice_rejects_duplicate_invoice_no(client, auth_headers, create_invoice) -> None:
    kept = await create_invoice(invoice_no="INV-EDIT-KEEP")
    other = await create_invoice(invoice_no="INV-EDIT-OTHER")

    response = await client.put(
        f"/api/v1/invoices/{other.id}",
        headers=auth_headers,
        json={"invoice_no": kept.invoice_no},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Invoice number already exists"}


async def test_update_invoice_allows_unique_invoice_no_and_date(client, auth_headers, create_invoice, db) -> None:
    await create_invoice(invoice_no="INV-EDIT-UNIQUE-A")
    invoice = await create_invoice(invoice_no="INV-EDIT-UNIQUE-B")

    response = await client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=auth_headers,
        json={"invoice_no": "INV-EDIT-UNIQUE-C", "item_summary": None, "invoice_date": "2024-02-02"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["invoice_no"] == "INV-EDIT-UNIQUE-C"
    assert payload["invoice_date"] == "2024-02-02"
    correction_logs = (await db.execute(select(CorrectionLog).order_by(CorrectionLog.id.asc()))).scalars().all()
    assert [(item.field_name, item.old_value, item.new_value) for item in correction_logs] == [
        ("invoice_date", "2024-01-01", "2024-02-02"),
        ("item_summary", "办公用品", None),
        ("invoice_no", "INV-EDIT-UNIQUE-B", "INV-EDIT-UNIQUE-C"),
    ]


async def test_update_invoice_no_changes_keeps_manual_flag_false(client, auth_headers, create_invoice, db) -> None:
    invoice = await create_invoice(invoice_no="INV-EDIT-SAME", buyer="Same Buyer")

    response = await client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=auth_headers,
        json={"buyer": "Same Buyer"},
    )

    assert response.status_code == 200
    assert response.json()["is_manually_corrected"] is False
    correction_logs = (await db.execute(select(CorrectionLog))).scalars().all()
    assert correction_logs == []


async def test_update_invoice_missing_returns_404(client, auth_headers) -> None:
    response = await client.put(
        "/api/v1/invoices/999999",
        headers=auth_headers,
        json={"buyer": "Missing Buyer"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Invoice not found"}


async def test_get_similar_invoices_with_embeddings(client, auth_headers, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    target = await create_invoice(invoice_no="INV-SIM-0", seller="Target Seller", item_summary="cloud service")
    near = await create_invoice(invoice_no="INV-SIM-1", seller="Target Seller", item_summary="cloud service plus")
    far = await create_invoice(invoice_no="INV-SIM-2", seller="Other Seller", item_summary="hosting")

    async def fake_similar_invoice_ids(self, db, invoice, limit=5):
        del self, db, limit
        assert invoice.id == target.id
        return [near.id, far.id]

    monkeypatch.setattr(invoices_api.SearchService, "similar_invoice_ids", fake_similar_invoice_ids)

    response = await client.get(f"/api/v1/invoices/{target.id}/similar", headers=auth_headers)

    assert response.status_code == 200
    assert [item["invoice_no"] for item in response.json()] == ["INV-SIM-1", "INV-SIM-2"]


async def test_get_similar_invoices_fts_fallback(client, auth_headers, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    target = await create_invoice(invoice_no="INV-FTS-0", seller="Fallback Seller", item_summary="printer toner")
    match = await create_invoice(invoice_no="INV-FTS-1", seller="Fallback Seller", item_summary="printer toner cyan")

    async def fake_similar_invoice_ids(self, db, invoice, limit=5):
        del self, db, limit
        assert invoice.id == target.id
        return [match.id]

    monkeypatch.setattr(invoices_api.SearchService, "similar_invoice_ids", fake_similar_invoice_ids)

    response = await client.get(f"/api/v1/invoices/{target.id}/similar", headers=auth_headers)

    assert response.status_code == 200
    assert [item["invoice_no"] for item in response.json()] == ["INV-FTS-1"]


async def test_get_similar_invoices_missing_invoice_returns_404(client, auth_headers) -> None:
    response = await client.get("/api/v1/invoices/999/similar", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Invoice not found"}


async def test_export_invoices_csv(client, auth_headers, create_invoice) -> None:
    await create_invoice(invoice_no="INV-CSV-1", buyer="Buyer A")
    await create_invoice(invoice_no="INV-CSV-2", buyer="Buyer B")
    await create_invoice(invoice_no="INV-CSV-3", buyer="Buyer C")

    response = await client.get("/api/v1/invoices/export?format=csv", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(b"\xef\xbb\xbf")

    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 3
    assert rows[0].keys() == {
        "invoice_no",
        "buyer",
        "seller",
        "amount",
        "invoice_date",
        "invoice_type",
        "item_summary",
        "extraction_method",
        "confidence",
        "created_at",
    }


async def test_export_invoices_csv_applies_date_filter(client, auth_headers, create_invoice) -> None:
    await create_invoice(invoice_no="INV-DATE-1", invoice_date=__import__("datetime").date(2024, 1, 1))
    await create_invoice(invoice_no="INV-DATE-2", invoice_date=__import__("datetime").date(2024, 2, 1))
    await create_invoice(invoice_no="INV-DATE-3", invoice_date=__import__("datetime").date(2024, 3, 1))

    response = await client.get(
        "/api/v1/invoices/export?format=csv&date_from=2024-02-01&date_to=2024-02-29",
        headers=auth_headers,
    )

    assert response.status_code == 200
    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert [row["invoice_no"] for row in rows] == ["INV-DATE-2"]


async def test_export_invoices_requires_auth(client) -> None:
    response = await client.get("/api/v1/invoices/export?format=csv")

    assert response.status_code == 401
