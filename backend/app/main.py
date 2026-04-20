# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUntypedFunctionDecorator=false, reportAttributeAccessIssue=false

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path

from app.logging_config import configure_logging

configure_logging()

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware import DEFAULT_PROTECTED_PATHS, ContentSizeLimitMiddleware

from app.api import (
    account_router,
    admin_router,
    ai_settings_router,
    auth_router,
    classifier_settings_router,
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


async def _scan_orphan_user_directories(db, settings) -> None:
    """Log a WARNING for every ``STORAGE_PATH/users/{id}/`` directory
    whose owning row in ``users`` is gone.

    An orphan directory appears when the admin delete-user endpoint
    succeeds at the DB cascade but crashes before
    ``FileManager.delete_user_files``. Silent orphan accumulation
    eventually eats disk; surfacing it in the service log on boot
    gives the operator something actionable without risking
    accidental deletion of data the operator added out-of-band."""
    from app.models import User

    storage_root = Path(settings.STORAGE_PATH).expanduser()
    users_dir = storage_root / "users"
    if not users_dir.exists():
        return

    existing_user_ids = {
        row[0]
        for row in (await db.execute(text("SELECT id FROM users"))).all()
    }
    del User

    for entry in users_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            entry_user_id = int(entry.name)
        except ValueError:
            continue
        if entry_user_id not in existing_user_ids:
            logger.warning(
                "orphan user storage directory detected: %s (no users.id=%d row)",
                entry,
                entry_user_id,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    settings = get_settings()
    engine, _ = create_engine_and_session(settings.DATABASE_URL)
    await init_db(settings.DATABASE_URL)

    from app.services.bootstrap import bootstrap_admin_user

    async for db in get_db():
        await db.execute(
            text(
                "UPDATE scan_logs SET finished_at = :ts, error_message = :msg"
                " WHERE finished_at IS NULL AND error_message IS NULL"
            ),
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "msg": "Scan interrupted — service was restarted while scan was running",
            },
        )
        await db.commit()
        await bootstrap_admin_user(db, settings)
        await _scan_orphan_user_directories(db, settings)

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


from importlib.metadata import version as _pkg_version

try:
    _APP_VERSION = _pkg_version("invoice-maid")
except Exception:  # pragma: no cover
    _APP_VERSION = "0.3.0"

app = FastAPI(title="Invoice Maid", version=_APP_VERSION, lifespan=lifespan)
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
app.add_middleware(
    ContentSizeLimitMiddleware,
    max_content_size=25 * 1024 * 1024,
    protected_paths=DEFAULT_PROTECTED_PATHS,
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(invoice_router, prefix="/api/v1")
app.include_router(download_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(ai_settings_router, prefix="/api/v1")
app.include_router(classifier_settings_router, prefix="/api/v1")
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
        "version": _APP_VERSION,
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
