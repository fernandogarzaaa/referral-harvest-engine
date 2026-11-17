"""Ask-engine rule tests: every ask gate."""

from datetime import datetime, timedelta

import pytest

from referral_harvest.engine.ask import evaluate
from referral_harvest.models import AskStatus
from referral_harvest.transitions import transition
from referral_harvest.util import utcnow
from tests.conftest import seed_customer, seed_job, seed_rating

# 2026-01-15 is a Thursday; America/Chicago is CST (UTC-6) in January.
JAN15_1000_CHICAGO_UTC = datetime(2026, 1, 15, 16, 0)  # 10:00 local, outside quiet hours
JAN14_2200_CHICAGO_UTC = datetime(2026, 1, 15, 4, 0)  # 22:00 local, inside quiet hours


def _setup(db, tenant, now=None, **kw):
    """Seed customer + completed job + fresh 5/5 rating, time-consistent with `now`."""
    now = now or utcnow()
    customer = seed_customer(db, tenant, **kw.pop("customer_kw", {}))
    job_kw = kw.pop("job_kw", {})
    job_kw.setdefault("completed_at", now - timedelta(hours=2))
    job = seed_job(db, tenant, customer, **job_kw)
    rating_kw = kw.pop("rating_kw", {})
    rating_kw.setdefault("received_at", now - timedelta(hours=1))
    rating = seed_rating(db, job, **rating_kw)
    return customer, job, rating, now


def test_no_rating_no_ask(db, tenant):
    now = utcnow()
    customer, job, _, _ = _setup(db, tenant, now=now)
    assert evaluate(tenant, job, None, now) is None


def test_job_not_completed_no_ask(db, tenant):
    now = utcnow()
    customer, job, rating, _ = _setup(db, tenant, now=now, job_kw={"completed_at": None})
    assert evaluate(tenant, job, rating, now) is None


def test_stale_rating_no_ask(db, tenant):
    now = utcnow()
    _, job, _, _ = _setup(db, tenant, now=now, rating_kw={"received_at": now - timedelta(hours=49)})
    rating = job.rating
    assert evaluate(tenant, job, rating, now) is None


def test_future_rating_no_ask(db, tenant):
    now = utcnow()
    _, job, _, _ = _setup(db, tenant, now=now, rating_kw={"received_at": now + timedelta(hours=1)})
    assert evaluate(tenant, job, job.rating, now) is None


@pytest.mark.parametrize("score,scale", [(3.0, 5), (2.0, 5), (8.0, 10), (7.5, 10), (5.0, 7)])
def test_low_or_unknown_scale_no_ask(db, tenant, score, scale):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now, rating_kw={"score": score, "scale": scale})
    assert evaluate(tenant, job, job.rating, now) is None


@pytest.mark.parametrize("score,scale", [(4.0, 5), (5.0, 5), (9.0, 10), (10.0, 10)])
def test_satisfied_scores_produce_ask(db, tenant, score, scale):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now, rating_kw={"score": score, "scale": scale})
    ask = evaluate(tenant, job, job.rating, now)
    assert ask is not None
    assert ask.status == AskStatus.scheduled
    assert ask.sent_at == now  # daytime: send immediately


@pytest.mark.parametrize("consent", ["unknown", "opted_out"])
def test_consent_gate_blocks(db, tenant, consent):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now, customer_kw={"consent": consent})
    assert evaluate(tenant, job, job.rating, now) is None


