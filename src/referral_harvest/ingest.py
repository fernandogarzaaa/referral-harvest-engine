"""Ingest service: the single code path for job/rating intake.

Used by the webhook API (`POST /v1/events/*`) AND the scheduler's CRM poll
loop, so both integration styles behave identically.
"""

from __future__ import annotations

from sqlalchemy import select

from referral_harvest.engine.ask import evaluate
from referral_harvest.models import Consent, Customer, Job, Rating, Tenant
from referral_harvest.util import utcnow


def _parse_consent(value: str | None) -> Consent | None:
    if value is None:
        return None
    try:
        return Consent(value)
    except ValueError:
        return None


def upsert_customer(db, tenant: Tenant, data: dict) -> Customer:
    """Match by phone first (unique per tenant), then external_id; else create."""
    phone = data["phone_e164"]
    customer = db.scalar(
        select(Customer).where(Customer.tenant_id == tenant.id, Customer.phone_e164 == phone)
    )
    if customer is None and data.get("external_id"):
        customer = db.scalar(
            select(Customer).where(
                Customer.tenant_id == tenant.id,
                Customer.external_id == data["external_id"],
            )
        )
    if customer is None:
        customer = Customer(
            tenant_id=tenant.id,
            external_id=data.get("external_id"),
            name=data["name"],
            phone_e164=phone,
            consent=_parse_consent(data.get("consent")) or Consent.unknown,
            timezone=data.get("timezone") or "America/Chicago",
        )
        db.add(customer)
    else:
        customer.name = data["name"]
        customer.phone_e164 = phone
        if data.get("external_id"):
            customer.external_id = data["external_id"]
        if data.get("consent"):
            parsed = _parse_consent(data["consent"])
            if parsed is not None:
                customer.consent = parsed
        if data.get("timezone"):
            customer.timezone = data["timezone"]
    db.flush()
    return customer


def ingest_job_completed(db, tenant: Tenant, data: dict) -> Job:
    """Upsert a job (by tenant + external_id) and its customer. Returns the job."""
    customer = upsert_customer(db, tenant, data["customer"])
    job = db.scalar(
        select(Job).where(Job.tenant_id == tenant.id, Job.external_id == data["external_id"])
    )
    completed_at = data.get("completed_at")  # datetime or None
    if job is None:
        job = Job(
            tenant_id=tenant.id,
            customer_id=customer.id,
            external_id=data["external_id"],
            service_type=data["service_type"],
            tech_name=data.get("tech_name"),
            amount_cents=data.get("amount_cents"),
            # A job-completed event without a timestamp means "just completed".
            completed_at=completed_at or utcnow(),
        )
        db.add(job)
    else:
        job.customer_id = customer.id
        job.service_type = data["service_type"]
        if data.get("tech_name") is not None:
            job.tech_name = data["tech_name"]
        if data.get("amount_cents") is not None:
            job.amount_cents = data["amount_cents"]
        if completed_at is not None:
            job.completed_at = completed_at
    db.flush()
    return job


def ingest_rating(db, tenant: Tenant, job_external_id: str, data: dict):
    """Upsert the rating for a job and run the ask engine.

    Returns (rating, ask_or_none). Raises LookupError if the job is unknown.
    """
    job = db.scalar(
        select(Job).where(Job.tenant_id == tenant.id, Job.external_id == job_external_id)
    )
    if job is None:
        raise LookupError(f"unknown job_external_id: {job_external_id}")
    now = utcnow()
    rating = db.scalar(select(Rating).where(Rating.job_id == job.id))
    if rating is None:
        rating = Rating(job_id=job.id)
        db.add(rating)
    rating.score = float(data["score"])
    rating.scale = int(data["scale"])
    rating.received_at = data.get("received_at") or now
    db.flush()
    ask = evaluate(tenant, job, rating, now)
    db.flush()
    return rating, ask
