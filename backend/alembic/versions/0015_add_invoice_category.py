"""v1.2.0 Track A: add invoice_category taxonomy.

Revision ID: 0015_add_invoice_category
Revises: 0014_seed_default_trusted_senders
Create Date: 2026-05-09

Adds a structured ``invoice_category`` enum column to ``invoices`` so the
app can distinguish Chinese VAT invoices from SaaS invoices, receipts,
proforma invoices, and other legitimate billing documents.

Three coordinated changes:

1. New column ``invoice_category VARCHAR(32) NOT NULL DEFAULT 'vat_invoice'``
   with a b-tree index for the filter path (``GET /invoices?category=...``).
   All 250 production invoices are backfilled to ``'vat_invoice'`` (audit
   performed 2026-05-08: every row has ``invoice_type`` in the Chinese VAT
   frozenset; no foreign sellers).

2. FTS5 virtual table + 3 triggers are torn down and rebuilt to include
   ``invoice_category`` so operators can ``MATCH 'invoice_category:
   saas_invoice'`` in search. Pattern mirrors migration 0012's trigger
   rebuild for FK tightening.

3. No DB-level CHECK constraint — SQLite's ALTER TABLE lacks ADD CHECK.
   Validation lives in ``app.schemas.invoice.InvoiceCategory`` (Pydantic
   enum) at the API boundary + LLM extract prompt; the Postgres roadmap
   can add CHECK zero-cost when/if that deployment target lands.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0015_add_invoice_category"
down_revision = "0014_seed_default_trusted_senders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("invoice_category", sa.String(length=32), nullable=True),
    )

    op.execute(
        "UPDATE invoices SET invoice_category = 'vat_invoice' WHERE invoice_category IS NULL"
    )

    with op.batch_alter_table("invoices") as batch:
        batch.alter_column(
            "invoice_category",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="vat_invoice",
        )

    op.create_index(
        "ix_invoices_invoice_category",
        "invoices",
        ["invoice_category"],
    )

    op.execute("DROP TRIGGER IF EXISTS invoices_ai")
    op.execute("DROP TRIGGER IF EXISTS invoices_ad")
    op.execute("DROP TRIGGER IF EXISTS invoices_au")
    op.execute("DROP TABLE IF EXISTS invoices_fts")

    op.execute(
        """
        CREATE VIRTUAL TABLE invoices_fts USING fts5(
            invoice_no, buyer, seller, invoice_type, invoice_category,
            item_summary, raw_text,
            content='invoices', content_rowid='id'
        )
        """
    )
    op.execute("INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')")
    op.execute(
        """
        CREATE TRIGGER invoices_ai AFTER INSERT ON invoices BEGIN
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, invoice_category, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.invoice_category, new.item_summary, new.raw_text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER invoices_ad AFTER DELETE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, invoice_category, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.invoice_category, old.item_summary, old.raw_text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER invoices_au AFTER UPDATE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, invoice_category, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.invoice_category, old.item_summary, old.raw_text);
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, invoice_category, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.invoice_category, new.item_summary, new.raw_text);
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS invoices_ai")
    op.execute("DROP TRIGGER IF EXISTS invoices_ad")
    op.execute("DROP TRIGGER IF EXISTS invoices_au")
    op.execute("DROP TABLE IF EXISTS invoices_fts")

    op.execute(
        """
        CREATE VIRTUAL TABLE invoices_fts USING fts5(
            invoice_no, buyer, seller, invoice_type, item_summary, raw_text,
            content='invoices', content_rowid='id'
        )
        """
    )
    op.execute("INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')")
    op.execute(
        """
        CREATE TRIGGER invoices_ai AFTER INSERT ON invoices BEGIN
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER invoices_ad AFTER DELETE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER invoices_au AFTER UPDATE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END
        """
    )

    op.drop_index("ix_invoices_invoice_category", table_name="invoices")
    with op.batch_alter_table("invoices") as batch:
        batch.drop_column("invoice_category")
