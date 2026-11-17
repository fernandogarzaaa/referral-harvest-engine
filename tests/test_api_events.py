"""Event API tests: job/rating intake, validation, ask scheduling."""

from referral_harvest.models import Customer
from tests.conftest import unique_phone


def _job_payload(ext="job-1", phone=None, consent="opted_in", **kw):
    payload = {
        "external_id": ext,
        "customer": {
            "name": "Mike Smith",
            "phone_e164": phone or unique_phone(),
            "consent": consent,
        },
        "service_type": "AC Repair",
    }
    payload.update(kw)
    return payload


def test_job_completed_ok(client, app_tenant, app_db):
    key, tenant = app_tenant
    r = client.post("/v1/events/job-completed", json=_job_payload(), headers={"X-API-Key": key})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert job_id
    assert app_db.query(Customer).filter_by(tenant_id=tenant.id).count() == 1


def test_job_completed_upserts_customer(client, app_tenant, app_db):
    key, tenant = app_tenant
    phone = unique_phone()
    client.post(
        "/v1/events/job-completed",
        json=_job_payload(ext="j-1", phone=phone),
        headers={"X-API-Key": key},
    )
    p = _job_payload(ext="j-2", phone=phone)
    p["customer"]["name"] = "Michael Smith"
    client.post("/v1/events/job-completed", json=p, headers={"X-API-Key": key})
    customers = app_db.query(Customer).filter_by(tenant_id=tenant.id).all()
    assert len(customers) == 1
    assert customers[0].name == "Michael Smith"


def test_job_completed_bad_phone_422(client, app_tenant):
    key, _ = app_tenant
    p = _job_payload(phone="not-a-phone")
    r = client.post("/v1/events/job-completed", json=p, headers={"X-API-Key": key})
    assert r.status_code == 422


def test_job_completed_missing_key_422(client):
    r = client.post("/v1/events/job-completed", json=_job_payload())
    assert r.status_code == 422


def test_job_completed_bad_key_401(client):
    r = client.post(
        "/v1/events/job-completed", json=_job_payload(), headers={"X-API-Key": "rh_nope"}
    )
    assert r.status_code == 401


def test_rating_schedules_ask(client, app_tenant):
    key, _ = app_tenant
    client.post("/v1/events/job-completed", json=_job_payload(), headers={"X-API-Key": key})
    r = client.post(
        "/v1/events/rating-received",
        json={"job_external_id": "job-1", "score": 5, "scale": 5},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 202, r.text
    data = r.json()
    assert data["ask_scheduled"] is True
    assert data["ask_id"]


def test_rating_low_score_no_ask(client, app_tenant):
    key, _ = app_tenant
    client.post("/v1/events/job-completed", json=_job_payload(), headers={"X-API-Key": key})
    r = client.post(
        "/v1/events/rating-received",
        json={"job_external_id": "job-1", "score": 2, "scale": 5},
        headers={"X-API-Key": key},
    )
    assert r.json()["ask_scheduled"] is False
    assert r.json()["ask_id"] is None


def test_rating_unknown_job_404(client, app_tenant):
    key, _ = app_tenant
    r = client.post(
        "/v1/events/rating-received",
        json={"job_external_id": "nope", "score": 5, "scale": 5},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 404


def test_rating_unknown_consent_no_ask(client, app_tenant):
    key, _ = app_tenant
    p = _job_payload(consent="unknown")
    client.post("/v1/events/job-completed", json=p, headers={"X-API-Key": key})
    r = client.post(
        "/v1/events/rating-received",
        json={"job_external_id": "job-1", "score": 5, "scale": 5},
        headers={"X-API-Key": key},
    )
    assert r.json()["ask_scheduled"] is False


def test_asks_list(client, app_tenant):
    key, _ = app_tenant
    client.post("/v1/events/job-completed", json=_job_payload(), headers={"X-API-Key": key})
    client.post(
        "/v1/events/rating-received",
        json={"job_external_id": "job-1", "score": 5, "scale": 5},
        headers={"X-API-Key": key},
    )
    r = client.get("/v1/asks", headers={"X-API-Key": key})
    assert r.status_code == 200
    asks = r.json()["asks"]
    assert len(asks) == 1
    assert asks[0]["code"].startswith("MIKE-")
    r2 = client.get("/v1/asks?status=sent", headers={"X-API-Key": key})
    assert r2.json()["asks"] == []
