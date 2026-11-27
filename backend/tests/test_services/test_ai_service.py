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
    assert cache.prompt_type == "analyze_email_v3"
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
            content_hash=service._content_hash("analyze_email_v3", content),
            prompt_type="analyze_email_v3",
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


def test_extract_prompt_contains_transport_bare_currency_rule(settings) -> None:
    """Regression for v0.8.9: the extract prompt must explicitly authorize
    the LLM to bind a bare ``￥N.NN`` fragment to ``amount`` on transport
    e-tickets, even without a ``票价`` label. Without this clause the LLM
    falls back to the 0.01 sentinel (see production a test-case invoice where
    `￥6.50` sat unlabeled in the text and was discarded).

    A future edit removing this clause should fail this test loudly
    rather than silently reintroducing the regression."""
    from app.services.ai_service import AIService

    service = AIService(settings)
    prompt = service._load_prompt("extract_invoice.txt")

    assert "￥6.50" in prompt or "¥6.50" in prompt, (
        "prompt must show a bare-currency example so the LLM learns the pattern"
    )
    assert "even when no 票价" in prompt, (
        "prompt must explicitly say bare amounts are valid without a 票价 label"
    )
    assert "退票费" in prompt, (
        "prompt must mention 退票费 so the LLM knows to treat it as a refund-fee "
        "label, not a ticket-price label"
    )
    assert "relaxes VALIDITY only" in prompt, (
        "Path B relaxation must be scoped to validity, not amount extraction"
    )


def test_extract_prompt_still_rejects_scam_signals(settings) -> None:
    """Sanity: the v0.8.9 bare-currency rule must NOT weaken the Step 0
    scam rejection. Scam documents containing 代开 / 出售发票 / 加微信 must
    still be rejected even if they happen to have a ``￥XX`` figure."""
    from app.services.ai_service import AIService

    service = AIService(settings)
    prompt = service._load_prompt("extract_invoice.txt")

    assert "STEP 0 — SCAM / FRAUD REJECTION" in prompt
    assert "代开" in prompt
    assert "出售发票" in prompt
    assert "加微信" in prompt


def test_extract_prompt_excludes_refund_and_fee_labels_from_amount(settings) -> None:
    """The v0.8.9 change teaches the LLM to bind bare-currency to amount —
    but that must NOT spill into the refund/fee amount siblings. Verify
    the exclusion list is present and covers the five known label classes."""
    from app.services.ai_service import AIService

    service = AIService(settings)
    prompt = service._load_prompt("extract_invoice.txt")

    for label in ("退票费", "改签费", "手续费", "服务费", "退款"):
        assert label in prompt, (
            f"refund/fee label {label!r} must appear in the amount-exclusion rule"
        )


@pytest.mark.asyncio
async def test_fallback_model_chain_recovers_on_secondary_model(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import openai
    from app.services.ai_service import LLMCircuitBreaker, parse_model_chain

    assert parse_model_chain("m1", "m2, m1, , m3") == ["m1", "m2", "m3"]

    settings.LLM_MODEL = "auto/gemini-4-hydrogen"
    settings.LLM_FALLBACK_MODELS = "auto/gpt-6-luna,modelverse/deepseek-v4.1-flash-1017,auto/gemini-4-neon"
    result = make_analysis(is_invoice_related=True)

    attempted: list[str] = []

    class FlakyChat:
        async def create(self, **kwargs):
            attempted.append(kwargs["model"])
            if kwargs["model"] != "modelverse/deepseek-v4.1-flash-1017":
                req = httpx.Request("POST", "http://127.0.0.1:3425/v1/chat/completions")
                resp = httpx.Response(503, request=req)
                raise openai.InternalServerError("model_not_found", response=resp, body=None)
            return result

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: FakeRawClient([0.1, 0.2, 0.3]))
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FlakyChat())),
    )

    breaker = LLMCircuitBreaker(failure_threshold=2, cooldown_seconds=30.0)
    service = AIService(settings, circuit_breaker=breaker)
    analyzed = await service.analyze_email(db, "subj", "from@x.com", "body", [])
    assert analyzed.is_invoice_related is True
    assert attempted == [
        "auto/gemini-4-hydrogen",
        "auto/gpt-6-luna",
        "modelverse/deepseek-v4.1-flash-1017",
    ]
    assert breaker.snapshot()["state"] == "closed"


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_repeated_upstream_failures_and_recovers(
    db, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    import openai
    from app.services.ai_service import (
        LLMCircuitBreaker,
        LLMCircuitOpenError,
        LLMUnavailableError,
        _is_upstream_service_error,
        _unwrap_exception_message,
    )

    req = httpx.Request("POST", "http://127.0.0.1:3425/v1/chat/completions")
    upstream_err = openai.APIConnectionError(request=req)

    # Exercise wrapped instructor-style exception helpers
    wrapped = SimpleNamespace(last_attempt=SimpleNamespace(exception=lambda: upstream_err))
    assert _is_upstream_service_error(wrapped) is True
    assert _is_upstream_service_error(ValueError("bad schema")) is False
    assert _is_upstream_service_error(SimpleNamespace(last_attempt=SimpleNamespace(exception=lambda: None))) is False
    assert _unwrap_exception_message(wrapped)
    assert _unwrap_exception_message(SimpleNamespace(last_attempt=SimpleNamespace(exception=lambda: None)))

    class AlwaysFailChat:
        async def create(self, **kwargs):
            del kwargs
            raise upstream_err

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: FakeRawClient([0.1, 0.2, 0.3]))
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=AlwaysFailChat())),
    )

    breaker = LLMCircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
    service = AIService(settings, circuit_breaker=breaker)

    with pytest.raises(LLMUnavailableError):
        await service.analyze_email(db, "s1", "a@b.com", "b1", [])
    assert breaker.is_open is False

    with pytest.raises(LLMUnavailableError):
        await service.analyze_email(db, "s2", "a@b.com", "b2", [])
    assert breaker.is_open is True
    snap = breaker.snapshot()
    assert snap["state"] == "open"
    assert snap["consecutive_failures"] == 2
    assert snap["cooldown_remaining_seconds"] > 0

    with pytest.raises(LLMCircuitOpenError):
        await service.analyze_email(db, "s3", "a@b.com", "b3", [])

    # Record a second failure while already open -> returns False (not newly tripped)
    assert breaker.record_failure("still broken") is False
    # Recovery transitions back to healthy
    assert breaker.record_success() is True
    assert breaker.record_success() is False
    assert breaker.snapshot()["state"] == "closed"


