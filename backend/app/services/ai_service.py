# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportUnusedFunction=false

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import instructor
import openai
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import LLMCache
from app.schemas.invoice import EmailAnalysis, InvoiceExtract
from app.services.settings_resolver import resolve_ai_settings

logger = logging.getLogger(__name__)

CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0


class LLMUnavailableError(RuntimeError):
    """Raised when every model in the configured fallback chain fails."""


class LLMCircuitOpenError(LLMUnavailableError):
    """Raised immediately while the LLM circuit breaker is open."""


class LLMCircuitBreaker:
    """In-process circuit breaker protecting scans against sustained LLM outages."""

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
        cooldown_seconds: float = CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_until = 0.0
        self._last_error: str | None = None

    @property
    def is_open(self) -> bool:
        return self._opened_until > time.monotonic()

    def check(self) -> None:
        if self.is_open:
            remaining = max(0.0, self._opened_until - time.monotonic())
            raise LLMCircuitOpenError(
                f"LLM circuit breaker open ({remaining:.0f}s remaining): {self._last_error or 'upstream unavailable'}"
            )

    def record_success(self) -> bool:
        """Reset failure counters. Returns True if transitioning from degraded/open back to healthy."""
        was_tripped = self._consecutive_failures >= self._failure_threshold or self._opened_until > 0.0
        self._consecutive_failures = 0
        self._opened_until = 0.0
        self._last_error = None
        return was_tripped

    def record_failure(self, error: str) -> bool:
        """Record a chain-level failure. Returns True if this call just tripped the breaker open."""
        self._consecutive_failures += 1
        self._last_error = error[:500]
        if self._consecutive_failures >= self._failure_threshold:
            already_open = self.is_open
            self._opened_until = time.monotonic() + self._cooldown_seconds
            return not already_open
        return False

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._opened_until = 0.0
        self._last_error = None

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        is_open = self._opened_until > now
        return {
            "state": "open" if is_open else "closed",
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "cooldown_remaining_seconds": round(max(0.0, self._opened_until - now), 1) if is_open else 0.0,
        }


_llm_circuit_breaker = LLMCircuitBreaker()


def get_llm_circuit_breaker() -> LLMCircuitBreaker:
    return _llm_circuit_breaker


def reset_llm_circuit_breaker() -> None:
    _llm_circuit_breaker.reset()


def parse_model_chain(primary: str, fallback_models: str = "") -> list[str]:
    """Build an ordered, deduplicated model chain starting with ``primary``."""
    raw = [primary, *(fallback_models.split(",") if fallback_models else [])]
    chain: list[str] = []
    for item in raw:
        model = item.strip()
        if model and model not in chain:
            chain.append(model)
    return chain


def _unwrap_exception_message(exc: Exception) -> str:
    last_exc = getattr(exc, "last_attempt", None)
    if last_exc is not None and hasattr(last_exc, "exception"):
        inner = last_exc.exception()
        if inner is not None:
            return str(inner)[:500]
    return str(exc)[:500]


def _is_upstream_service_error(exc: Exception) -> bool:
    """Return True when the error indicates an upstream/provider failure rather than a prompt/schema issue."""
    if isinstance(exc, (openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError)):
        return True
    last_attempt = getattr(exc, "last_attempt", None)
    if last_attempt is not None and hasattr(last_attempt, "exception"):
        inner = last_attempt.exception()
        if isinstance(inner, (openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError)):
            return True
    return False


async def _resolve_safelink(url: str) -> str:
    """Follow an Outlook SafeLink redirect to get the real URL. Returns original on failure."""
    if "safelinks.protection.outlook.com" not in url:
        return url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.head(url)
            return str(response.url)
    except Exception:
        return url


