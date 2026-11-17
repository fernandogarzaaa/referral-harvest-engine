"""Referral lifecycle: list, book, complete (completing mints the reward)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from referral_harvest.adapters.factory import get_reward_fulfiller
from referral_harvest.api.deps import get_db, get_tenant
from referral_harvest.config import get_settings
from referral_harvest.models import (
    Referral,
    ReferralStatus,
    Reward,
    RewardKind,
    RewardStatus,
    Tenant,
    tenant_setting,
)
from referral_harvest.transitions import IllegalTransition, transition
from referral_harvest.util import utcnow

router = APIRouter(prefix="/v1/referrals", tags=["referrals"])
log = logging.getLogger(__name__)


class BookPayload(BaseModel):
    booked_at: str | None = None

    def parsed(self):
        if not self.booked_at:
            return utcnow()
        from datetime import datetime

        return datetime.fromisoformat(self.booked_at)


class CompletePayload(BaseModel):
    completed_at: str | None = None

    def parsed(self):
        if not self.completed_at:
            return utcnow()
        from datetime import datetime

        return datetime.fromisoformat(self.completed_at)


def _get_referral(db: Session, tenant: Tenant, referral_id: str) -> Referral:
    referral = db.scalar(
        select(Referral).where(Referral.id == referral_id, Referral.tenant_id == tenant.id)
    )
    if referral is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="referral not found")
    return referral


@router.get("")
def list_referrals(
    status: ReferralStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    q = select(Referral).where(Referral.tenant_id == tenant.id).order_by(Referral.created_at.desc())
    if status is not None:
        q = q.where(Referral.status == status)
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return {
        "referrals": [
            {
                "id": r.id,
                "code": r.code,
                "referee_name": r.referee_name,
                "referee_phone_e164": r.referee_phone_e164,
                "status": r.status.value,
                "booked_at": r.booked_at.isoformat() if r.booked_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ]
    }


@router.post("/{referral_id}/book")
def book_referral(
    referral_id: str,
    payload: BookPayload | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    referral = _get_referral(db, tenant, referral_id)
    try:
        transition(referral, ReferralStatus.booked)
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    referral.booked_at = (payload or BookPayload()).parsed()
    db.commit()
    return {"referral_id": referral.id, "status": referral.status.value}


@router.post("/{referral_id}/complete")
def complete_referral(
    referral_id: str,
    payload: CompletePayload | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    referral = _get_referral(db, tenant, referral_id)
    try:
        transition(referral, ReferralStatus.completed)
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    referral.completed_at = (payload or CompletePayload()).parsed()

    reward = Reward(
        referral_id=referral.id,
        kind=RewardKind(tenant_setting(tenant, "reward_kind")),
        amount_cents=int(tenant_setting(tenant, "reward_amount_cents")),
        status=RewardStatus.pending,
    )
    db.add(reward)
    db.flush()

    # Advance the reward through the tenant's fulfiller now; on failure it
    # stays pending/approved and the scheduler retry job picks it up.
    fulfiller = get_reward_fulfiller(tenant, get_settings())
    try:
        fulfiller.fulfill(reward)
    except Exception:
        log.warning("reward auto-fulfill failed for %s; will retry", reward.id, exc_info=True)
    db.commit()
    db.refresh(reward)
    return {
        "referral_id": referral.id,
        "status": referral.status.value,
        "reward": {"id": reward.id, "status": reward.status.value},
    }
