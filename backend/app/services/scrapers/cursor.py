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


CURSOR_SELECTOR_VERSION = "2026-05-10-stripe-portal"

# Cursor's billing page hosts a "Manage in Stripe" button that opens the
# Stripe customer portal in a new tab. Cursor itself never exposes
# invoice PDFs; the real invoice list (and PDF downloads) live on the
# Stripe-hosted portal page.
CURSOR_BILLING_URL = "https://cursor.com/dashboard/billing"
CURSOR_LOGIN_URL_FRAGMENT = "/login"

# The portal's React shell doesn't expose a stable DOM selector, so we
# scrape invoice.stripe.com/i/... URLs from the raw HTML the portal
# server returns.
STRIPE_INVOICE_URL_RE = re.compile(
    r"(https://invoice\.stripe\.com/i/[^\s\"'<>]+)"
)

# Wall-clock ceiling per scrape; the scheduler's global loop cannot be
# blocked indefinitely by one hung account.
SCRAPE_TIMEOUT_SECONDS = 60.0

NAV_TIMEOUT_MS = 90_000
STRIPE_LOAD_TIMEOUT_MS = 60_000
DOWNLOAD_TIMEOUT_MS = 30_000

SEEN_URLS_CAP = 1000

SYNTHETIC_FROM_ADDR = "billing@cursor.com"

STATE_FORMAT = "cursor_stripe_v1"

# Matches "$1,001.79" / "US$9.99" / "€12.00" — loose; PDF is authoritative.
_AMOUNT_RE = re.compile(
    r"(?:US\$|[$€£¥])\s?[\d,]+(?:\.\d{1,2})?",
)

