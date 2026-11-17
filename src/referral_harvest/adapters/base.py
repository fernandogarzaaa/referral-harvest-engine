"""Adapter interfaces. Every external system sits behind one of these ABCs.

- CRMAdapter: where completed jobs come from (webhook events, polling, CSV).
- SMSSender: how ask texts go out.
- RewardFulfiller: how rewards get fulfilled.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class CredentialError(RuntimeError):
    """Raised when an adapter is constructed/used without required credentials.

    Adapters never fabricate data: no creds -> CredentialError, never fake jobs.
    """


@dataclass
class JobIngest:
    """Normalized completed-job record, regardless of source."""

    external_id: str
    customer_name: str
    customer_phone_e164: str
    service_type: str
    completed_at: datetime | None = None
    customer_external_id: str | None = None
    customer_consent: str | None = None  # "opted_in" | "opted_out" | None
    tech_name: str | None = None
    amount_cents: int | None = None
    customer_timezone: str | None = None


class CRMAdapter(ABC):
    @abstractmethod
    def fetch_completed_jobs(self, since: datetime) -> list[JobIngest]:
        """Return jobs completed at/after `since`. Empty list if none."""
        ...


class SMSSender(ABC):
    @abstractmethod
    def send(self, to_e164: str, body: str) -> str:
        """Send one SMS; return the provider message SID."""
        ...


class RewardFulfiller(ABC):
    @abstractmethod
    def fulfill(self, reward) -> None:
        """Fulfill (or advance toward fulfillment of) a reward. May raise on failure."""
        ...
