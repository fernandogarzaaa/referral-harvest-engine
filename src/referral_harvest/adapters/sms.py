"""SMS senders.

- ConsoleSender: dev default; prints the message (visible in `docker compose logs`).
- TwilioSender: real Twilio; needs TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER.
- WebhookSender: POSTs {to, body} to the tenant's own gateway URL.
"""

import uuid

import httpx

from referral_harvest.adapters.base import CredentialError, SMSSender


class ConsoleSender(SMSSender):
    """Log to stdout. The walkthrough asserts the ask text appears here."""

    def send(self, to_e164: str, body: str) -> str:
        sid = f"console-{uuid.uuid4().hex[:12]}"
        print(f"[console-sms] to={to_e164} sid={sid} body={body}", flush=True)
        return sid


class TwilioSender(SMSSender):
    """Real Twilio sender. Raises CredentialError without TWILIO_* env vars."""

    def __init__(
        self,
        account_sid: str | None,
        auth_token: str | None,
        from_number: str | None,
    ):
        if not account_sid or not auth_token or not from_number:
            raise CredentialError(
                "TwilioSender needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER"
            )
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def send(self, to_e164: str, body: str) -> str:
        from twilio.rest import Client  # lazy: twilio is an optional dependency

        client = Client(self.account_sid, self.auth_token)
        message = client.messages.create(to=to_e164, from_=self.from_number, body=body)
        return message.sid


class WebhookSender(SMSSender):
    """Hand the actual sending to the tenant's own system via webhook."""

    def __init__(self, webhook_url: str | None):
        if not webhook_url:
            raise CredentialError(
                "WebhookSender needs a target URL (tenant setting 'sms_webhook_url')"
            )
        self.webhook_url = webhook_url

    def send(self, to_e164: str, body: str) -> str:
        resp = httpx.post(self.webhook_url, json={"to": to_e164, "body": body}, timeout=15)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return str(data.get("sid") or f"webhook-{uuid.uuid4().hex[:12]}")
