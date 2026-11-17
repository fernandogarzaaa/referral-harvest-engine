"""Referral code generation: {FIRSTNAME}-{4 alnum}, unique per tenant."""

import secrets
import string

from sqlalchemy import exists, select

from referral_harvest.models import ReferralAsk

_ALNUM = string.ascii_uppercase + string.digits
SUFFIX_LEN = 4
MAX_RETRIES = 10


class CodeExhausted(RuntimeError):
    """Raised when a unique code cannot be generated within MAX_RETRIES."""


def first_name_token(name: str) -> str:
    """First whitespace-separated token, alpha-only, uppercased; FRIEND fallback."""
    parts = (name or "").split()
    token = "".join(ch for ch in parts[0].upper()[:12]) if parts else ""
    token = "".join(ch for ch in token if ch.isalpha())
    return token or "FRIEND"


def generate_code(db, tenant_id: str, customer_name: str) -> str:
    """Generate a code unique within the tenant; raises CodeExhausted."""
    base = first_name_token(customer_name)
    for _ in range(MAX_RETRIES):
        suffix = "".join(secrets.choice(_ALNUM) for _ in range(SUFFIX_LEN))
        code = f"{base}-{suffix}"
        taken = db.scalar(
            select(exists().where(ReferralAsk.tenant_id == tenant_id, ReferralAsk.code == code))
        )
        if not taken:
            return code
    raise CodeExhausted(
        f"could not mint a unique code for tenant {tenant_id} after {MAX_RETRIES} retries"
    )
