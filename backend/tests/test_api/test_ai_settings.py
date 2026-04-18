from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
from sqlalchemy import select

import app.api.ai_settings as ai_settings_api
import app.database as database_module
import app.services.ai_service as ai_service_module
from app.models import AppSettings
from app.services.email_scanner import decrypt_password
from app.services.settings_resolver import SettingsResolver, invalidate_ai_settings_cache, resolve_ai_settings


async def test_get_ai_settings_returns_masked_values(client, auth_headers, db, settings) -> None:
    response = await client.get("/api/v1/settings/ai", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "llm_base_url": settings.LLM_BASE_URL,
        "llm_api_key_masked": "te...-key",
        "llm_model": settings.LLM_MODEL,
        "llm_embed_model": settings.LLM_EMBED_MODEL,
        "embed_dim": settings.EMBED_DIM,
        "source": "environment",
    }

    resolved = await resolve_ai_settings(db)
    assert resolved["llm_api_key"] == settings.LLM_API_KEY


def test_mask_api_key_handles_empty_and_short_values() -> None:
    assert ai_settings_api._mask_api_key("") == ""
    assert ai_settings_api._mask_api_key("abcd") == "****"


async def test_put_ai_settings_updates_model(client, auth_headers, db) -> None:
    response = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"llm_model": "gpt-4o"},
    )

    assert response.status_code == 200
    assert response.json()["llm_model"] == "gpt-4o"
    assert response.json()["source"] == "database"

    stored = await db.get(AppSettings, "llm_model")
    assert stored is not None
    assert stored.value == "gpt-4o"


async def test_put_ai_settings_updates_existing_entry(client, auth_headers, db) -> None:
    db.add(AppSettings(key="llm_model", value="old-model"))
    await db.commit()

    response = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"llm_model": "new-model"},
    )

    assert response.status_code == 200
    stored = await db.get(AppSettings, "llm_model")
    assert stored is not None
    assert stored.value == "new-model"


async def test_put_ai_settings_encrypts_api_key_and_masks_response(client, auth_headers, db, settings) -> None:
    response = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"llm_api_key": "sk-new"},
    )

    assert response.status_code == 200
    assert response.json()["llm_api_key_masked"] == "sk-...-new"

    stored = await db.get(AppSettings, "llm_api_key")
    assert stored is not None
    assert stored.value != "sk-new"
    assert decrypt_password(stored.value, settings.JWT_SECRET) == "sk-new"


async def test_put_ai_settings_rejects_invalid_base_url(client, auth_headers) -> None:
    response = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"llm_base_url": "ftp://internal.invalid"},
    )

    assert response.status_code == 422


async def test_put_ai_settings_rejects_invalid_values(client, auth_headers) -> None:
    empty_key = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"llm_api_key": ""},
    )
    non_positive_dim = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"embed_dim": 0},
    )

    assert empty_key.status_code == 422
    assert non_positive_dim.status_code == 422


async def test_put_ai_settings_resets_embedding_storage_when_embed_dim_changes(
    client, auth_headers, monkeypatch, settings
) -> None:
    reset_calls: list[tuple[int, bool]] = []

    async def fake_reset_embedding_objects(session, embed_dim: int, sqlite_vec_requested: bool) -> bool:
        del session
        reset_calls.append((embed_dim, sqlite_vec_requested))
        return False

    monkeypatch.setattr(ai_settings_api, "reset_embedding_objects", fake_reset_embedding_objects)

    response = await client.put(
        "/api/v1/settings/ai",
        headers=auth_headers,
        json={"embed_dim": 8},
    )

    assert response.status_code == 200
    assert response.json()["embed_dim"] == 8
    assert reset_calls == [(8, settings.SQLITE_VEC_ENABLED)]


async def test_get_ai_models_returns_model_ids(client, auth_headers, monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, timeout: float):
            assert timeout == 10.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url: str, headers: dict[str, str]):
            assert url == "https://llm.invalid/v1/models"
            assert headers == {"Authorization": "Bearer test-key"}
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
            )

    monkeypatch.setattr(ai_settings_api.httpx, "AsyncClient", FakeAsyncClient)

    response = await client.get("/api/v1/settings/ai/models", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"models": ["gpt-4o", "gpt-4o-mini"]}