@pytest.mark.asyncio
async def test_non_upstream_error_does_not_trip_circuit_breaker(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.ai_service import LLMCircuitBreaker, LLMUnavailableError

    class SchemaFailChat:
        async def create(self, **kwargs):
            del kwargs
            raise ValueError("schema validation failed")

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: FakeRawClient([0.1, 0.2, 0.3]))
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=SchemaFailChat())),
    )

    breaker = LLMCircuitBreaker(failure_threshold=1, cooldown_seconds=60.0)
    service = AIService(settings, circuit_breaker=breaker)
    with pytest.raises(LLMUnavailableError):
        await service.analyze_email(db, "s", "a@b.com", "b", [])
    assert breaker.is_open is False


@pytest.mark.asyncio
async def test_embed_text_uses_independent_embed_endpoint(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.LLM_EMBED_BASE_URL = "https://embed-only.invalid/v1"
    settings.LLM_EMBED_API_KEY = "embed-secret-key"

    created_clients: list[dict[str, object]] = []
    raw_client = FakeRawClient([0.1, 0.2, 0.3])

    def fake_openai(**kwargs):
        created_clients.append(kwargs)
        return raw_client

    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", fake_openai)
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions(None))),
    )

    service = AIService(settings)
    emb = await service.embed_text("invoice text", db)
    assert emb == [0.1, 0.2, 0.3]
    assert created_clients[0]["base_url"] == "https://embed-only.invalid/v1"
    assert created_clients[0]["api_key"] == "embed-secret-key"


@pytest.mark.asyncio
async def test_extract_invoice_fields_from_images_uses_vision_model_and_cache(
    db, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.LLM_VISION_MODEL = "auto/gemini-4-neon"
    extract_result = InvoiceExtract(
        is_valid_tax_invoice=True,
        invoice_no="12345678",
        buyer="Buyer Co",
        seller="Seller Co",
        amount=99.5,
        invoice_date="2026-11-26",
        invoice_type="增值税电子普通发票",
        item_summary="Cloud service",
        confidence=0.92,
    )
    chat = FakeChatCompletions(extract_result)
    monkeypatch.setattr("app.services.ai_service.AsyncOpenAI", lambda **kwargs: FakeRawClient([0.1, 0.2, 0.3]))
    monkeypatch.setattr(
        "app.services.ai_service.instructor.from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=chat)),
    )

    service = AIService(settings)
    first = await service.extract_invoice_fields_from_images(db, ["ZmFrZQ=="], context_text="from: billing@vendor.com")
    assert first.invoice_no == "12345678"
    assert chat.calls[0]["model"] == "auto/gemini-4-neon"
    user_parts = chat.calls[0]["messages"][1]["content"]
    assert user_parts[1]["image_url"]["url"] == "data:image/png;base64,ZmFrZQ=="

    # Second call hits cache
    second = await service.extract_invoice_fields_from_images(db, ["ZmFrZQ=="], context_text="from: billing@vendor.com")
    assert second.invoice_no == "12345678"
    assert len(chat.calls) == 1


