from __future__ import annotations

import io
import zipfile


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

    with zipfile.ZipFile(io.BytesIO(batch.content), "r") as zf:
        names = zf.namelist()
        assert "invoice.pdf" in names
        assert "invoices_summary.csv" in names, (
            "batch-download ZIP must include an invoices_summary.csv with the "
            "structured metadata for every included invoice"
        )
        summary_bytes = zf.read("invoices_summary.csv")
        assert summary_bytes.startswith(b"\xef\xbb\xbf"), "summary CSV must begin with UTF-8 BOM"
        decoded = summary_bytes[3:].decode("utf-8")
        header_line = decoded.splitlines()[0]
        assert "invoice_no" in header_line and "buyer" in header_line and "amount" in header_line


async def test_batch_download_summary_includes_all_selected_invoices(
    client, auth_headers, create_invoice, settings
) -> None:
    """The summary CSV rows must correspond 1:1 to the invoices that got
    bundled into the ZIP — this is the contract the user relies on when
    reconciling a batch download against their records."""
    import pathlib

    ids = []
    for idx, invoice_no in enumerate(["INV-A", "INV-B", "INV-C"], start=1):
        inv = await create_invoice(
            file_path=f"inv_{idx}.pdf",
            invoice_no=invoice_no,
        )
        pathlib.Path(settings.STORAGE_PATH).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(settings.STORAGE_PATH) / f"inv_{idx}.pdf").write_bytes(b"pdf")
        ids.append(inv.id)

    response = await client.post(
        "/api/v1/invoices/batch-download", headers=auth_headers, json={"ids": ids}
    )
    assert response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(response.content), "r") as zf:
        summary = zf.read("invoices_summary.csv").decode("utf-8-sig")

    data_lines = [line for line in summary.splitlines() if line and not line.startswith("invoice_no")]
    assert len(data_lines) == 3
    assert any("INV-A" in line for line in data_lines)
    assert any("INV-B" in line for line in data_lines)
    assert any("INV-C" in line for line in data_lines)


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