async def test_get_ai_models_handles_upstream_failure(client, auth_headers, monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, timeout: float):
            assert timeout == 10.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def get(self, url: str, headers: dict[str, str]):
            del url, headers
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(ai_settings_api.httpx, "AsyncClient", FakeAsyncClient)

    response = await client.get("/api/v1/settings/ai/models", headers=auth_headers)

    assert response.status_code == 502
    assert response.json() == {"detail": "Failed to fetch models from upstream"}


async def test_test_ai_connection_success(client, auth_headers, monkeypatch) -> None:
    chat_calls: list[dict[str, object]] = []
    embed_calls: list[dict[str, object]] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            chat_calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    class FakeEmbeddings:
        async def create(self, **kwargs):
            embed_calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * 1536)]
            )

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str):
            assert base_url == "https://llm.invalid/v1"
            assert api_key == "test-key"
            self.chat = SimpleNamespace(completions=FakeCompletions())
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(ai_settings_api.openai, "AsyncOpenAI", FakeAsyncOpenAI)

    response = await client.post("/api/v1/settings/ai/test-connection", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["chat"]["ok"] is True
    assert body["chat"]["model"] == "test-model"
    assert body["chat"]["detail"] == "OK"
    assert "latency_ms" in body["chat"]
    assert body["embed"]["ok"] is True
    assert body["embed"]["model"] == "test-embed-model"
    assert body["embed"]["dim"] == 1536
    assert "latency_ms" in body["embed"]
    assert chat_calls == [{
        "model": "test-model",
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 5,
        "timeout": 10.0,
    }]
    assert embed_calls == [{
        "model": "test-embed-model",
        "input": "test",
        "encoding_format": "float",
        "timeout": 10.0,
    }]


async def test_test_ai_connection_dim_mismatch(client, auth_headers, monkeypatch) -> None:
    class FakeCompletions:
        async def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

    class FakeEmbeddings:
        async def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 768)])

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url, api_key):
            del base_url, api_key
            self.chat = SimpleNamespace(completions=FakeCompletions())
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(ai_settings_api.openai, "AsyncOpenAI", FakeAsyncOpenAI)

    response = await client.post("/api/v1/settings/ai/test-connection", headers=auth_headers)
    body = response.json()
    assert body["ok"] is True
    assert body["embed"]["ok"] is True
    assert body["embed"]["dim"] == 768
    assert body["embed"]["dim_mismatch"] is True
    assert "WARNING" in body["embed"]["detail"]


async def test_test_ai_connection_no_expected_dim_skips_mismatch_check(client, auth_headers, monkeypatch, db) -> None:
    db.add(AppSettings(key="embed_dim", value="0"))
    await db.commit()
    invalidate_ai_settings_cache()

    class FakeCompletions:
        async def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

    class FakeEmbeddings:
        async def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 42)])

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url, api_key):
            del base_url, api_key
            self.chat = SimpleNamespace(completions=FakeCompletions())
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(ai_settings_api.openai, "AsyncOpenAI", FakeAsyncOpenAI)
    response = await client.post("/api/v1/settings/ai/test-connection", headers=auth_headers)
    body = response.json()
    assert body["embed"]["ok"] is True
    assert body["embed"]["dim"] == 42
    assert "dim_mismatch" not in body["embed"]


