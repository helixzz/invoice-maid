from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false

import logging
import struct
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Invoice

logger = logging.getLogger(__name__)


def serialize_f32(vector: list[float]) -> bytes:
    """Pack floats into compact binary format for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)


class SearchService:
    def __init__(self, settings: Settings):
        self._settings = settings

    async def search_fts(
        self,
        db: AsyncSession,
        query: str,
        user_id: int,
        date_from: date | None = None,
        date_to: date | None = None,
        categories: list[str] | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[Invoice], int]:
        """Full-text search via FTS5 with optional date range filter and pagination.

        Tenant isolation: every branch filters ``Invoice.user_id == user_id``.
        The FTS MATCH branch first gets candidate rowids from the shared
        FTS index (FTS5 cannot filter by user_id itself), then the SQL
        hydration query constrains to the caller's rows. This is safe
        because we rely on the ORM filter, not on the FTS index, for
        authorization — the FTS index is a shared tokenizer, not a
        security boundary.

        v1.2.0 Track A: ``categories`` takes a list of invoice_category
        enum values. Empty list / None means "no filter" (backward-compat
        with v1.1.x clients)."""
        if not query.strip():
            stmt = select(Invoice).where(Invoice.user_id == user_id)
            count_stmt = select(func.count(Invoice.id)).where(Invoice.user_id == user_id)

            if date_from is not None:
                stmt = stmt.where(Invoice.invoice_date >= date_from)
                count_stmt = count_stmt.where(Invoice.invoice_date >= date_from)
            if date_to is not None:
                stmt = stmt.where(Invoice.invoice_date <= date_to)
                count_stmt = count_stmt.where(Invoice.invoice_date <= date_to)
            if categories:
                stmt = stmt.where(Invoice.invoice_category.in_(categories))
                count_stmt = count_stmt.where(Invoice.invoice_category.in_(categories))

            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
            stmt = stmt.offset(max(page - 1, 0) * size).limit(size)

            result = await db.execute(stmt)
            return list(result.scalars().all()), total

        fts_result = await db.execute(
            text(
                """
                SELECT rowid
                FROM invoices_fts
                WHERE invoices_fts MATCH :query
                ORDER BY bm25(invoices_fts), rowid DESC
                """
            ),
            {"query": query},
        )
        matching_ids = [int(row[0]) for row in fts_result.fetchall()]

        if not matching_ids:
            return [], 0

        stmt = select(Invoice).where(
            Invoice.user_id == user_id,
            Invoice.id.in_(matching_ids),
        )
        count_stmt = select(func.count(Invoice.id)).where(
            Invoice.user_id == user_id,
            Invoice.id.in_(matching_ids),
        )

        if date_from is not None:
            stmt = stmt.where(Invoice.invoice_date >= date_from)
            count_stmt = count_stmt.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Invoice.invoice_date <= date_to)
            count_stmt = count_stmt.where(Invoice.invoice_date <= date_to)
        if categories:
            stmt = stmt.where(Invoice.invoice_category.in_(categories))
            count_stmt = count_stmt.where(Invoice.invoice_category.in_(categories))

        total = (await db.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        stmt = stmt.offset(max(page - 1, 0) * size).limit(size)

        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    async def search_semantic(
        self,
        db: AsyncSession,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[int]:
        """Semantic search via sqlite-vec KNN. Returns invoice IDs
        ordered by similarity. Caller must apply its own tenant filter
        on the returned IDs — the shared vector index has no user
        concept."""
        if not self._settings.sqlite_vec_available or not query_embedding:
            return []

        try:
            vec_bytes = serialize_f32(query_embedding)
            result = await db.execute(
                text(
                    """
                    SELECT rowid, distance
                    FROM invoice_embeddings
                    WHERE embedding MATCH :query
                    ORDER BY distance
                    LIMIT :limit
                    """
                ),
                {"query": vec_bytes, "limit": limit},
            )
            return [int(row[0]) for row in result.fetchall()]
        except Exception as exc:
            logger.warning("Semantic search failed: %s", exc)
            return []

    async def search(
        self,
        db: AsyncSession,
        query: str,
        user_id: int,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        size: int = 20,
        query_embedding: list[float] | None = None,
    ) -> tuple[list[Invoice], int]:
        """Combined search: FTS5 + optional semantic fallback/augmentation,
        both tenant-scoped to ``user_id``."""
        fts_results, fts_total = await self.search_fts(
            db=db,
            query=query,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            page=page,
            size=size,
        )

        if query_embedding is None or not self._settings.sqlite_vec_available:
            return fts_results, fts_total

        semantic_ids = await self.search_semantic(db, query_embedding, limit=size * 2)
        if not semantic_ids:
            return fts_results, fts_total

        existing_ids = {invoice.id for invoice in fts_results}
        additional_ids = [invoice_id for invoice_id in semantic_ids if invoice_id not in existing_ids]
        if not additional_ids:
            return fts_results, fts_total

        additional_stmt = select(Invoice).where(
            Invoice.user_id == user_id,
            Invoice.id.in_(additional_ids[:size]),
        )
        if date_from is not None:
            additional_stmt = additional_stmt.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            additional_stmt = additional_stmt.where(Invoice.invoice_date <= date_to)

        additional_result = await db.execute(additional_stmt)
        additional_invoices = list(additional_result.scalars().all())
        additional_by_id = {invoice.id: invoice for invoice in additional_invoices}

        merged_results = list(fts_results)
        for invoice_id in additional_ids:
            invoice = additional_by_id.get(invoice_id)
            if invoice is not None:
                merged_results.append(invoice)
            if len(merged_results) >= size:
                break

        return merged_results, max(fts_total, len(merged_results))

    async def fetch_invoices_by_ids(
        self, db: AsyncSession, invoice_ids: list[int], user_id: int
    ) -> list[Invoice]:
        if not invoice_ids:
            return []

        result = await db.execute(
            select(Invoice).where(
                Invoice.user_id == user_id,
                Invoice.id.in_(invoice_ids),
            )
        )
        invoices_by_id = {invoice.id: invoice for invoice in result.scalars().all()}
        return [invoices_by_id[invoice_id] for invoice_id in invoice_ids if invoice_id in invoices_by_id]

    async def similar_invoice_ids(self, db: AsyncSession, invoice: Invoice, limit: int = 5) -> list[int]:
        """Find invoices similar to ``invoice``. The caller is
        responsible for having already resolved ``invoice`` through a
        tenant-scoped query; the returned IDs are filtered back to the
        same ``invoice.user_id`` via ``fetch_invoices_by_ids``."""
        if self._settings.sqlite_vec_available:
            try:
                embedding_result = await db.execute(
                    text("SELECT embedding FROM invoice_embeddings WHERE rowid = :invoice_id"),
                    {"invoice_id": invoice.id},
                )
                embedding = embedding_result.scalar_one_or_none()
                if embedding is not None:
                    result = await db.execute(
                        text(
                            """
                            SELECT rowid, distance
                            FROM invoice_embeddings
                            WHERE embedding MATCH :query AND rowid != :invoice_id
                            ORDER BY distance, rowid DESC
                            LIMIT :limit
                            """
                        ),
                        {"query": embedding, "invoice_id": invoice.id, "limit": limit},
                    )
                    similar_ids = [int(row[0]) for row in result.fetchall()]
                    if similar_ids:
                        return similar_ids
            except Exception as exc:
                logger.warning("Similar invoice semantic search failed: %s", exc)

        query = self._build_similar_fts_query(invoice)
        if not query:
            return []

        result = await db.execute(
            text(
                """
                SELECT rowid
                FROM invoices_fts
                WHERE invoices_fts MATCH :query AND rowid != :invoice_id
                ORDER BY bm25(invoices_fts), rowid DESC
                LIMIT :limit
                """
            ),
            {"query": query, "invoice_id": invoice.id, "limit": limit},
        )
        return [int(row[0]) for row in result.fetchall()]

    def _build_similar_fts_query(self, invoice: Invoice) -> str:
        terms: list[str] = []
        if invoice.seller.strip():
            terms.append(f'seller:{self._quote_fts_value(invoice.seller.strip())}')
        if invoice.item_summary and invoice.item_summary.strip():
            terms.append(f'item_summary:{self._quote_fts_value(invoice.item_summary.strip())}')
        return " OR ".join(terms)

    def _quote_fts_value(self, value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"'


async def store_embedding(db: AsyncSession, invoice_id: int, embedding: list[float]) -> None:
    """Store invoice embedding in sqlite-vec virtual table."""
    vec_bytes = serialize_f32(embedding)
    await db.execute(
        text("INSERT OR REPLACE INTO invoice_embeddings(rowid, embedding) VALUES (:id, :embedding)"),
        {"id": invoice_id, "embedding": vec_bytes},
    )
    await db.commit()
