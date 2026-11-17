"""Twilio inbound webhook tests: STOP/START consent, bare-code redemption."""

from referral_harvest.models import Consent, Customer
from tests.conftest import unique_phone


def _seed_customer(app_db, tenant, phone=None, consent="opted_in"):
    phone = phone or unique_phone()
    c = Customer(
        tenant_id=tenant.id,
        name="Mike Smith",
        phone_e164=phone,
        consent=Consent(consent),
    )
    app_db.add(c)
    app_db.commit()
    return c


def _twilio(client, from_phone, body):
    return client.post("/v1/webhooks/twilio", data={"From": from_phone, "Body": body})


def _get_customer(app_db, tenant, phone):
    app_db.expire_all()
    return app_db.query(Customer).filter_by(tenant_id=tenant.id, phone_e164=phone).one()


def test_stop_opts_out(client, app_tenant, app_db):
    _, tenant = app_tenant
    phone = unique_phone()
    _seed_customer(app_db, tenant, phone=phone)
    r = _twilio(client, phone, "STOP")
    assert r.status_code == 200
    assert "unsubscribed" in r.text
    assert "xml" in r.headers["content-type"]
    assert _get_customer(app_db, tenant, phone).consent == Consent.opted_out


def test_start_opts_in(client, app_tenant, app_db):
    _, tenant = app_tenant
    phone = unique_phone()
    _seed_customer(app_db, tenant, phone=phone, consent="opted_out")
    r = _twilio(client, phone, "START")
    assert "subscribed again" in r.text
    assert _get_customer(app_db, tenant, phone).consent == Consent.opted_in


def test_help(client, app_tenant, app_db):
    _, tenant = app_tenant
    phone = unique_phone()
    _seed_customer(app_db, tenant, phone=phone)
    r = _twilio(client, phone, "HELP")
    assert "$25 off" in r.text


def _live_code(client, key, phone):
    client.post(
        "/v1/events/job-completed",
        json={
            "external_id": f"job-{phone[-4:]}",
            "customer": {"name": "Mike Smith", "phone_e164": phone, "consent": "opted_in"},
            "service_type": "AC Repair",
        },
        headers={"X-API-Key": key},
    )
    client.post(
        "/v1/events/rating-received",
        json={"job_external_id": f"job-{phone[-4:]}", "score": 5, "scale": 5},
        headers={"X-API-Key": key},
    )
    asks = client.get("/v1/asks", headers={"X-API-Key": key}).json()["asks"]
    assert len(asks) == 1
    return asks[0]["code"]


def test_sms_code_redeem(client, app_tenant, app_db):
    key, tenant = app_tenant
    phone = unique_phone()
    _seed_customer(app_db, tenant, phone=phone)
    code = _live_code(client, key, phone)
    r = _twilio(client, phone, code.lower())  # lowercase also works
    assert "applied" in r.text
    assert code in r.text


def test_sms_unknown_code(client, app_tenant, app_db):
    _, tenant = app_tenant
    phone = unique_phone()
    _seed_customer(app_db, tenant, phone=phone)
    r = _twilio(client, phone, "MIKE-ZZZZ")
    assert "didn't work" in r.text


def test_sms_unknown_sender(client, app_tenant):
    r = _twilio(client, unique_phone(), "MIKE-ABCD")
    assert "don't recognize" in r.text


def test_sms_gibberish(client, app_tenant, app_db):
    _, tenant = app_tenant
    phone = unique_phone()
    _seed_customer(app_db, tenant, phone=phone)
    r = _twilio(client, phone, "hello there")
    assert "didn't understand" in r.text
