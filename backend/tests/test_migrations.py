from __future__ import annotations

import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


TENANT_TABLES = (
    "invoices",
    "email_accounts",
    "scan_logs",
    "extraction_logs",
    "correction_logs",
    "saved_views",
    "webhook_logs",
)


@pytest.fixture
def migration_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Alembic's ``env.py`` calls ``logging.config.fileConfig`` every run,
    which disables every existing logger (``disable_existing_loggers``
    defaults to True). If we don't undo that, the caplog-based tests in
    the rest of the suite silently lose their log records. Snapshot the
    current logger state, yield, then restore."""
    db_path = tmp_path / "migration_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    logger_dict = logging.root.manager.loggerDict
    disabled_before = {
        name: getattr(lg, "disabled", False)
        for name, lg in logger_dict.items()
        if isinstance(lg, logging.Logger)
    }
    saved_level = logging.root.level
    saved_handlers = list(logging.root.handlers)
    yield db_path
    for name, was_disabled in disabled_before.items():
        lg = logger_dict.get(name)
        if isinstance(lg, logging.Logger):
            lg.disabled = was_disabled
    for name, lg in logger_dict.items():
        if name not in disabled_before and isinstance(lg, logging.Logger):
            lg.disabled = False
    logging.root.handlers = saved_handlers
    logging.root.setLevel(saved_level)


def _upgrade_to(db_path: Path, revision: str) -> None:
    config = _alembic_config(f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(config, revision)


def _downgrade_to(db_path: Path, revision: str) -> None:
    config = _alembic_config(f"sqlite+aiosqlite:///{db_path}")
    command.downgrade(config, revision)


def _current_revision(db_path: Path) -> str | None:
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        result = conn.execute(sa.text("SELECT version_num FROM alembic_version")).first()
    engine.dispose()
    return result[0] if result else None


def test_0011_backfills_existing_rows_to_admin_when_users_row_present(
    migration_db: Path,
) -> None:
    """Production path: run migration 0010 (which creates users table),
    manually insert users[1] the way the application's bootstrap hook
    does on first boot, insert sample rows into tenant tables with
    NULL user_id, then run migration 0011 and verify every tenant row
    now points at user_id=1 and the indexes exist.

    Note: ``saved_views`` and ``webhook_logs`` are intentionally not
    seeded here because they are never created by any Alembic
    migration — they come from ``Base.metadata.create_all`` at first
    app start. Migration 0011 must skip them when they're absent; the
    downgrade test below exercises the mixed case."""

    _upgrade_to(migration_db, "0010_users_and_sessions")

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, hashed_password, is_active, is_admin, "
                "created_at, updated_at) VALUES "
                "(1, 'admin@local', 'hash', 1, 1, '2026-04-21', '2026-04-21')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO email_accounts (id, name, type, username, "
                "is_active, created_at) VALUES "
                "(2, 'Acc', 'imap', 'u@e.com', 1, '2026-04-21')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO invoices (invoice_no, buyer, seller, amount, "
                "invoice_date, invoice_type, file_path, raw_text, email_uid, "
                "email_account_id, source_format, extraction_method, confidence, "
                "is_manually_corrected, created_at) VALUES "
                "('INV-1', 'B', 'S', 10.00, '2026-01-01', 'vat', 'f.pdf', '', "
                "'u1', 2, 'pdf', 'regex', 0.9, 0, '2026-04-21')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO scan_logs (email_account_id, started_at, "
                "emails_scanned, invoices_found) VALUES (2, '2026-04-21', 0, 0)"
            )
        )
    engine.dispose()

    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    assert _current_revision(migration_db) == "0011_add_user_id_to_tenant_tables"

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        tables_present = {
            row[0]
            for row in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
        }
        for table in ("invoices", "email_accounts", "scan_logs"):
            cols = conn.execute(sa.text(f"PRAGMA table_info({table})")).all()
            col_names = [c[1] for c in cols]
            assert "user_id" in col_names, f"{table} missing user_id"

        for table in ("saved_views", "webhook_logs"):
            assert table not in tables_present, (
                f"{table} should not be created by migrations at this revision"
            )

        invoice_uid = conn.execute(
            sa.text("SELECT user_id FROM invoices WHERE invoice_no='INV-1'")
        ).scalar_one()
        assert invoice_uid == 1

        account_uid = conn.execute(
            sa.text("SELECT user_id FROM email_accounts WHERE id=2")
        ).scalar_one()
        assert account_uid == 1

        scan_uid = conn.execute(
            sa.text("SELECT user_id FROM scan_logs LIMIT 1")
        ).scalar_one()
        assert scan_uid == 1

        indexes = {
            row[1]
            for row in conn.execute(
                sa.text("SELECT * FROM sqlite_master WHERE type='index'")
            ).all()
        }
        for expected in (
            "ix_invoices_user_id_invoice_date",
            "ix_email_accounts_user_id",
            "ix_scan_logs_user_id_started_at",
            "ix_extraction_logs_user_id_created_at",
            "ix_correction_logs_user_id",
        ):
            assert expected in indexes, f"index {expected} missing"
    engine.dispose()


def test_0011_leaves_user_id_null_when_users_table_is_empty(
    migration_db: Path,
) -> None:
    """Fresh-install path: run 0010 but do NOT insert users[1] (bootstrap
    has not run yet in the simulated test). 0011 must still complete
    without error and every user_id in freshly-inserted tenant rows
    must remain NULL — the first-boot bootstrap hook will claim them
    later via a separate code path in Phase 4."""

    _upgrade_to(migration_db, "0010_users_and_sessions")

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO email_accounts (id, name, type, username, "
                "is_active, created_at) VALUES "
                "(2, 'Acc', 'imap', 'u@e.com', 1, '2026-04-21')"
            )
        )
    engine.dispose()

    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        account_uid = conn.execute(
            sa.text("SELECT user_id FROM email_accounts WHERE id=2")
        ).scalar_one()
        assert account_uid is None
    engine.dispose()


def test_0011_downgrade_drops_user_id_and_indexes(migration_db: Path) -> None:
    """Exercise the downgrade path on the mixed schema produced by
    ``alembic upgrade head`` without ever starting the app — i.e.
    ``saved_views``/``webhook_logs`` absent. Downgrade must be a
    no-op for the missing tables and cleanly strip user_id + indexes
    from the present ones."""
    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    _downgrade_to(migration_db, "0010_users_and_sessions")

    assert _current_revision(migration_db) == "0010_users_and_sessions"

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        tables_present = {
            row[0]
            for row in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
        }
        for table in TENANT_TABLES:
            if table not in tables_present:
                continue
            cols = conn.execute(sa.text(f"PRAGMA table_info({table})")).all()
            col_names = [c[1] for c in cols]
            assert "user_id" not in col_names, (
                f"downgrade left user_id on {table}"
            )

        indexes = {
            row[1]
            for row in conn.execute(
                sa.text("SELECT * FROM sqlite_master WHERE type='index'")
            ).all()
        }
        for removed in (
            "ix_invoices_user_id_invoice_date",
            "ix_email_accounts_user_id",
            "ix_scan_logs_user_id_started_at",
            "ix_extraction_logs_user_id_created_at",
            "ix_correction_logs_user_id",
        ):
            assert removed not in indexes, f"downgrade left index {removed}"
    engine.dispose()


def test_0011_upgrade_and_downgrade_when_all_tables_exist(
    migration_db: Path,
) -> None:
    """Covers the production-shaped case: every tenant table exists
    (simulating post-``create_all`` state) when 0011 runs. Verifies
    that saved_views and webhook_logs also pick up user_id + indexes
    + the downgrade cleanly removes them. This is the path production
    will take."""
    _upgrade_to(migration_db, "0010_users_and_sessions")

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE saved_views ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR(255) NOT NULL, "
                "filter_json TEXT NOT NULL, "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE webhook_logs ("
                "id INTEGER PRIMARY KEY, "
                "event VARCHAR(128) NOT NULL, "
                "invoice_no VARCHAR(128) NOT NULL, "
                "url VARCHAR(1000) NOT NULL, "
                "status_code INTEGER, "
                "success BOOLEAN NOT NULL, "
                "error_detail VARCHAR(2000), "
                "created_at DATETIME NOT NULL)"
            )
        )
    engine.dispose()

    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        for table in ("saved_views", "webhook_logs"):
            cols = conn.execute(sa.text(f"PRAGMA table_info({table})")).all()
            col_names = [c[1] for c in cols]
            assert "user_id" in col_names, f"{table} missing user_id"

        indexes = {
            row[1]
            for row in conn.execute(
                sa.text("SELECT * FROM sqlite_master WHERE type='index'")
            ).all()
        }
        assert "ix_saved_views_user_id" in indexes
        assert "ix_webhook_logs_user_id_created_at" in indexes
    engine.dispose()

    _downgrade_to(migration_db, "0010_users_and_sessions")

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        for table in ("saved_views", "webhook_logs"):
            cols = conn.execute(sa.text(f"PRAGMA table_info({table})")).all()
            col_names = [c[1] for c in cols]
            assert "user_id" not in col_names
    engine.dispose()
