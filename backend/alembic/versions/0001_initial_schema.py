"""initial schema

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-04-16 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_encrypted", sa.String(length=1024), nullable=True),
        sa.Column("oauth_token_path", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scan_uid", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "llm_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_type", sa.String(length=32), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_cache_content_hash", "llm_cache", ["content_hash"], unique=True)

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_no", sa.String(length=128), nullable=False),
        sa.Column("buyer", sa.String(length=255), nullable=False),
        sa.Column("seller", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("invoice_type", sa.String(length=128), nullable=False),
        sa.Column("item_summary", sa.String(length=500), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_uid", sa.String(length=255), nullable=False),
        sa.Column("email_account_id", sa.Integer(), nullable=False),
        sa.Column("source_format", sa.String(length=32), nullable=False, server_default="pdf"),
        sa.Column("extraction_method", sa.String(length=32), nullable=False, server_default="llm"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_invoices_email_account_id", "invoices", ["email_account_id"], unique=False)
    op.create_index("ix_invoices_invoice_date", "invoices", ["invoice_date"], unique=False)
    op.create_index("ix_invoices_invoice_no", "invoices", ["invoice_no"], unique=True)

    op.create_table(
        "scan_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_account_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emails_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invoices_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_scan_logs_email_account_id", "scan_logs", ["email_account_id"], unique=False)

    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS invoices_fts
        USING fts5(
            invoice_no, buyer, seller, invoice_type, item_summary, raw_text,
            content='invoices', content_rowid='id'
        );
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS invoices_ai AFTER INSERT ON invoices BEGIN
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS invoices_ad AFTER DELETE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS invoices_au AFTER UPDATE ON invoices BEGIN
            INSERT INTO invoices_fts(invoices_fts, rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES ('delete', old.id, old.invoice_no, old.buyer, old.seller, old.invoice_type, old.item_summary, old.raw_text);
            INSERT INTO invoices_fts(rowid, invoice_no, buyer, seller, invoice_type, item_summary, raw_text)
            VALUES (new.id, new.invoice_no, new.buyer, new.seller, new.invoice_type, new.item_summary, new.raw_text);
        END;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS invoices_au")
    op.execute("DROP TRIGGER IF EXISTS invoices_ad")
    op.execute("DROP TRIGGER IF EXISTS invoices_ai")
    op.execute("DROP TABLE IF EXISTS invoices_fts")

    op.drop_index("ix_scan_logs_email_account_id", table_name="scan_logs")
    op.drop_table("scan_logs")

    op.drop_index("ix_invoices_invoice_no", table_name="invoices")
    op.drop_index("ix_invoices_invoice_date", table_name="invoices")
    op.drop_index("ix_invoices_email_account_id", table_name="invoices")
    op.drop_table("invoices")

    op.drop_index("ix_llm_cache_content_hash", table_name="llm_cache")
    op.drop_table("llm_cache")

    op.drop_table("email_accounts")
