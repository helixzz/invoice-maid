from __future__ import annotations

from app.models import (
    Base,
    CorrectionLog,
    EmailAccount,
    ExtractionLog,
    Invoice,
    LLMCache,
    SavedView,
    ScanLog,
    WebhookLog,
)


TENANT_MODELS = (
    Invoice,
    EmailAccount,
    ScanLog,
    ExtractionLog,
    CorrectionLog,
    SavedView,
    WebhookLog,
)


def test_models_exports_and_relationships() -> None:
    assert Base.__name__ == "Base"
    assert CorrectionLog.__tablename__ == "correction_logs"
    assert EmailAccount.__tablename__ == "email_accounts"
    assert ExtractionLog.__tablename__ == "extraction_logs"
    assert Invoice.__tablename__ == "invoices"
    assert LLMCache.__tablename__ == "llm_cache"
    assert SavedView.__tablename__ == "saved_views"
    assert ScanLog.__tablename__ == "scan_logs"
    assert WebhookLog.__tablename__ == "webhook_logs"
    assert "classification_tier" in ExtractionLog.__table__.columns


def test_tenant_models_have_nullable_user_id_fk_to_users() -> None:
    for model in TENANT_MODELS:
        column = model.__table__.columns.get("user_id")
        assert column is not None, f"{model.__name__} is missing user_id column"
        assert column.nullable is True, (
            f"{model.__name__}.user_id must be nullable in Phase 2 "
            "(Phase 3 tightens to NOT NULL)"
        )
        assert column.index is True, (
            f"{model.__name__}.user_id must be indexed for per-tenant filtering"
        )
        fks = list(column.foreign_keys)
        assert len(fks) == 1, (
            f"{model.__name__}.user_id must have exactly one foreign key"
        )
        fk = fks[0]
        assert fk.column.table.name == "users", (
            f"{model.__name__}.user_id must reference users table"
        )
        assert fk.column.name == "id", (
            f"{model.__name__}.user_id must reference users.id"
        )
        assert fk.ondelete == "CASCADE", (
            f"{model.__name__}.user_id must cascade on user deletion"
        )


def test_non_tenant_tables_do_not_have_user_id() -> None:
    for model in (LLMCache,):
        assert "user_id" not in model.__table__.columns, (
            f"{model.__name__} is instance-scoped by design and must NOT have "
            "a user_id column in Phase 2. See migration 0011 docstring."
        )
