from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.services.email_scanner import RawEmail, encrypt_password
from app.services.scrapers.api_source import BaseAPIScraper


class _DummyAPIScraper(BaseAPIScraper):
    def __init__(self, invoices: list[RawEmail] | None = None) -> None:
        self._invoices = invoices or []
        self.received_api_key: str | None = None
        self.received_org_id: str | None = None

    async def fetch_invoices(
        self,
        *,
        account: Any,
        api_key: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        del progress_callback
        self.received_api_key = api_key
        self.received_org_id = self._decrypt_credential(account, "secondary_password_encrypted")
        return list(self._invoices)


def _make_account(**kwargs: Any) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "secondary_credential_encrypted": None,
        "secondary_password_encrypted": None,
        "totp_secret_encrypted": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_api_scraper_decrypts_credentials_and_updates_scan_state(settings) -> None:
    encrypted_key = encrypt_password("sk-live-secret-123", settings.JWT_SECRET)
    encrypted_org = encrypt_password("org_42", settings.JWT_SECRET)
    account = _make_account(
        secondary_credential_encrypted=encrypted_key,
        secondary_password_encrypted=encrypted_org,
    )
    now = datetime.now(timezone.utc)
    inv1 = RawEmail(uid="api-1", subject="Inv 1", from_addr="billing@vendor.com", body_text="ok", body_html="", received_at=now)
    inv2 = RawEmail(uid="api-2", subject="Inv 2", from_addr="billing@vendor.com", body_text="ok", body_html="", received_at=now)
    scraper = _DummyAPIScraper(invoices=[inv1, inv2])

    prev_state = json.dumps({"_format": "api_source_v1", "seen_uids": ["api-0", "api-1"]})
    results = await scraper.scan(account, last_uid=prev_state)

    assert len(results) == 2
    assert scraper.received_api_key == "sk-live-secret-123"
    assert scraper.received_org_id == "org_42"
    assert scraper._scan_events == []
    assert scraper._last_scan_state is not None
    state = json.loads(scraper._last_scan_state)
    assert state["_format"] == "api_source_v1"
    assert set(state["seen_uids"]) == {"api-0", "api-1", "api-2"}


@pytest.mark.asyncio
async def test_api_scraper_missing_or_corrupt_api_key_emits_auth_required(settings) -> None:
    del settings
    scraper = _DummyAPIScraper()
    assert await scraper.scan(_make_account()) == []
    assert scraper._scan_events == [
        {"kind": "auth_required", "error_detail": "[API_KEY_MISSING] No API key configured"}
    ]

    corrupt_scraper = _DummyAPIScraper()
    assert await corrupt_scraper.scan(
        _make_account(secondary_credential_encrypted="not-valid-fernet")
    ) == []
    assert len(corrupt_scraper._scan_events) == 1
    assert corrupt_scraper._scan_events[0]["kind"] == "auth_required"


@pytest.mark.asyncio
async def test_api_scraper_handles_malformed_last_uid_and_default_properties(settings) -> None:
    now = datetime.now(timezone.utc)
    scraper = _DummyAPIScraper(
        invoices=[RawEmail(uid="u1", subject="S", from_addr="a@b.com", body_text="", body_html="", received_at=now)]
    )
    assert scraper._scan_events == []
    assert scraper._last_scan_state is None

    account = _make_account(
        secondary_credential_encrypted=encrypt_password("key-ok", settings.JWT_SECRET)
    )
    results = await scraper.scan(account, last_uid="{bad-json")
    assert len(results) == 1
    state = json.loads(scraper._last_scan_state or "{}")
    assert state["seen_uids"] == ["u1"]

    results_fresh = await scraper.scan(account, last_uid=None)
    assert len(results_fresh) == 1
    state_fresh = json.loads(scraper._last_scan_state or "{}")
    assert state_fresh["seen_uids"] == ["u1"]
