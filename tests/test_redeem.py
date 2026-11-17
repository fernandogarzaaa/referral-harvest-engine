"""Code-redemption abuse paths: bad codes, expiry, max uses."""

from datetime import timedelta

import pytest

from referral_harvest.models import AskStatus, ReferralAsk
from referral_harvest.redeem import RedeemError, redeem_code
from referral_harvest.util import utcnow
from tests.conftest import seed_customer, seed_job


def _ask(db, tenant, customer, job, code="MIKE-ABCD", **kw):
    kw.setdefault("sent_at", utcnow())
    kw.setdefault("expires_at", utcnow() + timedelta(days=30))
    kw.setdefault("status", AskStatus.sent)
    ask = ReferralAsk(
        tenant_id=tenant.id,
        job_id=job.id,
        customer_id=customer.id,
        code=code,
        **kw,
    )
    db.add(ask)
    db.flush()
    return ask


def test_unknown_code_raises_redeem_error(db, tenant):
    with pytest.raises(RedeemError, match="unknown"):
        redeem_code(db, tenant, "NOPE-XXXX", "Jane Doe", "+15550002222")


def test_expired_code_raises_and_marks_ask_expired(db, tenant):
    now = utcnow()
    customer = seed_customer(db, tenant)
    job = seed_job(db, tenant, customer)
    ask = _ask(db, tenant, customer, job, expires_at=now - timedelta(seconds=1))
    with pytest.raises(RedeemError, match="expired"):
        redeem_code(db, tenant, ask.code, "Jane Doe", "+15550002222", now=now)
    db.refresh(ask)
    assert ask.status == AskStatus.expired


def test_code_valid_at_exact_expiry_instant(db, tenant):
    now = utcnow()
    customer = seed_customer(db, tenant)
    job = seed_job(db, tenant, customer)
    ask = _ask(db, tenant, customer, job, expires_at=now)
    ref = redeem_code(db, tenant, ask.code, "Jane Doe", "+15550002222", now=now)
    assert ref.code == ask.code


def test_max_uses_per_code_enforced(db, tenant):
    tenant.settings = {**(tenant.settings or {}), "max_uses_per_code": 2}
    now = utcnow()
    customer = seed_customer(db, tenant)
    job = seed_job(db, tenant, customer)
    ask = _ask(db, tenant, customer, job)
    redeem_code(db, tenant, ask.code, "Jane Doe", "+15550002221", now=now)
    redeem_code(db, tenant, ask.code, "John Doe", "+15550002222", now=now)
    with pytest.raises(RedeemError, match="maximum"):
        redeem_code(db, tenant, ask.code, "Janet Doe", "+15550002223", now=now)
