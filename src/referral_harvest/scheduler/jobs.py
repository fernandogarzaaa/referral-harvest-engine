"""Background jobs: CRM polling, ask sending, ask expiry, reward retry.

All watermarks live in the SyncState table, never in memory.
"""

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from referral_harvest.adapters.factory import (
    get_crm_adapter,
    get_reward_fulfiller,
    get_sms_sender,
)
from referral_harvest.config import get_settings
from referral_harvest.db import SessionLocal
from referral_harvest.ingest import ingest_job_completed
from referral_harvest.models import (
    AskStatus,
    Customer,
    ReferralAsk,
    Reward,
    RewardStatus,
    SmsMessage,
    SyncState,
    Tenant,
    tenant_setting,
)
from referral_harvest.transitions import transition
from referral_harvest.util import parse_hhmm, utcnow

log = logging.getLogger(__name__)

CRM_WATERMARK_KEY = "crm_watermark"


def _watermark(db, tenant_id: str) -> datetime:
    row = db.scalar(
        select(SyncState).where(
            SyncState.tenant_id == tenant_id, SyncState.key == CRM_WATERMARK_KEY
        )
    )
    if row and row.value:
        return datetime.fromisoformat(row.value)
    return datetime(2020, 1, 1)


def _save_watermark(db, tenant_id: str, value: datetime) -> None:
    row = db.scalar(
        select(SyncState).where(
            SyncState.tenant_id == tenant_id, SyncState.key == CRM_WATERMARK_KEY
        )
    )
    if row is None:
        row = SyncState(tenant_id=tenant_id, key=CRM_WATERMARK_KEY, value="")
        db.add(row)
    row.value = value.isoformat()
    row.updated_at = utcnow()


def poll_crm() -> int:
    """Fetch newly completed jobs for polling tenants; ingest via the API code path."""
    settings = get_settings()
    ingested = 0
    db = SessionLocal()
    try:
        tenants = db.scalars(select(Tenant)).all()
        for tenant in tenants:
            kind = (tenant_setting(tenant, "crm_adapter") or "webhook").lower()
            if kind == "webhook":
                continue
            adapter = get_crm_adapter(tenant, settings)
            since = _watermark(db, tenant.id)
            try:
                jobs = adapter.fetch_completed_jobs(since)
            except Exception:
                log.exception("CRM poll failed for tenant %s", tenant.slug)
                continue
            newest = since
            for item in jobs:
                payload = {
                    "external_id": item.external_id,
                    "customer": {
                        "external_id": item.customer_external_id,
                        "name": item.customer_name,
                        "phone_e164": item.customer_phone_e164,
                        "consent": item.customer_consent,
                        "timezone": item.customer_timezone,
                    },
                    "service_type": item.service_type,
                    "tech_name": item.tech_name,
                    "amount_cents": item.amount_cents,
                    "completed_at": item.completed_at,
                }
                ingest_job_completed(db, tenant, payload)
                ingested += 1
                if item.completed_at and item.completed_at > newest:
                    newest = item.completed_at
            _save_watermark(db, tenant.id, newest)
            db.commit()
    finally:
        db.close()
    return ingested


def _render_ask_body(tenant: Tenant, customer: Customer, job_service: str, code: str) -> str:
    template = tenant_setting(tenant, "ask_template")
    reward_dollars = tenant_setting(tenant, "reward_amount_cents") / 100
    link = f"{get_settings().app_base_url.rstrip('/')}/r/{code}"
    return template.format(
        name=customer.name.split()[0],
        service=job_service,
        trade=tenant_setting(tenant, "trade"),
        code=code,
        reward=f"{reward_dollars:.0f}",
        link=link,
    )


def _in_quiet_hours_now(customer: Customer, tenant: Tenant, now) -> bool:
    from zoneinfo import ZoneInfo

    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(customer.timezone)).replace(tzinfo=None)
    sh, sm = parse_hhmm(tenant_setting(tenant, "quiet_hours_start"))
    eh, em = parse_hhmm(tenant_setting(tenant, "quiet_hours_end"))
    t = (local.hour, local.minute)
    if (sh, sm) < (eh, em):
        return (sh, sm) <= t < (eh, em)
    return t >= (sh, sm) or t < (eh, em)


