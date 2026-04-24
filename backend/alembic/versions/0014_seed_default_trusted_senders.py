"""Seed default invoice-provider domains into classifier_trusted_senders.

Revision ID: 0014_seed_default_trusted_senders
Revises: 0013_migrate_files_to_user_subdirs
Create Date: 2026-04-25

Seeds seven well-known Chinese invoice-delivery platforms into the
``classifier_trusted_senders`` app setting so tier-1 classification
recognises them out of the box. Operators can still edit the list
via ``PUT /settings/classifier``; this migration only seeds when the
setting is currently empty or missing — never overwrites a
user-configured value.

Why these seven:

* ``qcloudmail.com`` — Tencent Cloud Mail; used by Sam's Club /
  Walmart and other retailers to deliver electronic invoices.
* ``fapiao.jd.com`` — JD.com's invoice-delivery subdomain.
* ``shuidi.com`` — Shuidi (水滴) electronic-invoice aggregator.
* ``noreply@invoice.alipay.com`` — Alipay's invoice notification
  mailbox; stored as a full email address because the domain
  ``alipay.com`` sends a huge volume of non-invoice mail.
* ``inv.nuonuo.com`` — 诺诺网 (Nuonuo) invoice platform.
* ``piaoyi.baiwang.com`` — 百望云 (Baiwang) invoice platform.
* ``eeds.chinatax.gov.cn`` — 国家税务总局 / e-tax delivery
  subdomain; anything from the government tax authority is
  inherently trustworthy as an invoice source.

The classifier's ``_sender_trusted`` check (v0.9.1 and later)
accepts three pattern shapes: exact email (``user@domain``),
domain-suffix (``domain.com`` or ``@domain.com``), and legacy
substring. Mixing a full email for Alipay with domain-suffix for
the others is deliberate — Alipay's broader domain is noisy, while
the specific subdomains are narrow enough to be safe as domain
matches.

Idempotency: runs a single UPDATE with a WHERE clause that only
fires when ``classifier_trusted_senders`` is NULL or empty. Re-runs
are no-ops. Downgrade does nothing because we cannot distinguish
between "this migration seeded these seven" and "the operator
happened to configure the same seven".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_seed_default_trusted_senders"
down_revision = "0013_migrate_files_to_user_subdirs"
branch_labels = None
depends_on = None


DEFAULT_TRUSTED_SENDERS = ",".join(
    [
        "qcloudmail.com",
        "fapiao.jd.com",
        "shuidi.com",
        "noreply@invoice.alipay.com",
        "inv.nuonuo.com",
        "piaoyi.baiwang.com",
        "eeds.chinatax.gov.cn",
    ]
)


def upgrade() -> None:
    bind = op.get_bind()

    # Defensive: ``app_settings`` is one of the tables that's born from
    # ``Base.metadata.create_all`` at first app start, not from an
    # alembic migration. On a fresh install where alembic runs before
    # the first app boot, the table may not exist yet. Skip silently
    # in that case; the table will be created on first boot (by
    # create_all) with the default empty-string value from
    # ``database.init_db``, and a re-run of this migration (or a
    # future seed migration) can populate it. Existing deployments
    # have the table, so the happy path runs.
    inspector = sa.inspect(bind)
    if "app_settings" not in inspector.get_table_names():
        return

    # Additive seed: only write when the setting is missing or empty.
    # Never clobber an operator-configured value.
    existing = bind.execute(
        sa.text(
            "SELECT value FROM app_settings WHERE key = 'classifier_trusted_senders'"
        )
    ).first()

    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO app_settings (key, value, updated_at) "
                "VALUES ('classifier_trusted_senders', :value, CURRENT_TIMESTAMP)"
            ),
            {"value": DEFAULT_TRUSTED_SENDERS},
        )
    elif not (existing[0] or "").strip():
        bind.execute(
            sa.text(
                "UPDATE app_settings SET value = :value, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE key = 'classifier_trusted_senders'"
            ),
            {"value": DEFAULT_TRUSTED_SENDERS},
        )


def downgrade() -> None:
    # Intentional no-op. We cannot distinguish "the migration wrote these
    # seven" from "the operator independently configured these seven", so
    # clearing the value on downgrade risks destroying a legitimate
    # operator customization.
    pass
