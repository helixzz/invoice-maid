from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.services.search_service import SearchService, serialize_f32, store_embedding


def test_serialize_f32_returns_bytes() -> None:
    assert isinstance(serialize_f32([0.1, 0.2]), bytes)


@pytest.mark.asyncio
async def test_search_fts_blank_query_and_filters(db, settings, create_invoice, admin_user) -> None:
    await create_invoice(invoice_no="INV-1", invoice_date=date(2024, 1, 1))
    await create_invoice(invoice_no="INV-2", invoice_date=date(2024, 1, 3))
    service = SearchService(settings)

    invoices, total = await service.search_fts(db, "", user_id=admin_user.id, date_from=date(2024, 1, 2), page=1, size=10)

    assert total == 1
    assert [invoice.invoice_no for invoice in invoices] == ["INV-2"]


@pytest.mark.asyncio
async def test_search_fts_blank_query_date_to_only(db, settings, create_invoice, admin_user) -> None:
    await create_invoice(invoice_no="INV-3", invoice_date=date(2024, 1, 1))
    await create_invoice(invoice_no="INV-4", invoice_date=date(2024, 1, 3))
    service = SearchService(settings)
    invoices, total = await service.search_fts(db, "", user_id=admin_user.id, date_to=date(2024, 1, 2), page=1, size=10)
    assert total == 1
    assert invoices[0].invoice_no == "INV-3"


@pytest.mark.asyncio
async def test_search_fts_with_matches_and_no_matches(db, settings, create_invoice, admin_user) -> None:
    await create_invoice(invoice_no="INV-11", buyer="Alpha", seller="Beta", raw_text="foo bar")
    service = SearchService(settings)

    invoices, total = await service.search_fts(db, "Alpha", user_id=admin_user.id, page=1, size=10)
    assert total == 1
    assert invoices[0].invoice_no == "INV-11"

    invoices, total = await service.search_fts(db, "missing", user_id=admin_user.id, page=1, size=10)
    assert invoices == []
    assert total == 0


