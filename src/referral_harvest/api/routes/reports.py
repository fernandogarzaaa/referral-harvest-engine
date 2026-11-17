"""Unit-economics report."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from referral_harvest.api.deps import get_db, get_tenant
from referral_harvest.engine.economics import compute_unit_economics
from referral_harvest.models import Tenant
from referral_harvest.util import utcnow

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("/unit-economics")
def unit_economics(
    start: str | None = Query(default=None, description="ISO-8601 period start"),
    end: str | None = Query(default=None, description="ISO-8601 period end"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    end_dt = datetime.fromisoformat(end) if end else utcnow()
    start_dt = datetime.fromisoformat(start) if start else end_dt - timedelta(days=30)
    return compute_unit_economics(db, tenant, start_dt, end_dt)
