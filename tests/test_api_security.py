"""Auth and rate-limit tests."""

from datetime import UTC, datetime

from referral_harvest.models import ApiKey


def test_revoked_key_401(client, app_tenant, app_db):
    key, tenant = app_tenant
    record = app_db.query(ApiKey).filter_by(tenant_id=tenant.id).one()
    record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    app_db.commit()
    r = client.get("/v1/asks", headers={"X-API-Key": key})
    assert r.status_code == 401


def test_tenant_isolation(client, app_tenant):
    key1, _ = app_tenant
    # Second tenant/key in the same app DB.
    from referral_harvest.db import SessionLocal
    from referral_harvest.models import Tenant
    from referral_harvest.security import make_api_key

    db = SessionLocal()
    other = Tenant(name="Other", slug="other-tenant-x")
    db.add(other)
    db.flush()
    raw2, rec2 = make_api_key(other.id)
    db.add(rec2)
    db.commit()
    db.close()
    try:
        r = client.get("/v1/asks", headers={"X-API-Key": raw2})
        assert r.status_code == 200
        assert r.json()["asks"] == []  # tenant 1's ask is invisible
        r1 = client.get("/v1/asks", headers={"X-API-Key": key1})
        assert r1.status_code == 200
    finally:
        db = SessionLocal()
        db.query(ApiKey).filter_by(tenant_id=other.id).delete()
        db.query(Tenant).filter_by(id=other.id).delete()
        db.commit()
        db.close()


def test_redeem_rate_limit_trips(client, app_tenant):
    key, _ = app_tenant
    statuses = set()
    for _ in range(12):
        r = client.post(
            "/v1/redeem",
            json={"code": "NOPE-0000", "referee_name": "R", "referee_phone_e164": "+15550009999"},
            headers={"X-API-Key": key},
        )
        statuses.add(r.status_code)
    assert 429 in statuses  # limiter tripped within 12 rapid calls


def test_healthz_and_readyz(client):
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"ready": True}
