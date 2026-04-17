# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUntypedFunctionDecorator=false, reportCallInDefaultInitializer=false

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser
from app.models import EmailAccount
from app.schemas.email_account import (
    EmailAccountCreate,
    EmailAccountResponse,
    EmailAccountUpdate,
    OAuthInitiateResponse,
    OAuthStatusResponse,
)
from app.services.email_scanner import (
    OAuthFlowState,
    OutlookScanner,
    ScannerFactory,
    _is_personal_microsoft_account,
    encrypt_password,
    oauth_registry,
)

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
        outlook_account_type=account.outlook_account_type,
        is_active=account.is_active,
        last_scan_uid=account.last_scan_uid,
        created_at=account.created_at.isoformat(),
    )


def _oauth_state_response(state: OAuthFlowState) -> OAuthStatusResponse:
    expires_at = state.expires_at.isoformat().replace("+00:00", "Z") if state.expires_at else None
    return OAuthStatusResponse(
        status=state.status,
        verification_uri=state.verification_uri or None,
        user_code=state.user_code or None,
        expires_at=expires_at,
        detail=state.detail,
    )


async def _get_account_or_404(db: AsyncSession, account_id: int) -> EmailAccount:
    account = await db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


def _ensure_outlook_account(account: EmailAccount) -> None:
    if account.type != "outlook":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth is only supported for Outlook accounts")


def _default_oauth_token_path(account_id: int) -> str:
    storage_path = Path(get_settings().STORAGE_PATH).expanduser()
    oauth_dir = (storage_path.parent / "oauth").resolve()
    oauth_dir.mkdir(parents=True, exist_ok=True)
    return str(oauth_dir / f"account_{account_id}_token.json")


def _attach_flow_task(account: EmailAccount, scanner: OutlookScanner, flow: dict[str, object], state: OAuthFlowState) -> None:
    async def runner() -> None:
        try:
            result = await scanner.complete_device_flow_async(account, flow)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            state.status = "error"
            state.detail = str(exc)
            return

        if result.get("access_token"):
            state.status = "authorized"
            state.detail = None
            return

        expires_in = flow.get("expires_in")
        if isinstance(expires_in, int) and state.expires_at and datetime.now(timezone.utc) > state.expires_at:
            state.status = "expired"
            state.detail = "Device code expired"
            return

        state.status = "error"
        state.detail = str(result.get("error_description") or result.get("error") or "Outlook authorization failed")

    state.task = asyncio.create_task(runner())


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
    outlook_account_type = payload.outlook_account_type
    if payload.type == "outlook" and outlook_account_type == "personal":
        outlook_account_type = (
            "personal" if _is_personal_microsoft_account(payload.username) else "organizational"
        )

    account = EmailAccount(
        name=payload.name,
        type=payload.type,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        outlook_account_type=outlook_account_type,
        password_encrypted=encrypt_password(payload.password, settings.JWT_SECRET) if payload.password else None,
        oauth_token_path=payload.oauth_token_path,
        is_active=payload.is_active,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    if account.type == "outlook" and not account.oauth_token_path:
        account.oauth_token_path = _default_oauth_token_path(account.id)
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
    account = await _get_account_or_404(db, account_id)

    settings = get_settings()
    if payload.name is not None:
        account.name = payload.name
    if payload.host is not None:
        account.host = payload.host
    if payload.port is not None:
        account.port = payload.port
    if payload.username is not None:
        account.username = payload.username
    if payload.outlook_account_type is not None:
        account.outlook_account_type = payload.outlook_account_type
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
    account = await _get_account_or_404(db, account_id)

    await db.delete(account)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/test-connection", response_model=ConnectionTestResponse)
async def test_account_connection(
    account_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ConnectionTestResponse:
    account = await _get_account_or_404(db, account_id)

    scanner = ScannerFactory.get_scanner(account.type)
    try:
        ok = await scanner.test_connection(account)
    except RuntimeError as exc:
        if account.type == "outlook" and str(exc) == "Outlook authorization required. Use the Settings page to authenticate.":
            return ConnectionTestResponse(ok=False, detail="Outlook authorization required. Use the Authenticate button.")
        return ConnectionTestResponse(ok=False, detail="Connection test failed")
    except Exception:
        return ConnectionTestResponse(ok=False, detail="Connection test failed")

    if ok:
        return ConnectionTestResponse(ok=True)
    return ConnectionTestResponse(ok=False, detail="Connection test failed")


@router.post("/{account_id}/oauth/initiate", response_model=OAuthInitiateResponse)
async def initiate_account_oauth(
    account_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> OAuthInitiateResponse:
    account = await _get_account_or_404(db, account_id)
    _ensure_outlook_account(account)
    scanner = OutlookScanner()

    if await scanner.has_cached_token_async(account):
        oauth_registry.remove(account.id)
        return OAuthInitiateResponse(status="authorized")

    existing_state = oauth_registry.get(account.id)
    if existing_state and existing_state.status == "pending":
        return OAuthInitiateResponse(
            status=existing_state.status,
            verification_uri=existing_state.verification_uri or None,
            user_code=existing_state.user_code or None,
            expires_at=existing_state.expires_at.isoformat().replace("+00:00", "Z") if existing_state.expires_at else None,
        )

    flow = await scanner.initiate_device_flow_async(account)
    expires_in = int(flow.get("expires_in") or flow.get("expiresAt") or 900)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    state = OAuthFlowState(
        status="pending",
        verification_uri=str(flow.get("verification_uri") or flow.get("verification_url") or ""),
        user_code=str(flow.get("user_code") or ""),
        expires_at=expires_at,
    )
    oauth_registry.set(account.id, state)
    _attach_flow_task(account, scanner, flow, state)
    return OAuthInitiateResponse(
        status="pending",
        verification_uri=state.verification_uri or None,
        user_code=state.user_code or None,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
    )


@router.get("/{account_id}/oauth/status", response_model=OAuthStatusResponse)
async def get_account_oauth_status(
    account_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> OAuthStatusResponse:
    account = await _get_account_or_404(db, account_id)
    _ensure_outlook_account(account)

    state = oauth_registry.get(account.id)
    if state is not None:
        return _oauth_state_response(state)

    scanner = OutlookScanner()
    if await scanner.has_cached_token_async(account):
        return OAuthStatusResponse(status="authorized")
    return OAuthStatusResponse(status="none", detail="Authorization not started")