def test_cooldown_blocks_recent_ask(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    customer, job, _, _ = _setup(db, tenant, now=now)
    first = evaluate(tenant, job, job.rating, now)
    assert first is not None
    # Simulate the ask having been sent 10 days ago.
    transition(first, AskStatus.sent)
    first.sent_at = now - timedelta(days=10)
    db.flush()
    job2 = seed_job(db, tenant, customer, completed_at=now - timedelta(hours=2))
    rating2 = seed_rating(db, job2, received_at=now - timedelta(hours=1))
    assert evaluate(tenant, job2, rating2, now) is None


def test_cooldown_expires(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    customer, job, _, _ = _setup(db, tenant, now=now)
    first = evaluate(tenant, job, job.rating, now)
    assert first is not None
    transition(first, AskStatus.sent)
    first.sent_at = now - timedelta(days=100)
    db.flush()
    job2 = seed_job(db, tenant, customer, completed_at=now - timedelta(hours=2))
    rating2 = seed_rating(db, job2, received_at=now - timedelta(hours=1))
    assert evaluate(tenant, job2, rating2, now) is not None


def test_quiet_hours_defers_to_0900_local(db, tenant):
    now = JAN14_2200_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask is not None
    # Next 09:00 America/Chicago after Jan 14 22:00 CST = Jan 15 09:00 CST = 15:00 UTC.
    assert ask.sent_at == datetime(2026, 1, 15, 15, 0)
    assert ask.status == AskStatus.scheduled


def test_quiet_hours_boundary_morning(db, tenant):
    # 08:59 Chicago = 14:59 UTC -> still quiet -> deferred to 15:00 UTC.
    now = datetime(2026, 1, 15, 14, 59)
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask.sent_at == datetime(2026, 1, 15, 15, 0)


def test_idempotent_per_job(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now)
    first = evaluate(tenant, job, job.rating, now)
    assert first is not None
    assert evaluate(tenant, job, job.rating, now) is None


def test_expires_30_days_after_send_time(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask.expires_at == now + timedelta(days=30)


def test_unbound_objects_raise():
    from referral_harvest.models import Job, Rating, Tenant

    with pytest.raises(ValueError, match="session-bound"):
        evaluate(Tenant(name="x", slug="x"), Job(), Rating(), utcnow())


def test_quiet_hours_non_overnight_window(db, tenant):
    """Daytime quiet window (01:00-05:00) exercises the non-overnight branch."""
    tenant.settings = {
        **(tenant.settings or {}),
        "quiet_hours_start": "01:00",
        "quiet_hours_end": "05:00",
    }
    # 03:00 Chicago -> inside -> deferred to 05:00 local = 11:00 UTC.
    now = datetime(2026, 1, 15, 9, 0)
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask is not None
    assert ask.sent_at == datetime(2026, 1, 15, 11, 0)
    # 06:00 Chicago -> outside -> sent immediately (fresh customer dodges cooldown).
    now2 = datetime(2026, 1, 15, 12, 0)
    _, job2, _, _ = _setup(db, tenant, now=now2)
    ask2 = evaluate(tenant, job2, job2.rating, now2)
    assert ask2 is not None
    assert ask2.sent_at == now2


def test_quiet_hours_exact_start_is_quiet(db, tenant):
    # Exactly 20:00 Chicago -> t >= start -> quiet -> deferred to next 09:00 local.
    now = datetime(2026, 1, 16, 2, 0)  # 20:00 CST Jan 15
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask is not None
    assert ask.sent_at == datetime(2026, 1, 16, 15, 0)


def test_quiet_hours_exact_end_is_not_quiet(db, tenant):
    # Exactly 09:00 Chicago -> t < end fails -> not quiet -> sent immediately.
    now = datetime(2026, 1, 15, 15, 0)  # 09:00 CST
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask is not None
    assert ask.sent_at == now


def test_rating_received_exactly_now_is_not_future(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now, rating_kw={"received_at": now})
    assert evaluate(tenant, job, job.rating, now) is not None


def test_rating_at_exactly_max_age_is_fresh(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    _, job, _, _ = _setup(db, tenant, now=now, rating_kw={"received_at": now - timedelta(hours=48)})
    assert evaluate(tenant, job, job.rating, now) is not None


def test_cooldown_at_exactly_cutoff_blocks(db, tenant):
    now = JAN15_1000_CHICAGO_UTC
    customer, job, _, _ = _setup(db, tenant, now=now)
    first = evaluate(tenant, job, job.rating, now)
    assert first is not None
    transition(first, AskStatus.sent)
    first.sent_at = now - timedelta(days=90)  # exactly the cutoff -> still blocked
    db.flush()
    job2 = seed_job(db, tenant, customer, completed_at=now - timedelta(hours=2))
    rating2 = seed_rating(db, job2, received_at=now - timedelta(hours=1))
    assert evaluate(tenant, job2, rating2, now) is None


def test_quiet_hours_non_overnight_exact_boundaries(db, tenant):
    """Daytime window edges: exactly 01:00 is quiet, exactly 05:00 is not."""
    tenant.settings = {
        **(tenant.settings or {}),
        "quiet_hours_start": "01:00",
        "quiet_hours_end": "05:00",
    }
    # Exactly 01:00 Chicago -> start <= t -> quiet -> deferred to 05:00 local.
    now = datetime(2026, 1, 15, 7, 0)
    _, job, _, _ = _setup(db, tenant, now=now)
    ask = evaluate(tenant, job, job.rating, now)
    assert ask is not None
    assert ask.sent_at == datetime(2026, 1, 15, 11, 0)
    # Exactly 05:00 Chicago -> t < end fails -> not quiet -> sent immediately.
    now2 = datetime(2026, 1, 15, 11, 0)
    _, job2, _, _ = _setup(db, tenant, now=now2)
    ask2 = evaluate(tenant, job2, job2.rating, now2)
    assert ask2 is not None
    assert ask2.sent_at == now2
