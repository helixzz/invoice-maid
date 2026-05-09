from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.scrapers import playwright_session as ps_mod
from app.services.scrapers.playwright_session import (
    PlaywrightSession,
    PlaywrightUnavailableError,
)


class _FakePage:
    def __init__(self) -> None:
        self.closed = False
        self.context: Any = None

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, *, storage_state: Any, user_agent: str) -> None:
        self._storage_state_in = storage_state
        self.user_agent = user_agent
        self.page = _FakePage()
        self.page.context = self
        self.closed = False
        self.captured_state = {"cookies": [{"name": "fresh"}], "origins": []}

    async def new_page(self) -> _FakePage:
        return self.page

    async def storage_state(self) -> dict[str, Any]:
        return self.captured_state

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[_FakeContext] = []
        self.closed = False

    async def new_context(self, *, storage_state: Any = None, user_agent: str = "") -> _FakeContext:
        ctx = _FakeContext(storage_state=storage_state, user_agent=user_agent)
        self.contexts.append(ctx)
        return ctx

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser
        self.launch_kwargs: dict[str, Any] | None = None

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launch_kwargs = kwargs
        return self._browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()
        self.chromium = _FakeChromium(self.browser)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _FakePlaywrightFactory:
    def __init__(self) -> None:
        self.instance = _FakePlaywright()
        self.started = False

    def __call__(self) -> "_FakePlaywrightFactory":
        return self

    async def start(self) -> _FakePlaywright:
        self.started = True
        return self.instance


def _account(storage_state: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        playwright_storage_state=storage_state,
    )


async def test_playwright_session_fresh_context_has_no_storage_state() -> None:
    factory = _FakePlaywrightFactory()
    session = PlaywrightSession(_account(None), async_playwright_factory=factory)
    async with session as page:
        assert isinstance(page, _FakePage)
    ctx = factory.instance.browser.contexts[0]
    assert ctx._storage_state_in is None
    assert ctx.user_agent == ps_mod.DESKTOP_CHROME_UA
    assert session.updated_storage_state is not None
    assert json.loads(session.updated_storage_state) == ctx.captured_state
    assert ctx.closed
    assert factory.instance.browser.closed
    assert factory.instance.stopped


async def test_playwright_session_loads_existing_storage_state_into_context() -> None:
    stored = {"cookies": [{"name": "old"}], "origins": []}
    factory = _FakePlaywrightFactory()
    session = PlaywrightSession(
        _account(json.dumps(stored)), async_playwright_factory=factory
    )
    async with session:
        pass
    ctx = factory.instance.browser.contexts[0]
    assert ctx._storage_state_in == stored


async def test_playwright_session_raises_when_playwright_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = PlaywrightSession(_account(None), async_playwright_factory=None)
    monkeypatch.setattr(ps_mod, "_async_playwright", None, raising=False)
    with pytest.raises(PlaywrightUnavailableError):
        await session.__aenter__()


async def test_playwright_session_exit_is_safe_when_enter_never_populated_state() -> None:
    session = PlaywrightSession(_account(None), async_playwright_factory=None)
    await session.__aexit__(None, None, None)
    assert session.updated_storage_state is None
