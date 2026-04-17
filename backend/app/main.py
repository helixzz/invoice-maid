# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUntypedFunctionDecorator=false, reportAttributeAccessIssue=false

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    account_router,
    auth_router,
    download_router,
    invoice_router,
    scan_router,
    stats_router,
    test_helper_router,
)
from app.config import get_settings
from app.database import create_engine_and_session, init_db

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    settings = get_settings()
    engine, _ = create_engine_and_session(settings.DATABASE_URL)
    await init_db(settings.DATABASE_URL)

    from app.tasks.scheduler import start_scheduler, stop_scheduler

    start_scheduler(settings)
    try:
        yield
    finally:
        stop_scheduler()
        await engine.dispose()


app = FastAPI(title="Invoice Maid", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(invoice_router, prefix="/api/v1")
app.include_router(download_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(scan_router, prefix="/api/v1")
app.include_router(stats_router, prefix="/api/v1")
app.include_router(test_helper_router, prefix="/api/v1")


@app.get("/api/v1/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


if FRONTEND_ASSETS.exists():  # pragma: no cover
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catch_all(full_path: str):
    del full_path
    if FRONTEND_DIST.exists():
        return FileResponse(str(FRONTEND_DIST / "index.html"))
    return {"error": "Frontend not built"}
