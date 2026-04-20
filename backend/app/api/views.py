from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser
from app.models import SavedView
from app.schemas.saved_view import SavedViewCreate, SavedViewResponse

router = APIRouter(prefix="/views", tags=["views"])


def _serialize_view(saved_view: SavedView) -> SavedViewResponse:
    return SavedViewResponse(
        id=saved_view.id,
        name=saved_view.name,
        filter_json=saved_view.filter_json,
        created_at=saved_view.created_at.isoformat(),
    )


@router.get("", response_model=list[SavedViewResponse])
async def list_views(
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[SavedViewResponse]:
    result = await db.execute(select(SavedView).order_by(SavedView.created_at.desc(), SavedView.id.desc()))
    return [_serialize_view(saved_view) for saved_view in result.scalars().all()]


@router.post("", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
async def create_view(
    payload: SavedViewCreate,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SavedViewResponse:
    saved_view = SavedView(
        user_id=_current_user.id,
        name=payload.name,
        filter_json=payload.filter_json,
    )
    db.add(saved_view)
    await db.commit()
    await db.refresh(saved_view)
    return _serialize_view(saved_view)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_view(
    view_id: int,
    _current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    saved_view = await db.get(SavedView, view_id)
    if saved_view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved view not found")

    await db.delete(saved_view)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
