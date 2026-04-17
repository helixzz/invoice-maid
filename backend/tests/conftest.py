from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import imap_tools
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

if not hasattr(imap_tools, "MailBoxPop3"):
    class _MailBoxPop3Fallback:  # pragma: no cover - compatibility shim for test env
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def login(self, *args, **kwargs):
            del args, kwargs
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        def fetch(self, *args, **kwargs):
            del args, kwargs
            return []

    imap_tools.MailBoxPop3 = _MailBoxPop3Fallback

import app.api.ai_settings as ai_settings_api
import app.api.auth as auth_api
import app.api.downloads as downloads_api
import app.api.email_accounts as accounts_api
import app.api.invoices as invoices_api
import app.api.scan as scan_api
import app.api.test_helpers as test_helpers_api
import app.deps as deps_module
import app.config as config_module
import app.main as main_module
from app.rate_limiter import limiter
import app.services.auth_service as auth_service_module
import app.services.ai_service as ai_service_module
import app.services.email_scanner as email_scanner_module
import app.services.scan_progress as scan_progress_module
import app.services.settings_resolver as settings_resolver_module
import app.tasks.scheduler as scheduler_module
from app.config import Settings
from app.database import create_fts5_objects, get_db
from app.main import app
from app.models import Base, CorrectionLog, EmailAccount, ExtractionLog, Invoice
from app.schemas.invoice import EmailAnalysis, InvoiceExtract
from app.services.auth_service import create_access_token
from app.services.email_scanner import encrypt_password
from app.services.email_scanner import oauth_registry
from app.services.settings_resolver import invalidate_ai_settings_cache


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    getter = lambda: settings
    for module in (
        config_module,
        ai_settings_api,
        auth_api,
        downloads_api,
        accounts_api,
        invoices_api,
        scan_api,
        test_helpers_api,
        deps_module,
        main_module,
        auth_service_module,
        ai_service_module,
        email_scanner_module,
        settings_resolver_module,
        scheduler_module,
    ):
        monkeypatch.setattr(module, "get_settings", getter, raising=False)


@pytest.fixture
def settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    storage_path = tmp_path / "storage"
    values = {
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
        "ADMIN_PASSWORD_HASH": "hashed:testpass",
        "JWT_SECRET": "test-secret",
        "LLM_BASE_URL": "https://llm.invalid/v1",
        "LLM_API_KEY": "test-key",
        "STORAGE_PATH": str(storage_path),
        "JWT_EXPIRE_MINUTES": 30,
        "LLM_MODEL": "test-model",
        "LLM_EMBED_MODEL": "test-embed-model",
        "EMBED_DIM": 3,
        "SCAN_INTERVAL_MINUTES": 15,
        "SQLITE_VEC_ENABLED": False,
        "WEBHOOK_URL": "",
        "WEBHOOK_SECRET": "",
        "ENABLE_TEST_HELPERS": False,
        "LOG_LEVEL": "INFO",
        "__runtime_sqlite_vec_available__": False,
    }
    for key, value in values.items():
        if key != "__runtime_sqlite_vec_available__":
            monkeypatch.setenv(key, str(value))
    config_module.get_settings.cache_clear()
    invalidate_ai_settings_cache()
    settings_obj = Settings(**values)
    _patch_settings(monkeypatch, settings_obj)
    monkeypatch.setattr(
        auth_service_module,
        "bcrypt",
        SimpleNamespace(
            verify=lambda secret, hashed: hashed == f"hashed:{secret}",
        ),
    )
    return settings_obj


