"""Unit-economics reporting.

Cost model (documented formula — the INTEGRATION.md walkthrough asserts it):
  sms_cost_cents       = sum(SmsMessage.cost_cents) in the period
  rewards_cost_cents   = sum(Reward.amount_cents) for rewards fulfilled in the period
  total_cost_cents     = sms_cost_cents + rewards_cost_cents
  cost_per_referred_job_cents = total_cost_cents / referrals_completed (None if zero)

SMS pricing: flat SMS_COST_USD_PER_MESSAGE per outbound message (Twilio all-in
upper bound, single segment), recorded on each SmsMessage at send time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from referral_harvest.models import (
    AskStatus,
    Job,
    Referral,
    ReferralAsk,
    ReferralStatus,
    Reward,
    RewardStatus,
    SmsMessage,
    Tenant,
    tenant_setting,
)


def compute_unit_economics(
    db, tenant: Tenant, period_start: datetime, period_end: datetime
) -> dict:
    jobs_completed = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.tenant_id == tenant.id,
            Job.completed_at >= period_start,
            Job.completed_at < period_end,
        )
    )
    asks_sent = db.scalar(
        select(func.count())
        .select_from(ReferralAsk)
        .where(
            ReferralAsk.tenant_id == tenant.id,
            ReferralAsk.status == AskStatus.sent,
            ReferralAsk.sent_at >= period_start,
            ReferralAsk.sent_at < period_end,
        )
    )
    referrals_claimed = db.scalar(
        select(func.count())
        .select_from(Referral)
        .where(
            Referral.tenant_id == tenant.id,
            Referral.created_at >= period_start,
            Referral.created_at < period_end,
        )
    )
    referrals_booked = db.scalar(
        select(func.count())
        .select_from(Referral)
        .where(
            Referral.tenant_id == tenant.id,
            Referral.status.in_([ReferralStatus.booked, ReferralStatus.completed]),
            Referral.booked_at >= period_start,
            Referral.booked_at < period_end,
        )
    )
    referrals_completed = db.scalar(
        select(func.count())
        .select_from(Referral)
        .where(
            Referral.tenant_id == tenant.id,
            Referral.status == ReferralStatus.completed,
            Referral.completed_at >= period_start,
            Referral.completed_at < period_end,
        )
    )
    sms_cost = (
        db.scalar(
            select(func.coalesce(func.sum(SmsMessage.cost_cents), 0.0)).where(
                SmsMessage.tenant_id == tenant.id,
                SmsMessage.sent_at >= period_start,
                SmsMessage.sent_at < period_end,
            )
        )
        or 0.0
    )
    rewards_cost = (
        db.scalar(
            select(func.coalesce(func.sum(Reward.amount_cents), 0)).where(
                Reward.status == RewardStatus.fulfilled,
                Reward.fulfilled_at >= period_start,
                Reward.fulfilled_at < period_end,
                Reward.referral_id.in_(select(Referral.id).where(Referral.tenant_id == tenant.id)),
            )
        )
        or 0
    )
    total_cost = round(sms_cost + rewards_cost)
    cost_per_job = round(total_cost / referrals_completed, 2) if referrals_completed else None
    paid_cac = tenant_setting(tenant, "paid_cac_cents")
    savings = round(paid_cac - cost_per_job, 2) if cost_per_job is not None else None

    return {
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "jobs_completed": jobs_completed,
        "asks_sent": asks_sent,
        "referrals_claimed": referrals_claimed,
        "referrals_booked": referrals_booked,
        "referrals_completed": referrals_completed,
        "sms_cost_cents": round(sms_cost),
        "rewards_fulfilled_cents": rewards_cost,
        "total_cost_cents": total_cost,
        "cost_per_referred_job_cents": cost_per_job,
        "paid_cac_cents": paid_cac,
        "savings_vs_cac_cents": savings,
    }
