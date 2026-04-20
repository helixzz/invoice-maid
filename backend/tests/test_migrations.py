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


@pytest.fixture
def bootstrap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set ADMIN_EMAIL + ADMIN_PASSWORD_HASH so migration 0012's
    just-in-time admin seeding can run when users[] is empty."""
    monkeypatch.setenv("ADMIN_EMAIL", "admin@local")
    monkeypatch.setenv(
        "ADMIN_PASSWORD_HASH",
        "$2b$12$placeholderhashforunittestsonly",
    )


def _seed_admin_user(db_path: Path) -> None:
    """Insert users[1] manually (simulating the bootstrap hook running
    after migration 0011 but before migration 0012 — the production
    path on an upgrade from v0.9.0-alpha.4)."""
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, hashed_password, is_active, is_admin, "
                "created_at, updated_at) VALUES "
                "(1, 'admin@local', 'hash', 1, 1, '2026-04-21', '2026-04-21')"
            )
        )
    engine.dispose()


def _insert_invoice(
    db_path: Path, *, invoice_no: str, user_id: int | None = 1, email_account_id: int = 1
) -> None:
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO invoices (invoice_no, buyer, seller, amount, "
                "invoice_date, invoice_type, file_path, raw_text, email_uid, "
                "email_account_id, source_format, extraction_method, confidence, "
                "is_manually_corrected, created_at, user_id) VALUES "
                "(:invoice_no, 'B', 'S', 10.00, '2026-01-01', 'vat', 'f.pdf', 'buyer seller summary', "
                "'u1', :email_account_id, 'pdf', 'regex', 0.9, 0, '2026-04-21', :user_id)"
            ),
            {"invoice_no": invoice_no, "user_id": user_id, "email_account_id": email_account_id},
        )
    engine.dispose()


def test_0012_upgrade_tightens_user_id_and_replaces_unique(
    migration_db: Path,
) -> None:
    """Upgrade path: users[1] already exists, existing invoices are
    carried forward, user_id goes NOT NULL with FK, the global
    UNIQUE(invoice_no) index becomes non-unique, and a composite
    UNIQUE(user_id, invoice_no) index takes its place."""
    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")
    _seed_admin_user(migration_db)
    _insert_invoice(migration_db, invoice_no="INV-1", user_id=1)

    _upgrade_to(migration_db, "0012_tighten_user_id_constraints")

    assert _current_revision(migration_db) == "0012_tighten_user_id_constraints"

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        cols = {
            row[1]: row
            for row in conn.execute(sa.text("PRAGMA table_info(invoices)")).all()
        }
        user_id_col = cols["user_id"]
        assert user_id_col[3] == 1, "invoices.user_id must be NOT NULL"

        indexes = {
            row[1]: row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='invoices'"
                )
            ).all()
        }
        assert "uq_invoices_user_id_invoice_no" in indexes
        assert "ix_invoices_invoice_no" in indexes

        composite_sql = conn.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE name='uq_invoices_user_id_invoice_no'"
            )
        ).scalar_one()
        assert "UNIQUE" in composite_sql.upper()
        assert "user_id" in composite_sql
        assert "invoice_no" in composite_sql

        legacy_sql = conn.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE name='ix_invoices_invoice_no'")
        ).scalar_one()
        assert "UNIQUE" not in legacy_sql.upper()

        fks = conn.execute(sa.text("PRAGMA foreign_key_list(invoices)")).all()
        fk_targets = [(fk[2], fk[3], fk[4], fk[6]) for fk in fks]
        assert ("users", "user_id", "id", "CASCADE") in fk_targets
    engine.dispose()


def test_0012_recreates_fts_triggers_and_rebuilds_index(
    migration_db: Path,
) -> None:
    """FTS5 trigger lifecycle: the three invoices_ai/ad/au triggers
    don't survive a batch_alter_table rewrite. 0012 drops them up front
    and recreates them after, and repopulates the invoices_fts index so
    FTS queries against existing rows still return the right docids."""
    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")
    _seed_admin_user(migration_db)
    _insert_invoice(migration_db, invoice_no="INV-FTS", user_id=1)

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS invoices_fts USING fts5("
                "invoice_no, buyer, seller, invoice_type, item_summary, raw_text, "
                "content='invoices', content_rowid='id')"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TRIGGER IF NOT EXISTS invoices_ai AFTER INSERT ON invoices BEGIN "
                "INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, "
                "item_summary, raw_text) VALUES (new.id, new.invoice_no, new.buyer, "
                "new.seller, new.invoice_type, new.item_summary, new.raw_text); END"
            )
        )
        conn.execute(sa.text("INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')"))
    engine.dispose()

    _upgrade_to(migration_db, "0012_tighten_user_id_constraints")

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        triggers = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='invoices'"
                )
            ).all()
        }
        assert triggers == {"invoices_ai", "invoices_ad", "invoices_au"}, (
            f"FTS triggers must be recreated, got {triggers}"
        )

        match_row = conn.execute(
            sa.text(
                "SELECT rowid FROM invoices_fts WHERE invoices_fts MATCH 'buyer' LIMIT 1"
            )
        ).first()
        assert match_row is not None, "FTS index must contain pre-existing invoice rows"
    engine.dispose()


def test_0012_seeds_admin_from_env_when_users_empty(
    migration_db: Path, bootstrap_env: None
) -> None:
    """Fresh-install path: alembic runs before the app has ever booted,
    users table is empty. Migration 0012 reads ADMIN_EMAIL and
    ADMIN_PASSWORD_HASH from the environment and inserts users[1]
    itself so the NOT NULL + FK tightening succeeds."""
    del bootstrap_env
    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    _upgrade_to(migration_db, "0012_tighten_user_id_constraints")

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        users = conn.execute(sa.text("SELECT id, email FROM users")).all()
        assert len(users) == 1
        assert users[0][0] == 1
        assert users[0][1] == "admin@local"

        manual_account_user = conn.execute(
            sa.text("SELECT user_id FROM email_accounts WHERE type='manual'")
        ).scalar_one_or_none()
        assert manual_account_user == 1, (
            "The manual-upload sentinel account from migration 0008 "
            "must have its user_id backfilled by 0012"
        )
    engine.dispose()


def test_0012_refuses_to_run_without_admin_seed_inputs(
    migration_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If users table is empty and ADMIN_EMAIL / ADMIN_PASSWORD_HASH are
    missing, the migration must fail loudly with an actionable message
    rather than silently leave NULL user_ids in place.

    Note: alembic env.py calls ``load_dotenv`` on every invocation to
    surface ``backend/.env`` to migrations (see
    ``_load_dotenv_if_present``). That would silently re-hydrate the
    admin credentials here; we neutralize it for this test by
    pointing env.py at a non-existent path."""
    monkeypatch.setenv("ALEMBIC_SKIP_DOTENV", "1")

    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    with pytest.raises(RuntimeError, match="bootstrap admin"):
        _upgrade_to(migration_db, "0012_tighten_user_id_constraints")


