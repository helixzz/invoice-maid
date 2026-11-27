from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_migrations_0020_to_0022_upgrade_and_downgrade(tmp_path: Path, monkeypatch) -> None:
    db_file = tmp_path / "mig_test.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    try:
        # Upgrade to head (includes 0020, 0021, 0022)
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_file)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "failed_emails" in tables
            cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
            assert {"currency", "reimbursement_status", "tags", "notes"}.issubset(cols)
        finally:
            conn.close()

        # Downgrade back to 0019 cleanly
        command.downgrade(cfg, "0019_restore_invoice_number_format")

        conn = sqlite3.connect(db_file)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "failed_emails" not in tables
            cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
            assert "currency" not in cols
            assert "reimbursement_status" not in cols
        finally:
            conn.close()

        # Re-upgrade to head
        command.upgrade(cfg, "head")
    finally:
        # Alembic's fileConfig disables existing loggers by default; re-enable so
        # subsequent caplog-based tests in the same pytest process still capture logs.
        for lgr in logging.root.manager.loggerDict.values():
            if isinstance(lgr, logging.Logger):
                lgr.disabled = False