class AIService:
    def __init__(self, settings: Settings, circuit_breaker: LLMCircuitBreaker | None = None):
        self._settings = settings
        self._client_cache: dict[tuple[str, str], tuple[Any, AsyncOpenAI]] = {}
        self._prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        self._circuit_breaker = circuit_breaker or get_llm_circuit_breaker()

    def _settings_payload(self) -> dict[str, str | int]:
        return {
            "llm_base_url": self._settings.LLM_BASE_URL,
            "llm_api_key": self._settings.LLM_API_KEY,
            "llm_model": self._settings.LLM_MODEL,
            "llm_fallback_models": self._settings.LLM_FALLBACK_MODELS,
            "llm_vision_model": self._settings.LLM_VISION_MODEL,
            "llm_embed_base_url": self._settings.LLM_EMBED_BASE_URL,
            "llm_embed_api_key": self._settings.LLM_EMBED_API_KEY,
            "llm_embed_model": self._settings.LLM_EMBED_MODEL,
            "embed_dim": self._settings.EMBED_DIM,
        }

    async def _resolve_runtime_settings(self, db: AsyncSession | None) -> dict[str, str | int]:
        if db is None:
            return self._settings_payload()
        return dict(await resolve_ai_settings(db))

    def _get_clients(
        self,
        runtime_settings: dict[str, str | int],
        *,
        for_embedding: bool = False,
    ) -> tuple[Any, AsyncOpenAI]:
        if for_embedding:
            base_url = str(runtime_settings.get("llm_embed_base_url") or runtime_settings["llm_base_url"])
            api_key = str(runtime_settings.get("llm_embed_api_key") or runtime_settings["llm_api_key"])
        else:
            base_url = str(runtime_settings["llm_base_url"])
            api_key = str(runtime_settings["llm_api_key"])

        cache_key = (base_url, api_key)
        cached = self._client_cache.get(cache_key)
        if cached is not None:
            return cached

        raw_client = AsyncOpenAI(
            base_url=cache_key[0],
            api_key=cache_key[1],
            timeout=60.0,
            max_retries=0,
        )
        client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
        self._client_cache[cache_key] = (client, raw_client)
        return client, raw_client

    def _load_prompt(self, name: str) -> str:
        return (self._prompts_dir / name).read_text(encoding="utf-8")

    def _content_hash(self, prompt_type: str, content: str) -> str:
        return hashlib.sha256(f"{prompt_type}:{content}".encode("utf-8")).hexdigest()

    def _cache_expiry(self, prompt_type: str) -> datetime:
        now = datetime.now(timezone.utc)
        if prompt_type in ("classify", "analyze_email_v3"):
            return now + timedelta(days=7)
        return now + timedelta(days=365)

    async def _get_cache(self, db: AsyncSession, content_hash: str) -> str | None:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(LLMCache.response_json).where(
                LLMCache.content_hash == content_hash,
                (LLMCache.expires_at.is_(None)) | (LLMCache.expires_at > now),
            )
        )
        return result.scalar_one_or_none()

    async def _set_cache(
        self,
        db: AsyncSession,
        content_hash: str,
        prompt_type: str,
        response: str,
    ) -> None:
        try:
            cache_entry = LLMCache(
                content_hash=content_hash,
                prompt_type=prompt_type,
                response_json=response,
                expires_at=self._cache_expiry(prompt_type),
            )
            db.add(cache_entry)
            await db.commit()
        except IntegrityError:
            await db.rollback()

    async def _create_structured_with_fallback(
        self,
        *,
        runtime_settings: dict[str, str | int],
        response_model: Any,
        messages: list[dict[str, Any]],
        preferred_first_model: str = "",
    ) -> Any:
        self._circuit_breaker.check()
        client, _ = self._get_clients(runtime_settings)

        chain = parse_model_chain(
            str(runtime_settings["llm_model"]),
            str(runtime_settings.get("llm_fallback_models", "")),
        )
        if preferred_first_model:
            preferred = preferred_first_model.strip()
            chain = [preferred, *[m for m in chain if m != preferred]]

        errors: list[str] = []
        last_exc: Exception | None = None
        for idx, model in enumerate(chain):
            try:
                result = await client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    max_retries=1 if _len_gt_one(chain) else 2,
                    messages=messages,
                )
                self._circuit_breaker.record_success()
                if idx > 0:
                    logger.info("LLM call succeeded via fallback model %s (attempt %d/%d)", model, idx + 1, len(chain))
                return result
            except Exception as exc:
                last_exc = exc
                reason = _unwrap_exception_message(exc)
                errors.append(f"{model}: {reason}")
                if idx + 1 < len(chain):
                    logger.warning(
                        "LLM model %s failed (%s); falling back to %s",
                        model,
                        reason[:200],
                        chain[idx + 1],
                    )
                else:
                    logger.warning("LLM model %s failed (%s); no fallback models left", model, reason[:200])

        summary = "; ".join(errors)[:500]
        if last_exc is not None and _is_upstream_service_error(last_exc):
            self._circuit_breaker.record_failure(summary)
        raise LLMUnavailableError(f"All LLM models failed: {summary}") from last_exc

    async def analyze_email(
        self,
        db: AsyncSession,
        subject: str,
        from_addr: str,
        body: str,
        body_links: list[str],
    ) -> EmailAnalysis:
        runtime_settings = await self._resolve_runtime_settings(db)

        links_block = "\n".join(f"  <url>{url}</url>" for url in body_links) or "  (none)"
        content = (
            f"Subject: {subject}\n"
            f"From: {from_addr}\n"
            f"Body:\n{body[:2000]}\n\n"
            f"<links>\n{links_block}\n</links>"
        )
        cache_key = self._content_hash("analyze_email_v3", content)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            return EmailAnalysis.model_validate_json(cached)

        system_prompt = self._load_prompt("classify_email.txt")
        result = await self._create_structured_with_fallback(
            runtime_settings=runtime_settings,
            response_model=EmailAnalysis,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        await self._set_cache(db, cache_key, "analyze_email_v3", result.model_dump_json())
        return result

    async def classify_email(self, db: AsyncSession, subject: str, body: str) -> bool:
        result = await self.analyze_email(db, subject, "", body, [])
        return result.is_invoice_related

    async def extract_invoice_fields(self, db: AsyncSession, text: str) -> InvoiceExtract:
        runtime_settings = await self._resolve_runtime_settings(db)
        trimmed_text = text[:5000]
        cache_key = self._content_hash("extract", trimmed_text)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            return InvoiceExtract.model_validate_json(cached)

        system_prompt = self._load_prompt("extract_invoice.txt")
        result = await self._create_structured_with_fallback(
            runtime_settings=runtime_settings,
            response_model=InvoiceExtract,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"<invoice_text>\n{trimmed_text}\n</invoice_text>"},
            ],
        )
        await self._set_cache(db, cache_key, "extract", result.model_dump_json())
        return result

    async def extract_invoice_fields_from_images(
        self,
        db: AsyncSession,
        images_b64: list[str],
        context_text: str = "",
    ) -> InvoiceExtract:
        """Multimodal vision fallback for image-only or unreadable PDF invoices."""
        runtime_settings = await self._resolve_runtime_settings(db)
        digest_input = f"{context_text[:1000]}|" + "|".join(
            hashlib.sha256(img.encode("ascii", errors="ignore")).hexdigest() for img in images_b64
        )
        cache_key = self._content_hash("extract_vision", digest_input)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            return InvoiceExtract.model_validate_json(cached)

        system_prompt = self._load_prompt("extract_invoice.txt")
        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "The attached image(s) are rendered pages of an invoice PDF whose text layer "
                    "could not be extracted. Read all visible fields accurately and return structured JSON."
                    + (f"\n\n<email_context>\n{context_text[:1500]}\n</email_context>" if context_text else "")
                ),
            }
        ]
        for img_b64 in images_b64:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                }
            )

        vision_model = str(runtime_settings.get("llm_vision_model", "")).strip()
        result = await self._create_structured_with_fallback(
            runtime_settings=runtime_settings,
            response_model=InvoiceExtract,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            preferred_first_model=vision_model,
        )
        await self._set_cache(db, cache_key, "extract_vision", result.model_dump_json())
        return result

    async def embed_text(self, text: str, db: AsyncSession | None = None) -> list[float]:
        runtime_settings = await self._resolve_runtime_settings(db)
        _, raw_client = self._get_clients(runtime_settings, for_embedding=True)
        response = await raw_client.embeddings.create(
            model=str(runtime_settings["llm_embed_model"]),
            input=text[:8000],
        )
        embedding = response.data[0].embedding
        expected_dim = int(runtime_settings["embed_dim"])
        if len(embedding) != expected_dim:
            logger.warning(
                "Embedding dimension mismatch: expected %s, got %s",
                expected_dim,
                len(embedding),
            )
        return embedding


def _len_gt_one(items: list[str]) -> bool:
    return len(items) > 1
