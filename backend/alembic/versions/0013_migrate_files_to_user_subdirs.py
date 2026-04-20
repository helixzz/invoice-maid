"""Migrate invoice files from flat storage to per-user subdirectories.

Revision ID: 0013_migrate_files_to_user_subdirs
Revises: 0012_tighten_user_id_constraints
Create Date: 2026-04-21

Data migration. Moves each invoice file from the flat
``STORAGE_PATH/{filename}`` layout into the per-user layout
``STORAGE_PATH/users/{user_id}/invoices/{filename}``, and updates
``invoices.file_path`` accordingly.

Why per-user subdirectories? The canonical filename is derived
deterministically from invoice metadata (``buyer_seller_invoiceno_date_amount.pdf``).
Under the flat layout, two users who legitimately own invoices with
identical metadata would generate the same filename and overwrite
each other's files — a silent data-loss bug that only surfaces once
a second user exists. Per-user subdirectories make collision
structurally impossible: user A's file lives at
``users/1/invoices/X.pdf``, user B's at ``users/2/invoices/X.pdf``.

Idempotency contract (load-bearing for production safety):

* Row whose ``file_path`` already starts with ``users/`` → skip,
  no disk operation, log INFO.
* Row whose flat file exists on disk → ``mkdir -p`` the user dir,
  move the file, update ``file_path`` in DB. Log INFO.
* Row whose flat file is MISSING on disk (deleted manually, storage
  reset, stale row) → update ``file_path`` in DB only, do not
  attempt any disk op, log WARNING. The row is preserved so admins
  can audit it; the API will return 404 for download attempts (same
  behavior as before this migration).
* Row whose new user path already exists (partial prior run) → skip
  the move, still update ``file_path``. Log INFO.

Every action is logged so an operator can audit the migration after
the fact via ``journalctl -u invoice-maid``.

Foreign-key / connection considerations: Alembic's ``env.py`` in
this project does not install the app's SQLite ``connect`` hook, so
``PRAGMA foreign_keys`` is OFF during migration runs. That's safe
here because we never drop/recreate any FK-bearing row — only update
``file_path`` strings.

Dry-run flag: set ``DRY_RUN=1`` in the environment to log every
intended move without touching disk or DB. Useful on production DB
copies for pre-flight validation.

Rollback: ``downgrade()`` reverses the flatten + file moves. Files
move back from ``users/{user_id}/invoices/{filename}`` to
``{filename}`` at ``STORAGE_PATH`` root, and ``file_path`` is
rewritten to just the bare filename. Missing-on-disk rows are
rewritten in DB only (same rule as upgrade). Empty ``users/`` tree
is left in place for operator cleanup; removing it automatically
risks deleting a file the operator added out-of-band.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import sqlalchemy as sa
from alembic import op


revision = "0013_migrate_files_to_user_subdirs"
down_revision = "0012_tighten_user_id_constraints"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.0013")


def _derive_storage_path() -> Path:
    """Mirror how ``app/config.py`` resolves STORAGE_PATH so the
    migration targets the same directory the application uses.

    Precedence: ``STORAGE_PATH`` env var → URL-derived default
    (``{db_parent.parent}/invoices``) → ``./data/invoices`` fallback."""
    storage_env = os.getenv("STORAGE_PATH")
    if storage_env:
        return Path(storage_env).expanduser().resolve()

    conn = op.get_bind()
    url = str(conn.engine.url)
    if "sqlite" in url and "///" in url:
        raw = url.split("///", 1)[1]
        db_path = Path("/" + raw if not raw.startswith("/") else raw)
        return (db_path.parent.parent / "invoices").resolve()

    return Path("./data/invoices").resolve()


def _is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}


def upgrade() -> None:
    bind = op.get_bind()
    storage = _derive_storage_path()
    dry = _is_dry_run()

    if dry:
        logger.warning("DRY_RUN=1 — no disk or DB writes will be performed")

    rows = bind.execute(
        sa.text("SELECT id, user_id, file_path FROM invoices")
    ).fetchall()

    moved = 0
    already_migrated = 0
    missing_on_disk = 0
    collision_skipped = 0

    for row in rows:
        invoice_id, user_id, file_path = row[0], row[1], row[2]

        if file_path.startswith("users/"):
            already_migrated += 1
            continue

        old_abs = (storage / file_path).resolve()
        filename = Path(file_path).name
        new_rel = f"users/{user_id}/invoices/{filename}"
        new_abs = (storage / new_rel).resolve()

        if new_abs.exists() and not old_abs.exists():
            logger.info(
                "invoice %d: new path already populated, updating DB only: %s",
                invoice_id,
                new_rel,
            )
            if not dry:
                bind.execute(
                    sa.text("UPDATE invoices SET file_path = :new WHERE id = :id"),
                    {"new": new_rel, "id": invoice_id},
                )
            collision_skipped += 1
            continue

        if not old_abs.exists():
            logger.warning(
                "invoice %d: file missing on disk (%s), updating file_path in DB only",
                invoice_id,
                old_abs,
            )
            if not dry:
                bind.execute(
                    sa.text("UPDATE invoices SET file_path = :new WHERE id = :id"),
                    {"new": new_rel, "id": invoice_id},
                )
            missing_on_disk += 1
            continue

        logger.info(
            "invoice %d: moving %s -> %s",
            invoice_id,
            old_abs,
            new_abs,
        )
        if not dry:
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_abs), str(new_abs))
            bind.execute(
                sa.text("UPDATE invoices SET file_path = :new WHERE id = :id"),
                {"new": new_rel, "id": invoice_id},
            )
        moved += 1

    logger.info(
        "migration 0013 complete: moved=%d already_migrated=%d missing_on_disk=%d "
        "collision_skipped=%d dry_run=%s",
        moved,
        already_migrated,
        missing_on_disk,
        collision_skipped,
        dry,
    )


def downgrade() -> None:
    bind = op.get_bind()
    storage = _derive_storage_path()
    dry = _is_dry_run()

    if dry:
        logger.warning("DRY_RUN=1 — no disk or DB writes will be performed")

    rows = bind.execute(
        sa.text("SELECT id, user_id, file_path FROM invoices")
    ).fetchall()

    moved_back = 0
    already_flat = 0
    missing_on_disk = 0
    collision_skipped = 0

    for row in rows:
        invoice_id, _user_id, file_path = row[0], row[1], row[2]

        if not file_path.startswith("users/"):
            already_flat += 1
            continue

        new_abs = (storage / file_path).resolve()
        filename = Path(file_path).name
        old_rel = filename
        old_abs = (storage / old_rel).resolve()

        if old_abs.exists() and not new_abs.exists():
            logger.info(
                "invoice %d: old path already populated, updating DB only: %s",
                invoice_id,
                old_rel,
            )
            if not dry:
                bind.execute(
                    sa.text("UPDATE invoices SET file_path = :new WHERE id = :id"),
                    {"new": old_rel, "id": invoice_id},
                )
            collision_skipped += 1
            continue

        if not new_abs.exists():
            logger.warning(
                "invoice %d: file missing on disk (%s), updating file_path in DB only",
                invoice_id,
                new_abs,
            )
            if not dry:
                bind.execute(
                    sa.text("UPDATE invoices SET file_path = :new WHERE id = :id"),
                    {"new": old_rel, "id": invoice_id},
                )
            missing_on_disk += 1
            continue

        logger.info(
            "invoice %d: moving back %s -> %s",
            invoice_id,
            new_abs,
            old_abs,
        )
        if not dry:
            shutil.move(str(new_abs), str(old_abs))
            bind.execute(
                sa.text("UPDATE invoices SET file_path = :new WHERE id = :id"),
                {"new": old_rel, "id": invoice_id},
            )
        moved_back += 1

    logger.info(
        "migration 0013 downgrade complete: moved_back=%d already_flat=%d "
        "missing_on_disk=%d collision_skipped=%d dry_run=%s",
        moved_back,
        already_flat,
        missing_on_disk,
        collision_skipped,
        dry,
    )
