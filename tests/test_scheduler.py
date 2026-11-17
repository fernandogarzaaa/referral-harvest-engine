"""Scheduler job tests: send, expire, watermark persistence.

These use the app DB fixtures (app_db/app_tenant) because the scheduler jobs
read through the global SessionLocal, same as production.
"""

from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from referral_harvest.engine import evaluate
from referral_harvest.models import (
    AskStatus,
    Rating,
    ReferralAsk,
    SmsMessage,
    SyncState,
    tenant_setting,
)
from referral_harvest.scheduler import jobs
from referral_harvest.util import utcnow
from tests.conftest import seed_customer, seed_job, seed_rating


def _seed_ask(db, tenant, rating=5, sent_at=None):
    customer = seed_customer(db, tenant)
    job = seed_job(db, tenant, customer, completed_at=utcnow() - timedelta(hours=1))
    seed_rating(db, job, score=rating)
    r = db.scalar(select(Rating).where(Rating.job_id == job.id))
    ask = evaluate(tenant, job, r, utcnow())
    assert ask is not None
    if sent_at is not None:
        ask.sent_at = sent_at
        db.commit()
    return ask


def _set_quiet_hours(db, tenant, start, end):
    """Reassign (not mutate) so SQLAlchemy notices the JSON change."""
    settings = dict(tenant.settings)
    settings["quiet_hours_start"] = start
    settings["quiet_hours_end"] = end
    tenant.settings = settings
    db.commit()


def _chicago_now():
    return utcnow().replace(tzinfo=UTC).astimezone(ZoneInfo("America/Chicago"))


def _no_quiet_now(db, tenant):
    """Narrow quiet hours to a future window so sends proceed in tests."""
    local = _chicago_now()
    _set_quiet_hours(
        db,
        tenant,
        (local + timedelta(hours=2)).strftime("%H:%M"),
        (local + timedelta(hours=3)).strftime("%H:%M"),
    )


def test_send_due_asks_marks_sent(app_db, app_tenant):
    _, tenant = app_tenant
    ask = _seed_ask(app_db, tenant)
    _no_quiet_now(app_db, tenant)
    # force it to be due now (evaluate may have deferred to morning)
    app_db.expire_all()
    ask = app_db.get(ReferralAsk, ask.id)
    ask.sent_at = utcnow() - timedelta(seconds=30)
    app_db.commit()
    assert jobs.send_due_asks() == 1
    app_db.expire_all()
    ask = app_db.get(ReferralAsk, ask.id)
    assert ask.status == AskStatus.sent
    # an SmsMessage was recorded with the per-message cost
    msgs = app_db.query(SmsMessage).filter_by(ask_id=ask.id).all()
    assert len(msgs) == 1
    assert msgs[0].cost_cents == 1.3


def test_send_due_asks_respects_quiet_hours(app_db, app_tenant):
    _, tenant = app_tenant
    ask = _seed_ask(app_db, tenant)
    # cover "now" with a 2h window in the customer's own timezone
    local = _chicago_now()
    _set_quiet_hours(
        app_db,
        tenant,
        (local - timedelta(hours=1)).strftime("%H:%M"),
        (local + timedelta(hours=1)).strftime("%H:%M"),
    )
    app_db.expire_all()
    ask = app_db.get(ReferralAsk, ask.id)
    ask.sent_at = utcnow() - timedelta(seconds=30)
    app_db.commit()
    assert jobs.send_due_asks() == 0
    app_db.expire_all()
    assert app_db.get(ReferralAsk, ask.id).status == AskStatus.scheduled


def test_send_due_asks_skips_future(app_db, app_tenant):
    _, tenant = app_tenant
    ask = _seed_ask(app_db, tenant)
    _no_quiet_now(app_db, tenant)
    app_db.expire_all()
    ask = app_db.get(ReferralAsk, ask.id)
    ask.sent_at = utcnow() + timedelta(hours=2)
    app_db.commit()
    assert jobs.send_due_asks() == 0


def test_expire_asks(app_db, app_tenant):
    _, tenant = app_tenant
    ask = _seed_ask(app_db, tenant)
    _no_quiet_now(app_db, tenant)
    app_db.expire_all()
    ask = app_db.get(ReferralAsk, ask.id)
    ask.sent_at = utcnow() - timedelta(seconds=30)
    app_db.commit()
    assert jobs.send_due_asks() == 1
    app_db.expire_all()
    ask = app_db.get(ReferralAsk, ask.id)
    ask.expires_at = utcnow() - timedelta(seconds=1)
    app_db.commit()
    assert jobs.expire_asks() == 1
    app_db.expire_all()
    assert app_db.get(ReferralAsk, ask.id).status == AskStatus.expired


def test_watermark_roundtrip(app_db, app_tenant):
    from datetime import datetime

    _, tenant = app_tenant
    t0 = datetime(2026, 3, 4, 5, 6, 7)
    jobs._save_watermark(app_db, tenant.id, t0)
    app_db.commit()
    app_db.expire_all()
    assert jobs._watermark(app_db, tenant.id) == t0
    assert app_db.query(SyncState).filter_by(tenant_id=tenant.id).count() == 1


def test_render_ask_body_mentions_code_and_reward(app_db, app_tenant):
    _, tenant = app_tenant
    customer = seed_customer(app_db, tenant)
    body = jobs._render_ask_body(tenant, customer, "AC Repair", "MIKE-AB12")
    assert "MIKE-AB12" in body
    assert str(tenant_setting(tenant, "reward_amount_cents") // 100) in body
    assert "/r/MIKE-AB12" in body
