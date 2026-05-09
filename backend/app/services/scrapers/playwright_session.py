from __future__ import annotations

import json
from typing import Any

from app.models import EmailAccount


try:
    from playwright.async_api import async_playwright as _async_playwright  # pragma: no cover - only imported when playwright is installed on the host
    PLAYWRIGHT_AVAILABLE = True  # pragma: no cover - only taken when playwright is installed on the host
except ImportError:
    _async_playwright = None
    PLAYWRIGHT_AVAILABLE = False


DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


class PlaywrightUnavailableError(RuntimeError):
    """Raised when a scraper tries to open a PlaywrightSession but the
    ``playwright`` package is not installed. Scrapers catch this,
    log a warning, and return an empty list so a missing optional
    dependency never crashes the scheduler."""


class PlaywrightSession:
    """Async context manager that yields an authenticated Playwright
    ``Page`` for one scrape. Loads ``EmailAccount.playwright_storage_state``
    into the new browser context on entry and captures the refreshed
    state into ``updated_storage_state`` on exit so the scheduler can
    persist it back to the DB."""

    def __init__(self, account: EmailAccount, *, async_playwright_factory: Any = None) -> None:
        self._account = account
        self._factory = async_playwright_factory or _async_playwright
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._updated_storage_state: str | None = None

    async def __aenter__(self) -> Any:
        if self._factory is None:
            raise PlaywrightUnavailableError(
                "playwright is not installed; install 'playwright>=1.45' to use scrapers"
            )
        self._playwright = await self._factory().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        storage_state: Any = None
        if self._account.playwright_storage_state:
            storage_state = json.loads(self._account.playwright_storage_state)
        self._context = await self._browser.new_context(
            storage_state=storage_state,
            user_agent=DESKTOP_CHROME_UA,
        )
        self._page = await self._context.new_page()
        return self._page

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        if self._context is not None:
            new_state = await self._context.storage_state()
            self._updated_storage_state = json.dumps(new_state)
        if self._page is not None:
            await self._page.close()
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    @property
    def updated_storage_state(self) -> str | None:
        return self._updated_storage_state
