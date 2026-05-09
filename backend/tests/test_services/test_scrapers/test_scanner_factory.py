from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.scrapers import factory as factory_mod
from app.services.scrapers.base import BaseScraper
from app.services.scrapers.cursor import CursorScraper
from app.services.scrapers.factory import ScraperFactory


def test_scraper_factory_returns_cursor_scraper_for_cursor_type() -> None:
    scraper = ScraperFactory.get_scraper("cursor")
    assert isinstance(scraper, CursorScraper)
    assert ScraperFactory.is_scraper_type("cursor") is True


def test_scraper_factory_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unknown scraper account type"):
        ScraperFactory.get_scraper("nonexistent-portal")
    assert ScraperFactory.is_scraper_type("imap") is False
    assert ScraperFactory.is_scraper_type("nonexistent-portal") is False


def test_scraper_factory_register_extends_registry_without_editing_module() -> None:
    class _StubScraper(BaseScraper):
        async def scan(
            self, account: Any, last_uid: Any = None, options: Any = None,
            progress_callback: Any = None,
        ) -> list[Any]:
            return []

    try:
        ScraperFactory.register("stub-portal", _StubScraper)
        assert ScraperFactory.is_scraper_type("stub-portal") is True
        assert isinstance(ScraperFactory.get_scraper("stub-portal"), _StubScraper)
    finally:
        factory_mod._REGISTRY.pop("stub-portal", None)


async def test_base_scraper_default_test_connection_returns_false() -> None:
    class _MinimalScraper(BaseScraper):
        async def scan(
            self, account: Any, last_uid: Any = None, options: Any = None,
            progress_callback: Any = None,
        ) -> list[Any]:
            return []

    account = SimpleNamespace(id=1)
    assert await _MinimalScraper().test_connection(account) is False
