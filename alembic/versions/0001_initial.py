"""Initial schema: tenants, customers, jobs, ratings, asks, referrals, rewards.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "api_key",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("prefix", sa.String(16), nullable=False, index=True),
        sa.Column("key_hash", sa.String(200), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "customer",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone_e164", sa.String(20), nullable=False),
        sa.Column(
            "consent", sa.Enum("unknown", "opted_in", "opted_out", name="consent"), nullable=False
        ),
        sa.Column("timezone", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_customer_tenant_external"),
        sa.UniqueConstraint("tenant_id", "phone_e164", name="uq_customer_tenant_phone"),
    )
    op.create_table(
        "job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=False),
        sa.Column("tech_name", sa.String(100), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_job_tenant_external"),
    )
    op.create_table(
        "rating",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("job.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "referral_ask",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("scheduled", "sent", "expired", "cancelled", name="askstatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_ask_tenant_code"),
    )
    # DB-level idempotency: at most one scheduled/sent ask per job.
    op.create_index(
        "uq_ask_job_active",
        "referral_ask",
        ["job_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('scheduled', 'sent')"),
        postgresql_where=sa.text("status IN ('scheduled', 'sent')"),
    )
    op.create_table(
        "referral",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "ask_id",
            sa.String(36),
            sa.ForeignKey("referral_ask.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("referee_name", sa.String(200), nullable=False),
        sa.Column("referee_phone_e164", sa.String(20), nullable=False),
        sa.Column(
            "status",
            sa.Enum("claimed", "booked", "completed", "cancelled", name="referralstatus"),
            nullable=False,
        ),
        sa.Column("booked_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "reward",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "referral_id",
            sa.String(36),
            sa.ForeignKey("referral.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "kind",
            sa.Enum("credit", "cash", "gift_card", name="rewardkind"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "fulfilled", "cancelled", name="rewardstatus"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
        sa.Column("fulfilled_via", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sms_message",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "ask_id",
            sa.String(36),
            sa.ForeignKey("referral_ask.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("to_e164", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cost_cents", sa.Float(), nullable=False),
        sa.Column("provider_sid", sa.String(100), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sync_state",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_sync_tenant_key"),
    )


def downgrade() -> None:
    for table in (
        "sync_state",
        "sms_message",
        "reward",
        "referral",
        "referral_ask",
        "rating",
        "job",
        "customer",
        "api_key",
        "tenant",
    ):
        op.drop_table(table)