def test_0012_downgrade_refuses_on_cross_user_invoice_no_collision(
    migration_db: Path,
) -> None:
    """The pre-0012 schema enforces global UNIQUE(invoice_no). If a
    second user has been added and both own an invoice with the same
    invoice_no, downgrading would violate that constraint.
    Preflight must refuse rather than either blowing up halfway
    through or silently dropping data."""
    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")
    _seed_admin_user(migration_db)
    _insert_invoice(migration_db, invoice_no="INV-CONFLICT", user_id=1)

    _upgrade_to(migration_db, "0012_tighten_user_id_constraints")

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, hashed_password, is_active, "
                "is_admin, created_at, updated_at) VALUES "
                "(2, 'other@example.com', 'h', 1, 0, '2026-04-21', '2026-04-21')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO email_accounts (id, user_id, name, type, username, "
                "is_active, created_at) VALUES "
                "(99, 2, 'Other', 'imap', 'o@example.com', 1, '2026-04-21')"
            )
        )
    engine.dispose()

    _insert_invoice(migration_db, invoice_no="INV-CONFLICT", user_id=2, email_account_id=99)

    with pytest.raises(RuntimeError, match="Cannot downgrade 0012"):
        _downgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")


def test_0012_downgrade_restores_nullable_and_global_unique(
    migration_db: Path,
) -> None:
    """Clean downgrade: when no invoice_no collisions exist, 0012
    downgrades cleanly back to the 0011 shape — user_id nullable,
    no FK to users, and ix_invoices_invoice_no back to UNIQUE."""
    _upgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")
    _seed_admin_user(migration_db)
    _insert_invoice(migration_db, invoice_no="INV-ROUNDTRIP", user_id=1)

    _upgrade_to(migration_db, "0012_tighten_user_id_constraints")
    _downgrade_to(migration_db, "0011_add_user_id_to_tenant_tables")

    assert _current_revision(migration_db) == "0011_add_user_id_to_tenant_tables"

    sync_url = f"sqlite:///{migration_db}"
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        cols = {
            row[1]: row
            for row in conn.execute(sa.text("PRAGMA table_info(invoices)")).all()
        }
        assert cols["user_id"][3] == 0, "user_id must be nullable again"

        fks = conn.execute(sa.text("PRAGMA foreign_key_list(invoices)")).all()
        fk_targets = [fk[2] for fk in fks]
        assert "users" not in fk_targets, "FK to users must be dropped on downgrade"

        legacy_sql = conn.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE name='ix_invoices_invoice_no'"
            )
        ).scalar_one()
        assert "UNIQUE" in legacy_sql.upper()

        composite = conn.execute(
            sa.text(
                "SELECT 1 FROM sqlite_master WHERE name='uq_invoices_user_id_invoice_no'"
            )
        ).first()
        assert composite is None

        surviving = conn.execute(
            sa.text("SELECT invoice_no FROM invoices")
        ).scalar_one()
        assert surviving == "INV-ROUNDTRIP"
    engine.dispose()