@pytest.mark.asyncio
async def test_search_fts_with_query_and_date_from(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
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
    results, total = await service.search_fts(db, "Q", user_id=admin_user.id, date_from=date(2024, 1, 2))
    assert total == 1
    assert results[0].invoice_no == "INV-Q2"


@pytest.mark.asyncio
async def test_search_semantic_disabled_and_failure_paths(db, settings, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
    service = SearchService(settings)
    assert await service.search_semantic(db, [0.1]) == []
    settings.sqlite_vec_available = True

    async def broken_execute(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "execute", broken_execute)
    assert await service.search_semantic(db, [0.1]) == []


@pytest.mark.asyncio
async def test_search_semantic_and_combined_search(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
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

    results, total = await service.search(db, "A", user_id=admin_user.id, query_embedding=[0.1, 0.2, 0.3], size=5)
    assert results
    assert total >= len(results)


@pytest.mark.asyncio
async def test_search_combined_returns_fts_when_semantic_not_needed(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
    await create_invoice(invoice_no="INV-C", buyer="C")
    service = SearchService(settings)
    monkeypatch.setattr(service, "search_semantic", pytest.fail)

    results, total = await service.search(db, "C", user_id=admin_user.id, query_embedding=None)

    assert total == 1
    assert results[0].invoice_no == "INV-C"


@pytest.mark.asyncio
async def test_search_covers_remaining_merge_paths(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
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
    results, total = await service.search(db, "x", user_id=admin_user.id, query_embedding=[1.0])
    assert results == [first]
    assert total == 1

    async def fake_search_semantic_same(db, query_embedding, limit=20):
        del db, query_embedding, limit
        return [first.id]

    monkeypatch.setattr(service, "search_semantic", fake_search_semantic_same)
    results, total = await service.search(db, "x", user_id=admin_user.id, query_embedding=[1.0])
    assert results == [first]
    assert total == 1

    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "invoices.id IN" in sql:
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
    results, total = await service.search(db, "x", user_id=admin_user.id, date_from=date(2024, 1, 1), date_to=date(2024, 1, 3), query_embedding=[1.0], size=3)
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
        if "invoices.id IN" in sql:
            return ResultNoMatch()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute_none)
    results, total = await service.search(db, "x", user_id=admin_user.id, query_embedding=[1.0], size=2)
    assert results == [first]
    assert total == 1


@pytest.mark.asyncio
async def test_search_fts_date_to_path(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
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
    results, total = await service.search_fts(db, "D", user_id=admin_user.id, date_to=date(2024, 1, 2))
    assert total == 1
    assert results[0].invoice_no == "INV-D1"


@pytest.mark.asyncio
async def test_store_embedding_persists_row(db, admin_user) -> None:
    await store_embedding(db, 99, [0.1, 0.2, 0.3])
    result = await db.execute(text("SELECT rowid FROM invoice_embeddings WHERE rowid = 99"))
    assert result.scalar_one() == 99


@pytest.mark.asyncio
async def test_search_merged_results_break_on_size_limit(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
    first = await create_invoice(invoice_no="INV-LIM1", buyer="Lim1")
    second = await create_invoice(invoice_no="INV-LIM2", buyer="Lim2")
    third = await create_invoice(invoice_no="INV-LIM3", buyer="Lim3")

    settings.sqlite_vec_available = True
    service = SearchService(settings)

    async def fts_returns_one(*args, **kwargs):
        del args, kwargs
        return [first], 1

    async def semantic_returns_two_extra(db, query_embedding, limit=20):
        del db, query_embedding, limit
        return [second.id, third.id]

    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "invoices.id IN" in sql:
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

    results, _total = await service.search(db, "x", user_id=admin_user.id, query_embedding=[1.0], size=2)
    assert len(results) == 2
    assert results[0].invoice_no == "INV-LIM1"
    assert results[1].invoice_no == "INV-LIM2"


@pytest.mark.asyncio
async def test_similar_invoice_ids_prefers_sqlite_vec(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
    target = await create_invoice(invoice_no="INV-SVC-SIM-0", seller="ACME", item_summary="paper")
    other = await create_invoice(invoice_no="INV-SVC-SIM-1", seller="ACME", item_summary="paper clips")
    settings.sqlite_vec_available = True
    service = SearchService(settings)
    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "SELECT embedding FROM invoice_embeddings" in sql:
            return type("Result", (), {"scalar_one_or_none": lambda self: b"blob"})()
        if "SELECT rowid, distance" in sql and "rowid != :invoice_id" in sql:
            return type("Result", (), {"fetchall": lambda self: [(other.id, 0.01), (target.id, 0.99)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)

    result = await service.similar_invoice_ids(db, target, limit=5)

    assert result == [other.id, target.id]


@pytest.mark.asyncio
async def test_similar_invoice_ids_falls_back_to_fts(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
    target = await create_invoice(invoice_no="INV-SVC-FTS-0", seller="Fallback", item_summary="red chair")
    other = await create_invoice(invoice_no="INV-SVC-FTS-1", seller="Fallback", item_summary="red chair cushion")
    settings.sqlite_vec_available = False
    service = SearchService(settings)
    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "FROM invoices_fts" in sql and "rowid != :invoice_id" in sql:
            assert params == {"query": 'seller:"Fallback" OR item_summary:"red chair"', "invoice_id": target.id, "limit": 5}
            return type("Result", (), {"fetchall": lambda self: [(other.id,)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)

    result = await service.similar_invoice_ids(db, target, limit=5)

    assert result == [other.id]


@pytest.mark.asyncio
async def test_similar_invoice_ids_handles_sqlite_vec_failure_and_fetch_by_ids(db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch, admin_user) -> None:
    target = await create_invoice(invoice_no="INV-SVC-FAIL-0", seller="Broken", item_summary="desk")
    other = await create_invoice(invoice_no="INV-SVC-FAIL-1", seller="Broken", item_summary="desk lamp")
    settings.sqlite_vec_available = True
    service = SearchService(settings)
    original_execute = db.execute

    async def patched_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "SELECT embedding FROM invoice_embeddings" in sql:
            raise RuntimeError("boom")
        if "FROM invoices_fts" in sql and "rowid != :invoice_id" in sql:
            return type("Result", (), {"fetchall": lambda self: [(other.id,)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", patched_execute)

    result = await service.similar_invoice_ids(db, target, limit=5)
    fetched = await service.fetch_invoices_by_ids(db, [other.id, 999999], user_id=admin_user.id)

    assert result == [other.id]
    assert [invoice.invoice_no for invoice in fetched] == ["INV-SVC-FAIL-1"]


@pytest.mark.asyncio
async def test_similar_invoice_ids_falls_back_when_embedding_missing_or_knn_empty(
    db, settings, create_invoice, monkeypatch: pytest.MonkeyPatch
, admin_user) -> None:
    target = await create_invoice(invoice_no="INV-SVC-MISS-0", seller="Fallback", item_summary="mouse")
    other = await create_invoice(invoice_no="INV-SVC-MISS-1", seller="Fallback", item_summary="mouse pad")
    settings.sqlite_vec_available = True
    service = SearchService(settings)
    original_execute = db.execute

    async def missing_embedding_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "SELECT embedding FROM invoice_embeddings" in sql:
            return type("Result", (), {"scalar_one_or_none": lambda self: None})()
        if "FROM invoices_fts" in sql and "rowid != :invoice_id" in sql:
            return type("Result", (), {"fetchall": lambda self: [(other.id,)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", missing_embedding_execute)
    assert await service.similar_invoice_ids(db, target, limit=5) == [other.id]

    async def empty_knn_execute(statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "SELECT embedding FROM invoice_embeddings" in sql:
            return type("Result", (), {"scalar_one_or_none": lambda self: b"blob"})()
        if "SELECT rowid, distance" in sql and "rowid != :invoice_id" in sql:
            return type("Result", (), {"fetchall": lambda self: []})()
        if "FROM invoices_fts" in sql and "rowid != :invoice_id" in sql:
            return type("Result", (), {"fetchall": lambda self: [(other.id,)]})()
        return await original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", empty_knn_execute)
    assert await service.similar_invoice_ids(db, target, limit=5) == [other.id]


def test_build_similar_fts_query(settings) -> None:
    service = SearchService(settings)
    invoice = type("InvoiceStub", (), {"seller": 'A "Seller"', "item_summary": "paper goods"})()

    assert service._build_similar_fts_query(invoice) == 'seller:"A ""Seller""" OR item_summary:"paper goods"'


def test_build_similar_fts_query_handles_missing_terms(settings) -> None:
    service = SearchService(settings)

    seller_missing = type("InvoiceStub", (), {"seller": "   ", "item_summary": "paper goods"})()
    all_missing = type("InvoiceStub", (), {"seller": "   ", "item_summary": "   "})()

    assert service._build_similar_fts_query(seller_missing) == 'item_summary:"paper goods"'
    assert service._build_similar_fts_query(all_missing) == ""


@pytest.mark.asyncio
async def test_similar_invoice_ids_returns_empty_when_no_fallback_query(db, settings, create_invoice, admin_user) -> None:
    target = await create_invoice(invoice_no="INV-SVC-EMPTY-0", seller="   ", item_summary=None)
    settings.sqlite_vec_available = False
    service = SearchService(settings)

    assert await service.similar_invoice_ids(db, target, limit=5) == []


@pytest.mark.asyncio
async def test_fetch_invoices_by_ids_empty(settings, db, admin_user) -> None:
    service = SearchService(settings)

    assert await service.fetch_invoices_by_ids(db, [], user_id=admin_user.id) == []


@pytest.mark.asyncio
async def test_similar_invoice_ids_returns_empty_when_no_query_terms(settings, db, admin_user) -> None:
    service = SearchService(settings)
    invoice = type("InvoiceStub", (), {"id": 1, "seller": "   ", "item_summary": None})()

    assert await service.similar_invoice_ids(db, invoice, limit=5) == []
