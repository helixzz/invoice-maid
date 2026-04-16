# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import instructor
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import LLMCache
from app.schemas.invoice import EmailClassification, InvoiceExtract

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, settings: Settings):
        raw_client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            timeout=60.0,
            max_retries=2,
        )
        self._client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
        self._raw_client = raw_client
        self._model = settings.LLM_MODEL
        self._embed_model = settings.LLM_EMBED_MODEL
        self._embed_dim = settings.EMBED_DIM
        self._prompts_dir = Path(__file__).resolve().parent.parent / "prompts"

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
        cache_entry = LLMCache(
            content_hash=content_hash,
            prompt_type=prompt_type,
            response_json=response,
        )
        db.add(cache_entry)
        await db.commit()

    async def classify_email(self, db: AsyncSession, subject: str, body: str) -> bool:
        content = f"Subject: {subject}\n\nBody: {body[:2000]}"
        cache_key = self._content_hash("classify", content)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            result = EmailClassification.model_validate_json(cached)
            return result.is_invoice_related

        system_prompt = self._load_prompt("classify_email.txt")
        result = await self._client.chat.completions.create(
            model=self._model,
            response_model=EmailClassification,
            max_retries=2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        await self._set_cache(db, cache_key, "classify", result.model_dump_json())
        return result.is_invoice_related

    async def extract_invoice_fields(self, db: AsyncSession, text: str) -> InvoiceExtract:
        trimmed_text = text[:5000]
        cache_key = self._content_hash("extract", trimmed_text)

        cached = await self._get_cache(db, cache_key)
        if cached is not None:
            return InvoiceExtract.model_validate_json(cached)

        system_prompt = self._load_prompt("extract_invoice.txt")
        result = await self._client.chat.completions.create(
            model=self._model,
            response_model=InvoiceExtract,
            max_retries=3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"<invoice_text>\n{trimmed_text}\n</invoice_text>"},
            ],
        )
        await self._set_cache(db, cache_key, "extract", result.model_dump_json())
        return result

    async def embed_text(self, text: str) -> list[float]:
        response = await self._raw_client.embeddings.create(
            model=self._embed_model,
            input=text[:8000],
        )
        embedding = response.data[0].embedding
        if len(embedding) != self._embed_dim:
            logger.warning(
                "Embedding dimension mismatch: expected %s, got %s",
                self._embed_dim,
                len(embedding),
            )
        return embedding
