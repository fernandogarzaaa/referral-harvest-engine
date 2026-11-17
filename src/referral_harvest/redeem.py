"""Code redemption: the single path for turning a referral code into a Referral.

Used by POST /v1/redeem and by the Twilio inbound webhook (bare-code SMS).
"""

from __future__ import annotations

from sqlalchemy import func, select

from referral_harvest.models import (
    AskStatus,
    Referral,
    ReferralAsk,
    ReferralStatus,
    Tenant,
    tenant_setting,
)
from referral_harvest.transitions import IllegalTransition, transition
from referral_harvest.util import utcnow


class RedeemError(ValueError):
    """A redemption that must be rejected (bad code, expired, max uses)."""


def _live_ask(db, tenant: Tenant, code: str) -> ReferralAsk | None:
    return db.scalar(
        select(ReferralAsk).where(
            ReferralAsk.tenant_id == tenant.id,
            ReferralAsk.code == code,
            ReferralAsk.status.in_([AskStatus.scheduled, AskStatus.sent]),
        )
    )


def redeem_code(
    db,
    tenant: Tenant,
    code: str,
    referee_name: str,
    referee_phone_e164: str,
    now=None,
) -> Referral:
    """Validate a code and create a claimed Referral. Raises RedeemError."""
    now = now or utcnow()
    normalized = (code or "").strip().upper()
    ask = _live_ask(db, tenant, normalized)
    if ask is None:
        raise RedeemError("code is unknown, expired, or cancelled")
    if ask.expires_at < now:
        try:
            transition(ask, AskStatus.expired)
        except IllegalTransition:
            pass
        db.flush()
        raise RedeemError("code has expired")

    max_uses = tenant_setting(tenant, "max_uses_per_code")
    uses = db.scalar(
        select(func.count())
        .select_from(Referral)
        .where(
            Referral.ask_id == ask.id,
            Referral.status != ReferralStatus.cancelled,
        )
    )
    if uses >= max_uses:
        raise RedeemError("code has reached its maximum number of uses")

    referral = Referral(
        tenant_id=tenant.id,
        ask_id=ask.id,
        code=ask.code,
        referee_name=referee_name,
        referee_phone_e164=referee_phone_e164,
        status=ReferralStatus.claimed,
    )
    db.add(referral)
    db.flush()
    return referral
