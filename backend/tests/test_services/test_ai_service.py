from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import LLMCache
from app.schemas.invoice import EmailAnalysis, InvoiceExtract, UrlKind
from app.services.ai_service import AIService, _resolve_safelink


class FakeChatCompletions:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeRawClient:
    def __init__(self, embedding):
        self.embedding = embedding
        self.calls = []
        self.embeddings = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=self.embedding)])


def make_analysis(**overrides) -> EmailAnalysis:
    defaults = {
        "is_invoice_related": True,
        "invoice_confidence": 0.8,
        "best_download_url": None,
        "url_confidence": 0.0,
        "url_is_safelink": False,
        "url_kind": UrlKind.NONE,
        "skip_reason": None,
    }
    defaults.update(overrides)
    return EmailAnalysis(**defaults)


@pytest.mark.asyncio
async def test_analyze_email_uses_cache_and_persists_v2_key(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    result = make_analysis(best_download_url="https://example.com/invoice.xml", url_confidence=0.82, url_kind=UrlKind.DIRECT_FILE)
    chat = FakeChatCompletions(result)
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    analyzed = await service.analyze_email(
        db,
        "subject",
        "from@example.com",
        "body",
        ["https://example.com/invoice.xml"],
    )
    assert analyzed.best_download_url == "https://example.com/invoice.xml"
    assert len(chat.calls) == 1
    cache = (await db.execute(__import__("sqlalchemy").select(LLMCache))).scalar_one()
    assert cache.prompt_type == "analyze_email_v2"
    cached = await service.analyze_email(
        db,
        "subject",
        "from@example.com",
        "body",
        ["https://example.com/invoice.xml"],
    )
    assert cached.best_download_url == "https://example.com/invoice.xml"
    assert len(chat.calls) == 1


@pytest.mark.asyncio
async def test_analyze_email_includes_from_and_links_xml(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    result = make_analysis(best_download_url="https://example.com/a.xml", url_confidence=0.9, url_kind=UrlKind.DIRECT_FILE)
    chat = FakeChatCompletions(result)
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    await service.analyze_email(
        db,
        "subject",
        "sender@test",
        "body text",
        ["https://example.com/a.xml", "https://example.com/b"],
    )

    content = chat.calls[0]["messages"][1]["content"]
    assert "Subject: subject" in content
    assert "From: sender@test" in content
    assert "<links>" in content
    assert "<url>https://example.com/a.xml</url>" in content
    assert "<url>https://example.com/b</url>" in content


@pytest.mark.asyncio
async def test_analyze_email_cache_hit_returns_email_analysis(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    chat = FakeChatCompletions(None)
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    cached_result = make_analysis(best_download_url="https://example.com/cached.pdf", url_confidence=0.76, url_kind=UrlKind.DIRECT_FILE)
    content = (
        "Subject: subject\n"
        "From: from@test\n"
        "Body:\nbody\n\n"
        "<links>\n  <url>https://example.com/cached.pdf</url>\n</links>"
    )
    db.add(
        LLMCache(
            content_hash=service._content_hash("analyze_email_v2", content),
            prompt_type="analyze_email_v2",
            response_json=cached_result.model_dump_json(),
        )
    )
    await db.commit()

    analyzed = await service.analyze_email(db, "subject", "from@test", "body", ["https://example.com/cached.pdf"])

    assert analyzed.best_download_url == "https://example.com/cached.pdf"
    assert chat.calls == []


@pytest.mark.asyncio
async def test_classify_email_alias_returns_boolean_from_analyze_email(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    result = make_analysis()
    chat = FakeChatCompletions(result)
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    assert await service.classify_email(db, "subject", "body") is True
    assert "From: " in chat.calls[0]["messages"][1]["content"]
    assert "<links>\n  (none)\n</links>" in chat.calls[0]["messages"][1]["content"]


def test_email_analysis_should_download_property() -> None:
    assert make_analysis(best_download_url="https://example.com/a.pdf", url_confidence=0.6, url_kind=UrlKind.DIRECT_FILE).should_download is True
    assert make_analysis(best_download_url="https://example.com/a.pdf", url_confidence=0.59, url_kind=UrlKind.DIRECT_FILE).should_download is False


@pytest.mark.asyncio
async def test_resolve_safelink_returns_original_for_non_safelink() -> None:
    url = "https://example.com/file.pdf"
    assert await _resolve_safelink(url) == url


@pytest.mark.asyncio
async def test_resolve_safelink_follows_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        url = "https://real.example.com/file.pdf"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def head(self, url: str) -> FakeResponse:
            assert "safelinks.protection.outlook.com" in url
            return FakeResponse()

    monkeypatch.setattr("app.services.ai_service.httpx.AsyncClient", lambda **kwargs: FakeClient())

    resolved = await _resolve_safelink("https://nam01.safelinks.protection.outlook.com/?url=x")
    assert resolved == "https://real.example.com/file.pdf"


@pytest.mark.asyncio
async def test_resolve_safelink_returns_original_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def head(self, url: str):
            raise RuntimeError(url)

    monkeypatch.setattr("app.services.ai_service.httpx.AsyncClient", lambda **kwargs: FakeClient())
    url = "https://nam01.safelinks.protection.outlook.com/?url=x"

    assert await _resolve_safelink(url) == url


@pytest.mark.asyncio
async def test_extract_invoice_fields_uses_cache(db, settings, monkeypatch: pytest.MonkeyPatch, mock_ai_service) -> None:
    result = mock_ai_service.extract_invoice_fields.return_value
    chat = FakeChatCompletions(result)
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    extracted = await service.extract_invoice_fields(db, "x" * 6000)
    assert isinstance(extracted, InvoiceExtract)
    assert extracted.invoice_no == result.invoice_no
    assert len(chat.calls) == 1
    cached = await service.extract_invoice_fields(db, "x" * 6000)
    assert cached.invoice_no == result.invoice_no
    assert len(chat.calls) == 1


@pytest.mark.asyncio
async def test_embed_text_warns_on_dimension_mismatch(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_client = FakeRawClient([0.1, 0.2])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions(None))),
    )

    service = AIService(settings)
    embedding = await service.embed_text("y" * 9000)
    assert embedding == [0.1, 0.2]
    assert raw_client.calls[0]["input"] == "y" * 8000


@pytest.mark.asyncio
async def test_embed_text_matching_dimension(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions(None))),
    )

    service = AIService(settings)
    assert await service.embed_text("ok") == [0.1, 0.2, 0.3]


def test_prompt_helpers(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions(None))),
    )
    service = AIService(settings)

    assert "发票" in service._load_prompt("classify_email.txt")
    assert service._content_hash("kind", "value") == service._content_hash("kind", "value")


@pytest.mark.asyncio
async def test_set_cache_rolls_back_on_integrity_error(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions(None))),
    )

    service = AIService(settings)
    rollback = db.rollback
    rollback_calls: list[bool] = []

    async def failing_commit() -> None:
        raise IntegrityError("insert", {}, Exception("duplicate"))

    async def tracked_rollback() -> None:
        rollback_calls.append(True)
        await rollback()

    monkeypatch.setattr(db, "commit", failing_commit)
    monkeypatch.setattr(db, "rollback", tracked_rollback)

    await service._set_cache(db, "hash", "extract", "{}")

    assert rollback_calls == [True]
