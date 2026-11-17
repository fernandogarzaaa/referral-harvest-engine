"""Adapter subpackage: CRM polling, SMS sending, reward fulfillment."""

from referral_harvest.adapters.base import (
    CredentialError,
    CRMAdapter,
    JobIngest,
    RewardFulfiller,
    SMSSender,
)

__all__ = [
    "CredentialError",
    "CRMAdapter",
    "JobIngest",
    "RewardFulfiller",
    "SMSSender",
]
