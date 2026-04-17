"""Root-logger configuration for the FastAPI process.

Uvicorn configures only its own logger tree (``uvicorn.*``). Loggers obtained
via ``logging.getLogger(__name__)`` in ``app.*`` inherit the root logger,
which has no handler by default, so ``logger.info(...)`` calls from app code
are silently dropped.

This module installs a single stderr handler on the root logger (systemd
captures stderr into the journal) with level overrides for a few noisy
third-party libraries.

Configuration:
    LOG_LEVEL   Root level for ``app.*`` loggers (DEBUG, INFO, WARNING, ERROR,
                CRITICAL). Invalid values fall back to INFO. Default: INFO.

The top-level :func:`configure_logging` is idempotent.
"""
from __future__ import annotations

import logging
import logging.config
import os
from typing import Final

_DEFAULT_LEVEL: Final[str] = "INFO"
_VALID_LEVELS: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
)

_configured: bool = False


def _resolve_level(explicit: str | None) -> str:
    raw = explicit if explicit is not None else os.environ.get("LOG_LEVEL", _DEFAULT_LEVEL)
    candidate = raw.strip().upper()
    if candidate not in _VALID_LEVELS:
        return _DEFAULT_LEVEL
    return candidate


def build_config(level: str) -> dict[str, object]:
    """Return a ``logging.config.dictConfig`` payload for ``level``."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "default",
            },
        },
        "root": {
            "level": level,
            "handlers": ["stderr"],
        },
        "loggers": {
            # Declare explicitly so LOG_LEVEL overrides still apply to app
            # code even if a third-party library raises the root level later.
            "app": {"level": level, "propagate": True},
            # INFO surfaces scheduled-job firing; DEBUG is far too chatty.
            "apscheduler": {"level": "INFO", "propagate": True},
            # SQLAlchemy emits INFO for every connect / transaction — keep
            # at WARNING regardless of root level.
            "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
            # passlib probes bcrypt's version at import and emits a benign
            # traceback on bcrypt >= 4.1; suppress.
            "passlib": {"level": "ERROR", "propagate": True},
            # Uvicorn configures its own tree; don't fight it.
            "uvicorn": {"propagate": False},
            "uvicorn.error": {"propagate": False},
            "uvicorn.access": {"propagate": False},
        },
    }


def configure_logging(level: str | None = None) -> None:
    """Install the root-logger handler once. Subsequent calls are no-ops."""
    global _configured
    if _configured:
        return
    resolved = _resolve_level(level)
    logging.config.dictConfig(build_config(resolved))
    _configured = True


def reset_for_tests() -> None:
    """Force the next ``configure_logging`` call to re-apply."""
    global _configured
    _configured = False
