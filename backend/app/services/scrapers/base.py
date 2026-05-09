from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from app.models import EmailAccount
from app.services.email_scanner import RawEmail, ScanOptions


class BaseScraper(ABC):
    @abstractmethod
    async def scan(
        self,
        account: EmailAccount,
        last_uid: str | None = None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        """Scrape a SaaS portal and return synthetic RawEmail rows that the
        existing scheduler pipeline can consume unchanged. Subclasses may
        set ``_last_scan_state`` (polymorphic JSON appended to
        ``EmailAccount.last_scan_uid``), ``_scan_events`` (diagnostic
        events drained into ExtractionLog rows), and
        ``_updated_storage_state`` (JSON to persist back to
        ``EmailAccount.playwright_storage_state``)."""

    async def test_connection(self, account: EmailAccount) -> bool:
        del account
        return False


class ScraperAuthRequiredError(RuntimeError):
    """Raised when a scrape cannot proceed without operator interaction
    (missing credentials, 2FA with no stored TOTP, expired
    storage_state). Callers log ``_scan_events`` rather than letting
    this escape; the exception exists so the scraper can unwind the
    in-progress scan cleanly."""
