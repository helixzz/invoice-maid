"""Add nullable user_id columns to tenant tables (Phase 2 of v0.9.0).

Revision ID: 0011_add_user_id_to_tenant_tables
Revises: 0010_users_and_sessions
Create Date: 2026-04-21

Additive only: adds a nullable ``user_id INTEGER`` column to each
tenant-scoped table, backfills existing rows to ``users[1]`` (the
bootstrap admin row inserted by the application's lifespan hook after
migration 0010), and creates per-table indexes tuned for the
multi-tenant query shapes the app actually issues.

No NOT NULL. No FK constraint. No unique-composite changes. Those
structural tightenings are deferred to migration 0012 (Phase 3) where
they belong — doing them here would couple schema correctness to
bootstrap having run, which the application guarantees only *after*
alembic has completed. Keeping this migration purely additive also
means zero risk to the existing v0.8.x/v0.9.0-alpha.3 query planner:
old queries see an extra column they ignore, new queries use the new
indexes.

Backfill rule: if a ``users`` row with ``id = 1`` exists at migration
time, every NULL ``user_id`` is updated to ``1``. If no such row exists
(fresh install where bootstrap will run on first app start *after* this
migration), the column is left NULL for every row; the running app's
first-boot bootstrap creates the user and the separate per-table
tenant-association work in Phase 4 will claim orphan rows then.
Crucially: the ``tenant_tables`` the app actually writes to on a fresh
install are empty at this point, so the NULL-is-fine branch never
matters in practice — it only exists to keep this migration runnable
standalone during test-harness migrations and documentation builds.

Indexing strategy (per sustained query-shape analysis of the codebase):

* ``invoices``       — composite ``(user_id, invoice_date DESC)``
                       matches the default list + search query.
* ``email_accounts`` — single-column ``(user_id)`` — small table, only
                       ever filtered by user.
* ``scan_logs``      — composite ``(user_id, started_at DESC)`` for the
                       scan-history UI and orphan-cleanup pass.
* ``extraction_logs``— composite ``(user_id, created_at DESC)`` —
                       largest table by row count; this is the one that
                       really needs the composite.
* ``correction_logs``— single-column ``(user_id)``.
* ``saved_views``    — single-column ``(user_id)``.
* ``webhook_logs``   — composite ``(user_id, created_at DESC)`` for the
                       per-user delivery audit view in Phase 5.

Rollback drops the indexes and columns cleanly. It does not touch data
in other columns. SQLite's ``batch_alter_table`` handles the column
drop by table rebuild; that's an O(N) copy but the alternative (leaving
dead columns) silently breaks subsequent downgrades.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_add_user_id_to_tenant_tables"
down_revision = "0010_users_and_sessions"
branch_labels = None
depends_on = None


# (table, index_name, index_cols) — declarative so upgrade and downgrade
# stay in lockstep. ``index_cols`` is the list literally passed to
# ``op.create_index``; DESC ordering is handled by ``sa.text(...)`` to
# survive SQLite's index expression parser.
TENANT_TABLES: tuple[tuple[str, str, list], ...] = (
    (
        "invoices",
        "ix_invoices_user_id_invoice_date",
        ["user_id", sa.text("invoice_date DESC")],
    ),
    (
        "email_accounts",
        "ix_email_accounts_user_id",
        ["user_id"],
    ),
    (
        "scan_logs",
        "ix_scan_logs_user_id_started_at",
        ["user_id", sa.text("started_at DESC")],
    ),
    (
        "extraction_logs",
        "ix_extraction_logs_user_id_created_at",
        ["user_id", sa.text("created_at DESC")],
    ),
    (
        "correction_logs",
        "ix_correction_logs_user_id",
        ["user_id"],
    ),
    (
        "saved_views",
        "ix_saved_views_user_id",
        ["user_id"],
    ),
    (
        "webhook_logs",
        "ix_webhook_logs_user_id_created_at",
        ["user_id", sa.text("created_at DESC")],
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Some tenant tables (``saved_views``, ``webhook_logs``) are not
    # created by any Alembic migration — they come from
    # ``Base.metadata.create_all`` at application startup. On a fresh
    # install the deploy script runs ``alembic upgrade head`` before the
    # first app boot, so those tables do not yet exist at this revision.
    # Skip them here; ``create_all`` on first app start materialises
    # them with the ``user_id`` column already present via the ORM
    # definition, and the matching column-level ``index=True`` creates
    # the same index SQLAlchemy-side. Existing deployments already have
    # every table, so in practice the loop runs over all 7 entries.
    tables_to_migrate = tuple(
        (table, index_name, index_cols)
        for table, index_name, index_cols in TENANT_TABLES
        if table in existing_tables
    )

    # Step 1: add the nullable column to every tenant table that exists.
    # FK and NOT NULL tightening are deferred to Phase 3 migration 0012
    # so this migration stays purely additive and the query planner
    # keeps its existing plans.
    for table, _index_name, _index_cols in tables_to_migrate:
        op.add_column(
            table,
            sa.Column("user_id", sa.Integer(), nullable=True),
        )

    # Step 2: backfill. If users[1] exists (production path: bootstrap
    # ran on first boot after migration 0010), claim every existing row
    # for that user. If not (fresh greenfield install where alembic
    # upgrade runs before the app has ever started), leave NULL — the
    # tables are empty anyway, so there's nothing to backfill.
    admin_exists = bind.execute(
        sa.text("SELECT 1 FROM users WHERE id = 1 LIMIT 1")
    ).first()

    if admin_exists is not None:
        for table, _index_name, _index_cols in tables_to_migrate:
            bind.execute(
                sa.text(f"UPDATE {table} SET user_id = 1 WHERE user_id IS NULL")
            )

    # Step 3: index. Done after backfill so the index builds against
    # populated data rather than absorbing an N-row UPDATE afterward.
    for table, index_name, index_cols in tables_to_migrate:
        op.create_index(index_name, table, index_cols)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    tables_to_revert = tuple(
        (table, index_name, index_cols)
        for table, index_name, index_cols in TENANT_TABLES
        if table in existing_tables
    )

    # Reverse order: drop indexes first (they reference the column),
    # then the column itself. SQLite's batch_alter_table rewrites the
    # table on drop_column — acceptable because downgrade is an operator
    # action, not a hot-path.
    existing_indexes: dict[str, set[str]] = {
        table: {idx["name"] for idx in inspector.get_indexes(table)}
        for table, _index_name, _index_cols in tables_to_revert
    }
    for table, index_name, _index_cols in reversed(tables_to_revert):
        if index_name in existing_indexes.get(table, set()):
            op.drop_index(index_name, table_name=table)

    for table, _index_name, _index_cols in reversed(tables_to_revert):
        columns = {col["name"] for col in inspector.get_columns(table)}
        if "user_id" in columns:
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column("user_id")
