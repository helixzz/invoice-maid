from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.services.search_service import SearchService, serialize_f32, store_embedding


def test_serialize_f32_returns_bytes() -> None:
    assert isinstance(serialize_f32([0.1, 0.2]), bytes)


@pytest.mark.asyncio
async def test_search_fts_blank_query_and_filters(db, settings, create_invoice) -> None:
    await create_invoice(invoice_no="INV-1", invoice_date=date(2024, 1, 1))
    await create_invoice(invoice_no="INV-2", invoice_date=date(2024, 1, 3))
    service = SearchService(settings)

    invoices, total = await service.search_fts(db, "", date_from=date(2024, 1, 2), page=1, size=10)

    assert total == 1
    assert [invoice.invoice_no for invoice in invoices] == ["INV-2"]


@pytest.mark.asyncio
async def test_search_fts_blank_query_date_to_only(db, settings, create_invoice) -> None:
    await create_invoice(invoice_no="INV-3", invoice_date=date(2024, 1, 1))
    await create_invoice(invoice_no="INV-4", invoice_date=date(2024, 1, 3))
    service = SearchService(settings)
    invoices, total = await service.search_fts(db, "", date_to=date(2024, 1, 2), page=1, size=10)
    assert total == 1
    assert invoices[0].invoice_no == "INV-3"


@pytest.mark.asyncio
async def test_search_fts_with_matches_and_no_matches(db, settings, create_invoice) -> None:
    await create_invoice(invoice_no="INV-11", buyer="Alpha", seller="Beta", raw_text="foo bar")
    service = SearchService(settings)

    invoices, total = await service.search_fts(db, "Alpha", page=1, size=10)
    assert total == 1
    assert invoices[0].invoice_no == "INV-11"

    invoices, total = await service.search_fts(db, "missing", page=1, size=10)
    assert invoices == []
    assert total == 0


