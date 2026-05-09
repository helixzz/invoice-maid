from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.services.email_scanner import encrypt_password
from app.services.scrapers import cursor as cursor_mod
from app.services.scrapers.cursor import (
    CURSOR_DASHBOARD_URL,
    SEEN_INVOICE_IDS_CAP,
    CursorScraper,
)


JWT_TEST_SECRET = "test-secret-cursor"


class _FakeContext:
    def __init__(self, storage_state: dict[str, Any] | None = None) -> None:
        self._state = storage_state or {"cookies": [{"name": "fresh"}], "origins": []}

    async def storage_state(self) -> dict[str, Any]:
        return self._state


class _FakeCursorPage:
    def __init__(
        self,
        *,
        initial_url: str = CURSOR_DASHBOARD_URL,
        invoice_rows: list[dict[str, Any]] | None = None,
        pdf_bytes_by_url: dict[str, bytes] | None = None,
        url_after_login: str = CURSOR_DASHBOARD_URL,
        has_2fa: bool = False,
        list_invoices_exc: BaseException | None = None,
        authenticated: bool | None = None,
    ) -> None:
        self._url = initial_url
        self._url_after_login = url_after_login
        self.invoice_rows = invoice_rows or []
        self.pdf_bytes_by_url = pdf_bytes_by_url or {}
        self._has_2fa = has_2fa
        self._list_invoices_exc = list_invoices_exc
        self._authenticated = (
            authenticated if authenticated is not None else "/login" not in initial_url
        )

        self.goto_calls: list[str] = []
        self.fill_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []
        self.download_calls: list[str] = []
        self.list_invoices_calls: list[str] = []

        self.context = _FakeContext()

    @property
    def url(self) -> str:
        return self._url

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.goto_calls.append(url)
        if not self._authenticated and url.startswith("https://cursor.com"):
            self._url = "https://cursor.com/login"
        else:
            self._url = url

    async def fill(self, selector: str, value: str) -> None:
        self.fill_calls.append((selector, value))
        if selector == 'input[type="password"]':
            self._authenticated = True
            self._url = self._url_after_login

    async def click(self, selector: str, **_kwargs: Any) -> None:
        self.click_calls.append(selector)

    async def has_totp_challenge(self) -> bool:
        return self._has_2fa

    async def list_invoices(self, selector: str) -> list[dict[str, Any]]:
        self.list_invoices_calls.append(selector)
        if self._list_invoices_exc is not None:
            raise self._list_invoices_exc
        return list(self.invoice_rows)

    async def download_bytes(self, url: str) -> bytes | None:
        self.download_calls.append(url)
        return self.pdf_bytes_by_url.get(url)


class _FakeSession:
    def __init__(self, page: _FakeCursorPage) -> None:
        self._page = page
        self.updated_storage_state: str | None = None

    def __call__(self, account: Any) -> "_FakeSession":
        self._account = account
        return self

    async def __aenter__(self) -> _FakeCursorPage:
        return self._page

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb


def _make_account(
    settings: Any,
    *,
    secondary_credential: str | None = "cursor-user@example.com",
    secondary_password: str | None = "s3cret",
    totp_secret: str | None = None,
    storage_state: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        user_id=1,
        name="cursor-test",
        type="cursor",
        username="cursor-test",
        playwright_storage_state=storage_state,
        secondary_credential_encrypted=(
            encrypt_password(secondary_credential, settings.JWT_SECRET)
            if secondary_credential else None
        ),
        secondary_password_encrypted=(
            encrypt_password(secondary_password, settings.JWT_SECRET)
            if secondary_password else None
        ),
        totp_secret_encrypted=(
            encrypt_password(totp_secret, settings.JWT_SECRET)
            if totp_secret else None
        ),
    )


def _scraper_with(page: _FakeCursorPage) -> tuple[CursorScraper, _FakeSession]:
    session = _FakeSession(page)
    scraper = CursorScraper(session_cls=session)
    return scraper, session


def _sample_rows() -> list[dict[str, Any]]:
    return [
        {
            "invoice_id": "in_001",
            "pdf_url": "https://billing.stripe.com/p/inv/in_001/pdf",
            "amount": "20.00",
            "currency": "USD",
            "period": "2026-04",
            "invoice_date": datetime(2026, 4, 1, tzinfo=timezone.utc),
        },
        {
            "invoice_id": "in_002",
            "pdf_url": "https://billing.stripe.com/p/inv/in_002/pdf",
            "amount": "20.00",
            "currency": "USD",
            "period": "2026-05",
            "invoice_date": datetime(2026, 5, 1, tzinfo=timezone.utc),
        },
    ]


