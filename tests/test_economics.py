"""Unit-economics computation tests."""

from datetime import timedelta

from referral_harvest.engine.economics import compute_unit_economics
from referral_harvest.models import (
    AskStatus,
    Referral,
    ReferralAsk,
    ReferralStatus,
    Reward,
    RewardKind,
    RewardStatus,
    SmsMessage,
)
from referral_harvest.transitions import transition
from referral_harvest.util import utcnow
from tests.conftest import seed_customer, seed_job


def _complete_referral(db, tenant, ask=None):
    ref = Referral(
        tenant_id=tenant.id,
        ask_id=ask.id if ask else None,
        code="MIKE-ABCD",
        referee_name="Referee",
        referee_phone_e164="+15550009999",
    )
    db.add(ref)
    db.flush()
    transition(ref, ReferralStatus.booked)
    ref.booked_at = utcnow()
    transition(ref, ReferralStatus.completed)
    ref.completed_at = utcnow()
    reward = Reward(
        referral_id=ref.id,
        kind=RewardKind.credit,
        amount_cents=2500,
        status=RewardStatus.pending,
    )
    db.add(reward)
    db.flush()
    transition(reward, RewardStatus.approved)
    reward.approved_at = utcnow()
    transition(reward, RewardStatus.fulfilled)
    reward.fulfilled_at = utcnow()
    reward.fulfilled_via = "dashboard"
    db.flush()
    return ref, reward


def test_economics_math(db, tenant):
    now = utcnow()
    start = now - timedelta(days=30)
    customer = seed_customer(db, tenant)
    job1 = seed_job(db, tenant, customer, completed_at=now - timedelta(days=2))
    job2 = seed_job(db, tenant, customer, completed_at=now - timedelta(days=1))
    assert job1.id and job2.id
    for _ in range(3):
        db.add(
            SmsMessage(
                tenant_id=tenant.id,
                to_e164="+15550001001",
                body="hi",
                cost_cents=1.3,
                sent_at=now - timedelta(days=1),
            )
        )
    _complete_referral(db, tenant)

    report = compute_unit_economics(db, tenant, start, now + timedelta(seconds=1))
    assert report["jobs_completed"] == 2
    assert report["referrals_claimed"] == 1
    assert report["referrals_booked"] == 1
    assert report["referrals_completed"] == 1
    assert report["sms_cost_cents"] == 4  # round(3 x 1.3c)
    assert report["rewards_fulfilled_cents"] == 2500
    assert report["total_cost_cents"] == 2504  # round(2503.9)
    assert report["cost_per_referred_job_cents"] == 2504.0
    assert report["paid_cac_cents"] == 30000
    assert report["savings_vs_cac_cents"] == 27496.0


def test_economics_empty_period(db, tenant):
    now = utcnow()
    report = compute_unit_economics(db, tenant, now - timedelta(days=30), now)
    assert report["jobs_completed"] == 0
    assert report["total_cost_cents"] == 0
    assert report["cost_per_referred_job_cents"] is None
    assert report["savings_vs_cac_cents"] is None


def test_economics_respects_period(db, tenant):
    now = utcnow()
    old = now - timedelta(days=60)
    customer = seed_customer(db, tenant)
    seed_job(db, tenant, customer, completed_at=old)
    report = compute_unit_economics(db, tenant, now - timedelta(days=30), now)
    assert report["jobs_completed"] == 0


def test_asks_sent_counted_and_scoped(db, tenant):
    now = utcnow()
    start = now - timedelta(days=30)
    customer = seed_customer(db, tenant)
    job = seed_job(db, tenant, customer, completed_at=now - timedelta(days=2))
    db.add(
        ReferralAsk(
            tenant_id=tenant.id,
            job_id=job.id,
            customer_id=customer.id,
            code="MIKE-ABCD",
            sent_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=29),
            status=AskStatus.sent,
        )
    )
    db.flush()
    result = compute_unit_economics(db, tenant, start, now)
    assert result["asks_sent"] == 1


def test_period_boundaries_inclusive_start_exclusive_end(db, tenant):
    now = utcnow()
    start = now - timedelta(days=30)
    customer = seed_customer(db, tenant)
    seed_job(db, tenant, customer, completed_at=start)  # exactly at start -> counted
    seed_job(db, tenant, customer, completed_at=now)  # exactly at end -> not counted

    def _ask_at(code, sent_at):
        job = seed_job(db, tenant, customer, completed_at=sent_at)
        ask = ReferralAsk(
            tenant_id=tenant.id,
            job_id=job.id,
            customer_id=customer.id,
            code=code,
            sent_at=sent_at,
            expires_at=now + timedelta(days=1),
            status=AskStatus.sent,
        )
        db.add(ask)
        db.flush()
        return ask

    def _referral_at(ask, at):
        ref = Referral(
            tenant_id=tenant.id,
            ask_id=ask.id,
            code=ask.code,
            referee_name="Edge Case",
            referee_phone_e164="+15550009999",
        )
        db.add(ref)
        db.flush()
        transition(ref, ReferralStatus.booked)
        transition(ref, ReferralStatus.completed)
        ref.created_at = at
        ref.booked_at = at
        ref.completed_at = at
        db.flush()
        return ref

    def _reward_for(ref, amount_cents, fulfilled_at):
        reward = Reward(
            referral_id=ref.id,
            kind=RewardKind.credit,
            amount_cents=amount_cents,
            status=RewardStatus.fulfilled,
            fulfilled_at=fulfilled_at,
        )
        db.add(reward)
        db.flush()
        return reward

    # Entities exactly at start are counted; exactly at end are not.
    ask1 = _ask_at("MIKE-EDGE1", start)
    ref1 = _referral_at(ask1, start)
    ref2 = _referral_at(_ask_at("MIKE-EDGE2", start), start)
    _referral_at(_ask_at("MIKE-EDGE3", now), now)
    _reward_for(ref1, 2500, start)  # counted
    _reward_for(ref2, 2500, now)  # exactly at end -> not counted
    for when in (start, now):
        db.add(
            SmsMessage(
                tenant_id=tenant.id,
                to_e164="+15550001111",
                body="b",
                sent_at=when,
                cost_cents=2,
            )
        )
    db.flush()

    result = compute_unit_economics(db, tenant, start, now)
    assert result["jobs_completed"] == 3
    assert result["asks_sent"] == 2
    assert result["referrals_claimed"] == 2
    assert result["referrals_booked"] == 2
    assert result["referrals_completed"] == 2
    assert result["sms_cost_cents"] == 2
    assert result["rewards_fulfilled_cents"] == 2500
    assert result["total_cost_cents"] == 2502
    assert result["cost_per_referred_job_cents"] == 1251.0