async def test_test_ai_connection_failure(client, auth_headers, monkeypatch) -> None:
    class FakeCompletions:
        async def create(self, **kwargs):
            del kwargs
            raise openai.APIConnectionError(message="boom", request=None)

    class FakeEmbeddings:
        async def create(self, **kwargs):
            del kwargs
            raise openai.APIConnectionError(message="boom", request=None)

    class FakeAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str):
            del base_url, api_key
            self.chat = SimpleNamespace(completions=FakeCompletions())
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(ai_settings_api.openai, "AsyncOpenAI", FakeAsyncOpenAI)

    response = await client.post("/api/v1/settings/ai/test-connection", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["chat"]["ok"] is False
    assert body["chat"]["model"] == "test-model"
    assert body["embed"]["ok"] is False
    assert body["embed"]["model"] == "test-embed-model"


async def test_test_ai_connection_handles_each_openai_error(client, auth_headers, monkeypatch) -> None:
    import httpx

    class FakeRequest:
        pass

    mock_request = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")
    mock_response = httpx.Response(400, request=mock_request)

    errors = [
        openai.AuthenticationError(message="bad key", response=httpx.Response(401, request=mock_request), body=None),
        openai.NotFoundError(message="no model", response=httpx.Response(404, request=mock_request), body=None),
        openai.PermissionDeniedError(message="denied", response=httpx.Response(403, request=mock_request), body=None),
        openai.RateLimitError(message="rate", response=httpx.Response(429, request=mock_request), body=None),
        openai.APITimeoutError(request=mock_request),
        openai.BadRequestError(message="bad", response=mock_response, body=None),
        RuntimeError("unexpected"),
    ]
    expected_chat_error_types = ["auth", "model_not_found", "permission", "rate_limited", "timeout", "bad_request", "unknown"]
    expected_chat_ok = [False, False, False, True, False, False, False]
    expected_embed_error_types = expected_chat_error_types
    expected_embed_ok = expected_chat_ok

    for error, exp_err_type, exp_chat_ok, exp_embed_err, exp_embed_ok in zip(
        errors, expected_chat_error_types, expected_chat_ok, expected_embed_error_types, expected_embed_ok
    ):
        chat_error = error
        embed_error = error

        class FakeCompletions:
            async def create(self, **kwargs):
                del kwargs
                raise chat_error

        class FakeEmbeddings:
            async def create(self, **kwargs):
                del kwargs
                raise embed_error

        class FakeAsyncOpenAI:
            def __init__(self, *, base_url, api_key):
                del base_url, api_key
                self.chat = SimpleNamespace(completions=FakeCompletions())
                self.embeddings = FakeEmbeddings()

        monkeypatch.setattr(ai_settings_api.openai, "AsyncOpenAI", FakeAsyncOpenAI)
        response = await client.post("/api/v1/settings/ai/test-connection", headers=auth_headers)
        body = response.json()
        assert body["chat"]["error_type"] == exp_err_type, f"chat error_type for {type(error).__name__}"
        assert body["chat"]["ok"] == exp_chat_ok, f"chat ok for {type(error).__name__}"
        assert body["embed"]["error_type"] == exp_embed_err, f"embed error_type for {type(error).__name__}"
        assert body["embed"]["ok"] == exp_embed_ok, f"embed ok for {type(error).__name__}"


async def test_settings_resolver_falls_back_to_settings_for_unknown_key(db, settings) -> None:
    resolver = SettingsResolver(db)

    assert await resolver.get("JWT_SECRET") == settings.JWT_SECRET


async def test_ai_settings_requires_authentication(client) -> None:
    response = await client.get("/api/v1/settings/ai")

    assert response.status_code == 401


async def test_resolve_ai_settings_falls_back_to_environment_when_db_empty(db, settings) -> None:
    await db.execute(AppSettings.__table__.delete())
    await db.commit()
    invalidate_ai_settings_cache()

    resolved = await resolve_ai_settings(db)

    assert resolved == {
        "llm_base_url": settings.LLM_BASE_URL,
        "llm_api_key": settings.LLM_API_KEY,
        "llm_model": settings.LLM_MODEL,
        "llm_embed_model": settings.LLM_EMBED_MODEL,
        "embed_dim": settings.EMBED_DIM,
        "source": "environment",
    }


async def test_resolve_ai_settings_uses_cache_until_invalidated(db) -> None:
    await database_module.seed_ai_settings(db)
    first = await resolve_ai_settings(db)
    entry = await db.get(AppSettings, "llm_model")
    assert entry is not None
    entry.value = "cached-model"
    await db.commit()

    second = await resolve_ai_settings(db)
    assert second["llm_model"] == first["llm_model"]

    invalidate_ai_settings_cache()
    third = await resolve_ai_settings(db)
    assert third["llm_model"] == "cached-model"


async def test_seed_ai_settings_creates_rows_from_environment(db) -> None:
    await db.execute(AppSettings.__table__.delete())
    await db.commit()
    invalidate_ai_settings_cache()

    await database_module.seed_ai_settings(db)

    rows = (await db.execute(select(AppSettings).order_by(AppSettings.key))).scalars().all()
    assert [row.key for row in rows] == [
        "embed_dim",
        "llm_api_key",
        "llm_base_url",
        "llm_embed_model",
        "llm_model",
    ]
    assert rows[1].value != "test-key"


async def test_seed_ai_settings_does_not_overwrite_existing_rows(db, settings) -> None:
    await database_module.seed_ai_settings(db)
    existing = await db.get(AppSettings, "llm_model")
    assert existing is not None
    existing.value = "kept-model"
    await db.commit()

    await database_module.seed_ai_settings(db)

    stored = await db.get(AppSettings, "llm_model")
    assert stored is not None
    assert stored.value == "kept-model"

    stored_key = await db.get(AppSettings, "llm_api_key")
    assert stored_key is not None
    assert decrypt_password(stored_key.value, settings.JWT_SECRET) == settings.LLM_API_KEY


async def test_seed_ai_settings_runs_when_only_embed_dim_exists(db) -> None:
    await db.execute(AppSettings.__table__.delete())
    db.add(AppSettings(key="embed_dim", value="9"))
    await db.commit()

    await database_module.seed_ai_settings(db)

    rows = (await db.execute(select(AppSettings.key).order_by(AppSettings.key))).scalars().all()
    assert rows == [
        "embed_dim",
        "llm_api_key",
        "llm_base_url",
        "llm_embed_model",
        "llm_model",
    ]


async def test_init_db_seeds_ai_settings_from_environment(tmp_path) -> None:
    database_module._engine = None
    database_module._session_factory = None
    invalidate_ai_settings_cache()

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}"
    await database_module.init_db(db_url)

    assert database_module._session_factory is not None
    async with database_module._session_factory() as session:
        rows = (await session.execute(select(AppSettings).order_by(AppSettings.key))).scalars().all()
        assert len(rows) == 7

    assert database_module._engine is not None
    await database_module._engine.dispose()


