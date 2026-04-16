from __future__ import annotations


async def test_download_invoice_and_batch_download(client, auth_headers, create_invoice, settings) -> None:
    invoice = await create_invoice(file_path="invoice.pdf")
    storage_file = __import__("pathlib").Path(settings.STORAGE_PATH) / "invoice.pdf"
    storage_file.parent.mkdir(parents=True, exist_ok=True)
    storage_file.write_bytes(b"pdf-data")

    single = await client.get(f"/api/v1/invoices/{invoice.id}/download", headers=auth_headers)
    assert single.status_code == 200
    assert single.content == b"pdf-data"

    batch = await client.post(
        "/api/v1/invoices/batch-download",
        headers=auth_headers,
        json={"ids": [invoice.id]},
    )
    assert batch.status_code == 200
    assert batch.headers["content-type"].startswith("application/zip")


async def test_download_invoice_not_found_paths(client, auth_headers, create_invoice) -> None:
    missing_invoice = await client.get("/api/v1/invoices/999/download", headers=auth_headers)
    assert missing_invoice.status_code == 404

    invoice = await create_invoice(file_path="missing.pdf")
    missing_file = await client.get(f"/api/v1/invoices/{invoice.id}/download", headers=auth_headers)
    assert missing_file.status_code == 404

    no_ids = await client.post("/api/v1/invoices/batch-download", headers=auth_headers, json={"ids": []})
    assert no_ids.status_code == 400

    no_invoices = await client.post("/api/v1/invoices/batch-download", headers=auth_headers, json={"ids": [999]})
    assert no_invoices.status_code == 404


async def test_download_invoice_rejects_path_traversal(client, auth_headers, create_invoice) -> None:
    invoice = await create_invoice(file_path="../escape.pdf")

    response = await client.get(f"/api/v1/invoices/{invoice.id}/download", headers=auth_headers)

    assert response.status_code == 404
