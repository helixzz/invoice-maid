"""Backfill oauth_token_path for existing Outlook accounts where it is NULL.

Revision ID: 0005_backfill_oauth_token_path
Revises: 0004_outlook_account_type
Create Date: 2026-04-17
"""

from __future__ import annotations

from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "0005_backfill_oauth_token_path"
down_revision = "0004_outlook_account_type"
branch_labels = None
depends_on = None


def _derive_oauth_dir() -> Path:
    conn = op.get_bind()
    url = str(conn.engine.url)
    if "sqlite" in url and "///" in url:
        raw = url.split("///", 1)[1]
        db_path = "/" + raw if not raw.startswith("/") else raw
        return Path(db_path).parent.parent / "oauth"
    return Path("/var/lib/invoice-maid/data/oauth")


def upgrade() -> None:
    conn = op.get_bind()
    oauth_dir = _derive_oauth_dir()
    oauth_dir.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        sa.text(
            "SELECT id FROM email_accounts"
            " WHERE type = 'outlook'"
            " AND (oauth_token_path IS NULL OR oauth_token_path = '')"
        )
    ).fetchall()

    for (account_id,) in rows:
        token_path = str(oauth_dir / f"account_{account_id}_token.json")
        conn.execute(
            sa.text("UPDATE email_accounts SET oauth_token_path = :path WHERE id = :id"),
            {"path": token_path, "id": account_id},
        )


def downgrade() -> None:
    pass