@pytest.fixture(autouse=True)
def _force_playwright_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cursor_mod, "PLAYWRIGHT_AVAILABLE", True, raising=False)


async def test_b1_first_scan_with_empty_state_fetches_all_invoices(settings: Any) -> None:
    storage_json = json.dumps({"cookies": [{"name": "existing"}], "origins": []})
    account = _make_account(settings, storage_state=storage_json)
    rows = _sample_rows()
    page = _FakeCursorPage(
        invoice_rows=rows,
        pdf_bytes_by_url={row["pdf_url"]: f"pdf-{row['invoice_id']}".encode() for row in rows},
    )
    scraper, _ = _scraper_with(page)

    progress_updates: list[dict[str, Any]] = []
    result = await scraper.scan(
        account,
        last_uid=None,
        progress_callback=progress_updates.append,
    )

    assert [e.uid for e in result] == ["cursor:in_001", "cursor:in_002"]
    assert result[0].attachments[0].payload == b"pdf-in_001"
    assert result[0].from_addr == "billing@cursor.com"
    assert result[0].headers["_scraper"] == "cursor"
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["_format"] == "cursor_scraper_v1"
    assert state["seen_invoice_ids"] == ["in_001", "in_002"]
    assert len(progress_updates) == 2


async def test_b2_incremental_scan_filters_previously_seen_ids(settings: Any) -> None:
    account = _make_account(settings, storage_state="{}")
    rows = _sample_rows()
    page = _FakeCursorPage(
        invoice_rows=rows,
        pdf_bytes_by_url={row["pdf_url"]: b"x" for row in rows},
    )
    scraper, _ = _scraper_with(page)

    prior = json.dumps({
        "_format": "cursor_scraper_v1",
        "seen_invoice_ids": ["in_001"],
        "last_scan_at": "2026-04-02T00:00:00Z",
    })
    result = await scraper.scan(account, last_uid=prior)

    assert [e.uid for e in result] == ["cursor:in_002"]
    state = json.loads(scraper._last_scan_state or "{}")
    assert set(state["seen_invoice_ids"]) == {"in_001", "in_002"}


async def test_b3_login_flow_runs_when_credentials_present(settings: Any) -> None:
    account = _make_account(settings, storage_state=None)
    rows = _sample_rows()
    page = _FakeCursorPage(
        initial_url="https://cursor.com/login",
        url_after_login="https://cursor.com/dashboard",
        invoice_rows=rows,
        pdf_bytes_by_url={row["pdf_url"]: b"p" for row in rows},
    )
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    selectors_filled = [sel for sel, _ in page.fill_calls]
    values_filled = [val for _, val in page.fill_calls]
    assert 'input[type="email"]' in selectors_filled
    assert 'input[type="password"]' in selectors_filled
    assert "s3cret" in values_filled
    assert "s3cret" not in (scraper._last_scan_state or "")
    assert len(result) == 2


async def test_b4_login_required_but_credentials_missing_emits_auth_required(
    settings: Any,
) -> None:
    account = _make_account(
        settings,
        secondary_credential=None,
        secondary_password=None,
        storage_state=None,
    )
    page = _FakeCursorPage(initial_url="https://cursor.com/login")
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "credentials missing" in event["error_detail"]
        for event in scraper._scan_events
    )
    assert page.fill_calls == []


async def test_b5_2fa_mode_a_uses_stored_totp_secret(settings: Any) -> None:
    account = _make_account(settings, storage_state=None, totp_secret="ABCDEF123456")
    rows = _sample_rows()[:1]
    page = _FakeCursorPage(
        initial_url="https://cursor.com/login",
        url_after_login="https://cursor.com/dashboard",
        has_2fa=True,
        invoice_rows=rows,
        pdf_bytes_by_url={rows[0]["pdf_url"]: b"p"},
    )
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    code_fills = [val for sel, val in page.fill_calls if sel == 'input[name="code"]']
    assert code_fills == ["ABCDEF"]
    assert len(result) == 1


