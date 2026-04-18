"""v0.7.5 last_scan_uid String -> Text

Revision ID: 0007_last_scan_uid_text
Revises: 0006_extraction_parse_metadata
Create Date: 2026-04-18 12:45:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_last_scan_uid_text"
down_revision = "0006_extraction_parse_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.alter_column(
            "last_scan_uid",
            type_=sa.Text(),
            existing_type=sa.String(255),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.alter_column(
            "last_scan_uid",
            type_=sa.String(255),
            existing_type=sa.Text(),
            existing_nullable=True,
        )