def _insert_invoice_with_file_path(
    db_path: Path, *, invoice_id: int, invoice_no: str, user_id: int, file_path: str
) -> None:
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO invoices (id, invoice_no, buyer, seller, amount, "
                "invoice_date, invoice_type, file_path, raw_text, email_uid, "
                "email_account_id, source_format, extraction_method, confidence, "
                "is_manually_corrected, created_at, user_id) VALUES "
                "(:id, :invoice_no, 'B', 'S', 10.00, '2026-01-01', 'vat', :file_path, '', "
                "'u1', 2, 'pdf', 'regex', 0.9, 0, '2026-04-21', :user_id)"
            ),
            {
                "id": invoice_id,
                "invoice_no": invoice_no,
                "user_id": user_id,
                "file_path": file_path,
            },
        )
    engine.dispose()


def _read_file_path(db_path: Path, invoice_id: int) -> str:
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        path = conn.execute(
            sa.text("SELECT file_path FROM invoices WHERE id = :id"),
            {"id": invoice_id},
        ).scalar_one()
    engine.dispose()
    return path


@pytest.fixture
def storage_path_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point migration 0013 at a fresh STORAGE_PATH so its file moves
    happen inside the test tmp_path rather than the developer's actual
    data directory. Migration 0013 resolves STORAGE_PATH from the env
    var first (see ``_derive_storage_path``), so this fixture gates
    every file operation cleanly."""
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    return storage


def _seed_invoice_infra(db_path: Path) -> None:
    """Every 0013 test seeds the same baseline: upgrade to 0011, seed
    user 1 (so 0012's NOT NULL tightening succeeds), seed one email
    account (id=2 because migration 0008 already seeded id=1 as the
    Manual Uploads sentinel), then upgrade to 0012. Invoice rows get
    added separately so each test controls its own file_path shape."""
    _upgrade_to(db_path, "0011_add_user_id_to_tenant_tables")
    _seed_admin_user(db_path)
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO email_accounts (id, user_id, name, type, username, "
                "is_active, created_at) VALUES "
                "(2, 1, 'A', 'imap', 'a@e.com', 1, '2026-04-21')"
            )
        )
    engine.dispose()
    _upgrade_to(db_path, "0012_tighten_user_id_constraints")


def test_0013_moves_flat_file_into_user_subdir(
    migration_db: Path, storage_path_env: Path
) -> None:
    _seed_invoice_infra(migration_db)

    flat_file = storage_path_env / "invoice-001.pdf"
    flat_file.write_bytes(b"pdf-content-001")
    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-1", user_id=1,
        file_path="invoice-001.pdf",
    )

    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")

    new_path = storage_path_env / "users" / "1" / "invoices" / "invoice-001.pdf"
    assert new_path.exists()
    assert new_path.read_bytes() == b"pdf-content-001"
    assert not flat_file.exists()
    assert _read_file_path(migration_db, 1) == "users/1/invoices/invoice-001.pdf"


def test_0013_skips_rows_already_migrated(
    migration_db: Path, storage_path_env: Path
) -> None:
    """Idempotency: running 0013 against a DB whose file_path is
    already in the new users/ form must leave the filesystem and the
    DB unchanged."""
    _seed_invoice_infra(migration_db)

    new_dir = storage_path_env / "users" / "1" / "invoices"
    new_dir.mkdir(parents=True)
    target = new_dir / "inv.pdf"
    target.write_bytes(b"already-migrated")
    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-1", user_id=1,
        file_path="users/1/invoices/inv.pdf",
    )

    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")

    assert target.read_bytes() == b"already-migrated"
    assert _read_file_path(migration_db, 1) == "users/1/invoices/inv.pdf"


def test_0013_missing_file_on_disk_updates_db_only(
    migration_db: Path, storage_path_env: Path
) -> None:
    """Degraded-state safety: row whose file is already gone from disk
    (manual deletion, storage wipe) must not fail the migration. The
    DB is still rewritten so future downloads behave identically (404
    because the file is gone, same as before)."""
    _seed_invoice_infra(migration_db)

    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-MISSING", user_id=1,
        file_path="deleted.pdf",
    )

    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")

    assert _read_file_path(migration_db, 1) == "users/1/invoices/deleted.pdf"
    assert not (storage_path_env / "users" / "1" / "invoices" / "deleted.pdf").exists()


def test_0013_new_path_already_exists_updates_db_without_moving(
    migration_db: Path, storage_path_env: Path
) -> None:
    """Partial-run recovery: if the new path is already populated (from
    a crashed prior migration run) and the old path is missing, just
    update the DB — never overwrite existing data."""
    _seed_invoice_infra(migration_db)

    new_dir = storage_path_env / "users" / "1" / "invoices"
    new_dir.mkdir(parents=True)
    existing_new = new_dir / "partial.pdf"
    existing_new.write_bytes(b"survived-partial-run")
    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-PARTIAL", user_id=1,
        file_path="partial.pdf",
    )

    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")

    assert existing_new.read_bytes() == b"survived-partial-run"
    assert _read_file_path(migration_db, 1) == "users/1/invoices/partial.pdf"


def test_0013_dry_run_makes_no_disk_or_db_changes(
    migration_db: Path, storage_path_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DRY_RUN=1 must be a perfectly read-only pass. Operators rely on
    this for pre-flight validation against production DB copies."""
    _seed_invoice_infra(migration_db)

    flat_file = storage_path_env / "dryrun.pdf"
    flat_file.write_bytes(b"pre-dry-run")
    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-DRY", user_id=1,
        file_path="dryrun.pdf",
    )

    monkeypatch.setenv("DRY_RUN", "1")
    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")

    assert flat_file.exists()
    assert flat_file.read_bytes() == b"pre-dry-run"
    assert not (storage_path_env / "users").exists()
    assert _read_file_path(migration_db, 1) == "dryrun.pdf"


