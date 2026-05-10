from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.models import EmailAccount
from app.services.email_scanner import (
    RawAttachment,
    RawEmail,
    ScanOptions,
)
from app.services.scrapers.base import BaseScraper, ScraperAuthRequiredError
from app.services.scrapers.playwright_session import (
    PLAYWRIGHT_AVAILABLE,
    PlaywrightSession,
    PlaywrightUnavailableError,
)

try:  # pragma: no cover - only imported when playwright is installed on the host
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - falls back when playwright missing

    class PlaywrightTimeoutError(Exception):
        """Fallback used when playwright is not installed; real TimeoutError
        from playwright.async_api is a distinct exception we want to catch."""


logger = logging.getLogger(__name__)


CURSOR_SELECTOR_VERSION = "2026-05-10"

# Billing tab on the Cursor dashboard. Post-login this renders the
# Stripe-portal-style invoice list. If the session is not authenticated,
# Cursor issues a 302 to /login.
CURSOR_BILLING_URL = "https://cursor.com/dashboard?tab=billing"
CURSOR_LOGIN_URL_FRAGMENT = "/login"

# Defensive selectors with fallbacks. Primary matches Stripe-hosted PDF
# links (/pdf suffix) and Cursor's billing-proxy endpoint. Fallback walks
# table rows containing "Paid" status and picks any PDF link inside.
INVOICE_PDF_LINK_SELECTORS: tuple[str, ...] = (
    'a[href*="/pdf"]',
    'a[download][href*="invoice"]',
    'tr:has(td:has-text("Paid")) a[href*="pdf"]',
)

# Upper bound on wall-clock time inside one scrape. The scheduler holds
# the global scan loop, so a hung dashboard (e.g. cookieless session
# redirecting forever) cannot be allowed to block other accounts.
SCRAPE_TIMEOUT_SECONDS = 60.0

NAV_TIMEOUT_MS = 90_000
SELECTOR_WAIT_TIMEOUT_MS = 15_000

SEEN_INVOICE_IDS_CAP = 1000

SYNTHETIC_FROM_ADDR = "billing@cursor.com"

STATE_FORMAT = "cursor_scraper_v1"

# Match Stripe invoice IDs (in_XXXX) and Cursor proxy IDs (numeric / slug).
_INVOICE_ID_RE = re.compile(r"(?:in_[A-Za-z0-9]+|invoices/([A-Za-z0-9_-]+)/pdf)")


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


def _extract_invoice_id(pdf_url: str) -> str:
    """Pull a stable ID out of a PDF URL. Prefers the Stripe ``in_XXXX``
    form; falls back to the trailing segment before ``/pdf`` for Cursor's
    billing proxy. Returns the full URL hash-ish as last resort so two
    distinct invoices never collide."""
    match = _INVOICE_ID_RE.search(pdf_url)
    if match:
        raw_match = match.group(0)
        if raw_match.startswith("in_"):
            return raw_match
        return match.group(1)
    stripped = pdf_url.rstrip("/")
    if stripped.endswith("/pdf"):
        stripped = stripped[: -len("/pdf")]
    tail = stripped.rsplit("/", 1)[-1] or pdf_url
    return tail


