"""Full-loop test: job -> rating -> ask -> redeem -> book -> complete -> reward -> economics.

Fixture math (spec gate 4): 49 SMS x $0.013 = 63.7c -> 64c, plus one $25.00
fulfilled reward, divided by 1 completed referral = 2564c per referred job.
"""

from datetime import UTC, timedelta

from referral_harvest.models import ReferralAsk, Reward, SmsMessage, tenant_setting
from referral_harvest.scheduler import jobs
from referral_harvest.transitions import transition
from referral_harvest.util import utcnow
from tests.conftest import unique_phone


def _api_job_and_rating(client, key, phone):
    r = client.post(
        "/v1/events/job-completed",
        json={
            "external_id": "loop-job-1",
            "customer": {"name": "Mike Smith", "phone_e164": phone, "consent": "opted_in"},
            "service_type": "AC Repair",
        },
        headers={"X-API-Key": key},
    )
    assert r.status_code == 202
    r = client.post(
        "/v1/events/rating-received",
        json={"job_external_id": "loop-job-1", "score": 5, "scale": 5},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 202
    assert r.json()["ask_scheduled"] is True


def test_full_loop_economics_2564(client, app_tenant, app_db):
    key, tenant = app_tenant
    phone = unique_phone()
    # The API-test DB is shared across the session, so earlier tests may have
    # left due asks behind. Drain them first so this test counts only its own.
    jobs.send_due_asks()
    _api_job_and_rating(client, key, phone)

    # narrow quiet hours so the scheduler sends immediately
    from zoneinfo import ZoneInfo

    local = utcnow().replace(tzinfo=UTC).astimezone(ZoneInfo("America/Chicago"))
    settings = dict(tenant.settings)
    settings["quiet_hours_start"] = (local + timedelta(hours=2)).strftime("%H:%M")
    settings["quiet_hours_end"] = (local + timedelta(hours=3)).strftime("%H:%M")
    tenant.settings = settings
    ask = app_db.query(ReferralAsk).filter_by(tenant_id=tenant.id).one()
    ask.sent_at = utcnow() - timedelta(seconds=30)
    app_db.commit()
    assert jobs.send_due_asks() == 1

    # 48 more background SMS on other asks bring the tenant to 49 messages
    for _ in range(48):
        app_db.add(
            SmsMessage(
                tenant_id=tenant.id,
                to_e164=unique_phone(),
                body="background ask",
                cost_cents=1.3,
                sent_at=utcnow(),
            )
        )
    app_db.commit()

    # redeem -> book -> complete
    code = app_db.query(ReferralAsk).filter_by(tenant_id=tenant.id).one().code
    r = client.post(
        "/v1/redeem",
        json={"code": code, "referee_name": "Jane Doe", "referee_phone_e164": unique_phone()},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    referral_id = r.json()["referral_id"]

    r = client.post(
        f"/v1/referrals/{referral_id}/book",
        json={"booked_at": (utcnow() + timedelta(days=2)).isoformat()},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200

    r = client.post(f"/v1/referrals/{referral_id}/complete", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["reward"]["status"] == "approved"  # manual fulfiller approves

    # operator marks the $25 payout done
    from referral_harvest.models import Referral

    reward = (
        app_db.query(Reward)
        .join(Referral, Reward.referral_id == Referral.id)
        .filter(Referral.tenant_id == tenant.id)
        .one()
    )
    assert reward.amount_cents == tenant_setting(tenant, "reward_amount_cents") == 2500
    transition(reward, "fulfilled")
    reward.fulfilled_at = utcnow()  # as the dashboard's fulfill button does
    app_db.commit()

    r = client.get("/v1/reports/unit-economics", headers={"X-API-Key": key})
    assert r.status_code == 200
    econ = r.json()
    assert econ["sms_cost_cents"] == 64  # round(49 * 1.3)
    assert econ["rewards_fulfilled_cents"] == 2500
    assert econ["referrals_completed"] == 1
    assert econ["cost_per_referred_job_cents"] == 2564
