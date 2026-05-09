from __future__ import annotations

from app.services.scrapers.base import BaseScraper
from app.services.scrapers.cursor import CursorScraper


_REGISTRY: dict[str, type[BaseScraper]] = {
    "cursor": CursorScraper,
}


class ScraperFactory:
    @staticmethod
    def get_scraper(account_type: str) -> BaseScraper:
        cls = _REGISTRY.get(account_type)
        if cls is None:
            raise ValueError(f"Unknown scraper account type: {account_type}")
        return cls()

    @staticmethod
    def is_scraper_type(account_type: str) -> bool:
        return account_type in _REGISTRY

    @staticmethod
    def register(account_type: str, scraper_cls: type[BaseScraper]) -> None:
        _REGISTRY[account_type] = scraper_cls