# Matches "May 7, 2026" — loose; PDF is authoritative.
_DATE_RE = re.compile(
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},\s+\d{4}"
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_seen_urls(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    urls = data.get("seen_urls") or []
    if not isinstance(urls, list):
        return set()
    return {str(x) for x in urls}


def _cap_seen(seen: set[str], ordered_new: list[str]) -> list[str]:
    """FIFO-evict oldest entries when the cap is hit; ``ordered_new``
    preserves discovery order so the newest invoices survive truncation."""
    merged = list(dict.fromkeys([*sorted(seen), *ordered_new]))
    if len(merged) > SEEN_URLS_CAP:
        merged = merged[-SEEN_URLS_CAP:]
    return merged


def _invoice_id_from_url(url: str) -> str:
    """Stable filename-safe ID for a Stripe invoice URL.

    URL path after ``/i/`` is ``<acct_xxx>/<uniq>?query``; ``<uniq>`` is
    the per-invoice component. Falls back to the last path segment if
    the shape is unexpected."""
    path = url.split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    # parts after filtering: [scheme, host, "i", acct_xxx, uniq, ...]
    if len(parts) >= 5 and parts[2] == "i":
        return parts[4][:64] or parts[3][:64]
    return parts[-1][:64] if parts else "invoice"




class CursorScraper(BaseScraper):
    """Cursor billing scraper — Stripe customer portal flow.

    Cursor's dashboard only offers a "Manage in Stripe" button; actual
    invoices live on the Stripe-hosted customer portal. Flow:

    1. Load Cursor billing using stored ``playwright_storage_state``.
    2. Click "Manage in Stripe" → Stripe portal opens in a new page.
    3. Scroll the portal to force lazy-loaded invoice rows to render.
    4. Extract ``invoice.stripe.com/i/...`` URLs from the raw HTML.
    5. For each new URL: visit, click "Download invoice", capture PDF
       via ``page.expect_download``, build ``RawEmail``.

    Only storage_state auth is supported (Mode B); Mode A was removed
    in v1.2.1. Recapture via ``scripts/cursor_login_local.py`` when the
    session expires — scraper emits ``auth_required`` on /login redirect.
    """

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

        seen = _parse_seen_urls(last_uid)
        new_invoices: list[RawEmail] = []
        new_urls_ordered: list[str] = []

        try:
            await asyncio.wait_for(
                self._scan_body(
                    account=account,
                    seen=seen,
                    new_invoices=new_invoices,
                    new_urls_ordered=new_urls_ordered,
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
            "seen_urls": _cap_seen(seen, new_urls_ordered),
            "last_scan_at": _utcnow_iso(),
        })
        return new_invoices

    async def _scan_body(
        self,
        *,
        account: EmailAccount,
        seen: set[str],
        new_invoices: list[RawEmail],
        new_urls_ordered: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        async with self._session_cls(account) as page:
            await self._run_scrape(
                page=page,
                account=account,
                seen=seen,
                new_invoices=new_invoices,
                new_urls_ordered=new_urls_ordered,
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
        new_urls_ordered: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        await page.goto(
            CURSOR_BILLING_URL,
            wait_until="load",
            timeout=NAV_TIMEOUT_MS,
        )
        await asyncio.sleep(3)  # React hydration — button may not exist at DOMContentLoaded

        if CURSOR_LOGIN_URL_FRAGMENT in str(page.url):
            self._emit_auth_required(
                "[CURSOR_AUTH] dashboard redirected to /login — session cookies missing or "
                "expired. Recapture storage_state via scripts/cursor_login_local.py."
            )
            raise ScraperAuthRequiredError("login redirect")

        stripe_page = await self._open_stripe_portal(page)
        if stripe_page is None:
            logger.info(
                "CursorScraper found no Stripe portal entry point for account %s",
                account.id,
            )
            return

        try:
            await self._scroll_stripe_page_to_bottom(stripe_page)

            invoice_urls = await self._extract_stripe_invoice_urls(stripe_page)
            if not invoice_urls:
                logger.info(
                    "CursorScraper found no Stripe invoice URLs on portal page for account %s",
                    account.id,
                )
                return

            for invoice_url in invoice_urls:
                if invoice_url in seen or invoice_url in new_urls_ordered:
                    continue

                raw_email = await self._process_single_invoice(
                    stripe_page=stripe_page,
                    account=account,
                    invoice_url=invoice_url,
                )
                if raw_email is None:
                    continue

                new_invoices.append(raw_email)
                new_urls_ordered.append(invoice_url)
                if progress_callback is not None:
                    progress_callback({
                        "folder_fetch_msg": f"Cursor: {len(new_invoices)} invoice(s) fetched",
                    })
        finally:
            try:
                await stripe_page.close()
            except Exception:  # pragma: no cover - defensive
                pass

    async def _open_stripe_portal(self, page: Any) -> Any:
        """Click "Manage in Stripe" and return the popped Stripe portal
        page via ``context.expect_page``. Returns ``None`` if the button
        is absent (account has no billing set up)."""
        button = page.locator('button:has-text("Manage in Stripe")').first
        count = await button.count()
        if not count:
            return None
        async with page.context.expect_page(timeout=STRIPE_LOAD_TIMEOUT_MS) as new_page_info:
            await button.click()
        new_page = await new_page_info.value
        try:
            await new_page.wait_for_load_state("load", timeout=STRIPE_LOAD_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.debug("Stripe portal load event did not fire within timeout")
        return new_page

    async def _scroll_stripe_page_to_bottom(self, stripe_page: Any) -> None:
        """Force Stripe's virtualised invoice rows to render by scrolling
        to the document bottom."""
        try:
            await stripe_page.evaluate(
                "() => window.scrollTo(0, document.body.scrollHeight)"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Stripe portal scroll failed (non-fatal): %s", exc)
        try:
            await asyncio.sleep(0.5)
        except Exception:  # pragma: no cover - defensive
            pass

    async def _extract_stripe_invoice_urls(self, stripe_page: Any) -> list[str]:
        html = await stripe_page.content()
        if not html:
            return []
        matches = STRIPE_INVOICE_URL_RE.findall(html)
        deduped: list[str] = []
        seen_local: set[str] = set()
        for url in matches:
            if url in seen_local:
                continue
            seen_local.add(url)
            deduped.append(url)
        return deduped

    async def _process_single_invoice(
        self,
        *,
        stripe_page: Any,
        account: EmailAccount,
        invoice_url: str,
    ) -> RawEmail | None:
        try:
            await stripe_page.goto(
                invoice_url,
                wait_until="load",
                timeout=STRIPE_LOAD_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError as exc:
            logger.warning("Stripe invoice navigation timed out for %s: %s", invoice_url, exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Stripe invoice navigation failed for %s: %s", invoice_url, exc)
            return None

        pdf_bytes = await self._download_invoice_pdf(stripe_page)
        if pdf_bytes is None:
            return None

        page_text = ""
        try:
            page_text = await stripe_page.inner_text("body") or ""
        except Exception:
            try:
                page_text = await stripe_page.text_content("body") or ""
            except Exception:  # pragma: no cover - defensive
                page_text = ""

        meta = self._extract_invoice_metadata(page_text)
        return self._build_raw_email(
            account=account,
            invoice_url=invoice_url,
            pdf_bytes=pdf_bytes,
            meta=meta,
        )

    async def _download_invoice_pdf(self, stripe_page: Any) -> bytes | None:
        """Click "Download invoice" and capture the PDF via
        ``expect_download``. Returns ``None`` on any per-invoice failure
        so the outer loop can skip and continue."""
        button = stripe_page.locator('button:has-text("Download invoice")').first
        try:
            count = await button.count()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Stripe download button count() failed: %s", exc)
            return None
        if not count:
            logger.info("Stripe invoice page has no Download button — skipping")
            return None

        try:
            async with stripe_page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
                await button.click()
            download = await download_info.value
        except PlaywrightTimeoutError as exc:
            logger.warning("Stripe invoice download timed out: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Stripe invoice download failed: %s", exc)
            return None

        try:
            path = await download.path()
            if path is None:
                return None
            with open(path, "rb") as f:
                payload = f.read()
        except Exception as exc:
            logger.warning("Stripe invoice download read failed: %s", exc)
            return None

        if not payload:
            return None
        return payload

    def _extract_invoice_metadata(self, text: str) -> dict[str, str]:
        """Loose-regex extraction of date/amount from the invoice page
        body. The PDF drives the canonical LLM extractor; this is only
        for the RawEmail subject/body preview."""
        meta: dict[str, str] = {"date_text": "", "amount_text": "", "page_text": ""}
        if not text:
            return meta
        meta["page_text"] = text.strip()[:500]
        date_match = _DATE_RE.search(text)
        if date_match:
            meta["date_text"] = date_match.group(0)
        amount_match = _AMOUNT_RE.search(text)
        if amount_match:
            meta["amount_text"] = amount_match.group(0)
        return meta

    async def _capture_storage_state(self, page: Any) -> None:
        state = await page.context.storage_state()
        self._updated_storage_state = json.dumps(state)

    def _build_raw_email(
        self,
        *,
        account: EmailAccount,
        invoice_url: str,
        pdf_bytes: bytes,
        meta: dict[str, str],
    ) -> RawEmail:
        invoice_id = _invoice_id_from_url(invoice_url)
        received_at = datetime.now(timezone.utc)
        filename = f"cursor-invoice-{invoice_id}.pdf"
        date_text = meta.get("date_text", "")
        amount_text = meta.get("amount_text", "")
        subject = f"Cursor Invoice {invoice_id}"
        if date_text or amount_text:
            subject = (
                f"Cursor Invoice {invoice_id} — {date_text} {amount_text}".strip()
            )
        body_text = (
            f"Cursor invoice {invoice_id}\n"
            f"URL: {invoice_url}\n"
            f"Date: {date_text}\n"
            f"Amount: {amount_text}\n"
            f"{meta.get('page_text', '')}"
        ).strip()
        return RawEmail(
            uid=f"cursor:{invoice_id}",
            subject=subject,
            body_text=body_text,
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
            body_links=[invoice_url],
            headers={
                "_scraper": "cursor",
                "_invoice_id": invoice_id,
                "_invoice_url": invoice_url,
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