async def test_b6_2fa_mode_b_no_stored_totp_emits_auth_required(settings: Any) -> None:
    account = _make_account(settings, storage_state=None, totp_secret=None)
    page = _FakeCursorPage(
        initial_url="https://cursor.com/login",
        url_after_login="https://cursor.com/dashboard",
        has_2fa=True,
    )
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "no TOTP secret stored" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_b7_scan_navigates_dashboard_then_follows_invoice_pdf_urls(settings: Any) -> None:
    account = _make_account(settings, storage_state="{}")
    rows = _sample_rows()
    page = _FakeCursorPage(
        invoice_rows=rows,
        pdf_bytes_by_url={row["pdf_url"]: b"p" for row in rows},
    )
    scraper, _ = _scraper_with(page)

    await scraper.scan(account)

    assert page.goto_calls[0] == CURSOR_DASHBOARD_URL
    assert page.download_calls == [rows[0]["pdf_url"], rows[1]["pdf_url"]]


async def test_b8_pdf_download_populates_raw_attachment_payload(settings: Any) -> None:
    account = _make_account(settings, storage_state="{}")
    rows = _sample_rows()[:1]
    page = _FakeCursorPage(
        invoice_rows=rows,
        pdf_bytes_by_url={rows[0]["pdf_url"]: b"%PDF-1.7 cursor-bytes"},
    )
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    assert len(result) == 1
    att = result[0].attachments[0]
    assert att.payload == b"%PDF-1.7 cursor-bytes"
    assert att.content_type == "application/pdf"
    assert att.size == len(b"%PDF-1.7 cursor-bytes")
    assert att.filename == "cursor-invoice-in_001.pdf"


async def test_b9_storage_state_is_persisted_back_to_account_via_scheduler_hook(
    settings: Any,
) -> None:
    account = _make_account(settings, storage_state=json.dumps({"cookies": []}))
    rows = _sample_rows()[:1]
    page = _FakeCursorPage(
        invoice_rows=rows,
        pdf_bytes_by_url={rows[0]["pdf_url"]: b"p"},
    )
    page.context = _FakeContext({"cookies": [{"name": "refreshed"}], "origins": []})
    scraper, _ = _scraper_with(page)

    await scraper.scan(account)

    assert scraper._updated_storage_state is not None
    parsed = json.loads(scraper._updated_storage_state)
    assert parsed["cookies"] == [{"name": "refreshed"}]

    account.playwright_storage_state = scraper._updated_storage_state
    assert json.loads(account.playwright_storage_state)["cookies"][0]["name"] == "refreshed"


async def test_b10_network_timeout_during_list_invoices_propagates_and_emits_event(
    settings: Any,
) -> None:
    account = _make_account(settings, storage_state="{}")
    page = _FakeCursorPage(list_invoices_exc=TimeoutError("navigation timeout"))
    scraper, _ = _scraper_with(page)

    with pytest.raises(TimeoutError):
        await scraper.scan(account)

    assert any(
        event["kind"] == "auth_required" and "network timeout" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_b11_selector_mismatch_emits_auth_required_and_returns_empty(
    settings: Any,
) -> None:
    account = _make_account(settings, storage_state="{}")
    page = _FakeCursorPage(list_invoices_exc=LookupError("no rows matched"))
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "Cursor UI may have changed" in event["error_detail"]
        for event in scraper._scan_events
    )


async def test_b12_seen_invoice_ids_are_capped_at_1000_with_fifo_eviction(
    settings: Any,
) -> None:
    account = _make_account(settings, storage_state="{}")
    prior_ids = [f"legacy-{i:04d}" for i in range(SEEN_INVOICE_IDS_CAP)]
    prior = json.dumps({
        "_format": "cursor_scraper_v1",
        "seen_invoice_ids": prior_ids,
        "last_scan_at": "2026-04-02T00:00:00Z",
    })
    new_rows = [
        {
            "invoice_id": f"fresh-{i}",
            "pdf_url": f"https://billing.stripe.com/p/inv/fresh-{i}/pdf",
            "amount": "1",
            "currency": "USD",
            "period": "x",
            "invoice_date": datetime.now(timezone.utc),
        }
        for i in range(5)
    ]
    page = _FakeCursorPage(
        invoice_rows=new_rows,
        pdf_bytes_by_url={row["pdf_url"]: b"p" for row in new_rows},
    )
    scraper, _ = _scraper_with(page)

    await scraper.scan(account, last_uid=prior)

    state = json.loads(scraper._last_scan_state or "{}")
    seen = state["seen_invoice_ids"]
    assert len(seen) == SEEN_INVOICE_IDS_CAP
    assert "legacy-0000" not in seen
    assert "fresh-4" in seen
    assert "legacy-0999" in seen


