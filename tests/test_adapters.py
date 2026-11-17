"""Adapter tests: interfaces, CSV parsing, credential gating, sender/fulfiller behavior."""

from datetime import datetime

import httpx
import pytest

from referral_harvest.adapters.base import CredentialError
from referral_harvest.adapters.crm import (
    CSVAdapter,
    ServiceTitanAdapter,
    SmartMovingAdapter,
    WebhookCRMAdapter,
)
from referral_harvest.adapters.factory import (
    get_crm_adapter,
    get_reward_fulfiller,
    get_sms_sender,
)
from referral_harvest.adapters.fulfill import ManualFulfiller, WebhookFulfiller
from referral_harvest.adapters.sms import ConsoleSender, TwilioSender, WebhookSender
from referral_harvest.config import Settings
from referral_harvest.models import (
    Referral,
    Reward,
    RewardKind,
    RewardStatus,
    Tenant,
)

SETTINGS = Settings()


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self.status_code = status
        self.content = b"{}" if payload is None else b'{"ok": true}'

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


def _tenant(db, **settings_overrides):
    t = Tenant(name="Adapter Shop", slug=f"adapter-{id(settings_overrides)}")
    merged = dict(t.settings or {})
    merged.update(settings_overrides)
    t.settings = merged
    db.add(t)
    db.flush()
    return t


# --- CRM adapters -----------------------------------------------------------


def test_webhook_adapter_is_noop():
    assert WebhookCRMAdapter().fetch_completed_jobs(datetime(2026, 1, 1)) == []


def test_csv_adapter(tmp_path):
    path = tmp_path / "jobs.csv"
    path.write_text(
        "external_id,customer_name,customer_phone_e164,service_type,completed_at,"
        "tech_name,amount_cents,customer_consent\n"
        "j1,Amy,+15550001001,AC Repair,2026-01-10T10:00:00,Sam,65000,opted_in\n"
        "j2,Bo,+15550001002,Plumbing,2026-01-01T10:00:00,,0,\n"
        "j3,Cy,+15550001003,Electrical,2026-01-20T10:00:00,Kim,12000,opted_out\n"
    )
    jobs = CSVAdapter(str(path)).fetch_completed_jobs(datetime(2026, 1, 5))
    assert [j.external_id for j in jobs] == ["j1", "j3"]
    assert jobs[0].tech_name == "Sam"
    assert jobs[0].amount_cents == 65000
    assert jobs[0].customer_consent == "opted_in"
    assert jobs[1].tech_name == "Kim"
    assert jobs[1].customer_consent == "opted_out"


def test_csv_adapter_missing_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("external_id,customer_name\nj1,Amy\n")
    with pytest.raises(ValueError, match="missing required columns"):
        CSVAdapter(str(path)).fetch_completed_jobs(datetime(2026, 1, 1))


def test_csv_adapter_missing_file():
    with pytest.raises(FileNotFoundError):
        CSVAdapter("/nonexistent/jobs.csv").fetch_completed_jobs(datetime(2026, 1, 1))


def test_servicetitan_needs_creds():
    with pytest.raises(CredentialError):
        ServiceTitanAdapter(None, None, "123")
    with pytest.raises(CredentialError):
        ServiceTitanAdapter("id", "secret", None)


def test_servicetitan_mapping(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"access_token": "tok"}))
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _Resp(
            {
                "data": [
                    {
                        "id": 42,
                        "customerId": 7,
                        "customerName": "Amy",
                        "customerPhone": "+15550001001",
                        "jobType": "AC Repair",
                        "technician": {"name": "Sam"},
                        "completedOn": "2026-01-10T10:00:00",
                    }
                ]
            }
        ),
    )
    jobs = ServiceTitanAdapter("id", "secret", "123").fetch_completed_jobs(datetime(2026, 1, 1))
    assert len(jobs) == 1
    assert jobs[0].external_id == "42"
    assert jobs[0].customer_name == "Amy"
    assert jobs[0].tech_name == "Sam"


def test_smartmoving_needs_key():
    with pytest.raises(CredentialError):
        SmartMovingAdapter(None)


