"""v0.7.4 extraction parse metadata

Revision ID: 0006_extraction_parse_metadata
Revises: 0005_backfill_oauth_token_path
Create Date: 2026-04-18 10:30:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_extraction_parse_metadata"
down_revision = "0005_backfill_oauth_token_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_logs", sa.Column("parse_method", sa.String(length=32), nullable=True))
    op.add_column("extraction_logs", sa.Column("parse_format", sa.String(length=10), nullable=True))
    op.add_column("extraction_logs", sa.Column("download_outcome", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("extraction_logs", "download_outcome")
    op.drop_column("extraction_logs", "parse_format")
    op.drop_column("extraction_logs", "parse_method")
