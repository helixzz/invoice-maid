# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import EmailAccount
from app.schemas.email_account import EmailAccountCreate, EmailAccountResponse, EmailAccountUpdate
from app.services.email_scanner import ScannerFactory, encrypt_password

router = APIRouter(prefix="/accounts", tags=["accounts"])


class ConnectionTestResponse(BaseModel):
    ok: bool
    detail: str | None = None


def _serialize_account(account: EmailAccount) -> EmailAccountResponse:
    return EmailAccountResponse(
        id=account.id,
        name=account.name,
        type=account.type,
        host=account.host,
        port=account.port,
        username=account.username,
        is_active=account.is_active,
        last_scan_uid=account.last_scan_uid,
        created_at=account.created_at.isoformat(),
    )


@router.get("", response_model=list[EmailAccountResponse])
async def list_accounts(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[EmailAccountResponse]:
    result = await db.execute(select(EmailAccount).order_by(EmailAccount.id.desc()))
    accounts = list(result.scalars().all())
    return [_serialize_account(account) for account in accounts]


@router.post("", response_model=EmailAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: EmailAccountCreate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> EmailAccountResponse:
    settings = get_settings()
    account = EmailAccount(
        name=payload.name,
        type=payload.type,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password_encrypted=encrypt_password(payload.password, settings.JWT_SECRET) if payload.password else None,
        oauth_token_path=payload.oauth_token_path,
        is_active=payload.is_active,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return _serialize_account(account)


@router.put("/{account_id}", response_model=EmailAccountResponse)
async def update_account(
    account_id: int,
    payload: EmailAccountUpdate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> EmailAccountResponse:
    account = await db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    settings = get_settings()
    if payload.name is not None:
        account.name = payload.name
    if payload.host is not None:
        account.host = payload.host
    if payload.port is not None:
        account.port = payload.port
    if payload.username is not None:
        account.username = payload.username
    if payload.password is not None:
        account.password_encrypted = encrypt_password(payload.password, settings.JWT_SECRET)
    if payload.is_active is not None:
        account.is_active = payload.is_active

    await db.commit()
    await db.refresh(account)
    return _serialize_account(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    account = await db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    await db.delete(account)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/test-connection", response_model=ConnectionTestResponse)
async def test_account_connection(
    account_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ConnectionTestResponse:
    account = await db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    scanner = ScannerFactory.get_scanner(account.type)
    try:
        ok = await scanner.test_connection(account)
    except Exception:
        return ConnectionTestResponse(ok=False, detail="Connection test failed")

    if ok:
        return ConnectionTestResponse(ok=True)
    return ConnectionTestResponse(ok=False, detail="Connection test failed")
