"""FastAPI dependencies: DB session, tenant auth, redeem rate limiting."""

import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from referral_harvest.db import get_db as _get_db
from referral_harvest.models import ApiKey, Tenant
from referral_harvest.security import verify_api_key

get_db = _get_db


def get_tenant(
    x_api_key: str = Header(..., description="Tenant API key (rh_...)"),
    db: Session = Depends(get_db),
) -> Tenant:
    """Authenticate via X-API-Key: prefix lookup + bcrypt verify."""
    prefix = x_api_key[:12]
    candidates = (
        db.scalars(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None)))
        .unique()
        .all()
    )
    for record in candidates:
        if verify_api_key(x_api_key, record):
            tenant = db.get(Tenant, record.tenant_id)
            if tenant is not None:
                return tenant
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")


class RateLimiter:
    """In-memory sliding-window rate limiter (per key, e.g. client IP)."""

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window_start = now - self.window_seconds
        hits = self._hits[key]
        while hits and hits[0] <= window_start:
            hits.popleft()
        if len(hits) >= self.max_calls:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded, slow down",
            )
        hits.append(now)


# 10 redemptions/minute per client IP on the public redeem endpoint.
redeem_limiter = RateLimiter(max_calls=10, window_seconds=60)


def check_redeem_rate_limit(request: Request, x_api_key: str = Header(default="")) -> None:
    # Bucket by client IP + key prefix (no bcrypt here; auth runs separately).
    client = request.client.host if request.client else "unknown"
    redeem_limiter.check(f"{client}:{x_api_key[:12]}")