def test_smartmoving_mapping(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _Resp(
            {
                "jobs": [
                    {
                        "id": "sm-1",
                        "customer_name": "Bo",
                        "customer_phone": "+15550001002",
                        "service_type": "Move",
                        "completed_at": "2026-01-10T10:00:00",
                    }
                ]
            }
        ),
    )
    jobs = SmartMovingAdapter("key").fetch_completed_jobs(datetime(2026, 1, 1))
    assert jobs[0].external_id == "sm-1"
    assert jobs[0].service_type == "Move"


# --- SMS senders ------------------------------------------------------------


def test_console_sender(capsys):
    sid = ConsoleSender().send("+15550001001", "hello")
    assert sid.startswith("console-")
    out = capsys.readouterr().out
    assert "[console-sms]" in out and "+15550001001" in out and "hello" in out


def test_twilio_needs_creds():
    with pytest.raises(CredentialError):
        TwilioSender(None, None, None)


def test_webhook_sender_needs_url():
    with pytest.raises(CredentialError):
        WebhookSender(None)


def test_webhook_sender_ok(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"sid": "SM123"}))
    assert WebhookSender("https://example.com/sms").send("+1", "hi") == "SM123"


def test_webhook_sender_http_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(status=500))
    with pytest.raises(httpx.HTTPStatusError):
        WebhookSender("https://example.com/sms").send("+1", "hi")


# --- Reward fulfillers ------------------------------------------------------


def _reward(db, tenant):
    ref = Referral(
        tenant_id=tenant.id,
        code="MIKE-ABCD",
        referee_name="R",
        referee_phone_e164="+15550009999",
    )
    db.add(ref)
    db.flush()
    reward = Reward(referral_id=ref.id, kind=RewardKind.credit, amount_cents=2500)
    db.add(reward)
    db.flush()
    return reward


def test_manual_fulfiller_approves(db, tenant):
    reward = _reward(db, tenant)
    ManualFulfiller().fulfill(reward)
    assert reward.status == RewardStatus.approved
    assert reward.approved_at is not None
    # idempotent second call
    ManualFulfiller().fulfill(reward)
    assert reward.status == RewardStatus.approved


def test_webhook_fulfiller_needs_url():
    with pytest.raises(CredentialError):
        WebhookFulfiller(None)


def test_webhook_fulfiller_ok(monkeypatch, db, tenant):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"ok": True}))
    reward = _reward(db, tenant)
    WebhookFulfiller("https://example.com/rewards").fulfill(reward)
    assert reward.status == RewardStatus.fulfilled
    assert reward.fulfilled_via == "webhook"
    assert reward.approved_at is not None


def test_webhook_fulfiller_http_error_leaves_pending(monkeypatch, db, tenant):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(status=500))
    reward = _reward(db, tenant)
    with pytest.raises(httpx.HTTPStatusError):
        WebhookFulfiller("https://example.com/rewards").fulfill(reward)
    assert reward.status == RewardStatus.pending


# --- Factory ----------------------------------------------------------------


def test_factory_defaults(db, tenant):
    assert isinstance(get_crm_adapter(tenant, SETTINGS), WebhookCRMAdapter)
    assert isinstance(get_sms_sender(tenant, SETTINGS), ConsoleSender)
    assert isinstance(get_reward_fulfiller(tenant, SETTINGS), ManualFulfiller)


def test_factory_overrides(db):
    t = _tenant(
        db,
        crm_adapter="csv",
        csv_path="/tmp/x.csv",
        sms_sender="webhook",
        sms_webhook_url="https://x",
        reward_fulfiller="webhook",
        reward_webhook_url="https://y",
    )
    assert isinstance(get_crm_adapter(t, SETTINGS), CSVAdapter)
    assert isinstance(get_sms_sender(t, SETTINGS), WebhookSender)
    assert isinstance(get_reward_fulfiller(t, SETTINGS), WebhookFulfiller)


def test_factory_unknown_kind(db):
    t = _tenant(db, crm_adapter="pigeon")
    with pytest.raises(ValueError, match="unknown crm_adapter"):
        get_crm_adapter(t, SETTINGS)
