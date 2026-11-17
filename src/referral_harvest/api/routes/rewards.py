"""Reward queue: list, manual approve, manual fulfill (dashboard path)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from referral_harvest.api.deps import get_db, get_tenant
from referral_harvest.models import Reward, RewardStatus, Tenant
from referral_harvest.transitions import IllegalTransition, transition
from referral_harvest.util import utcnow

router = APIRouter(prefix="/v1/rewards", tags=["rewards"])


class FulfillPayload(BaseModel):
    via: str | None = None


def _get_reward(db: Session, tenant: Tenant, reward_id: str) -> Reward:
    reward = (
        db.scalars(
            select(Reward)
            .join(Reward.referral)
            .where(Reward.id == reward_id, Reward.referral.has(tenant_id=tenant.id))
        )
        .unique()
        .one_or_none()
    )
    if reward is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reward not found")
    return reward


@router.get("")
def list_rewards(
    status: RewardStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    q = (
        select(Reward)
        .join(Reward.referral)
        .where(Reward.referral.has(tenant_id=tenant.id))
        .order_by(Reward.created_at.desc())
    )
    if status is not None:
        q = q.where(Reward.status == status)
    rows = db.scalars(q.limit(limit).offset(offset)).unique().all()
    return {
        "rewards": [
            {
                "id": r.id,
                "referral_id": r.referral_id,
                "kind": r.kind.value,
                "amount_cents": r.amount_cents,
                "status": r.status.value,
                "fulfilled_via": r.fulfilled_via,
            }
            for r in rows
        ]
    }


@router.post("/{reward_id}/approve")
def approve_reward(
    reward_id: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    reward = _get_reward(db, tenant, reward_id)
    if reward.status != RewardStatus.pending:
        return {"reward_id": reward.id, "status": reward.status.value}  # idempotent
    transition(reward, RewardStatus.approved)
    reward.approved_at = utcnow()
    db.commit()
    return {"reward_id": reward.id, "status": reward.status.value}


@router.post("/{reward_id}/fulfill")
def fulfill_reward(
    reward_id: str,
    payload: FulfillPayload | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    reward = _get_reward(db, tenant, reward_id)
    try:
        transition(reward, RewardStatus.fulfilled)
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    reward.fulfilled_at = utcnow()
    reward.fulfilled_via = (payload or FulfillPayload()).via or "dashboard"
    db.commit()
    return {"reward_id": reward.id, "status": reward.status.value}
