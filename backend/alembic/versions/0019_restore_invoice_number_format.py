"""Restore official invoice-number formatting and add format-insensitive dedup.

Revision ID: 0019_restore_invoice_number_format
Revises: 0018_normalize_invoice_no
Create Date: 2026-08-10

Migration 0018 correctly removed duplicate rows but incorrectly stripped the
official hyphen from stored invoice numbers. This migration restores the
verified Stripe-style formats while enforcing uniqueness through an expression
index that ignores hyphens and spaces. Display values remain faithful to PDFs.
"""
from __future__ import annotations

from alembic import op


revision = "0019_restore_invoice_number_format"
down_revision = "0018_normalize_invoice_no"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM invoices
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM invoices
            GROUP BY user_id, REPLACE(REPLACE(invoice_no, '-', ''), ' ', '')
        )
        """
    )
    op.execute(
        """
        UPDATE invoices
        SET invoice_no = substr(invoice_no, 1, 8) || '-' || substr(invoice_no, 9, 4)
        WHERE length(invoice_no) = 12
          AND substr(invoice_no, 9, 4) GLOB '[0-9][0-9][0-9][0-9]'
          AND substr(invoice_no, 1, 8) IN (
              'JUPOBR1U', 'V8HP6PXG', 'WHKUK2JG', 'MRRULHSX', 'MUC5Q38R'
          )
        """
    )
    op.execute(
        """
        UPDATE invoices
        SET seller = 'OpenAI OpCo, LLC'
        WHERE seller = 'OpenAI'
          AND instr(raw_text, 'OpenAI OpCo, LLC') > 0
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invoices_user_id_invoice_no_dedup_key
        ON invoices (
            user_id,
            REPLACE(REPLACE(invoice_no, '-', ''), ' ', '')
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_invoices_user_id_invoice_no_dedup_key")