def test_0013_downgrade_reverses_move(
    migration_db: Path, storage_path_env: Path
) -> None:
    _seed_invoice_infra(migration_db)

    flat_file = storage_path_env / "roundtrip.pdf"
    flat_file.write_bytes(b"roundtrip-content")
    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-RT", user_id=1,
        file_path="roundtrip.pdf",
    )

    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")
    assert not flat_file.exists()
    _downgrade_to(migration_db, "0012_tighten_user_id_constraints")

    assert flat_file.exists()
    assert flat_file.read_bytes() == b"roundtrip-content"
    assert _read_file_path(migration_db, 1) == "roundtrip.pdf"


def test_0013_downgrade_skips_rows_already_flat(
    migration_db: Path, storage_path_env: Path
) -> None:
    """Idempotency on the reverse direction: if the row is already in
    flat form (someone manually reverted it, or the upgrade partially
    ran), downgrade must not error."""
    _seed_invoice_infra(migration_db)

    flat_file = storage_path_env / "already-flat.pdf"
    flat_file.write_bytes(b"already-flat")
    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-FLAT", user_id=1,
        file_path="already-flat.pdf",
    )

    _downgrade_to(migration_db, "0012_tighten_user_id_constraints")

    assert flat_file.read_bytes() == b"already-flat"
    assert _read_file_path(migration_db, 1) == "already-flat.pdf"


