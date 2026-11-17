"""Adapter factory: pick implementations from tenant settings + global config."""

from referral_harvest.adapters.base import CRMAdapter, RewardFulfiller, SMSSender
from referral_harvest.adapters.crm import (
    CSVAdapter,
    ServiceTitanAdapter,
    SmartMovingAdapter,
    WebhookCRMAdapter,
)
from referral_harvest.adapters.fulfill import ManualFulfiller, WebhookFulfiller
from referral_harvest.adapters.sms import ConsoleSender, TwilioSender, WebhookSender
from referral_harvest.config import Settings
from referral_harvest.models import Tenant, tenant_setting


def get_crm_adapter(tenant: Tenant, settings: Settings) -> CRMAdapter:
    kind = (tenant_setting(tenant, "crm_adapter") or "webhook").lower()
    if kind == "webhook":
        return WebhookCRMAdapter()
    if kind == "csv":
        return CSVAdapter(tenant_setting(tenant, "csv_path") or "")
    if kind == "servicetitan":
        return ServiceTitanAdapter(
            settings.servicetitan_client_id,
            settings.servicetitan_client_secret,
            tenant.settings.get("servicetitan_tenant_id"),
        )
    if kind == "smartmoving":
        return SmartMovingAdapter(
            tenant.settings.get("smartmoving_api_key") or settings.smartmoving_api_key,
            base_url=tenant.settings.get("smartmoving_base_url"),
            jobs_path=tenant.settings.get("smartmoving_jobs_path"),
        )
    raise ValueError(f"unknown crm_adapter: {kind!r}")


def get_sms_sender(tenant: Tenant, settings: Settings) -> SMSSender:
    kind = (
        tenant_setting(tenant, "sms_sender") or settings.default_sms_sender or "console"
    ).lower()
    if kind == "console":
        return ConsoleSender()
    if kind == "twilio":
        return TwilioSender(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
        )
    if kind == "webhook":
        return WebhookSender(tenant_setting(tenant, "sms_webhook_url"))
    raise ValueError(f"unknown sms_sender: {kind!r}")


def get_reward_fulfiller(tenant: Tenant, _settings: Settings) -> RewardFulfiller:
    kind = (tenant_setting(tenant, "reward_fulfiller") or "manual").lower()
    if kind == "manual":
        return ManualFulfiller()
    if kind == "webhook":
        return WebhookFulfiller(tenant_setting(tenant, "reward_webhook_url"))
    raise ValueError(f"unknown reward_fulfiller: {kind!r}")
