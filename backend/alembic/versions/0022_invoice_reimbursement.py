"""Add reimbursement workflow columns (reimbursement_status, tags, notes) to invoices.

Revision ID: 0022_invoice_reimbursement
Revises: 0021_invoice_currency
Create Date: 2026-11-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_invoice_reimbursement"
down_revision = "0021_invoice_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "reimbursement_status",
            sa.String(length=32),
            nullable=False,
            server_default="unreimbursed",
        ),
    )
    op.add_column(
        "invoices",
        sa.Column("tags", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "invoices",
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_invoices_user_id_reimbursement_status",
        "invoices",
        ["user_id", "reimbursement_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_user_id_reimbursement_status", table_name="invoices")
    op.drop_column("invoices", "notes")
    op.drop_column("invoices", "tags")
    op.drop_column("invoices", "reimbursement_status")
