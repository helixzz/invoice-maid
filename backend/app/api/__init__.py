# pyright: reportMissingImports=false, reportUnknownVariableType=false

from app.api.auth import router as auth_router
from app.api.downloads import router as download_router
from app.api.email_accounts import router as account_router
from app.api.invoices import router as invoice_router
from app.api.scan import router as scan_router
from app.api.stats import router as stats_router
from app.api.test_helpers import router as test_helper_router

__all__ = [
    "account_router",
    "auth_router",
    "download_router",
    "invoice_router",
    "scan_router",
    "stats_router",
    "test_helper_router",
]
