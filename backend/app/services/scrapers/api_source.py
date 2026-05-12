from __future__ import annotations

from abc import abstractmethod
from typing import Any, Callable

from app.models import EmailAccount
from app.services.email_scanner import RawEmail, ScanOptions
from app.services.scrapers.base import BaseScraper


class BaseAPIScraper(BaseScraper):
    """Base class for API-key-based invoice sources (AWS Billing, OpenAI, etc.).

    Subclasses implement ``fetch_invoices()`` — the HTTP logic specific to
    each vendor's invoicing API.  The base class handles decryption of
    credentials stored in the ``EmailAccount`` row:

    * ``secondary_credential_encrypted`` → API key          (decrypted on demand)
    * ``secondary_password_encrypted``    → Org / Account ID (decrypted on demand)
    * ``totp_secret_encrypted``           → Optional extra secret

    Adding a new source:

    1. Subclass ``BaseAPIScraper``.
    2. Implement ``fetch_invoices(account, progress_callback)``.
    3. Register in ``app.services.scrapers.factory._REGISTRY``.
    4. Add the source type to the frontend SettingsView type selector.
    5. Add a per-type config form component if needed.

    Example registration::

        # factory.py
        from app.services.scrapers.aws import AWSBillingScraper
        ScraperFactory.register("aws_billing", AWSBillingScraper)
    """

    async def scan(
        self,
        account: EmailAccount,
        last_uid: str | None = None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        del options
        self._scan_events = []
        self._last_scan_state = None

        api_key = self._decrypt_credential(account, "secondary_credential_encrypted")
        if not api_key:
            self._emit_auth_required("[API_KEY_MISSING] No API key configured")
            return []

        invoices = await self.fetch_invoices(
            account=account,
            api_key=api_key,
            progress_callback=progress_callback,
        )

        self._last_scan_state = self._build_scan_state(last_uid, invoices)
        return invoices

    @abstractmethod
    async def fetch_invoices(
        self,
        *,
        account: EmailAccount,
        api_key: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        """Call the vendor's API and return synthetic RawEmail rows.

        Subclasses MUST override this.  ``api_key`` is already decrypted.
        Additional fields (org ID, account number) can be read from
        ``account.secondary_password_encrypted`` via ``_decrypt_credential``."""

    def _decrypt_credential(self, account: EmailAccount, field: str) -> str:
        """Decrypt an encrypted account field.  Returns empty string on failure."""
        from app.services.email_scanner import decrypt_password

        raw = getattr(account, field, None)
        if not raw:
            return ""
        try:
            return decrypt_password(account, raw)
        except Exception:
            return ""

    def _emit_auth_required(self, detail: str) -> None:
        self._scan_events.append({
            "kind": "auth_required",
            "error_detail": detail,
        })

    def _build_scan_state(
        self, last_uid: str | None, invoices: list[RawEmail]
    ) -> str | None:
        import json
        from datetime import datetime, timezone

        existing = set()
        if last_uid:
            try:
                data = json.loads(last_uid)
                existing = set(data.get("seen_uids", []))
            except (TypeError, ValueError):
                pass
        new_uids = [e.uid for e in invoices if e.uid not in existing]
        seen = list(existing) + new_uids
        return json.dumps({
            "_format": "api_source_v1",
            "seen_uids": seen[-1000:],
            "last_scan_at": datetime.now(timezone.utc).isoformat(),
        })

    @property
    def _scan_events(self) -> list[dict[str, Any]]:
        if not hasattr(self, "__scan_events"):
            self.__scan_events: list[dict[str, Any]] = []
        return self.__scan_events

    @_scan_events.setter
    def _scan_events(self, val: list[dict[str, Any]]) -> None:
        self.__scan_events = val

    @property
    def _last_scan_state(self) -> str | None:
        return getattr(self, "__last_scan_state", None)

    @_last_scan_state.setter
    def _last_scan_state(self, val: str | None) -> None:
        self.__last_scan_state = val