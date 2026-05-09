"""v1.2.0 Track B: add SaaS-scraper credential fields to email_accounts.

Revision ID: 0016_add_scraper_account_fields
Revises: 0015_add_invoice_category
Create Date: 2026-05-09

Adds four nullable columns to ``email_accounts`` so an ``EmailAccount`` row
with ``type='cursor'`` (and future ``scraper:*`` types) can carry the
artefacts a Playwright-driven scrape needs:

* ``playwright_storage_state`` — JSON blob (cookies + localStorage) that a
  fresh Chromium context can load so subsequent scans skip the login flow.
  Stored as plaintext per the v1.2.0 Track-B design §4.2: the cookies are
  already bearer tokens, and anyone with DB read access has effective
  admin regardless of whether they're Fernet-wrapped.
* ``secondary_credential_encrypted`` — Fernet(JWT_SECRET)-wrapped login
  identifier (e.g. the Cursor login email, distinct from
  ``EmailAccount.username`` which doubles as the account label).
* ``secondary_password_encrypted`` — Fernet(JWT_SECRET)-wrapped password.
* ``totp_secret_encrypted`` — Fernet(JWT_SECRET)-wrapped TOTP seed for
  automated 2FA. NULL when the operator prefers the Mode-B manual
  ``playwright_storage_state`` paste workflow.

Legacy IMAP/POP3/Outlook/QQ/manual rows keep NULL for all four columns
and are unaffected. Downgrade drops the columns via SQLite
batch_alter_table so rollback from v1.2.0 → v1.1.0 is clean.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0016_add_scraper_account_fields"
down_revision = "0015_add_invoice_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_accounts",
        sa.Column("playwright_storage_state", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_accounts",
        sa.Column("secondary_credential_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_accounts",
        sa.Column("secondary_password_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_accounts",
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("email_accounts") as batch:
        batch.drop_column("totp_secret_encrypted")
        batch.drop_column("secondary_password_encrypted")
        batch.drop_column("secondary_credential_encrypted")
        batch.drop_column("playwright_storage_state")