class CursorScraper(BaseScraper):
    """Cursor billing scraper — Mode B only (storage_state auth).

    Mode A (password + TOTP) was removed in v1.2.1 because Cursor now
    fronts auth with WorkOS SSO + MFA, which cannot be scripted reliably.
    Operators must capture a real browser ``storage_state`` via
    ``scripts/cursor_login_local.py`` (which persists the httpOnly
    session cookies) and paste the resulting JSON into the account's
    ``playwright_storage_state``. A session captured from
    ``document.cookie`` in a Tampermonkey script will NOT include the
    httpOnly ``workos-session`` + ``__Secure-*`` cookies and will
    redirect to /login — the scraper detects that and emits
    ``auth_required`` immediately rather than hanging."""

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
            await asyncio.wait_for(
                self._scan_body(
                    account=account,
                    seen=seen,
                    new_invoices=new_invoices,
                    new_ids_ordered=new_ids_ordered,
                    progress_callback=progress_callback,
                ),
                timeout=SCRAPE_TIMEOUT_SECONDS,
            )
        except PlaywrightUnavailableError:
            self._emit_auth_required("[CURSOR_DEPENDENCY] playwright package not installed")
            return []
        except ScraperAuthRequiredError:
            return []
        except asyncio.TimeoutError:
            logger.warning(
                "CursorScraper exceeded %.0fs wall-clock for account %s; aborting",
                SCRAPE_TIMEOUT_SECONDS,
                account.id,
            )
            self._emit_auth_required(
                f"[CURSOR_TIMEOUT] scrape exceeded {SCRAPE_TIMEOUT_SECONDS:.0f}s; "
                "session may be invalid — recapture via scripts/cursor_login_local.py"
            )
            return []
        except Exception as exc:
            logger.exception("CursorScraper failed for account %s", account.id)
            self._emit_auth_required(
                f"[CURSOR_ERROR] {type(exc).__name__}: {exc}"
            )
            return []

        self._last_scan_state = json.dumps({
            "_format": STATE_FORMAT,
            "seen_invoice_ids": _cap_seen(seen, new_ids_ordered),
            "last_scan_at": _utcnow_iso(),
        })
        return new_invoices

    async def _scan_body(
        self,
        *,
        account: EmailAccount,
        seen: set[str],
        new_invoices: list[RawEmail],
        new_ids_ordered: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
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
        await page.goto(
            CURSOR_BILLING_URL,
            wait_until="load",
            timeout=NAV_TIMEOUT_MS,
        )

        if CURSOR_LOGIN_URL_FRAGMENT in str(page.url):
            self._emit_auth_required(
                "[CURSOR_AUTH] dashboard redirected to /login — session cookies missing or "
                "expired. Recapture storage_state via scripts/cursor_login_local.py."
            )
            raise ScraperAuthRequiredError("login redirect")

        links = await self._locate_invoice_links(page)
        if not links:
            # No links yet — either no invoices, or selectors drifted.
            # We don't raise here; the scheduler will re-run on the next
            # cadence and we avoid false auth_required noise.
            logger.info(
                "CursorScraper found no invoice links for account %s (selectors=%s)",
                account.id,
                INVOICE_PDF_LINK_SELECTORS,
            )
            return

        for link in links:
            href = await link.get_attribute("href")
            if not href:
                continue
            pdf_url = self._absolutize(href)
            invoice_id = _extract_invoice_id(pdf_url)
            if invoice_id in seen or invoice_id in new_ids_ordered:
                continue

            pdf_bytes = await self._download_pdf(page, pdf_url)
            if pdf_bytes is None:
                continue

            row_meta = await self._extract_row_meta(link)
            new_invoices.append(
                self._build_raw_email(account, invoice_id, pdf_url, pdf_bytes, row_meta)
            )
            new_ids_ordered.append(invoice_id)
            if progress_callback is not None:
                progress_callback({
                    "folder_fetch_msg": f"Cursor: {len(new_invoices)} invoice(s) fetched",
                })

    async def _locate_invoice_links(self, page: Any) -> list[Any]:
        """Try each selector in order. The first one that yields any links
        wins. We intentionally don't `wait_for_selector` on every
        selector — a missing selector is the expected case (this
        account has no invoices yet) and we don't want to pay 15s per
        fallback."""
        # Give the primary selector a single wait-window to let the
        # billing table hydrate. If it times out, we still try
        # fallbacks before giving up, because the primary may simply
        # be wrong for this account's billing UI variant.
        primary = INVOICE_PDF_LINK_SELECTORS[0]
        try:
            await page.wait_for_selector(
                primary, timeout=SELECTOR_WAIT_TIMEOUT_MS, state="visible"
            )
        except PlaywrightTimeoutError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("wait_for_selector(%s) raised %s; trying fallbacks", primary, exc)

        for selector in INVOICE_PDF_LINK_SELECTORS:
            matches = await page.locator(selector).all()
            if matches:
                return matches
        return []

    async def _download_pdf(self, page: Any, pdf_url: str) -> bytes | None:
        """Download a PDF using the authenticated browser context. Using
        ``page.context.request.get`` means the request carries the same
        cookies as the visible page, so Cursor's 302-to-/login trick
        does not fire."""
        try:
            response = await page.context.request.get(
                pdf_url,
                headers={"Accept": "application/pdf"},
            )
        except PlaywrightTimeoutError as exc:
            logger.warning("Cursor PDF fetch timed out for %s: %s", pdf_url, exc)
            return None

        status = getattr(response, "status", None)
        if callable(status):
            status = status()
        if isinstance(status, int) and status >= 400:
            logger.warning("Cursor PDF fetch %s returned HTTP %s", pdf_url, status)
            return None
        body = await response.body()
        if not body:
            return None
        return body

    async def _extract_row_meta(self, link: Any) -> dict[str, str]:
        """Best-effort scrape of the table row surrounding the PDF link
        for date / amount. Failures are non-fatal — the invoice still
        flows through the pipeline with empty strings and the LLM
        extractor fills the gap from the PDF itself."""
        meta: dict[str, str] = {"date_text": "", "amount_text": ""}
        try:
            row = link.locator("xpath=ancestor::tr[1]")
            if await row.is_visible(timeout=1_000):
                text = await row.text_content()
                if text:
                    meta["row_text"] = text.strip()
        except Exception:  # pragma: no cover - defensive
            pass
        return meta

    def _absolutize(self, href: str) -> str:
        if href.startswith(("http://", "https://")):
            return href
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"https://cursor.com{href}"
        return href

    async def _capture_storage_state(self, page: Any) -> None:
        state = await page.context.storage_state()
        self._updated_storage_state = json.dumps(state)

    def _build_raw_email(
        self,
        account: EmailAccount,
        invoice_id: str,
        pdf_url: str,
        pdf_bytes: bytes,
        row_meta: dict[str, str],
    ) -> RawEmail:
        received_at = datetime.now(timezone.utc)
        filename = f"cursor-invoice-{invoice_id}.pdf"
        row_text = row_meta.get("row_text", "")
        subject = f"Cursor Invoice {invoice_id}"
        if row_text:
            subject = f"Cursor Invoice {invoice_id} — {row_text[:80]}"
        return RawEmail(
            uid=f"cursor:{invoice_id}",
            subject=subject,
            body_text=f"Cursor invoice {invoice_id}\n{row_text}".strip(),
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
                "_pdf_url": pdf_url,
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