@pytest.mark.asyncio
async def test_search_fts_with_query_and_date_from(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    first = await create_invoice(invoice_no="INV-Q1", invoice_date=date(2024, 1, 1), buyer="Q1")
    second = await create_invoice(invoice_no="INV-Q2", invoice_date=date(2024, 1, 3), buyer="Q2")
    service = SearchService(settings)
    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "FROM invoices_fts" in sql:
            return type("Result", (), {"fetchall": lambda self: [(first.id,), (second.id,)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)
    results, total = await service.search_fts(db, "Q", date_from=date(2024, 1, 2))
    assert total == 1
    assert results[0].invoice_no == "INV-Q2"


@pytest.mark.asyncio
async def test_search_semantic_disabled_and_failure_paths(db, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    service = SearchService(settings)
    assert await service.search_semantic(db, [0.1]) == []
    settings.sqlite_vec_available = True

    async def broken_execute(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "execute", broken_execute)
    assert await service.search_semantic(db, [0.1]) == []


@pytest.mark.asyncio
async def test_search_semantic_and_combined_search(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    first = await create_invoice(invoice_no="INV-A", buyer="A")
    second = await create_invoice(invoice_no="INV-B", buyer="B")
    await store_embedding(db, first.id, [0.1, 0.2, 0.3])
    await store_embedding(db, second.id, [0.3, 0.2, 0.1])
    settings.sqlite_vec_available = True
    service = SearchService(settings)

    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "SELECT rowid, distance" in sql and "invoice_embeddings" in sql:
            return type(
                "Result",
                (),
                {"fetchall": lambda self: [(first.id, 0.1), (second.id, 0.2)]},
            )()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)

    ids = await service.search_semantic(db, [0.1, 0.2, 0.3], limit=5)
    assert ids

    results, total = await service.search(db, "A", query_embedding=[0.1, 0.2, 0.3], size=5)
    assert results
    assert total >= len(results)


@pytest.mark.asyncio
async def test_search_combined_returns_fts_when_semantic_not_needed(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    await create_invoice(invoice_no="INV-C", buyer="C")
    service = SearchService(settings)
    monkeypatch.setattr(service, "search_semantic", pytest.fail)

    results, total = await service.search(db, "C", query_embedding=None)

    assert total == 1
    assert results[0].invoice_no == "INV-C"


@pytest.mark.asyncio
async def test_search_covers_remaining_merge_paths(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    first = await create_invoice(invoice_no="INV-X", buyer="X", invoice_date=date(2024, 1, 1))
    second = await create_invoice(invoice_no="INV-Y", buyer="Y", invoice_date=date(2024, 1, 2))
    service = SearchService(settings)
    settings.sqlite_vec_available = True

    async def fake_search_fts(**kwargs):
        del kwargs
        return [first], 1

    async def fake_search_semantic_empty(db, query_embedding, limit=20):
        del db, query_embedding, limit
        return []

    monkeypatch.setattr(service, "search_fts", fake_search_fts)
    monkeypatch.setattr(service, "search_semantic", fake_search_semantic_empty)
    results, total = await service.search(db, "x", query_embedding=[1.0])
    assert results == [first]
    assert total == 1

    async def fake_search_semantic_same(db, query_embedding, limit=20):
        del db, query_embedding, limit
        return [first.id]

    monkeypatch.setattr(service, "search_semantic", fake_search_semantic_same)
    results, total = await service.search(db, "x", query_embedding=[1.0])
    assert results == [first]
    assert total == 1

    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "WHERE invoices.id IN" in sql:
            class Result:
                def scalars(self_inner):
                    class Scalars:
                        def all(self):
                            return [second]

                    return Scalars()

            return Result()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)

    async def fake_search_semantic_more(db, query_embedding, limit=20):
        del db, query_embedding, limit
        return [first.id, 999, second.id]

    monkeypatch.setattr(service, "search_semantic", fake_search_semantic_more)
    results, total = await service.search(db, "x", date_from=date(2024, 1, 1), date_to=date(2024, 1, 3), query_embedding=[1.0], size=3)
    assert results == [first, second]
    assert total == 2

    class ResultNoMatch:
        def scalars(self):
            class Scalars:
                def all(self):
                    return []

            return Scalars()

    async def patched_execute_none(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "WHERE invoices.id IN" in sql:
            return ResultNoMatch()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute_none)
    results, total = await service.search(db, "x", query_embedding=[1.0], size=2)
    assert results == [first]
    assert total == 1


@pytest.mark.asyncio
async def test_search_fts_date_to_path(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    first = await create_invoice(invoice_no="INV-D1", invoice_date=date(2024, 1, 1), buyer="D1")
    second = await create_invoice(invoice_no="INV-D2", invoice_date=date(2024, 1, 3), buyer="D2")
    service = SearchService(settings)
    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "FROM invoices_fts" in sql:
            return type("Result", (), {"fetchall": lambda self: [(first.id,), (second.id,)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)
    results, total = await service.search_fts(db, "D", date_to=date(2024, 1, 2))
    assert total == 1
    assert results[0].invoice_no == "INV-D1"


@pytest.mark.asyncio
async def test_store_embedding_persists_row(db) -> None:
    await store_embedding(db, 99, [0.1, 0.2, 0.3])
    result = await db.execute(text("SELECT rowid FROM invoice_embeddings WHERE rowid = 99"))
    assert result.scalar_one() == 99


@pytest.mark.asyncio
async def test_search_merged_results_break_on_size_limit(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch) -> None:
    first = await create_invoice(invoice_no="INV-LIM1", buyer="Lim1")
    second = await create_invoice(invoice_no="INV-LIM2", buyer="Lim2")
    third = await create_invoice(invoice_no="INV-LIM3", buyer="Lim3")

    settings.sqlite_vec_available = True
    service = SearchService(settings)

    async def fts_returns_one(*args, **kwargs):
        return [first], 1

    async def semantic_returns_two_extra(db, query_embedding, limit=20):
        return [second.id, third.id]

    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "WHERE invoices.id IN" in sql:
            class Result:
                def scalars(self_inner):
                    class Scalars:
                        def all(self):
                            return [second, third]
                    return Scalars()
            return Result()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(service, "search_fts", fts_returns_one)
    monkeypatch.setattr(service, "search_semantic", semantic_returns_two_extra)
    monkeypatch.setattr(db, "execute", patched_execute)

    results, total = await service.search(db, "x", query_embedding=[1.0], size=2)
    assert len(results) == 2
    assert results[0].invoice_no == "INV-LIM1"
    assert results[1].invoice_no == "INV-LIM2"
