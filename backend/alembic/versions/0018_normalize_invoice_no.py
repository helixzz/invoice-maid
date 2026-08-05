"""Normalize invoice_no separators and remove dual-format duplicates.

Revision ID: 0018_normalize_invoice_no
Revises: 0017_rename_saas_to_overseas
Create Date: 2026-08-05

Vendors render the same invoice id with and without separators
("JUPOBR1U-0043" from one email vs "JUPOBR1U0043" from another), which
let 45 duplicate rows slip past UNIQUE(user_id, invoice_no). The code
now normalizes invoice_no (strip hyphens/spaces) before dedup; this
migration brings existing rows to the same canonical form:

1. For each (user_id, normalized invoice_no) group with >1 row, keep
   the oldest row (MIN(id)) and delete the newer duplicates.
2. Rewrite remaining invoice_no values to their normalized form.

Data-only migration. Deleted rows leave their files on disk; operators
can prune them with the orphan-file warning on next restart.
"""
from __future__ import annotations

from alembic import op


revision = "0018_normalize_invoice_no"
down_revision = "0017_rename_saas_to_overseas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM invoices WHERE id NOT IN (
            SELECT MIN(id) FROM invoices
            GROUP BY user_id, REPLACE(REPLACE(invoice_no, '-', ''), ' ', '')
        )
        """
    )
    op.execute(
        """
        UPDATE invoices
        SET invoice_no = REPLACE(REPLACE(invoice_no, '-', ''), ' ', '')
        WHERE invoice_no != REPLACE(REPLACE(invoice_no, '-', ''), ' ', '')
        """
    )


def downgrade() -> None:
    pass
