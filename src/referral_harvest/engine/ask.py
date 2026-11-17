"""The ask engine: decides whether a completed job earns a referral ask.

`evaluate(tenant, job, rating, now) -> ReferralAsk | None`

Rules (all tenant-configurable; defaults in parentheses):
1. Job completed; rating exists and is recent (<= 48h old).
2. Peak-satisfaction gate: score >= 4 on 5-scale, >= 9 on 10-scale.
   No ask on missing/low ratings: mistimed asks read as MLM and kill trust.
3. Consent gate: customer.consent == opted_in. Unknown/opted_out -> no ask, ever.
4. Cooldown: no sent ask for this customer in the last ask_cooldown_days (90).
5. Quiet hours: if now is in quiet hours (recipient-local), schedule the send
   for the next quiet_hours_end (09:00) recipient-local.
6. Idempotency: one scheduled/sent ask per job (checked here, enforced by a
   partial unique index at the DB level).

The session is recovered from the bound objects via `object_session`, so the
signature stays exactly as specified.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

from referral_harvest.engine.codes import generate_code
from referral_harvest.models import (
    AskStatus,
    Consent,
    Customer,
    ReferralAsk,
    tenant_setting,
)
from referral_harvest.util import parse_hhmm, utcnow


def _local_now(now_utc: datetime, tz_name: str) -> datetime:
    return now_utc.replace(tzinfo=UTC).astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)


def _in_quiet_hours(local: datetime, start_hm: str, end_hm: str) -> bool:
    sh, sm = parse_hhmm(start_hm)
    eh, em = parse_hhmm(end_hm)
    t = (local.hour, local.minute)
    start, end = (sh, sm), (eh, em)
    if start < end:
        return start <= t < end
    return t >= start or t < end  # overnight window, e.g. 20:00 -> 09:00


def _next_send_time(local: datetime, end_hm: str, tz_name: str) -> datetime:
    """Next quiet_hours_end in recipient-local time, returned as naive UTC."""
    eh, em = parse_hhmm(end_hm)
    candidate = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC).replace(tzinfo=None)


def _satisfied(tenant, rating) -> bool:
    if rating.scale == 5:
        return rating.score >= tenant_setting(tenant, "min_rating_for_ask")
    if rating.scale == 10:
        return rating.score >= tenant_setting(tenant, "min_rating_10_scale")
    return False


def evaluate(tenant, job, rating, now: datetime):
    """Evaluate ask rules; create and return a scheduled ReferralAsk, or None."""
    db = object_session(job) or object_session(tenant) or object_session(rating)
    if db is None:
        raise ValueError("evaluate() requires session-bound tenant/job/rating objects")

    # Rule 1: completed job, existing recent rating for THIS job.
    if job.completed_at is None or rating is None or rating.job_id != job.id:
        return None
    max_age = timedelta(hours=tenant_setting(tenant, "rating_max_age_hours"))
    if rating.received_at > now or now - rating.received_at > max_age:
        return None

    # Rule 2: peak-satisfaction gate.
    if not _satisfied(tenant, rating):
        return None

    # Rule 3: consent gate.
    customer = db.get(Customer, job.customer_id)
    if customer is None or customer.consent != Consent.opted_in:
        return None

    # Rule 4: cooldown — no scheduled/sent ask for this customer recently.
    cutoff = now - timedelta(days=tenant_setting(tenant, "ask_cooldown_days"))
    recent = db.scalar(
        select(ReferralAsk.id)
        .where(
            ReferralAsk.tenant_id == tenant.id,
            ReferralAsk.customer_id == customer.id,
            ReferralAsk.status.in_([AskStatus.scheduled, AskStatus.sent]),
            func.coalesce(ReferralAsk.sent_at, ReferralAsk.created_at) >= cutoff,
        )
        .limit(1)
    )
    if recent is not None:
        return None

    # Rule 6 (checked before insert): one scheduled/sent ask per job.
    existing = db.scalar(
        select(ReferralAsk.id)
        .where(
            ReferralAsk.job_id == job.id,
            ReferralAsk.status.in_([AskStatus.scheduled, AskStatus.sent]),
        )
        .limit(1)
    )
    if existing is not None:
        return None

    # Rule 5: quiet hours -> defer to next quiet_hours_end, recipient-local.
    local = _local_now(now, customer.timezone)
    q_start = tenant_setting(tenant, "quiet_hours_start")
    q_end = tenant_setting(tenant, "quiet_hours_end")
    send_at = (
        _next_send_time(local, q_end, customer.timezone)
        if _in_quiet_hours(local, q_start, q_end)
        else now
    )

    ask = ReferralAsk(
        tenant_id=tenant.id,
        job_id=job.id,
        customer_id=customer.id,
        code=generate_code(db, tenant.id, customer.name),
        # sent_at doubles as the scheduled send time while status=scheduled;
        # the scheduler overwrites it with the actual send time on send.
        # (Column stays nullable in the schema; evaluate() always populates it.)
        sent_at=send_at,
        expires_at=send_at + timedelta(days=tenant_setting(tenant, "ask_expiry_days")),
        status=AskStatus.scheduled,
        created_at=utcnow(),
    )
    db.add(ask)
    db.flush()
    return ask
