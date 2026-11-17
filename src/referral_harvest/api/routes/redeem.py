"""Referral code redemption (public endpoint, rate-limited)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from referral_harvest.api.deps import check_redeem_rate_limit, get_db, get_tenant
from referral_harvest.models import Tenant, tenant_setting
from referral_harvest.redeem import RedeemError, redeem_code
from referral_harvest.util import utcnow

router = APIRouter(tags=["redeem"])


class RedeemPayload(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    referee_name: str = Field(min_length=1, max_length=200)
    referee_phone_e164: str = Field(min_length=8, max_length=20)


@router.post("/v1/redeem", dependencies=[Depends(check_redeem_rate_limit)], status_code=201)
def redeem(
    payload: RedeemPayload,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    try:
        referral = redeem_code(
            db,
            tenant,
            payload.code,
            payload.referee_name,
            payload.referee_phone_e164,
            now=utcnow(),
        )
    except RedeemError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    db.commit()
    amount = tenant_setting(tenant, "reward_amount_cents")
    kind = tenant_setting(tenant, "reward_kind")
    return {
        "referral_id": referral.id,
        "code": referral.code,
        "reward_terms": {
            "amount_cents": amount,
            "kind": kind,
            "note": f"Referrer earns {amount / 100:.2f} {kind} when the referred job completes.",
        },
    }
