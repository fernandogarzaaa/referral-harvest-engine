"""Ask listing (read-only)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from referral_harvest.api.deps import get_db, get_tenant
from referral_harvest.models import AskStatus, Customer, Job, ReferralAsk, Tenant

router = APIRouter(prefix="/v1/asks", tags=["asks"])


@router.get("")
def list_asks(
    status: AskStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    q = (
        select(ReferralAsk, Customer.name, Job.external_id)
        .join(Customer, ReferralAsk.customer_id == Customer.id)
        .join(Job, ReferralAsk.job_id == Job.id)
        .where(ReferralAsk.tenant_id == tenant.id)
        .order_by(ReferralAsk.created_at.desc())
    )
    if status is not None:
        q = q.where(ReferralAsk.status == status)
    rows = db.execute(q.limit(limit).offset(offset)).all()
    return {
        "asks": [
            {
                "id": ask.id,
                "code": ask.code,
                "status": ask.status.value,
                "customer_name": customer_name,
                "job_external_id": job_external_id,
                "sent_at": ask.sent_at.isoformat() if ask.sent_at else None,
                "expires_at": ask.expires_at.isoformat() if ask.expires_at else None,
            }
            for ask, customer_name, job_external_id in rows
        ]
    }
