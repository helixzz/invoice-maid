from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import EmailAccount, Invoice, LLMCache, ScanLog, User
from app.services.email_scanner import encrypt_password
from app.services.file_manager import FileManager

router = APIRouter(prefix="/test-helpers", tags=["test-helpers"], include_in_schema=False)

SMOKE_ACCOUNT_NAME = "Smoke Mailbox"
SMOKE_ACCOUNT_TYPE = "imap"
SMOKE_ACCOUNT_HOST = "imap.smoke.invalid"
SMOKE_ACCOUNT_PORT = 993
SMOKE_ACCOUNT_USERNAME = "smoke@example.com"
SMOKE_ACCOUNT_PASSWORD = "smoke-account-secret"
SMOKE_ACCOUNT_LAST_UID = "smoke-uid-001"

SMOKE_INVOICE_NO = "SMOKE-INV-001"
SMOKE_INVOICE_BUYER = "Smoke Buyer Ltd"
SMOKE_INVOICE_SELLER = "Smoke Seller LLC"
SMOKE_INVOICE_AMOUNT = Decimal("123.45")
SMOKE_INVOICE_DATE = date(2024, 1, 15)
SMOKE_INVOICE_TYPE = "电子普通发票"
SMOKE_INVOICE_SUMMARY = "Smoke test office supplies"
SMOKE_INVOICE_UID = "smoke-email-uid-001"

_SMOKE_PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R>>endobj\n4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 24 100 Td (Smoke Invoice PDF) Tj ET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000010 00000 n \n0000000053 00000 n \n0000000110 00000 n \n0000000194 00000 n \ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n290\n%%EOF\n"


class SmokeSeedResponse(BaseModel):
    account_id: int
    invoice_id: int
    scan_log_id: int


def test_helpers_enabled() -> bool:
    return get_settings().ENABLE_TEST_HELPERS


def _require_test_helpers() -> Settings:
    settings = get_settings()
    if not settings.ENABLE_TEST_HELPERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return settings
async def _reset_database(db: AsyncSession, settings: Settings) -> None:
    shutil.rmtree(settings.STORAGE_PATH, ignore_errors=True)
    for model in (Invoice, ScanLog, EmailAccount, LLMCache):
        await db.execute(delete(model))
    await db.commit()


async def seed_smoke_data(db: AsyncSession, settings: Settings) -> SmokeSeedResponse:
    await _reset_database(db, settings)

    admin = (
        await db.execute(select(User).order_by(User.id).limit(1))
    ).scalar_one_or_none()
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No admin user; bootstrap hook must run before seeding smoke data",
        )

    file_manager = FileManager(settings.STORAGE_PATH)
    account = EmailAccount(
        user_id=admin.id,
        name=SMOKE_ACCOUNT_NAME,
        type=SMOKE_ACCOUNT_TYPE,
        host=SMOKE_ACCOUNT_HOST,
        port=SMOKE_ACCOUNT_PORT,
        username=SMOKE_ACCOUNT_USERNAME,
        password_encrypted=encrypt_password(SMOKE_ACCOUNT_PASSWORD, settings.JWT_SECRET),
        is_active=True,
        last_scan_uid=SMOKE_ACCOUNT_LAST_UID,
    )
    db.add(account)
    await db.flush()

    file_path = await file_manager.save_invoice(
        content=_SMOKE_PDF_BYTES,
        buyer=SMOKE_INVOICE_BUYER,
        seller=SMOKE_INVOICE_SELLER,
        invoice_no=SMOKE_INVOICE_NO,
        invoice_date=SMOKE_INVOICE_DATE,
        amount=SMOKE_INVOICE_AMOUNT,
        extension=".pdf",
    )
    invoice = Invoice(
        user_id=admin.id,
        invoice_no=SMOKE_INVOICE_NO,
        buyer=SMOKE_INVOICE_BUYER,
        seller=SMOKE_INVOICE_SELLER,
        amount=SMOKE_INVOICE_AMOUNT,
        invoice_date=SMOKE_INVOICE_DATE,
        invoice_type=SMOKE_INVOICE_TYPE,
        item_summary=SMOKE_INVOICE_SUMMARY,
        file_path=file_path,
        raw_text=f"{SMOKE_INVOICE_BUYER} {SMOKE_INVOICE_SELLER} {SMOKE_INVOICE_SUMMARY}",
        email_uid=SMOKE_INVOICE_UID,
        email_account_id=account.id,
        source_format="pdf",
        extraction_method="seed",
        confidence=1.0,
    )
    db.add(invoice)
    await db.flush()

    scan_log = ScanLog(
        user_id=admin.id,
        email_account_id=account.id,
        started_at=datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc),
        finished_at=datetime(2024, 1, 15, 8, 1, tzinfo=timezone.utc),
        emails_scanned=1,
        invoices_found=1,
        error_message=None,
    )
    db.add(scan_log)
    await db.commit()
    await db.refresh(account)
    await db.refresh(invoice)
    await db.refresh(scan_log)

    return SmokeSeedResponse(account_id=account.id, invoice_id=invoice.id, scan_log_id=scan_log.id)


async def _run_smoke_scan_in_session(db: AsyncSession) -> None:
    account = (
        await db.execute(select(EmailAccount).where(EmailAccount.username == SMOKE_ACCOUNT_USERNAME))
    ).scalar_one_or_none()
    if account is None:
        return

    now = datetime.now(timezone.utc)
    account.last_scan_uid = f"{SMOKE_ACCOUNT_LAST_UID}-triggered"
    db.add(
        ScanLog(
            user_id=account.user_id,
            email_account_id=account.id,
            started_at=now,
            finished_at=now,
            emails_scanned=1,
            invoices_found=1,
            error_message=None,
        )
    )
    await db.commit()


async def run_smoke_scan() -> None:
    if not test_helpers_enabled():
        return

    async for db in get_db():
        await _run_smoke_scan_in_session(db)
        return


@router.post("/reset-smoke", response_model=SmokeSeedResponse)
async def reset_smoke_data(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SmokeSeedResponse:
    del _current_user
    settings = _require_test_helpers()
    return await seed_smoke_data(db, settings)
