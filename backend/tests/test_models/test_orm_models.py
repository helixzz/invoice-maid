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
