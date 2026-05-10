from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from passlib.hash import bcrypt
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import EmailAccount, ExtractionLog, Invoice, LLMCache, ScanLog, User
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


async def _restore_admin_from_bootstrap(db: AsyncSession, settings: Settings) -> None:
    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
    if admin is None:
        return
    if admin.hashed_password != settings.ADMIN_PASSWORD_HASH:
        admin.hashed_password = settings.ADMIN_PASSWORD_HASH
    if not admin.is_admin:
        admin.is_admin = True
    if not admin.is_active:
        admin.is_active = True
    await db.commit()


async def seed_smoke_data(db: AsyncSession, settings: Settings) -> SmokeSeedResponse:
    await _reset_database(db, settings)
    await _restore_admin_from_bootstrap(db, settings)

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
        user_id=admin.id,
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
    db: AsyncSession = Depends(get_db),
) -> SmokeSeedResponse:
    # Intentionally NOT gated on CurrentUser — specs that rotate the
    # admin password mid-test (e.g. change-password.spec.ts) need a way
    # to restore the bootstrap admin WITHOUT valid credentials. The
    # _require_test_helpers() gate below + ENABLE_TEST_HELPERS=true
    # env flag is already the "this is not production" contract.
    settings = _require_test_helpers()
    return await seed_smoke_data(db, settings)


# Canonical Fix 8 regression shape from the 2026-04-20 Sam's Club
# investigation: 1 saved + 5 duplicate/low_confidence rows across 2
# emails = 2 cards (not 6 rows) after aggregation. If the future
# "simplifies" this fixture, the Fix 8 regression test dies silently.
FIX8_SAMS_CLUB_INVOICE_NO = "FIX8-SAMS-CLUB-001"


class Fix8SeedResponse(BaseModel):
    scan_log_id: int
    extraction_log_ids: list[int]


class MultiUserSeedResponse(BaseModel):
    admin_id: int
    second_user_email: str
    second_user_id: int
    second_user_password: str


# Pre-computed bcrypt hash avoids ~300ms per spec run. If you rotate the
# password literal, regenerate the hash or tests will silently fail auth.
_SECOND_USER_EMAIL = "second-user@smoke.invalid"
_SECOND_USER_PASSWORD = "smoke-second-user-password"
_SECOND_USER_PASSWORD_HASH = bcrypt.hash(_SECOND_USER_PASSWORD)


async def _reset_users_to_admin_only(db: AsyncSession) -> None:
    admin_row = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
    if admin_row is None:
        return
    await db.execute(delete(User).where(User.id != admin_row.id))
    await db.commit()


@router.post("/seed-fix8-scenario", response_model=Fix8SeedResponse)
async def seed_fix8_scenario(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Fix8SeedResponse:
    del _current_user
    _require_test_helpers()

    scan_log = (
        await db.execute(select(ScanLog).order_by(ScanLog.id.desc()).limit(1))
    ).scalar_one_or_none()
    if scan_log is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No scan_log exists; call /reset-smoke first",
        )

    rows: list[ExtractionLog] = [
        ExtractionLog(
            user_id=scan_log.user_id,
            scan_log_id=scan_log.id,
            email_uid="fix8-email-first",
            email_subject="山姆会员店 发票",
            attachment_filename="invoice.pdf",
            outcome="saved",
            classification_tier=1,
            parse_method="pdf_pdfplumber",
            parse_format="pdf",
            invoice_no=FIX8_SAMS_CLUB_INVOICE_NO,
            confidence=0.95,
        ),
        ExtractionLog(
            user_id=scan_log.user_id,
            scan_log_id=scan_log.id,
            email_uid="fix8-email-first",
            email_subject="山姆会员店 发票",
            attachment_filename="invoice.ofd",
            outcome="low_confidence",
            classification_tier=1,
            parse_method="ofd_struct",
            parse_format="ofd",
            confidence=0.42,
        ),
        ExtractionLog(
            user_id=scan_log.user_id,
            scan_log_id=scan_log.id,
            email_uid="fix8-email-first",
            email_subject="山姆会员店 发票",
            attachment_filename="invoice.xml",
            outcome="low_confidence",
            classification_tier=1,
            parse_method="xml_xpath",
            parse_format="xml",
            confidence=0.38,
        ),
        ExtractionLog(
            user_id=scan_log.user_id,
            scan_log_id=scan_log.id,
            email_uid="fix8-email-second",
            email_subject="山姆会员店 发票 (重发)",
            attachment_filename="invoice.pdf",
            outcome="duplicate",
            classification_tier=1,
            parse_method="pdf_pdfplumber",
            parse_format="pdf",
            invoice_no=FIX8_SAMS_CLUB_INVOICE_NO,
            confidence=0.95,
        ),
        ExtractionLog(
            user_id=scan_log.user_id,
            scan_log_id=scan_log.id,
            email_uid="fix8-email-second",
            email_subject="山姆会员店 发票 (重发)",
            attachment_filename="invoice.ofd",
            outcome="duplicate",
            classification_tier=1,
            parse_method="ofd_struct",
            parse_format="ofd",
            invoice_no=FIX8_SAMS_CLUB_INVOICE_NO,
            confidence=0.91,
        ),
        ExtractionLog(
            user_id=scan_log.user_id,
            scan_log_id=scan_log.id,
            email_uid="fix8-email-second",
            email_subject="山姆会员店 发票 (重发)",
            attachment_filename="invoice.xml",
            outcome="duplicate",
            classification_tier=1,
            parse_method="xml_xpath",
            parse_format="xml",
            invoice_no=FIX8_SAMS_CLUB_INVOICE_NO,
            confidence=0.89,
        ),
    ]
    for r in rows:
        db.add(r)
    await db.flush()
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return Fix8SeedResponse(
        scan_log_id=scan_log.id,
        extraction_log_ids=[r.id for r in rows],
    )


