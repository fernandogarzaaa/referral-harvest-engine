"""Twilio inbound webhook: STOP/START consent handling and bare-code redemption.

Signature validation: when TWILIO_AUTH_TOKEN is set and the `twilio` package
is importable, the X-Twilio-Signature header is verified; otherwise the
request is processed with a warning logged (documented behavior for dev).
"""

import logging
import re

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from referral_harvest.api.deps import get_db
from referral_harvest.config import get_settings
from referral_harvest.models import Consent, Customer, Tenant
from referral_harvest.redeem import RedeemError, redeem_code
from referral_harvest.util import utcnow

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^[A-Z]+-[A-Z0-9]{4}$")
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "YES", "UNSTOP"}


def _twiml(message: str) -> Response:
    body = (
        f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{message}</Message></Response>'
    )
    return Response(content=body, media_type="text/xml")


def _signature_ok(request: Request, form: dict) -> bool:
    settings = get_settings()
    if not settings.twilio_auth_token:
        return True
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        log.warning("twilio package missing; skipping signature validation")
        return True
    validator = RequestValidator(settings.twilio_auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    return validator.validate(url, dict(form), signature)


@router.post("/twilio")
async def twilio_inbound(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    fields = {k: v for k, v in form.items()}
    if not _signature_ok(request, fields):
        return _twiml("Signature validation failed.")
    from_phone = (fields.get("From") or "").strip()
    text = (fields.get("Body") or "").strip()
    upper = text.upper()

    customer = db.scalar(select(Customer).where(Customer.phone_e164 == from_phone))

    if upper in STOP_WORDS:
        if customer is not None:
            customer.consent = Consent.opted_out
            db.commit()
        return _twiml("You have been unsubscribed and will receive no further texts.")
    if upper in START_WORDS:
        if customer is not None:
            customer.consent = Consent.opted_in
            db.commit()
        return _twiml("You're subscribed again. Reply STOP to unsubscribe anytime.")
    if upper == "HELP":
        return _twiml(
            "Share your referral code with friends for $25 off their first visit. "
            "Reply STOP to unsubscribe."
        )

    if CODE_RE.match(upper):
        if customer is None:
            return _twiml(
                "We don't recognize this number. Ask your friend for their referral code."
            )
        tenant = db.get(Tenant, customer.tenant_id)
        try:
            referral = redeem_code(db, tenant, upper, from_phone, from_phone, now=utcnow())
        except RedeemError as e:
            return _twiml(f"That code didn't work: {e}")
        db.commit()
        return _twiml(
            f"Code {referral.code} applied! You get $25 off your first visit. "
            "We'll text you to schedule."
        )

    return _twiml("Sorry, we didn't understand that. Reply HELP for help, STOP to unsubscribe.")