def test_0013_downgrade_missing_file_updates_db_only(
    migration_db: Path, storage_path_env: Path
) -> None:
    _seed_invoice_infra(migration_db)

    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-DN", user_id=1,
        file_path="users/1/invoices/gone.pdf",
    )
    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")
    _downgrade_to(migration_db, "0012_tighten_user_id_constraints")

    assert _read_file_path(migration_db, 1) == "gone.pdf"
    assert not (storage_path_env / "gone.pdf").exists()


def test_0013_downgrade_collision_updates_db_without_moving(
    migration_db: Path, storage_path_env: Path
) -> None:
    _seed_invoice_infra(migration_db)

    flat = storage_path_env / "collision.pdf"
    flat.write_bytes(b"flat-already-here")

    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-CL", user_id=1,
        file_path="users/1/invoices/collision.pdf",
    )
    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")
    _downgrade_to(migration_db, "0012_tighten_user_id_constraints")

    assert flat.read_bytes() == b"flat-already-here"
    assert _read_file_path(migration_db, 1) == "collision.pdf"


def test_0013_storage_path_falls_back_to_db_relative_when_env_unset(
    migration_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The migration's ``_derive_storage_path`` uses a three-step
    precedence: STORAGE_PATH env -> URL-derived default
    (``{db_parent.parent}/invoices``) -> ``./data/invoices``. This
    exercises the URL-derived branch so a deploy that sets
    ``DATABASE_URL`` but forgets ``STORAGE_PATH`` still targets the
    right directory. ``ALEMBIC_SKIP_DOTENV`` disables env.py's
    ``.env`` auto-load so a dev ``.env`` doesn't smuggle its
    ``STORAGE_PATH`` in."""
    monkeypatch.setenv("ALEMBIC_SKIP_DOTENV", "1")
    monkeypatch.delenv("STORAGE_PATH", raising=False)

    _seed_invoice_infra(migration_db)

    storage_root = migration_db.parent.parent / "invoices"
    storage_root.mkdir(parents=True, exist_ok=True)

    flat_file = storage_root / "derived.pdf"
    flat_file.write_bytes(b"derived-branch")

    _insert_invoice_with_file_path(
        migration_db, invoice_id=1, invoice_no="INV-D", user_id=1,
        file_path="derived.pdf",
    )

    _upgrade_to(migration_db, "0013_migrate_files_to_user_subdirs")

    new_abs = storage_root / "users" / "1" / "invoices" / "derived.pdf"
    assert new_abs.exists()
    assert new_abs.read_bytes() == b"derived-branch"
    assert _read_file_path(migration_db, 1) == "users/1/invoices/derived.pdf"


def test_0013_storage_path_fallback_to_data_invoices_for_non_sqlite_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final fallback branch: non-SQLite URL with no STORAGE_PATH env
    var resolves to ``./data/invoices`` relative to CWD. This test
    exercises the helper directly rather than running alembic, since
    alembic already requires a connectable URL."""
    import importlib.util
    from unittest.mock import patch

    monkeypatch.delenv("STORAGE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    module_path = (
        BACKEND_ROOT / "alembic" / "versions" / "0013_migrate_files_to_user_subdirs.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0013_under_test", module_path
    )
    assert spec is not None and spec.loader is not None
    migration_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_module)

    class _FakeEngine:
        url = "postgresql://user@host/db"

    class _FakeConn:
        engine = _FakeEngine()

    with patch.object(migration_module.op, "get_bind", return_value=_FakeConn()):
        resolved = migration_module._derive_storage_path()

    assert resolved == (tmp_path / "data" / "invoices").resolve()