@router.post("/reset-users-to-admin-only", response_model=dict[str, int])
async def reset_users_to_admin_only(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    del _current_user
    _require_test_helpers()
    await _reset_users_to_admin_only(db)
    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one()
    return {"admin_id": admin.id}


@router.post("/seed-second-user", response_model=MultiUserSeedResponse)
async def seed_second_user(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MultiUserSeedResponse:
    del _current_user
    _require_test_helpers()

    existing = (
        await db.execute(select(User).where(User.email == _SECOND_USER_EMAIL))
    ).scalar_one_or_none()
    if existing is None:
        existing = User(
            email=_SECOND_USER_EMAIL,
            hashed_password=_SECOND_USER_PASSWORD_HASH,
            is_admin=False,
            is_active=True,
        )
        db.add(existing)
    else:
        existing.hashed_password = _SECOND_USER_PASSWORD_HASH
        existing.is_admin = False
        existing.is_active = True
    await db.commit()
    await db.refresh(existing)

    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one()
    return MultiUserSeedResponse(
        admin_id=admin.id,
        second_user_email=_SECOND_USER_EMAIL,
        second_user_id=existing.id,
        second_user_password=_SECOND_USER_PASSWORD,
    )


class CategoryMixSeedResponse(BaseModel):
    invoice_ids: list[int]
    categories: list[str]


@router.post("/seed-invoice-category-mix", response_model=CategoryMixSeedResponse)
async def seed_invoice_category_mix(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CategoryMixSeedResponse:
    del _current_user
    _require_test_helpers()
    settings = get_settings()

    account = (
        await db.execute(select(EmailAccount).order_by(EmailAccount.id).limit(1))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email_accounts row exists; call /reset-smoke first",
        )

    file_manager = FileManager(settings.STORAGE_PATH)
    admin = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one()

    mix = [
        ("CATMIX-VAT-001", "vat_invoice", "电子发票（普通发票）"),
        ("CATMIX-OVRS-001", "overseas_invoice", "Cursor Pro Subscription"),
        ("CATMIX-RCPT-001", "receipt", "Receipt"),
        ("CATMIX-PROF-001", "proforma", "PROFORMA INVOICE"),
        ("CATMIX-OTHR-001", "other", "Membership Fee"),
    ]
    created_ids: list[int] = []
    created_cats: list[str] = []
    for invoice_no, category, type_label in mix:
        file_path = await file_manager.save_invoice(
            content=_SMOKE_PDF_BYTES,
            buyer=f"Buyer {category}",
            seller=f"Seller {category}",
            invoice_no=invoice_no,
            invoice_date=date(2026, 5, 1),
            amount=Decimal("50.00"),
            extension=".pdf",
            user_id=admin.id,
        )
        invoice = Invoice(
            user_id=admin.id,
            invoice_no=invoice_no,
            buyer=f"Buyer {category}",
            seller=f"Seller {category}",
            amount=Decimal("50.00"),
            invoice_date=date(2026, 5, 1),
            invoice_type=type_label,
            invoice_category=category,
            item_summary=f"{category} sample",
            file_path=file_path,
            raw_text=f"{category} sample {type_label}",
            email_uid=f"catmix:{category}",
            email_account_id=account.id,
            source_format="pdf",
            extraction_method="seed",
            confidence=0.95,
        )
        db.add(invoice)
        await db.flush()
        created_ids.append(invoice.id)
        created_cats.append(category)
    await db.commit()

    return CategoryMixSeedResponse(invoice_ids=created_ids, categories=created_cats)
