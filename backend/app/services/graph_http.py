from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

GRAPH_RETRYABLE_STATUS_CODES = frozenset({429, 503, 504})
GRAPH_MAX_RETRIES = 3
GRAPH_MAX_RETRY_AFTER_SECONDS = 30.0


def parse_retry_after(headers: Any, attempt: int) -> float:
    """Resolve sleep duration from Retry-After header or exponential backoff (1s, 2s, 4s)."""
    raw = None
    if headers is not None and hasattr(headers, "get"):
        raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is not None:
        try:
            parsed = float(str(raw).strip())
            if parsed >= 0:
                return min(parsed, GRAPH_MAX_RETRY_AFTER_SECONDS)
        except (TypeError, ValueError):
            pass
    return float(min(2**attempt, GRAPH_MAX_RETRY_AFTER_SECONDS))


async def graph_get_with_retry(
    client: Any,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    max_retries: int = GRAPH_MAX_RETRIES,
) -> Any:
    """Execute a Microsoft Graph GET request with Retry-After aware backoff for 429/503/504."""
    response = None
    for attempt in range(max_retries + 1):
        if params is not None:
            response = await client.get(url, headers=headers, params=params)
        else:
            response = await client.get(url, headers=headers)

        status_code = getattr(response, "status_code", 200)
        if status_code not in GRAPH_RETRYABLE_STATUS_CODES or attempt >= max_retries:
            return response

        delay = parse_retry_after(getattr(response, "headers", None), attempt)
        logger.warning(
            "Microsoft Graph returned HTTP %s for %s; retrying in %.1fs (attempt %d/%d)",
            status_code,
            url.split("?", 1)[0],
            delay,
            attempt + 1,
            max_retries,
        )
        await asyncio.sleep(delay)

    return response  # pragma: no cover
