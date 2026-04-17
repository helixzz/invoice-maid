"""v0.2.0 audit trail and corrections

Revision ID: 0002_v020_audit_and_corrections
Revises: 0001_initial_schema
Create Date: 2026-04-17 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_v020_audit_and_corrections"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("is_manually_corrected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "correction_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_correction_logs_invoice_id", "correction_logs", ["invoice_id"], unique=False)

    op.create_table(
        "extraction_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_log_id", sa.Integer(), nullable=False),
        sa.Column("email_uid", sa.String(length=255), nullable=True),
        sa.Column("email_subject", sa.String(length=500), nullable=False),
        sa.Column("attachment_filename", sa.String(length=500), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("invoice_no", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("error_detail", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_log_id"], ["scan_logs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_extraction_logs_scan_log_id", "extraction_logs", ["scan_log_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_extraction_logs_scan_log_id", table_name="extraction_logs")
    op.drop_table("extraction_logs")

    op.drop_index("ix_correction_logs_invoice_id", table_name="correction_logs")
    op.drop_table("correction_logs")

    op.drop_column("invoices", "is_manually_corrected")
