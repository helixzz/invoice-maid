from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import LLMCache
from app.schemas.invoice import EmailClassification, InvoiceExtract
from app.services.ai_service import AIService


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


@pytest.mark.asyncio
async def test_classify_email_uses_cache_and_persists(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    result = EmailClassification(is_invoice_related=True, confidence=0.8, reason="invoice")
    chat = FakeChatCompletions(result)
    raw_client = FakeRawClient([0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: raw_client)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    assert await service.classify_email(db, "subject", "body") is True
    assert len(chat.calls) == 1
    cache = (await db.execute(__import__("sqlalchemy").select(LLMCache))).scalar_one()
    assert cache.prompt_type == "classify"
    assert await service.classify_email(db, "subject", "body") is True
    assert len(chat.calls) == 1


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
