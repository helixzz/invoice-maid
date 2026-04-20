"""Seed a 'Manual Uploads' pseudo EmailAccount for user-uploaded invoices (v0.8.6).

Revision ID: 0008_manual_upload_pseudo_account
Revises: 0007_last_scan_uid_text
Create Date: 2026-04-20

Manually uploaded invoices need a parent ``email_accounts`` row because
``invoices.email_account_id`` is a required foreign key. Rather than
loosen the FK (migration risk, breaks CASCADE-delete semantics, touches
every invoice row), we insert a single sentinel row with ``is_active=False``
so the scanner never tries to connect to it, and ``type='manual'`` so
downstream code can detect manual-upload origin via a cheap type check.

Idempotent: uses INSERT ... WHERE NOT EXISTS so re-running the migration
on a DB that already has the row is a no-op.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_manual_upload_pseudo_account"
down_revision = "0007_last_scan_uid_text"
branch_labels = None
depends_on = None


MANUAL_ACCOUNT_NAME = "Manual Uploads"
MANUAL_ACCOUNT_TYPE = "manual"
MANUAL_ACCOUNT_USERNAME = "system@manual-upload.local"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    existing = bind.execute(
        sa.text("SELECT id FROM email_accounts WHERE type = :type LIMIT 1"),
        {"type": MANUAL_ACCOUNT_TYPE},
    ).first()
    if existing is not None:
        return

    if dialect == "sqlite":
        default_now = sa.text("CURRENT_TIMESTAMP")
    else:
        default_now = sa.text("NOW()")

    bind.execute(
        sa.text(
            """
            INSERT INTO email_accounts (
                name, type, host, port, username, outlook_account_type,
                password_encrypted, oauth_token_path, is_active, last_scan_uid, created_at
            ) VALUES (
                :name, :type, NULL, NULL, :username, 'personal',
                NULL, NULL, 0, NULL, :created_at
            )
            """
        ),
        {
            "name": MANUAL_ACCOUNT_NAME,
            "type": MANUAL_ACCOUNT_TYPE,
            "username": MANUAL_ACCOUNT_USERNAME,
            "created_at": bind.execute(sa.select(default_now)).scalar(),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM email_accounts WHERE type = :type"),
        {"type": MANUAL_ACCOUNT_TYPE},
    )
