"""v0.4.0 classifier tier

Revision ID: 0003_v040_classifier_tier
Revises: 0002_v020_audit_and_corrections
Create Date: 2026-04-17 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_v040_classifier_tier"
down_revision = "0002_v020_audit_and_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_logs", sa.Column("classification_tier", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("extraction_logs", "classification_tier")
