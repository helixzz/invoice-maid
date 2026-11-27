"""Add failed_emails persistent retry queue table.

Revision ID: 0020_failed_emails_queue
Revises: 0019_restore_invoice_number_format
Create Date: 2026-11-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_failed_emails_queue"
down_revision = "0019_restore_invoice_number_format"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failed_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email_account_id", sa.Integer(), sa.ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email_uid", sa.String(length=255), nullable=False),
        sa.Column("email_subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("from_addr", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_links_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("headers_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_hydrated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email_account_id", "email_uid", name="uq_failed_emails_account_uid"),
    )
    op.create_index("ix_failed_emails_user_id", "failed_emails", ["user_id"])
    op.create_index("ix_failed_emails_email_account_id", "failed_emails", ["email_account_id"])
    op.create_index("ix_failed_emails_next_retry_at", "failed_emails", ["next_retry_at"])
    op.create_index("ix_failed_emails_status", "failed_emails", ["status"])


def downgrade() -> None:
    op.drop_index("ix_failed_emails_status", table_name="failed_emails")
    op.drop_index("ix_failed_emails_next_retry_at", table_name="failed_emails")
    op.drop_index("ix_failed_emails_email_account_id", table_name="failed_emails")
    op.drop_index("ix_failed_emails_user_id", table_name="failed_emails")
    op.drop_table("failed_emails")
