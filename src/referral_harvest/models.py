"""SQLAlchemy domain model. One table per aggregate; statuses are enums.

Timestamp convention: all datetimes are naive UTC (see referral_harvest.util.utcnow).
Money is integer cents. Tenant settings live in a JSON column; defaults are in
DEFAULT_TENANT_SETTINGS and deep-merged at read time by `tenant_setting()`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from referral_harvest.db import Base
from referral_harvest.util import utcnow


def _uuid() -> str:
    return str(uuid.uuid4())


class Consent(enum.StrEnum):
    unknown = "unknown"
    opted_in = "opted_in"
    opted_out = "opted_out"


class AskStatus(enum.StrEnum):
    scheduled = "scheduled"
    sent = "sent"
    expired = "expired"
    cancelled = "cancelled"


class ReferralStatus(enum.StrEnum):
    claimed = "claimed"
    booked = "booked"
    completed = "completed"
    cancelled = "cancelled"


class RewardStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    fulfilled = "fulfilled"
    cancelled = "cancelled"


class RewardKind(enum.StrEnum):
    credit = "credit"
    cash = "cash"
    gift_card = "gift_card"


DEFAULT_TENANT_SETTINGS: dict = {
    # Reward economics
    "reward_amount_cents": 2500,
    "reward_kind": "credit",
    "paid_cac_cents": 30000,
    # Ask gating
    "ask_cooldown_days": 90,
    "ask_expiry_days": 30,
    "max_uses_per_code": 10,
    "rating_scale": 5,
    "min_rating_for_ask": 4.0,  # 5-scale threshold
    "min_rating_10_scale": 9.0,  # 10-scale threshold
    "rating_max_age_hours": 48,
    # Quiet hours (recipient-local, overnight window)
    "quiet_hours_start": "20:00",
    "quiet_hours_end": "09:00",
    # Integrations (null = global default / disabled)
    "crm_adapter": "webhook",  # webhook | servicetitan | smartmoving | csv
    "csv_path": None,
    "sms_sender": None,  # console | twilio | webhook (null -> DEFAULT_SMS_SENDER)
    "sms_webhook_url": None,
    "reward_fulfiller": "manual",  # manual | webhook
    "reward_webhook_url": None,
    # Messaging
    "ask_template": (
        "Hi {name}, glad we could help with your {service}! Know anyone who needs "
        "a great {trade}? Share code {code} - they get $25 off their first visit "
        "and you get ${reward} when their job's done. {link}"
    ),
    "trade": "home-service pro",
}


def tenant_setting(tenant: Tenant, key: str):
    """Read a tenant setting with fallback to DEFAULT_TENANT_SETTINGS."""
    settings = tenant.settings or {}
    if key in settings and settings[key] is not None:
        return settings[key]
    return DEFAULT_TENANT_SETTINGS[key]


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    settings: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=lambda: dict(DEFAULT_TENANT_SETTINGS)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    customers: Mapped[list[Customer]] = relationship(back_populates="tenant")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="tenant")


class ApiKey(Base):
    __tablename__ = "api_key"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")


class Customer(Base):
    __tablename__ = "customer"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_customer_tenant_external"),
        UniqueConstraint("tenant_id", "phone_e164", name="uq_customer_tenant_phone"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    consent: Mapped[Consent] = mapped_column(default=Consent.unknown, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="America/Chicago")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="customers")


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id", name="uq_job_tenant_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    service_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tech_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    rating: Mapped[Rating | None] = relationship(back_populates="job", uselist=False)


class Rating(Base):
    __tablename__ = "rating"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    scale: Mapped[int] = mapped_column(Integer, nullable=False)  # 5 or 10
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    job: Mapped[Job] = relationship(back_populates="rating")


class ReferralAsk(Base):
    __tablename__ = "referral_ask"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_ask_tenant_code"),
        Index(
            "uq_ask_job_active",
            "job_id",
            unique=True,
            # one scheduled/sent ask per job (DB-level idempotency for the ask engine)
            sqlite_where=text("status IN ('scheduled', 'sent')"),
            postgresql_where=text("status IN ('scheduled', 'sent')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    # While status=scheduled: the time the ask should be sent (evaluate()
    # defers past quiet hours). The scheduler overwrites it with the actual
    # send time when the message goes out.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[AskStatus] = mapped_column(default=AskStatus.scheduled, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Referral(Base):
    __tablename__ = "referral"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ask_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("referral_ask.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    referee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    referee_phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[ReferralStatus] = mapped_column(default=ReferralStatus.claimed, nullable=False)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    reward: Mapped[Reward | None] = relationship(back_populates="referral", uselist=False)


class Reward(Base):
    __tablename__ = "reward"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    referral_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("referral.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    kind: Mapped[RewardKind] = mapped_column(default=RewardKind.credit, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RewardStatus] = mapped_column(default=RewardStatus.pending, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fulfilled_via: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    referral: Mapped[Referral] = relationship(back_populates="reward")


class SmsMessage(Base):
    """Every outbound SMS, for cost accounting and audit."""

    __tablename__ = "sms_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ask_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("referral_ask.id", ondelete="SET NULL"), nullable=True
    )
    to_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cost_cents: Mapped[float] = mapped_column(Float, nullable=False)
    provider_sid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SyncState(Base):
    """Durable scheduler watermarks (never in memory)."""

    __tablename__ = "sync_state"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_sync_tenant_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
