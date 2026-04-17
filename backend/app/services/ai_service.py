# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportUnusedFunction=false

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx
import instructor
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import LLMCache
from app.schemas.invoice import EmailAnalysis, InvoiceExtract
from app.services.settings_resolver import resolve_ai_settings

logger = logging.getLogger(__name__)


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
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client_cache: dict[tuple[str, str], tuple[Any, AsyncOpenAI]] = {}
        self._prompts_dir = Path(__file__).resolve().parent.parent / "prompts"

    def _settings_payload(self) -> dict[str, str | int]:
        return {
            "llm_base_url": self._settings.LLM_BASE_URL,
            "llm_api_key": self._settings.LLM_API_KEY,
            "llm_model": self._settings.LLM_MODEL,
            "llm_embed_model": self._settings.LLM_EMBED_MODEL,
            "embed_dim": self._settings.EMBED_DIM,
        }

    async def _resolve_runtime_settings(self, db: AsyncSession | None) -> dict[str, str | int]:
        if db is None:
            return self._settings_payload()
        return dict(await resolve_ai_settings(db))

    def _get_clients(self, runtime_settings: dict[str, str | int]) -> tuple[Any, AsyncOpenAI]:
        cache_key = (str(runtime_settings["llm_base_url"]), str(runtime_settings["llm_api_key"]))
        cached = self._client_cache.get(cache_key)
        if cached is not None:
            return cached

        raw_client = AsyncOpenAI(
            base_url=cache_key[0],
            api_key=cache_key[1],
            timeout=60.0,
            max_retries=2,
        )
        client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
        self._client_cache[cache_key] = (client, raw_client)
        return client, raw_client

    def _load_prompt(self, name: str) -> str:
        return (self._prompts_dir / name).read_text(encoding="utf-8")

    def _content_hash(self, prompt_type: str, content: str) -> str:
        return hashlib.sha256(f"{prompt_type}:{content}".encode("utf-8")).hexdigest()

    async def _get_cache(self, db: AsyncSession, content_hash: str) -> str | None:
        result = await db.execute(
            select(LLMCache.response_json).where(LLMCache.content_hash == content_hash)
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
            )
            db.add(cache_entry)
            await db.commit()
        except IntegrityError:
            await db.rollback()

    async def analyze_email(
        self,
        db: AsyncSession,
        subject: str,
        from_addr: str,
        body: str,
        body_links: list[str],
    ) -> EmailAnalysis:
        runtime_settings = await self._resolve_runtime_settings(db)
        client, _ = self._get_clients(runtime_settings)

        links_block = "\n".join(f"  <url>{url}</url>" for url in body_links) or "  (none)"
        content = (
            f"Subject: {subject}\n"
            f"From: {from_addr}\n"
            f"Body:\n{body[:2000]}\n\n"
            f"<links>\n{links_block}\n</links>"
        )
        cache_key = self._content_hash("analyze_email_v2", content)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            return EmailAnalysis.model_validate_json(cached)

        system_prompt = self._load_prompt("classify_email.txt")
        result = await client.chat.completions.create(
            model=str(runtime_settings["llm_model"]),
            response_model=EmailAnalysis,
            max_retries=3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        await self._set_cache(db, cache_key, "analyze_email_v2", result.model_dump_json())
        return result

    async def classify_email(self, db: AsyncSession, subject: str, body: str) -> bool:
        result = await self.analyze_email(db, subject, "", body, [])
        return result.is_invoice_related

    async def extract_invoice_fields(self, db: AsyncSession, text: str) -> InvoiceExtract:
        runtime_settings = await self._resolve_runtime_settings(db)
        client, _ = self._get_clients(runtime_settings)
        trimmed_text = text[:5000]
        cache_key = self._content_hash("extract", trimmed_text)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            return InvoiceExtract.model_validate_json(cached)

        system_prompt = self._load_prompt("extract_invoice.txt")
        result = await client.chat.completions.create(
            model=str(runtime_settings["llm_model"]),
            response_model=InvoiceExtract,
            max_retries=3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"<invoice_text>\n{trimmed_text}\n</invoice_text>"},
            ],
        )
        await self._set_cache(db, cache_key, "extract", result.model_dump_json())
        return result

    async def embed_text(self, text: str, db: AsyncSession | None = None) -> list[float]:
        runtime_settings = await self._resolve_runtime_settings(db)
        _, raw_client = self._get_clients(runtime_settings)
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