async def test_init_db_does_not_overwrite_existing_ai_settings(tmp_path) -> None:
    database_module._engine = None
    database_module._session_factory = None
    invalidate_ai_settings_cache()

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'existing-seed.db'}"
    engine, session_factory = database_module.create_engine_and_session(db_url)
    async with engine.begin() as connection:
        await connection.run_sync(AppSettings.metadata.create_all)

    async with session_factory() as session:
        session.add(AppSettings(key="llm_model", value="preexisting-model"))
        await session.commit()

    await database_module.init_db()

    async with session_factory() as session:
        stored = await session.get(AppSettings, "llm_model")
        assert stored is not None
        assert stored.value == "preexisting-model"

    await engine.dispose()


async def test_ai_service_uses_database_overrides(db, settings, monkeypatch) -> None:
    db.add_all(
        [
            AppSettings(key="llm_base_url", value="https://override.invalid/v1"),
            AppSettings(key="llm_api_key", value=database_module.encrypt_password("override-key", settings.JWT_SECRET)),
            AppSettings(key="llm_model", value="override-model"),
        ]
    )
    await db.commit()
    invalidate_ai_settings_cache()

    result = SimpleNamespace(
        is_invoice_related=True,
        model_dump_json=lambda: '{"is_invoice_related":true,"invoice_confidence":0.9,"best_download_url":null,"url_confidence":0.0,"url_is_safelink":false,"url_kind":"none","extraction_hints":{"platform":"unknown","likely_formats":[],"invoice_type_hint":null,"visible_invoice_no":null,"visible_invoice_date":null,"visible_amount":null,"parser_notes":null},"skip_reason":null}',
    )
    calls: list[dict[str, object]] = []

    class FakeChatCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return result

    class FakeRawClient:
        def __init__(self):
            self.embeddings = SimpleNamespace(create=None)

    raw_clients: list[dict[str, object]] = []

    def fake_async_openai(**kwargs):
        raw_clients.append(kwargs)
        return FakeRawClient()

    monkeypatch.setattr(ai_service_module, "AsyncOpenAI", fake_async_openai)
    monkeypatch.setattr(
        ai_service_module.instructor,
        "from_openai",
        lambda client, mode: SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions())),
    )

    service = ai_service_module.AIService(settings)
    assert await service.classify_email(db, "subject", "body") is True
    assert raw_clients == [
        {
            "base_url": "https://override.invalid/v1",
            "api_key": "override-key",
            "timeout": 60.0,
            "max_retries": 2,
        }
    ]
    assert calls[0]["model"] == "override-model"
