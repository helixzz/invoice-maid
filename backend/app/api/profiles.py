from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, assert_owned
from app.models import InvoiceProfile

router = APIRouter(prefix="/profiles", tags=["profiles"])


class InvoiceProfileResponse(BaseModel):
    id: int
    name: str
    title: str
    tax_id: str
    invoice_type_pref: str
    email: str
    phone: str
    address: str
    bank_name: str
    bank_account: str
    is_default: bool
    created_at: str


class InvoiceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=255)
    tax_id: str = ""
    invoice_type_pref: str = "数电普票"
    email: str = ""
    phone: str = ""
    address: str = ""
    bank_name: str = ""
    bank_account: str = ""
    is_default: bool = False


class InvoiceProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    tax_id: str | None = None
    invoice_type_pref: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    is_default: bool | None = None


def _serialize(profile: InvoiceProfile) -> InvoiceProfileResponse:
    return InvoiceProfileResponse(
        id=profile.id,
        name=profile.name,
        title=profile.title,
        tax_id=profile.tax_id,
        invoice_type_pref=profile.invoice_type_pref,
        email=profile.email,
        phone=profile.phone,
        address=profile.address,
        bank_name=profile.bank_name,
        bank_account=profile.bank_account,
        is_default=profile.is_default,
        created_at=profile.created_at.isoformat(),
    )


@router.get("", response_model=list[InvoiceProfileResponse])
async def list_profiles(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceProfileResponse]:
    rows = list(
        (
            await db.execute(
                select(InvoiceProfile)
                .where(InvoiceProfile.user_id == current_user.id)
                .order_by(InvoiceProfile.is_default.desc(), InvoiceProfile.id.asc())
            )
        ).scalars().all()
    )
    return [_serialize(p) for p in rows]


@router.post("", response_model=InvoiceProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: InvoiceProfileCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvoiceProfileResponse:
    now = datetime.now(timezone.utc)
    if payload.is_default:
        await db.execute(
            update(InvoiceProfile)
            .where(InvoiceProfile.user_id == current_user.id)
            .values(is_default=False)
        )
    profile = InvoiceProfile(
        user_id=current_user.id,
        name=payload.name.strip(),
        title=payload.title.strip(),
        tax_id=payload.tax_id.strip(),
        invoice_type_pref=payload.invoice_type_pref.strip() or "数电普票",
        email=payload.email.strip(),
        phone=payload.phone.strip(),
        address=payload.address.strip(),
        bank_name=payload.bank_name.strip(),
        bank_account=payload.bank_account.strip(),
        is_default=payload.is_default,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return _serialize(profile)


@router.put("/{profile_id}", response_model=InvoiceProfileResponse)
async def update_profile(
    profile_id: int,
    payload: InvoiceProfileUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> InvoiceProfileResponse:
    profile = assert_owned(await db.get(InvoiceProfile, profile_id), current_user)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_default") is True:
        await db.execute(
            update(InvoiceProfile)
            .where(InvoiceProfile.user_id == current_user.id, InvoiceProfile.id != profile_id)
            .values(is_default=False)
        )
    for k, v in updates.items():
        if isinstance(v, str):
            v = v.strip()
        setattr(profile, k, v)
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(profile)
    return _serialize(profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: int,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    profile = assert_owned(await db.get(InvoiceProfile, profile_id), current_user)
    await db.delete(profile)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
