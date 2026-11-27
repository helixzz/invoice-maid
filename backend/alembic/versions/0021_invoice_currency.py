"""Add real currency column to invoices and backfill overseas invoices.

Revision ID: 0021_invoice_currency
Revises: 0020_failed_emails_queue
Create Date: 2026-11-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_invoice_currency"
down_revision = "0020_failed_emails_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
    )
    op.execute(
        "UPDATE invoices SET currency = 'USD' WHERE invoice_category = 'overseas_invoice'"
    )
    op.create_index("ix_invoices_user_id_currency", "invoices", ["user_id", "currency"])


def downgrade() -> None:
    op.drop_index("ix_invoices_user_id_currency", table_name="invoices")
    op.drop_column("invoices", "currency")
