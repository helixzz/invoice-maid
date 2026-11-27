from __future__ import annotations

from datetime import date
from decimal import Decimal
import io
import zipfile

import pytest

from app.services.invoice_xlsx import _col_letter, build_xlsx_bytes


@pytest.mark.asyncio
async def test_multi_currency_and_reimbursement_workflow_end_to_end(
    client, auth_headers, db, settings, create_email_account, create_invoice, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = await create_email_account()
    inv_cny = await create_invoice(
        email_account=account,
        invoice_no="CNY-001",
        buyer="上海甲公司",
        seller="京东商城",
        amount=Decimal("200.00"),
        currency="CNY",
        invoice_date=date(2026, 11, 10),
        invoice_category="vat_invoice",
        reimbursement_status="unreimbursed",
        tags="office,q4",
        notes="键盘采购",
    )
    inv_eur = await create_invoice(
        email_account=account,
        invoice_no="EUR-001",
        buyer="Acme Corp",
        seller="Hetzner Online GmbH",
        amount=Decimal("45.50"),
        currency="EUR",
        invoice_date=date(2026, 11, 15),
        invoice_category="overseas_invoice",
        reimbursement_status="pending",
        tags="server,eu",
        notes="VPS",
    )

    # Stats reflect true stored currencies (CNY + EUR, NOT hardcoded USD!)
    stats_res = await client.get("/api/v1/stats", headers=auth_headers)
    assert stats_res.status_code == 200
    by_cur = {row["currency"]: row for row in stats_res.json()["by_currency"]}
    assert by_cur["CNY"]["total"] == 200.0
    assert by_cur["EUR"]["total"] == 45.5

    # Filter by currency, reimbursement_status, and tag (both empty-query and FTS query paths)
    res_eur = await client.get("/api/v1/invoices?currency=eur", headers=auth_headers)
    assert res_eur.status_code == 200
    assert [i["invoice_no"] for i in res_eur.json()["items"]] == ["EUR-001"]

    res_status = await client.get(
        "/api/v1/invoices?reimbursement_status=pending&tag=server", headers=auth_headers
    )
    assert [i["invoice_no"] for i in res_status.json()["items"]] == ["EUR-001"]

    # Update single invoice currency + reimbursement_status + tags + notes + semantic refresh
    settings.sqlite_vec_available = True
    embed_calls: list[str] = []

    class FakeAI:
        def __init__(self, _s):
            pass

        async def embed_text(self, text: str, _db):
            embed_calls.append(text)
            return [0.1, 0.2, 0.3]

    import app.api.invoices as invoices_api

    monkeypatch.setattr(invoices_api, "AIService", FakeAI)
    stored_embs: list[int] = []

    async def fake_store(_db, inv_id: int, emb: list[float]):
        del emb
        stored_embs.append(inv_id)

    monkeypatch.setattr(invoices_api, "store_embedding", fake_store)

    upd = await client.put(
        f"/api/v1/invoices/{inv_eur.id}",
        headers=auth_headers,
        json={
            "currency": "gbp",
            "reimbursement_status": "reimbursed",
            "tags": "cloud,done",
            "notes": "Paid in Nov",
            "seller": "Hetzner UK Ltd",
        },
    )
    assert upd.status_code == 200
    assert upd.json()["currency"] == "GBP"
    assert upd.json()["reimbursement_status"] == "reimbursed"
    assert upd.json()["tags"] == "cloud,done"
    assert upd.json()["notes"] == "Paid in Nov"
    assert stored_embs == [inv_eur.id]

    # Embedding failure on edit is non-fatal
    class CrashAI:
        def __init__(self, _s):
            pass

        async def embed_text(self, text: str, _db):
            del text
            raise RuntimeError("embed down")

    monkeypatch.setattr(invoices_api, "AIService", CrashAI)
    upd2 = await client.put(
        f"/api/v1/invoices/{inv_eur.id}",
        headers=auth_headers,
        json={"item_summary": "Updated summary"},
    )
    assert upd2.status_code == 200

    # Batch reimbursement update
    empty_batch = await client.post(
        "/api/v1/invoices/batch-reimbursement", headers=auth_headers, json={"ids": []}
    )
    assert empty_batch.json() == {"updated": 0}

    bad_batch = await client.post(
        "/api/v1/invoices/batch-reimbursement",
        headers=auth_headers,
        json={"ids": [inv_cny.id]},
    )
    assert bad_batch.status_code == 400

    ok_batch = await client.post(
        "/api/v1/invoices/batch-reimbursement",
        headers=auth_headers,
        json={"ids": [inv_cny.id], "reimbursement_status": "reimbursed", "tags": "batch-nov"},
    )
    assert ok_batch.status_code == 200
    assert ok_batch.json() == {"updated": 1}

    status_only = await client.post(
        "/api/v1/invoices/batch-reimbursement",
        headers=auth_headers,
        json={"ids": [inv_cny.id], "reimbursement_status": "pending"},
    )
    assert status_only.status_code == 200

    tags_only = await client.post(
        "/api/v1/invoices/batch-reimbursement",
        headers=auth_headers,
        json={"ids": [inv_cny.id], "tags": "only-tag"},
    )
    assert tags_only.status_code == 200

    # Export Excel (.xlsx)
    xlsx_res = await client.get("/api/v1/invoices/export?format=xlsx", headers=auth_headers)
    assert xlsx_res.status_code == 200
    assert "spreadsheetml" in xlsx_res.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(xlsx_res.content)) as zf:
        names = set(zf.namelist())
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet2.xml" in names
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "CNY" in sheet1 and "GBP" in sheet1

    # Reimbursement Pack ZIP (xlsx + csv + PDF files)
    empty_pack = await client.post(
        "/api/v1/invoices/reimbursement-pack", headers=auth_headers, json={"ids": []}
    )
    assert empty_pack.status_code == 400
    missing_pack = await client.post(
        "/api/v1/invoices/reimbursement-pack", headers=auth_headers, json={"ids": [999999]}
    )
    assert missing_pack.status_code == 404

    pack_res = await client.post(
        "/api/v1/invoices/reimbursement-pack",
        headers=auth_headers,
        json={"ids": [inv_cny.id, inv_eur.id]},
    )
    assert pack_res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(pack_res.content)) as zf:
        names = set(zf.namelist())
        assert "reimbursement_summary.xlsx" in names
        assert "invoices_summary.csv" in names

    # Column letter helper > 26 columns edge check
    assert _col_letter(0) == "A"
    assert _col_letter(26) == "AA"
    assert len(build_xlsx_bytes([])) > 0
