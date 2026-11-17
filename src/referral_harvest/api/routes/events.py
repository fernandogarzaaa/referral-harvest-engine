"""Event intake: the primary integration seam (their system POSTs these)."""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from referral_harvest.api.deps import get_db, get_tenant
from referral_harvest.ingest import ingest_job_completed, ingest_rating
from referral_harvest.models import Tenant

router = APIRouter(prefix="/v1/events", tags=["events"])

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _check_e164(v: str) -> str:
    if not E164_RE.match(v or ""):
        raise ValueError(f"must be E.164 format, got {v!r}")
    return v


class CustomerPayload(BaseModel):
    external_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    phone_e164: str
    consent: str | None = None
    timezone: str | None = None

    @field_validator("phone_e164")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        return _check_e164(v)


class JobCompletedPayload(BaseModel):
    external_id: str = Field(min_length=1, max_length=100)
    customer: CustomerPayload
    service_type: str = Field(min_length=1, max_length=100)
    tech_name: str | None = None
    amount_cents: int | None = Field(default=None, ge=0)
    completed_at: str | None = None  # ISO-8601; defaults to now (job just completed)

    @field_validator("completed_at")
    @classmethod
    def _parse_completed_at(cls, v):
        if v is None:
            return None
        from datetime import datetime

        return datetime.fromisoformat(v)


class RatingReceivedPayload(BaseModel):
    job_external_id: str = Field(min_length=1)
    score: float = Field(gt=0)
    scale: int = Field(ge=1)
    received_at: str | None = None

    @field_validator("received_at")
    @classmethod
    def _parse_received_at(cls, v):
        if v is None:
            return None
        from datetime import datetime

        return datetime.fromisoformat(v)


@router.post("/job-completed", status_code=202)
def job_completed(
    payload: JobCompletedPayload,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    job = ingest_job_completed(db, tenant, payload.model_dump())
    db.commit()
    return {"job_id": job.id}


@router.post("/rating-received", status_code=202)
def rating_received(
    payload: RatingReceivedPayload,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    try:
        rating, ask = ingest_rating(db, tenant, payload.job_external_id, payload.model_dump())
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    db.commit()
    return {
        "rating_id": rating.id,
        "ask_id": ask.id if ask else None,
        "ask_scheduled": ask is not None,
        "ask_send_at": ask.sent_at.isoformat() if ask and ask.sent_at else None,
    }
