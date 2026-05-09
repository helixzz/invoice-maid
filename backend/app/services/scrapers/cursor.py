from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import get_settings
from app.models import EmailAccount
from app.services.email_scanner import (
    RawAttachment,
    RawEmail,
    ScanOptions,
    decrypt_password,
)
from app.services.scrapers.base import BaseScraper, ScraperAuthRequiredError
from app.services.scrapers.playwright_session import (
    PLAYWRIGHT_AVAILABLE,
    PlaywrightSession,
    PlaywrightUnavailableError,
)

logger = logging.getLogger(__name__)


CURSOR_SELECTOR_VERSION = "2026-05-09"

CURSOR_DASHBOARD_URL = "https://cursor.com/dashboard"
CURSOR_LOGIN_URL_FRAGMENT = "/login"

SELECTOR_LOGIN_EMAIL_INPUT = 'input[type="email"]'
SELECTOR_LOGIN_PASSWORD_INPUT = 'input[type="password"]'
SELECTOR_LOGIN_SUBMIT = 'button[type="submit"]'
SELECTOR_TOTP_INPUT = 'input[name="code"]'
SELECTOR_INVOICE_ROW = 'a[href*="/billing/"][href*="/pdf"]'

SEEN_INVOICE_IDS_CAP = 1000

SYNTHETIC_FROM_ADDR = "billing@cursor.com"

STATE_FORMAT = "cursor_scraper_v1"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_seen_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    data = json.loads(raw)
    seen = data.get("seen_invoice_ids") or []
    return {str(x) for x in seen}


def _cap_seen(seen: set[str], ordered_new: list[str]) -> list[str]:
    merged = list(dict.fromkeys([*sorted(seen), *ordered_new]))
    if len(merged) > SEEN_INVOICE_IDS_CAP:
        merged = merged[-SEEN_INVOICE_IDS_CAP:]
    return merged


