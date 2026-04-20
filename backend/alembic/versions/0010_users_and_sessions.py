"""Create users + user_sessions for multi-user transition (Phase 1 of v0.9.0).

Revision ID: 0010_users_and_sessions
Revises: 0009_llm_cache_expires_at
Create Date: 2026-04-21

Adds the foundational tables for the multi-user transition without yet
tenant-scoping existing data (that's Phase 2 / migration 0011 onward).
The existing single-operator deployment continues to work exactly as
before because no existing table is modified here — this migration is
purely additive.

The bootstrap admin row is NOT inserted by Alembic. The application's
``lifespan`` hook creates ``users[1]`` on first boot after this migration
runs, reading ``ADMIN_PASSWORD_HASH`` and ``ADMIN_EMAIL`` from the
environment. This keeps secrets out of migration files and makes the
migration deterministically reversible.

Rollback: dropping both tables is safe because nothing else references
them yet. Phase 2's FK additions are the point where rollback becomes
substantive work — but that's a separate migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_users_and_sessions"
down_revision = "0009_llm_cache_expires_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
    )
    op.create_index(
        "ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_user_sessions_user_id_revoked_at",
        "user_sessions",
        ["user_id", "revoked_at"],
    )
    op.create_index(
        "ix_user_sessions_expires_at", "user_sessions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id_revoked_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_token_hash", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
