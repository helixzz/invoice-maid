"""Operational hardening: TTL-based eviction for LLMCache (v0.8.10).

Revision ID: 0009_llm_cache_expires_at
Revises: 0008_manual_upload_pseudo_account
Create Date: 2026-04-21

Adds an ``expires_at`` column to ``llm_cache`` so a nightly cleanup job can
evict stale entries instead of letting the table grow unboundedly. Existing
rows are backfilled with conservative expiration windows based on the
``prompt_type``:

  - ``classify``  expires 30 days after ``created_at`` (email subjects and
    classification heuristics drift over time; stale hits produce stale
    classifications)
  - ``analyze_email_v3`` same rationale as ``classify`` — 30 day window
  - ``extract`` expires 365 days after ``created_at`` (invoice PDF content
    is effectively immutable once captured; cached extractions stay valid
    for a year before we want to re-invoke the LLM under the current prompt)

An index on ``expires_at`` supports the cleanup job's
``WHERE expires_at < :now`` query without a full-table scan.

The eviction job itself is registered by ``start_scheduler`` in
``tasks/scheduler.py`` and runs hourly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_llm_cache_expires_at"
down_revision = "0008_manual_upload_pseudo_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_cache",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_llm_cache_expires_at", "llm_cache", ["expires_at"], unique=False
    )

    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        bind.execute(
            sa.text(
                "UPDATE llm_cache SET expires_at = "
                "CASE WHEN prompt_type IN ('classify', 'analyze_email_v3') "
                "THEN datetime(created_at, '+30 days') "
                "ELSE datetime(created_at, '+365 days') END "
                "WHERE expires_at IS NULL"
            )
        )
    else:
        bind.execute(
            sa.text(
                "UPDATE llm_cache SET expires_at = "
                "CASE WHEN prompt_type IN ('classify', 'analyze_email_v3') "
                "THEN created_at + INTERVAL '30 days' "
                "ELSE created_at + INTERVAL '365 days' END "
                "WHERE expires_at IS NULL"
            )
        )


def downgrade() -> None:
    op.drop_index("ix_llm_cache_expires_at", table_name="llm_cache")
    op.drop_column("llm_cache", "expires_at")
