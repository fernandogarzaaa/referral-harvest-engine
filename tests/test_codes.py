"""Code generation tests: format, per-tenant uniqueness, exhaustion."""

import re
import secrets

import pytest

from referral_harvest.engine.codes import (
    CodeExhausted,
    first_name_token,
    generate_code,
)
from referral_harvest.models import Tenant
from tests.conftest import seed_customer, seed_job

CODE_RE = re.compile(r"^[A-Z]+-[A-Z0-9]{4}$")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Mike Smith", "MIKE"),
        ("  ana  maria ", "ANA"),
        ("O'Brien", "OBRIEN"),
        ("", "FRIEND"),
        ("   ", "FRIEND"),
        ("123", "FRIEND"),
        ("Zoë Müller", "ZOË"),
    ],
)
def test_first_name_token(name, expected):
    assert first_name_token(name) == expected


def test_code_format(db, tenant):
    code = generate_code(db, tenant.id, "Mike Smith")
    assert CODE_RE.match(code), code
    assert code.startswith("MIKE-")


def test_codes_unique_per_tenant(db, tenant):
    seen = {generate_code(db, tenant.id, "Mike Smith") for _ in range(25)}
    assert len(seen) == 25


def test_codes_may_repeat_across_tenants(db, tenant):
    other = Tenant(name="Other Shop", slug="other-shop")
    db.add(other)
    db.flush()
    # Same name -> same base; just assert generation works for the other tenant.
    assert CODE_RE.match(generate_code(db, other.id, "Mike Smith"))


def test_code_exhaustion_raises(db, tenant, monkeypatch):
    # Force every draw to collide with an existing code.
    monkeypatch.setattr(secrets, "choice", lambda _alphabet: "A")
    first = generate_code(db, tenant.id, "Mike Smith")
    assert first == "MIKE-AAAA"
    # Persist it so the next draws collide.
    customer = seed_customer(db, tenant)
    job = seed_job(db, tenant, customer)
    from referral_harvest.models import ReferralAsk
    from referral_harvest.util import utcnow

    db.add(
        ReferralAsk(
            tenant_id=tenant.id,
            job_id=job.id,
            customer_id=customer.id,
            code=first,
            sent_at=utcnow(),
            expires_at=utcnow(),
        )
    )
    db.flush()
    with pytest.raises(CodeExhausted):
        generate_code(db, tenant.id, "Mike Smith")