@pytest_asyncio.fixture
async def engine(settings: Settings) -> AsyncIterator:
    del settings
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE TABLE invoice_embeddings (rowid INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"))
    await create_fts5_objects(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(settings: Settings, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    del settings

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db

    limiter.reset()
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    limiter.reset()
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_oauth_registry() -> None:
    oauth_registry._flows.clear()
    yield
    oauth_registry._flows.clear()


@pytest.fixture(autouse=True)
def reset_scan_progress_state() -> None:
    scan_progress_module._progress = scan_progress_module.ScanProgress()
    scan_progress_module._subscribers.clear()
    scan_progress_module._scan_lock = asyncio.Lock()
    yield
    scan_progress_module._progress = scan_progress_module.ScanProgress()
    scan_progress_module._subscribers.clear()
    scan_progress_module._scan_lock = asyncio.Lock()


@pytest.fixture
def auth_token(settings: Settings) -> str:
    del settings
    return create_access_token({"sub": "admin"})


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def mock_ai_service() -> SimpleNamespace:
    return SimpleNamespace(
        analyze_email=AsyncMock(
            return_value=EmailAnalysis(
                is_invoice_related=True,
                invoice_confidence=0.9,
                best_download_url=None,
                url_confidence=0.0,
                skip_reason=None,
            )
        ),
        classify_email=AsyncMock(return_value=True),
        extract_invoice_fields=AsyncMock(
            return_value=InvoiceExtract(
                buyer="测试购买方",
                seller="测试销售方",
                invoice_no="INV-LLM-001",
                invoice_date=date(2024, 1, 2),
                amount=Decimal("88.88"),
                item_summary="办公用品",
                invoice_type="电子普通发票",
                confidence=0.91,
            )
        ),
        embed_text=AsyncMock(return_value=[0.1, 0.2, 0.3]),
    )


@pytest_asyncio.fixture
async def create_email_account(
    db: AsyncSession, settings: Settings
) -> Callable[..., Awaitable[EmailAccount]]:
    async def factory(**overrides) -> EmailAccount:
        password = overrides.pop("password", "secret")
        defaults = {
            "name": "Test Account",
            "type": "imap",
            "host": "imap.example.com",
            "port": 993,
            "username": "user@example.com",
            "outlook_account_type": "personal",
            "password_encrypted": encrypt_password(password, settings.JWT_SECRET),
            "oauth_token_path": None,
            "is_active": True,
            "last_scan_uid": None,
        }
        defaults.update(overrides)
        account = EmailAccount(
            **defaults,
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return account

    return factory


@pytest_asyncio.fixture
async def create_invoice(
    db: AsyncSession, create_email_account: Callable[..., Awaitable[EmailAccount]]
) -> Callable[..., Awaitable[Invoice]]:
    async def factory(**overrides) -> Invoice:
        account = overrides.pop("email_account", None) or await create_email_account()
        defaults = {
            "invoice_no": "INV-001",
            "buyer": "Alpha Buyer",
            "seller": "Beta Seller",
            "amount": Decimal("100.00"),
            "invoice_date": date(2024, 1, 1),
            "invoice_type": "电子普通发票",
            "item_summary": "办公用品",
            "file_path": "invoice.pdf",
            "raw_text": "Alpha Buyer Beta Seller 办公用品",
            "email_uid": "uid-1",
            "email_account_id": account.id,
            "source_format": "pdf",
            "extraction_method": "regex",
            "confidence": 0.8,
            "is_manually_corrected": False,
        }
        defaults.update(overrides)
        invoice = Invoice(
            **defaults,
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        return invoice

    return factory


@pytest_asyncio.fixture
async def create_scan_log(
    db: AsyncSession, create_email_account: Callable[..., Awaitable[EmailAccount]]
) -> Callable[..., Awaitable[object]]:
    async def factory(**overrides):
        account = overrides.pop("email_account", None) or await create_email_account()
        defaults = {
            "email_account_id": account.id,
            "emails_scanned": 0,
            "invoices_found": 0,
            "error_message": None,
        }
        defaults.update(overrides)
        from app.models import ScanLog

        log = ScanLog(**defaults)
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    return factory


@pytest_asyncio.fixture
async def create_extraction_log(db: AsyncSession, create_scan_log) -> Callable[..., Awaitable[ExtractionLog]]:
    async def factory(**overrides) -> ExtractionLog:
        scan_log = overrides.pop("scan_log", None) or await create_scan_log()
        defaults = {
            "scan_log_id": scan_log.id,
            "email_uid": "uid-1",
            "email_subject": "Invoice",
            "attachment_filename": "invoice.pdf",
            "outcome": "saved",
            "invoice_no": "INV-001",
            "confidence": 0.9,
            "error_detail": None,
        }
        defaults.update(overrides)
        log = ExtractionLog(**defaults)
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    return factory


@pytest_asyncio.fixture
async def create_correction_log(db: AsyncSession, create_invoice) -> Callable[..., Awaitable[CorrectionLog]]:
    async def factory(**overrides) -> CorrectionLog:
        invoice = overrides.pop("invoice", None) or await create_invoice()
        defaults = {
            "invoice_id": invoice.id,
            "field_name": "buyer",
            "old_value": "Alpha Buyer",
            "new_value": "Updated Buyer",
        }
        defaults.update(overrides)
        log = CorrectionLog(**defaults)
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    return factory