def send_due_asks(now=None) -> int:
    """Send scheduled asks whose time has come; record cost; flip to sent."""
    now = now or utcnow()
    settings = get_settings()
    sent = 0
    db = SessionLocal()
    try:
        due = (
            db.scalars(
                select(ReferralAsk).where(
                    ReferralAsk.status == AskStatus.scheduled,
                    ReferralAsk.sent_at <= now,
                )
            )
            .unique()
            .all()
        )
        for ask in due:
            tenant = db.get(Tenant, ask.tenant_id)
            customer = db.get(Customer, ask.customer_id)
            if _in_quiet_hours_now(customer, tenant, now):
                continue  # try again next minute
            from referral_harvest.models import Job

            job = db.get(Job, ask.job_id)
            body = _render_ask_body(tenant, customer, job.service_type, ask.code)
            sender = get_sms_sender(tenant, settings)
            try:
                sid = sender.send(customer.phone_e164, body)
            except Exception:
                log.exception("SMS send failed for ask %s", ask.id)
                continue
            cost_cents = settings.sms_cost_usd_per_message * 100
            db.add(
                SmsMessage(
                    tenant_id=tenant.id,
                    ask_id=ask.id,
                    to_e164=customer.phone_e164,
                    body=body,
                    cost_cents=cost_cents,
                    provider_sid=sid,
                    sent_at=now,
                )
            )
            ask.sent_at = now  # actual send time overwrites the scheduled time
            transition(ask, AskStatus.sent)
            db.commit()
            sent += 1
    finally:
        db.close()
    return sent


def expire_asks(now=None) -> int:
    """Expire sent asks past their expires_at."""
    now = now or utcnow()
    expired = 0
    db = SessionLocal()
    try:
        stale = (
            db.scalars(
                select(ReferralAsk).where(
                    ReferralAsk.status == AskStatus.sent,
                    ReferralAsk.expires_at < now,
                )
            )
            .unique()
            .all()
        )
        for ask in stale:
            transition(ask, AskStatus.expired)
            expired += 1
        db.commit()
    finally:
        db.close()
    return expired


def retry_reward_fulfillment() -> int:
    """Retry webhook fulfillment for pending/approved rewards of webhook tenants."""
    settings = get_settings()
    retried = 0
    db = SessionLocal()
    try:
        tenants = db.scalars(select(Tenant)).all()
        for tenant in tenants:
            if (tenant_setting(tenant, "reward_fulfiller") or "manual").lower() != "webhook":
                continue
            pending = (
                db.scalars(
                    select(Reward)
                    .join(Reward.referral)
                    .where(
                        Reward.referral.has(tenant_id=tenant.id),
                        Reward.status.in_([RewardStatus.pending, RewardStatus.approved]),
                    )
                )
                .unique()
                .all()
            )
            fulfiller = get_reward_fulfiller(tenant, settings)
            for reward in pending:
                try:
                    fulfiller.fulfill(reward)
                    retried += 1
                except Exception:
                    log.exception("reward retry failed for %s", reward.id)
            db.commit()
    finally:
        db.close()
    return retried


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_crm, "interval", minutes=5, id="poll_crm", max_instances=1)
    scheduler.add_job(send_due_asks, "interval", minutes=1, id="send_due_asks", max_instances=1)
    scheduler.add_job(expire_asks, "interval", hours=1, id="expire_asks", max_instances=1)
    scheduler.add_job(
        retry_reward_fulfillment, "interval", minutes=5, id="retry_rewards", max_instances=1
    )
    scheduler.start()
    log.info("scheduler started: poll_crm/5m, send_due_asks/1m, expire_asks/1h, retry_rewards/5m")
    return scheduler
