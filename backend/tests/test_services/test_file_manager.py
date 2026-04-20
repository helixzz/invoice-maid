from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from app.services.file_manager import (
    FileManager,
    _format_amount,
    _normalize_extension,
    canonical_filename,
    sanitize_filename_component,
)


def test_sanitize_filename_component_handles_empty_unsafe_and_long_values() -> None:
    assert sanitize_filename_component("") == "unknown"
    assert sanitize_filename_component('A/B:C*D?E"F<G>H|') == "A_B_C_D_E_F_G_H"
    assert sanitize_filename_component("x" * 60, max_length=10) == "xxxxxxxxxx"


def test_format_amount_and_extension_normalization() -> None:
    assert _format_amount(None) == "0.00"
    assert _format_amount(Decimal("1.235")) == "1.24"
    assert _normalize_extension("") == ".pdf"
    assert _normalize_extension("xml") == ".xml"
    assert _normalize_extension(".$%^pdf") == ".pdf"


def test_canonical_filename_builds_expected_name() -> None:
    filename = canonical_filename(
        buyer="购买方/甲",
        seller="销售方*乙",
        invoice_no="NO:001",
        invoice_date=date(2024, 1, 2),
        amount=Decimal("20.50"),
        extension="pdf",
    )

    assert filename == "购买方_甲_销售方_乙_NO_001_20240102_20.50.pdf"


@pytest.mark.asyncio
async def test_save_invoice_handles_duplicates_and_delete(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)

    first = await manager.save_invoice(
        b"one", "A", "B", "001", date(2024, 1, 1), Decimal("10.00"), user_id=1
    )
    second = await manager.save_invoice(
        b"two", "A", "B", "001", date(2024, 1, 1), Decimal("10.00"), user_id=1
    )

    assert first == "users/1/invoices/A_B_001_20240101_10.00.pdf"
    assert second == "users/1/invoices/A_B_001_20240101_10.00_1.pdf"
    assert manager.get_full_path(first).read_bytes() == b"one"
    assert await manager.delete_invoice_file(first) is True
    assert await manager.delete_invoice_file(first) is False


@pytest.mark.asyncio
async def test_save_invoice_isolates_identical_canonical_names_across_users(
    settings,
) -> None:
    """Phase 4b tenant-isolation contract. Two users uploading invoices
    with identical canonical metadata (same buyer, seller, invoice_no,
    date, amount) must land in different subdirectories — never
    overwrite each other's files."""
    manager = FileManager(settings.STORAGE_PATH)

    user_a_path = await manager.save_invoice(
        b"user-a-content", "AcmeBuyer", "AcmeSeller", "SHARED-001",
        date(2024, 6, 15), Decimal("99.99"), user_id=1,
    )
    user_b_path = await manager.save_invoice(
        b"user-b-content", "AcmeBuyer", "AcmeSeller", "SHARED-001",
        date(2024, 6, 15), Decimal("99.99"), user_id=2,
    )

    assert user_a_path == "users/1/invoices/AcmeBuyer_AcmeSeller_SHARED-001_20240615_99.99.pdf"
    assert user_b_path == "users/2/invoices/AcmeBuyer_AcmeSeller_SHARED-001_20240615_99.99.pdf"
    assert user_a_path != user_b_path
    assert manager.get_full_path(user_a_path).read_bytes() == b"user-a-content"
    assert manager.get_full_path(user_b_path).read_bytes() == b"user-b-content"


@pytest.mark.asyncio
async def test_delete_user_files_removes_subdirectory(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)
    _ = await manager.save_invoice(
        b"one", "A", "B", "X", date(2024, 1, 1), Decimal("1.00"), user_id=7
    )
    _ = await manager.save_invoice(
        b"two", "A", "B", "Y", date(2024, 1, 2), Decimal("2.00"), user_id=7
    )

    deleted = await manager.delete_user_files(7)

    assert deleted == 2
    assert not (manager.storage_path / "users" / "7").exists()


@pytest.mark.asyncio
async def test_delete_user_files_is_noop_for_unknown_user(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)
    assert await manager.delete_user_files(999) == 0


@pytest.mark.asyncio
async def test_delete_user_files_handles_rmtree_failure(settings, monkeypatch) -> None:
    manager = FileManager(settings.STORAGE_PATH)
    _ = await manager.save_invoice(
        b"one", "A", "B", "X", date(2024, 1, 1), Decimal("1.00"), user_id=8
    )

    import shutil as _shutil

    def fail_rmtree(path):
        raise OSError("simulated failure")

    monkeypatch.setattr(_shutil, "rmtree", fail_rmtree)

    assert await manager.delete_user_files(8) == 0
    assert (manager.storage_path / "users" / "8").exists()


def test_get_full_path_rejects_path_traversal(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)

    with pytest.raises(ValueError, match="Path traversal"):
        manager.get_full_path("../escape.pdf")


def test_stream_zip_includes_existing_files_and_skips_missing(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)
    target = manager.storage_path / "invoice.pdf"
    target.write_bytes(b"payload")

    archive = manager.stream_zip(["invoice.pdf", "missing.pdf"])

    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        assert zf.namelist() == ["invoice.pdf"]
        assert zf.read("invoice.pdf") == b"payload"


def test_stream_zip_embeds_extra_in_memory_members(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)
    (manager.storage_path / "invoice.pdf").write_bytes(b"pdf-bytes")

    archive = manager.stream_zip(
        ["invoice.pdf"],
        extra_members=[
            ("invoices_summary.csv", b"\xef\xbb\xbfinvoice_no,buyer\n123,Acme"),
            ("notes.txt", b"hello"),
        ],
    )

    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        assert set(zf.namelist()) == {"invoice.pdf", "invoices_summary.csv", "notes.txt"}
        assert zf.read("invoice.pdf") == b"pdf-bytes"
        assert zf.read("invoices_summary.csv").startswith(b"\xef\xbb\xbf")
        assert zf.read("notes.txt") == b"hello"


def test_stream_zip_without_extra_members_is_backward_compatible(settings) -> None:
    manager = FileManager(settings.STORAGE_PATH)
    (manager.storage_path / "invoice.pdf").write_bytes(b"pdf")

    archive_no_kwarg = manager.stream_zip(["invoice.pdf"])
    archive_none = manager.stream_zip(["invoice.pdf"], extra_members=None)
    archive_empty = manager.stream_zip(["invoice.pdf"], extra_members=[])

    for archive in (archive_no_kwarg, archive_none, archive_empty):
        with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
            assert zf.namelist() == ["invoice.pdf"]
