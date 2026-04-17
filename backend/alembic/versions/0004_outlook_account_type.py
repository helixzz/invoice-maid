"""v0.4.4 outlook account type

Revision ID: 0004_outlook_account_type
Revises: 0003_v040_classifier_tier
Create Date: 2026-04-17 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_outlook_account_type"
down_revision = "0003_v040_classifier_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_accounts",
        sa.Column("outlook_account_type", sa.String(length=16), nullable=False, server_default="personal"),
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "outlook_account_type")
