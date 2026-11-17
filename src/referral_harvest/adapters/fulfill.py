"""Reward fulfillers.

- ManualFulfiller: approves the reward; a human completes fulfillment in the
  dashboard (approve -> fulfill buttons in the rewards queue).
- WebhookFulfiller: POSTs the reward payload to the tenant's URL; marks the
  reward fulfilled on a 2xx response, raises otherwise (scheduler retries).
"""

import httpx

from referral_harvest.adapters.base import CredentialError, RewardFulfiller
from referral_harvest.models import Reward, RewardStatus
from referral_harvest.transitions import transition
from referral_harvest.util import utcnow


class ManualFulfiller(RewardFulfiller):
    """Approve now; the operator fulfills manually via the dashboard."""

    def fulfill(self, reward: Reward) -> None:
        if reward.status == RewardStatus.pending:
            transition(reward, RewardStatus.approved)
            reward.approved_at = utcnow()
        # already approved -> nothing to do; fulfillment happens in the dashboard


class WebhookFulfiller(RewardFulfiller):
    """Let the tenant's system fulfill; mark fulfilled on 2xx."""

    def __init__(self, webhook_url: str | None):
        if not webhook_url:
            raise CredentialError(
                "WebhookFulfiller needs a target URL (tenant setting 'reward_webhook_url')"
            )
        self.webhook_url = webhook_url

    def fulfill(self, reward: Reward) -> None:
        payload = {
            "reward_id": reward.id,
            "referral_id": reward.referral_id,
            "kind": reward.kind.value,
            "amount_cents": reward.amount_cents,
        }
        resp = httpx.post(self.webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        if reward.status == RewardStatus.pending:
            transition(reward, RewardStatus.approved)
            reward.approved_at = utcnow()
        transition(reward, RewardStatus.fulfilled)
        reward.fulfilled_at = utcnow()
        reward.fulfilled_via = "webhook"
