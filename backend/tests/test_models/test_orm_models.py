from __future__ import annotations

from app.models import Base, EmailAccount, Invoice, LLMCache, ScanLog


def test_models_exports_and_relationships() -> None:
    assert Base.__name__ == "Base"
    assert EmailAccount.__tablename__ == "email_accounts"
    assert Invoice.__tablename__ == "invoices"
    assert LLMCache.__tablename__ == "llm_cache"
    assert ScanLog.__tablename__ == "scan_logs"
