from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

# When running E2E tests we route every request from the same loopback
# IP, so the per-minute caps (10/min for login, 5/min for register)
# would 429 mid-suite even though the traffic is legitimate. Disable
# enforcement entirely in that mode — the limiter still records hits
# (so tests that *want* to assert rate-limit behavior can opt in by
# constructing their own Limiter), but no real production deployment
# should ever set ENABLE_TEST_HELPERS=true.
limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=True,
    enabled=not get_settings().ENABLE_TEST_HELPERS,
)
