"""Add v2.0 Maid Agent tables: invoice_profiles, maid_memories, invoice_claim_tasks, maid_messages.

Revision ID: 0023_v2_maid_agent_and_claims
Revises: 0022_invoice_reimbursement
Create Date: 2026-11-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_v2_maid_agent_and_claims"
down_revision = "0022_invoice_reimbursement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("tax_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("invoice_type_pref", sa.String(length=64), nullable=False, server_default="数电普票"),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("address", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("bank_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("bank_account", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invoice_profiles_user_id", "invoice_profiles", ["user_id"])

    op.create_table(
        "maid_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
        sa.Column("rule_text", sa.String(length=500), nullable=False),
        sa.Column("match_pattern", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("action_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maid_memories_user_id", "maid_memories", ["user_id"])

    op.create_table(
        "invoice_claim_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("invoice_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_channel", sa.String(length=32), nullable=False, server_default="web"),
        sa.Column("source_chat_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("qr_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("merchant_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("expected_currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("receipt_date", sa.Date(), nullable=True),
        sa.Column("receipt_summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="awaiting_profile"),
        sa.Column("attention_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_screenshot_path", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("matched_invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("log_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invoice_claim_tasks_user_id", "invoice_claim_tasks", ["user_id"])
    op.create_index("ix_invoice_claim_tasks_profile_id", "invoice_claim_tasks", ["profile_id"])
    op.create_index("ix_invoice_claim_tasks_status", "invoice_claim_tasks", ["status"])
    op.create_index("ix_invoice_claim_tasks_matched_invoice_id", "invoice_claim_tasks", ["matched_invoice_id"])

    op.create_table(
        "maid_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_calls_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("attachments_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maid_messages_user_id", "maid_messages", ["user_id"])
    op.create_index("ix_maid_messages_session_id", "maid_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_maid_messages_session_id", table_name="maid_messages")
    op.drop_index("ix_maid_messages_user_id", table_name="maid_messages")
    op.drop_table("maid_messages")

    op.drop_index("ix_invoice_claim_tasks_matched_invoice_id", table_name="invoice_claim_tasks")
    op.drop_index("ix_invoice_claim_tasks_status", table_name="invoice_claim_tasks")
    op.drop_index("ix_invoice_claim_tasks_profile_id", table_name="invoice_claim_tasks")
    op.drop_index("ix_invoice_claim_tasks_user_id", table_name="invoice_claim_tasks")
    op.drop_table("invoice_claim_tasks")

    op.drop_index("ix_maid_memories_user_id", table_name="maid_memories")
    op.drop_table("maid_memories")

    op.drop_index("ix_invoice_profiles_user_id", table_name="invoice_profiles")
    op.drop_table("invoice_profiles")
