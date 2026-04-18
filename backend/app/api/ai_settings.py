from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import openai
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, reset_embedding_objects
from app.deps import CurrentUser
from app.models import AppSettings
from app.schemas.ai_settings import AISettingsResponse, AISettingsUpdate, ModelListResponse
from app.services.email_scanner import encrypt_password
from app.services.settings_resolver import (
    SettingsResolver,
    invalidate_ai_settings_cache,
    resolve_ai_settings,
)

router = APIRouter(prefix="/settings/ai", tags=["settings"])


def _mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    if value.startswith("sk-"):
        return f"sk-...{value[-4:]}"
    return f"{value[:2]}...{value[-4:]}"


def _serialize_response(payload: dict[str, object]) -> AISettingsResponse:
    return AISettingsResponse(
        llm_base_url=str(payload["llm_base_url"]),
        llm_api_key_masked=_mask_api_key(str(payload["llm_api_key"])),
        llm_model=str(payload["llm_model"]),
        llm_embed_model=str(payload["llm_embed_model"]),
        embed_dim=int(payload["embed_dim"]),
        source=str(payload["source"]),
    )


@router.get("", response_model=AISettingsResponse)
async def get_ai_settings(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> AISettingsResponse:
    return _serialize_response(await resolve_ai_settings(db))


@router.put("", response_model=AISettingsResponse)
async def update_ai_settings(
    payload: AISettingsUpdate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> AISettingsResponse:
    updates = payload.model_dump(exclude_none=True)
    settings = get_settings()
    current_settings = await resolve_ai_settings(db)

    for key, value in updates.items():
        stored_value = str(value)
        if key == "llm_api_key":
            stored_value = encrypt_password(stored_value, settings.JWT_SECRET)

        entry = await db.get(AppSettings, key)
        if entry is None:
            db.add(AppSettings(key=key, value=stored_value))
            continue
        entry.value = stored_value

    await db.commit()
    if "embed_dim" in updates and int(updates["embed_dim"]) != int(current_settings["embed_dim"]):
        settings.sqlite_vec_available = await reset_embedding_objects(
            db,
            embed_dim=int(updates["embed_dim"]),
            sqlite_vec_requested=settings.SQLITE_VEC_ENABLED,
        )
    invalidate_ai_settings_cache()
    return _serialize_response(await resolve_ai_settings(db))


@router.get("/models", response_model=ModelListResponse)
async def list_ai_models(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ModelListResponse:
    settings = await resolve_ai_settings(db)
    base_url = str(settings["llm_base_url"]).rstrip("/")
    headers = {"Authorization": f"Bearer {settings['llm_api_key']}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
        models = [str(item["id"]) for item in response.json().get("data", []) if item.get("id")]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch models from upstream",
        ) from exc

    return ModelListResponse(models=models)


async def _test_chat_model(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
    t0 = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
            timeout=10.0,
        )
        return {
            "ok": True,
            "model": model,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "detail": (response.choices[0].message.content or "")[:200],
        }
    except openai.AuthenticationError:
        return {"ok": False, "model": model, "error_type": "auth", "detail": "Invalid API key (401)"}
    except openai.NotFoundError:
        return {"ok": False, "model": model, "error_type": "model_not_found", "detail": f"Chat model '{model}' not found (404)"}
    except openai.PermissionDeniedError:
        return {"ok": False, "model": model, "error_type": "permission", "detail": "API key lacks access to this model (403)"}
    except openai.RateLimitError:
        return {"ok": True, "model": model, "error_type": "rate_limited", "detail": "Rate limited (429) — endpoint reachable"}
    except openai.APITimeoutError:
        return {"ok": False, "model": model, "error_type": "timeout", "detail": "Timed out after 10s"}
    except openai.APIConnectionError as exc:
        return {"ok": False, "model": model, "error_type": "connection", "detail": f"Cannot reach endpoint: {exc.__cause__ or exc}"[:500]}
    except openai.BadRequestError as exc:
        return {"ok": False, "model": model, "error_type": "bad_request", "detail": f"Provider rejected request (400): {str(exc)[:200]}"}
    except Exception as exc:
        return {"ok": False, "model": model, "error_type": "unknown", "detail": str(exc)[:500]}


async def _test_embed_model(
    base_url: str, api_key: str, model: str, expected_dim: int | None = None
) -> dict[str, Any]:
    client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
    t0 = time.monotonic()
    try:
        response = await client.embeddings.create(
            model=model,
            input="test",
            encoding_format="float",
            timeout=10.0,
        )
        embedding = response.data[0].embedding
        dim = len(embedding)
        latency_ms = int((time.monotonic() - t0) * 1000)
        result: dict[str, Any] = {
            "ok": True,
            "model": model,
            "dim": dim,
            "latency_ms": latency_ms,
            "detail": f"{dim}-dimensional embedding returned",
        }
        if expected_dim is not None and dim != expected_dim:
            result["dim_mismatch"] = True
            result["detail"] = (
                f"WARNING: got {dim} dims, config expects {expected_dim}. "
                "Update embed_dim to match, or semantic search will fail."
            )
        return result
    except openai.AuthenticationError:
        return {"ok": False, "model": model, "error_type": "auth", "detail": "Invalid API key (401)"}
    except openai.NotFoundError:
        return {"ok": False, "model": model, "error_type": "model_not_found", "detail": f"Embedding model '{model}' not found (404)"}
    except openai.PermissionDeniedError:
        return {"ok": False, "model": model, "error_type": "permission", "detail": "API key lacks access to this model (403)"}
    except openai.RateLimitError:
        return {"ok": True, "model": model, "error_type": "rate_limited", "detail": "Rate limited (429) — endpoint reachable"}
    except openai.APITimeoutError:
        return {"ok": False, "model": model, "error_type": "timeout", "detail": "Timed out after 10s"}
    except openai.APIConnectionError as exc:
        return {"ok": False, "model": model, "error_type": "connection", "detail": f"Cannot reach endpoint: {exc.__cause__ or exc}"[:500]}
    except openai.BadRequestError as exc:
        return {"ok": False, "model": model, "error_type": "bad_request", "detail": f"Provider rejected request (400): {str(exc)[:200]}"}
    except Exception as exc:
        return {"ok": False, "model": model, "error_type": "unknown", "detail": str(exc)[:500]}


@router.post("/test-connection")
async def test_ai_connection(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    resolver = SettingsResolver(db)
    base_url = await resolver.get("LLM_BASE_URL")
    api_key = await resolver.get("LLM_API_KEY")
    chat_model = await resolver.get("LLM_MODEL")
    embed_model = await resolver.get("LLM_EMBED_MODEL")
    embed_dim_value = await resolver.get("EMBED_DIM")
    expected_dim = int(embed_dim_value) if embed_dim_value else None

    chat_result, embed_result = await asyncio.gather(
        _test_chat_model(base_url, api_key, chat_model),
        _test_embed_model(base_url, api_key, embed_model, expected_dim),
    )
    return {
        "ok": bool(chat_result["ok"] and embed_result["ok"]),
        "chat": chat_result,
        "embed": embed_result,
    }
