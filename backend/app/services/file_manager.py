from __future__ import annotations

import io
from importlib import import_module
import logging
import re
import zipfile
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:  # pragma: no cover
    class _AsyncFile(Protocol):
        async def __aenter__(self) -> "_AsyncFile": ...

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

        async def write(self, data: bytes) -> int: ...


    class _AiofilesModule(Protocol):
        def open(self, file: str | Path, mode: str = "r") -> _AsyncFile: ...


    class _AiofilesOSModule(Protocol):
        async def remove(self, path: str | Path) -> None: ...


else:
    _AiofilesModule = object
    _AiofilesOSModule = object


aiofiles = cast(_AiofilesModule, cast(object, import_module("aiofiles")))
aiofiles_os = cast(_AiofilesOSModule, cast(object, import_module("aiofiles.os")))

logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_MULTIPLE_UNDERSCORES = re.compile(r'_+')
_VALID_EXTENSION_CHARS = re.compile(r'[^A-Za-z0-9._-]')


def sanitize_filename_component(s: str, max_length: int = 40) -> str:
    """Remove characters unsafe for filenames and limit length."""
    if not s:
        return "unknown"

    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", s)
    cleaned = _MULTIPLE_UNDERSCORES.sub("_", cleaned).strip("_")

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("_")

    return cleaned or "unknown"


def _format_amount(amount: Decimal | float | None) -> str:
    if amount is None:
        return "0.00"

    normalized = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{normalized:.2f}"


def _normalize_extension(extension: str) -> str:
    cleaned = _VALID_EXTENSION_CHARS.sub("", extension.strip())
    if not cleaned:
        return ".pdf"
    if not cleaned.startswith("."):
        cleaned = f".{cleaned}"
    return cleaned


def canonical_filename(
    buyer: str | None,
    seller: str | None,
    invoice_no: str | None,
    invoice_date: date | None,
    amount: Decimal | float | None,
    extension: str = ".pdf",
) -> str:
    """Generate canonical filename: {buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf."""
    parts = [
        sanitize_filename_component(buyer or "unknown"),
        sanitize_filename_component(seller or "unknown"),
        sanitize_filename_component(invoice_no or "unknown"),
        invoice_date.strftime("%Y%m%d") if invoice_date else "unknown",
        _format_amount(amount),
    ]
    return f"{'_'.join(parts)}{_normalize_extension(extension)}"


class FileManager:
    def __init__(self, storage_path: str):
        self._storage_path: Path
        self._storage_path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)

    @property
    def storage_path(self) -> Path:
        return self._storage_path

    def _user_invoices_dir(self, user_id: int) -> Path:
        """Resolve the per-user invoice directory, creating it if needed.

        Layout: ``STORAGE_PATH/users/{user_id}/invoices/``. Two users
        generating identical canonical filenames land in different
        subdirectories, so there is no cross-tenant file collision."""
        user_dir = self._storage_path / "users" / str(user_id) / "invoices"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    async def save_invoice(
        self,
        content: bytes,
        buyer: str | None,
        seller: str | None,
        invoice_no: str | None,
        invoice_date: date | None,
        amount: Decimal | float | None,
        extension: str = ".pdf",
        *,
        user_id: int,
    ) -> str:
        """Save invoice file under the per-user subdirectory and return
        its relative storage path.

        Returns a relative path of the form
        ``users/{user_id}/invoices/{canonical_filename}`` suitable for
        round-tripping through ``get_full_path``, ``stream_zip``, and
        ``delete_invoice_file``."""
        filename = canonical_filename(
            buyer=buyer,
            seller=seller,
            invoice_no=invoice_no,
            invoice_date=invoice_date,
            amount=amount,
            extension=extension,
        )

        user_dir = self._user_invoices_dir(user_id)
        file_path = user_dir / filename
        if file_path.exists():
            base = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while file_path.exists():
                file_path = user_dir / f"{base}_{counter}{suffix}"
                counter += 1

        async with aiofiles.open(file_path, "wb") as file_obj:
            _ = await file_obj.write(content)

        return str(file_path.relative_to(self._storage_path))

    def get_full_path(self, relative_path: str) -> Path:
        """Resolve a relative path to an absolute path within storage."""
        storage_root = self._storage_path.resolve()
        full_path = (storage_root / relative_path).resolve()

        try:
            _ = full_path.relative_to(storage_root)
        except ValueError as exc:
            raise ValueError("Path traversal detected") from exc

        return full_path

    def stream_zip(
        self,
        file_paths: list[str],
        extra_members: list[tuple[str, bytes]] | None = None,
    ) -> bytes:
        """Create a ZIP archive from relative file paths and return its bytes.

        ``extra_members`` is an optional list of ``(arcname, content_bytes)``
        pairs that get added to the archive as in-memory files — used to
        embed `invoices_summary.csv` alongside the downloaded PDFs without
        needing a temporary file on disk."""
        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for relative_path in file_paths:
                full_path = self.get_full_path(relative_path)
                if full_path.exists():
                    zip_file.write(full_path, arcname=full_path.name)
                else:
                    logger.warning("File not found for ZIP: %s", relative_path)
            for arcname, content in extra_members or []:
                zip_file.writestr(arcname, content)

        _ = buffer.seek(0)
        return buffer.getvalue()

    async def delete_invoice_file(self, relative_path: str) -> bool:
        """Delete an invoice file if it exists."""
        try:
            full_path = self.get_full_path(relative_path)
            if not full_path.exists():
                logger.warning("File not found for deletion: %s", relative_path)
                return False

            await aiofiles_os.remove(full_path)
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to delete file %s: %s", relative_path, exc)
            return False

    async def delete_user_files(self, user_id: int) -> int:
        """Recursively delete the per-user invoice directory.

        Returns the number of files removed. Used by the admin
        ``DELETE /admin/users/{id}`` endpoint after the cascade DB
        delete succeeds, and by the startup orphan-directory scan to
        clean up directories whose owning ``users`` row is gone. Silent
        no-op if the directory does not exist."""
        user_root = self._storage_path / "users" / str(user_id)
        if not user_root.exists():
            return 0

        import shutil

        file_count = sum(1 for _ in user_root.rglob("*") if _.is_file())
        try:
            shutil.rmtree(user_root)
        except OSError as exc:
            logger.error("Failed to delete user directory %s: %s", user_root, exc)
            return 0
        return file_count