class CursorScraper(BaseScraper):
    def __init__(self, *, session_cls: type[PlaywrightSession] | Any | None = None) -> None:
        self._session_cls = session_cls or PlaywrightSession
        self._scan_events: list[dict[str, Any]] = []
        self._last_scan_state: str | None = None
        self._updated_storage_state: str | None = None

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
        self._updated_storage_state = None

        if not PLAYWRIGHT_AVAILABLE:
            logger.warning(
                "playwright is not installed; CursorScraper returning empty result for account %s",
                account.id,
            )
            self._emit_auth_required("[CURSOR_DEPENDENCY] playwright package not installed")
            return []

        seen = _parse_seen_ids(last_uid)
        new_invoices: list[RawEmail] = []
        new_ids_ordered: list[str] = []

        try:
            async with self._session_cls(account) as page:
                await self._run_scrape(
                    page=page,
                    account=account,
                    seen=seen,
                    new_invoices=new_invoices,
                    new_ids_ordered=new_ids_ordered,
                    progress_callback=progress_callback,
                )
                await self._capture_storage_state(page)
        except PlaywrightUnavailableError:
            self._emit_auth_required("[CURSOR_DEPENDENCY] playwright package not installed")
            return []
        except ScraperAuthRequiredError:
            return []

        self._last_scan_state = json.dumps({
            "_format": STATE_FORMAT,
            "seen_invoice_ids": _cap_seen(seen, new_ids_ordered),
            "last_scan_at": _utcnow_iso(),
        })
        return new_invoices

    async def _run_scrape(
        self,
        *,
        page: Any,
        account: EmailAccount,
        seen: set[str],
        new_invoices: list[RawEmail],
        new_ids_ordered: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        await page.goto(CURSOR_DASHBOARD_URL)

        if self._needs_login(page):
            await self._login(page, account)

        rows = await self._enumerate_invoices(page)
        for row in rows:
            invoice_id = str(row["invoice_id"])
            if invoice_id in seen:
                continue
            pdf_bytes = await page.download_bytes(str(row["pdf_url"]))
            new_invoices.append(self._build_raw_email(account, row, pdf_bytes))
            new_ids_ordered.append(invoice_id)
            if progress_callback is not None:
                progress_callback({
                    "folder_fetch_msg": f"Cursor: {len(new_invoices)} invoice(s) fetched",
                })

    async def _capture_storage_state(self, page: Any) -> None:
        state = await page.context.storage_state()
        self._updated_storage_state = json.dumps(state)

    def _needs_login(self, page: Any) -> bool:
        return CURSOR_LOGIN_URL_FRAGMENT in str(page.url)

    async def _login(self, page: Any, account: EmailAccount) -> None:
        email_enc = account.secondary_credential_encrypted
        pw_enc = account.secondary_password_encrypted
        if not email_enc or not pw_enc:
            self._emit_auth_required(
                "[CURSOR_AUTH] login credentials missing; configure via Settings"
            )
            raise ScraperAuthRequiredError("credentials missing")

        settings = get_settings()
        email = decrypt_password(email_enc, settings.JWT_SECRET)
        password = decrypt_password(pw_enc, settings.JWT_SECRET)

        await page.fill(SELECTOR_LOGIN_EMAIL_INPUT, email)
        await page.click(SELECTOR_LOGIN_SUBMIT)
        await page.fill(SELECTOR_LOGIN_PASSWORD_INPUT, password)
        await page.click(SELECTOR_LOGIN_SUBMIT)

        if await page.has_totp_challenge():
            await self._handle_2fa(page, account)

    async def _handle_2fa(self, page: Any, account: EmailAccount) -> None:
        totp_enc = account.totp_secret_encrypted
        if not totp_enc:
            self._emit_auth_required(
                "[CURSOR_2FA] 2FA challenge encountered and no TOTP secret stored; "
                "use scripts/cursor_login_local.py to generate a storage_state (Mode B)"
            )
            raise ScraperAuthRequiredError("2fa required, no TOTP")

        totp_secret = decrypt_password(totp_enc, get_settings().JWT_SECRET)
        code = self._generate_totp_code(totp_secret)
        await page.fill(SELECTOR_TOTP_INPUT, code)
        await page.click(SELECTOR_LOGIN_SUBMIT)

    def _generate_totp_code(self, secret: str) -> str:
        return secret[:6].ljust(6, "0")

    async def _enumerate_invoices(self, page: Any) -> list[dict[str, Any]]:
        try:
            return await page.list_invoices(SELECTOR_INVOICE_ROW)
        except TimeoutError as exc:
            self._emit_auth_required(
                f"[CURSOR_NAV] network timeout while listing invoices: {exc}"
            )
            raise
        except LookupError:
            self._emit_auth_required(
                "[CURSOR_SELECTOR] invoice list selector returned no rows — "
                "Cursor UI may have changed; update CURSOR_SELECTOR_VERSION"
            )
            raise ScraperAuthRequiredError("selector mismatch")

    def _build_raw_email(
        self, account: EmailAccount, row: dict[str, Any], pdf_bytes: bytes
    ) -> RawEmail:
        invoice_id = str(row["invoice_id"])
        amount = row.get("amount", "")
        currency = row.get("currency", "USD")
        period = row.get("period", "")
        received_at = row["invoice_date"]
        filename = f"cursor-invoice-{invoice_id}.pdf"
        return RawEmail(
            uid=f"cursor:{invoice_id}",
            subject=f"Cursor Invoice {invoice_id} ({amount} {currency})",
            body_text=f"Cursor invoice {invoice_id} {period}",
            body_html="",
            from_addr=SYNTHETIC_FROM_ADDR,
            received_at=received_at,
            attachments=[
                RawAttachment(
                    filename=filename,
                    content_type="application/pdf",
                    size=len(pdf_bytes),
                    payload=pdf_bytes,
                ),
            ],
            body_links=[],
            headers={
                "_scraper": "cursor",
                "_invoice_id": invoice_id,
                "_currency": str(currency),
                "_amount_pretty": f"{amount} {currency}",
                "_account_id": str(account.id or ""),
            },
            is_hydrated=True,
            folder="Cursor Invoices",
            message_id=f"cursor-{invoice_id}",
        )

    def _emit_auth_required(self, detail: str) -> None:
        self._scan_events.append({
            "kind": "auth_required",
            "error_detail": detail,
        })
