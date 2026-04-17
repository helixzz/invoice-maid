# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUntypedFunctionDecorator=false, reportAttributeAccessIssue=false

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    account_router,
    ai_settings_router,
    auth_router,
    download_router,
    invoice_router,
    scan_router,
    stats_router,
    test_helper_router,
    view_router,
)
from app.config import get_settings
from app.database import create_engine_and_session, get_db, init_db
from app.models import Invoice, ScanLog
from app.rate_limiter import limiter
from app.tasks.scheduler import get_scheduler

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"


def _configured_worker_count() -> int | None:
    raw_value = os.environ.get("WEB_CONCURRENCY")
    if raw_value is None:
        return None
    try:
        worker_count = int(raw_value)
    except ValueError:
        return None
    return worker_count if worker_count > 0 else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    settings = get_settings()
    engine, _ = create_engine_and_session(settings.DATABASE_URL)
    await init_db(settings.DATABASE_URL)

    from app.tasks.scheduler import start_scheduler, stop_scheduler

    scheduler_started = False
    worker_count = _configured_worker_count()
    if worker_count and worker_count > 1:
        logger.warning(
            "Multiple workers detected (%s). Scheduler disabled to prevent duplicate jobs.", worker_count
        )
    else:
        start_scheduler(settings)
        scheduler_started = True
    try:
        yield
    finally:
        if scheduler_started:
            stop_scheduler()
        await engine.dispose()


app = FastAPI(title="Invoice Maid", version="0.2.1", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SlowAPIMiddleware)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(invoice_router, prefix="/api/v1")
app.include_router(download_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(ai_settings_router, prefix="/api/v1")
app.include_router(scan_router, prefix="/api/v1")
app.include_router(stats_router, prefix="/api/v1")
app.include_router(view_router, prefix="/api/v1")
app.include_router(test_helper_router, prefix="/api/v1")


@app.get("/api/v1/health", tags=["health"])
async def health(db: AsyncSession = Depends(get_db)) -> dict[str, str | bool | int | None]:
    scheduler = get_scheduler()
    scheduler_status = "running" if scheduler is not None and scheduler.running else "stopped"
    response: dict[str, str | bool | int | None] = {
        "status": "ok",
        "version": "0.2.1",
        "db": "ok",
        "scheduler": scheduler_status,
        "sqlite_vec": get_settings().sqlite_vec_available,
        "invoice_count": 0,
        "last_scan_at": None,
    }

    if scheduler_status != "running":
        response["status"] = "degraded"

    try:
        await db.execute(text("SELECT 1"))
        response["invoice_count"] = await db.scalar(select(func.count()).select_from(Invoice)) or 0
        last_scan_at = await db.scalar(select(func.max(ScanLog.finished_at)))
        if isinstance(last_scan_at, datetime):
            if last_scan_at.tzinfo is None:  # pragma: no branch
                last_scan_at = last_scan_at.replace(tzinfo=timezone.utc)
            response["last_scan_at"] = last_scan_at.isoformat()
    except Exception as exc:
        logger.exception("Health check failed: %s", exc)
        response["status"] = "degraded"
        response["db"] = "error"

    return response


if FRONTEND_ASSETS.exists():  # pragma: no cover
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="assets")


@app.get("/favicon.png", include_in_schema=False)
async def serve_favicon():
    favicon_path = FRONTEND_DIST / "favicon.png"
    if favicon_path.exists():
        return FileResponse(str(favicon_path), media_type="image/png")
    return Response(status_code=404)


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catch_all(full_path: str):
    del full_path
    if FRONTEND_DIST.exists():
        return FileResponse(str(FRONTEND_DIST / "index.html"))
    return {"error": "Frontend not built"}
