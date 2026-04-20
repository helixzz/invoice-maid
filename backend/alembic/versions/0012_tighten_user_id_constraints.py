"""Tighten user_id constraints on tenant tables (Phase 3 of v0.9.0).

Revision ID: 0012_tighten_user_id_constraints
Revises: 0011_add_user_id_to_tenant_tables
Create Date: 2026-04-21

Follows Phase 2 migration 0011 which added nullable ``user_id``
columns to seven tenant tables and backfilled existing rows to
``users[1]``. Phase 3 makes those columns ``NOT NULL``, adds the
``CASCADE`` foreign key to ``users.id``, and replaces the global
``UNIQUE(invoice_no)`` index on ``invoices`` with a composite
``UNIQUE(user_id, invoice_no)`` so two users can legally receive
invoices carrying the same number.

Bootstrap contract: on a fresh install the deploy script runs
``alembic upgrade head`` before the app has ever booted, so at this
point the ``users`` table may still be empty. Migration 0011's
backfill is a no-op in that shape, and migration 0008's
"Manual Uploads" sentinel ``email_account`` row from way earlier also
has ``user_id = NULL``. This migration therefore seeds ``users[1]``
itself from ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD_HASH`` environment
variables if the table is still empty — the same row the
application's ``bootstrap_admin_user`` would create on first boot.
Bootstrap on first boot becomes a no-op (the table is no longer
empty) and the env-var credentials remain the source of truth.
After seeding, any remaining ``NULL`` ``user_id`` rows are claimed
for that admin so the impending ``NOT NULL`` tightening succeeds.

If neither ``ADMIN_EMAIL`` nor ``ADMIN_PASSWORD_HASH`` is available in
the environment (CI-simulated test, programmatic migration), the
migration refuses to run rather than inserting a placeholder row or
silently leaving ``NULL`` values behind — corrupting the schema
contract is worse than failing loudly.

``invoices`` is processed LAST because of its FTS5 entanglement:

* The ``invoices_fts`` virtual table is a SQLite FTS5 external-content
  table with ``content='invoices'``. It survives a table rewrite, but
  its three sync triggers (``invoices_ai``, ``invoices_ad``,
  ``invoices_au``) do NOT — ``batch_alter_table`` copies data into a
  new table and drops the old, and triggers attached to the old table
  are lost in the process.
* ``create_fts5_objects`` in the application (``app/database.py``)
  recreates these triggers on every app start via ``IF NOT EXISTS``.
  That's a belt-and-braces safety net, but this migration must also
  recreate them explicitly so the database is left in a consistent
  state *before* the app next starts — a post-migration smoke test,
  an ad-hoc ``sqlite3`` read, or an operator-initiated scan all need
  the triggers in place without a prior app restart.
* After the table rewrite, the FTS5 index is repopulated via
  ``INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')`` so
  its rowid mappings line up with the new ``invoices`` table.

Foreign-key enforcement: Alembic's ``env.py`` in this project does
NOT install the application's SQLite ``connect`` hook, so the
Alembic connection runs with ``PRAGMA foreign_keys=OFF`` by default.
That means batch-table rewrites don't trip FK checks mid-rewrite
even though the parent tables (``email_accounts``, ``scan_logs``,
``invoices``) are themselves parents of FKs from other tables.
All tenant rows are backfilled to ``user_id=1`` before the tightening
pass, so once the app re-enables FK enforcement on next connect the
constraints are already satisfied.

Downgrade reverses the composite uniqueness, drops the FK, and
relaxes the columns back to nullable. A preflight check refuses the
downgrade if any user has more than one row with the same
``invoice_no`` — that would violate the global ``UNIQUE(invoice_no)``
the old schema enforces, and silently dropping data is worse than
refusing to run.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0012_tighten_user_id_constraints"
down_revision = "0011_add_user_id_to_tenant_tables"
branch_labels = None
depends_on = None


TENANT_TABLES_ORDER = (
    "email_accounts",
    "scan_logs",
    "extraction_logs",
    "correction_logs",
    "saved_views",
    "webhook_logs",
    "invoices",
)


FTS_TRIGGERS = ("invoices_ai", "invoices_ad", "invoices_au")


FTS_TRIGGER_SQL = {
    "invoices_ai": """
        CREATE TRIGGER invoices_ai AFTER INSERT ON invoices BEGIN
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END
    """,
    "invoices_ad": """
        CREATE TRIGGER invoices_ad AFTER DELETE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
        END
    """,
    "invoices_au": """
        CREATE TRIGGER invoices_au AFTER UPDATE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END
    """,
}


def _existing_tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _drop_fts_triggers_if_present(bind: sa.Connection) -> None:
    for trigger_name in FTS_TRIGGERS:
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))


def _recreate_fts_triggers(bind: sa.Connection) -> None:
    for trigger_name in FTS_TRIGGERS:
        bind.execute(sa.text(FTS_TRIGGER_SQL[trigger_name]))


def _rebuild_fts_index(bind: sa.Connection) -> None:
    has_fts = bind.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoices_fts' LIMIT 1"
        )
    ).first()
    if has_fts is not None:
        bind.execute(sa.text("INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')"))


def _ensure_admin_user_exists(bind: sa.Connection) -> None:
    existing = bind.execute(sa.text("SELECT id FROM users LIMIT 1")).first()
    if existing is not None:
        return

    admin_email = (os.getenv("ADMIN_EMAIL") or "admin@local").strip().lower()
    admin_password_hash = os.getenv("ADMIN_PASSWORD_HASH") or ""
    if not admin_email or not admin_password_hash:
        raise RuntimeError(
            "Migration 0012 requires a bootstrap admin user. The users "
            "table is empty and ADMIN_EMAIL / ADMIN_PASSWORD_HASH are "
            "not both set in the environment. Either start the app once "
            "before running this migration (so the bootstrap hook seeds "
            "users[1]) or set both env vars and re-run `alembic upgrade "
            "head`."
        )

    now = datetime.now(timezone.utc).isoformat()
    bind.execute(
        sa.text(
            "INSERT INTO users (id, email, hashed_password, is_active, "
            "is_admin, created_at, updated_at) VALUES "
            "(1, :email, :hash, 1, 1, :created_at, :updated_at)"
        ),
        {
            "email": admin_email,
            "hash": admin_password_hash,
            "created_at": now,
            "updated_at": now,
        },
    )


def _backfill_any_remaining_nulls(bind: sa.Connection, present: set[str]) -> None:
    for table in TENANT_TABLES_ORDER:
        if table not in present:
            continue
        bind.execute(
            sa.text(f"UPDATE {table} SET user_id = 1 WHERE user_id IS NULL")
        )


def _tighten_user_id_non_null_with_fk(table: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.alter_column(
            "user_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_foreign_key(
            f"fk_{table}_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def _relax_user_id_nullable_drop_fk(table: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(f"fk_{table}_user_id_users", type_="foreignkey")
        batch_op.alter_column(
            "user_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing_tables(bind)

    _ensure_admin_user_exists(bind)
    _backfill_any_remaining_nulls(bind, present)

    for table in TENANT_TABLES_ORDER:
        if table not in present:
            continue
        if table == "invoices":
            continue
        _tighten_user_id_non_null_with_fk(table)

    if "invoices" in present:
        _drop_fts_triggers_if_present(bind)

        op.drop_index("ix_invoices_invoice_no", table_name="invoices")

        with op.batch_alter_table("invoices") as batch_op:
            batch_op.alter_column(
                "user_id",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch_op.create_foreign_key(
                "fk_invoices_user_id_users",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

        op.create_index(
            "uq_invoices_user_id_invoice_no",
            "invoices",
            ["user_id", "invoice_no"],
            unique=True,
        )
        op.create_index(
            "ix_invoices_invoice_no",
            "invoices",
            ["invoice_no"],
            unique=False,
        )

        _recreate_fts_triggers(bind)
        _rebuild_fts_index(bind)


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing_tables(bind)

    if "invoices" in present:
        duplicate_invoice_no = bind.execute(
            sa.text(
                "SELECT invoice_no, COUNT(*) AS c FROM invoices "
                "GROUP BY invoice_no HAVING c > 1 LIMIT 1"
            )
        ).first()
        if duplicate_invoice_no is not None:
            raise RuntimeError(
                "Cannot downgrade 0012: at least two invoices share "
                f"invoice_no={duplicate_invoice_no[0]!r}. The pre-0012 "
                "schema enforces global UNIQUE(invoice_no); downgrade "
                "would either fail or silently corrupt. Resolve the "
                "duplicate manually before retrying."
            )

        _drop_fts_triggers_if_present(bind)

        op.drop_index("ix_invoices_invoice_no", table_name="invoices")
        op.drop_index("uq_invoices_user_id_invoice_no", table_name="invoices")

        with op.batch_alter_table("invoices") as batch_op:
            batch_op.drop_constraint("fk_invoices_user_id_users", type_="foreignkey")
            batch_op.alter_column(
                "user_id",
                existing_type=sa.Integer(),
                nullable=True,
            )

        op.create_index(
            "ix_invoices_invoice_no",
            "invoices",
            ["invoice_no"],
            unique=True,
        )

        _recreate_fts_triggers(bind)
        _rebuild_fts_index(bind)

    for table in reversed(TENANT_TABLES_ORDER):
        if table not in present:
            continue
        if table == "invoices":
            continue
        _relax_user_id_nullable_drop_fk(table)