async def test_b13_scheduler_drain_converts_auth_required_events_to_extraction_logs(
    settings: Any,
    db: Any,
    create_email_account: Callable[..., Any],
    mock_ai_service: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models import ExtractionLog, ScanLog
    import app.tasks.scheduler as scheduler_mod
    from app.services.scrapers.factory import ScraperFactory

    class _StubScraperForDrain:
        def __init__(self) -> None:
            self._scan_events = [{
                "kind": "auth_required",
                "error_detail": "[CURSOR_AUTH] no creds stored (drain test)",
            }]
            self._last_scan_state = None
            self._updated_storage_state = None

        async def scan(
            self, account: Any, last_uid: Any = None, options: Any = None,
            progress_callback: Any = None,
        ) -> list[Any]:
            del account, last_uid, options, progress_callback
            return []

    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(scheduler_mod, "get_db", override_get_db)
    monkeypatch.setattr(scheduler_mod, "AIService", lambda s: mock_ai_service)
    monkeypatch.setattr(
        ScraperFactory, "get_scraper", staticmethod(lambda t: _StubScraperForDrain())
    )
    monkeypatch.setattr(
        ScraperFactory, "is_scraper_type", staticmethod(lambda t: t == "cursor")
    )

    await create_email_account(type="cursor", host=None, port=None)
    await scheduler_mod.scan_all_accounts()

    logs = (await db.execute(select(ExtractionLog))).scalars().all()
    assert any(
        log.outcome == "auth_required"
        and log.error_detail is not None
        and "CURSOR_AUTH" in log.error_detail
        for log in logs
    ), [log.outcome for log in logs]
    assert (await db.execute(select(ScanLog))).scalars().first() is not None


async def test_cursor_scraper_returns_empty_when_playwright_absent(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cursor_mod, "PLAYWRIGHT_AVAILABLE", False, raising=False)
    account = _make_account(settings)
    page = _FakeCursorPage()
    scraper, _ = _scraper_with(page)

    result = await scraper.scan(account)

    assert result == []
    assert any(
        event["kind"] == "auth_required" and "[CURSOR_DEPENDENCY]" in event["error_detail"]
        for event in scraper._scan_events
    )
    assert scraper._last_scan_state is None


async def test_cursor_scraper_catches_playwright_unavailable_from_session(
    settings: Any,
) -> None:
    from app.services.scrapers.playwright_session import PlaywrightUnavailableError

    account = _make_account(settings)

    class _BrokenSession:
        def __call__(self, account: Any) -> "_BrokenSession":
            return self

        async def __aenter__(self) -> Any:
            raise PlaywrightUnavailableError("no playwright on host")

        async def __aexit__(self, *_: Any) -> None:
            return None

    scraper = CursorScraper(session_cls=_BrokenSession())
    result = await scraper.scan(account)

    assert result == []
    assert any(
        "[CURSOR_DEPENDENCY]" in event["error_detail"] for event in scraper._scan_events
    )


def test_cursor_scraper_default_totp_code_ljusts_short_secret_to_six_chars() -> None:
    scraper = CursorScraper()
    assert scraper._generate_totp_code("AB") == "AB0000"
    assert scraper._generate_totp_code("ABCDEFGH") == "ABCDEF"


async def test_scheduler_persists_updated_storage_state_when_scraper_sets_it(
    db: Any,
    create_email_account: Callable[..., Any],
    mock_ai_service: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models import EmailAccount
    import app.tasks.scheduler as scheduler_mod
    from app.services.scrapers.factory import ScraperFactory

    class _StubScraperEmittingState:
        def __init__(self) -> None:
            self._scan_events: list[dict[str, Any]] = []
            self._last_scan_state = None
            self._updated_storage_state = '{"cookies": [{"name": "rotated"}]}'

        async def scan(
            self, account: Any, last_uid: Any = None, options: Any = None,
            progress_callback: Any = None,
        ) -> list[Any]:
            del account, last_uid, options, progress_callback
            return []

    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(scheduler_mod, "get_db", override_get_db)
    monkeypatch.setattr(scheduler_mod, "AIService", lambda s: mock_ai_service)
    monkeypatch.setattr(
        ScraperFactory, "get_scraper", staticmethod(lambda t: _StubScraperEmittingState())
    )
    monkeypatch.setattr(
        ScraperFactory, "is_scraper_type", staticmethod(lambda t: t == "cursor")
    )

    account = await create_email_account(type="cursor", host=None, port=None)
    await scheduler_mod.scan_all_accounts()

    refreshed = (await db.execute(select(EmailAccount).where(EmailAccount.id == account.id))).scalar_one()
    await db.refresh(refreshed)
    assert refreshed.playwright_storage_state == '{"cookies": [{"name": "rotated"}]}'
