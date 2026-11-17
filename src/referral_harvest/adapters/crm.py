"""CRM adapters: where completed jobs come from.

- WebhookCRMAdapter: default. Events arrive via POST /v1/events/*; polling is a no-op.
- CSVAdapter: onboarding path without API access; reads a CSV file.
- ServiceTitanAdapter: real ServiceTitan API v2 (GET /v2/tenant/{id}/jobs).
- SmartMovingAdapter: SmartMoving read-only API (completed-jobs endpoint).
"""

from __future__ import annotations

import csv
from datetime import datetime

import httpx

from referral_harvest.adapters.base import CredentialError, CRMAdapter, JobIngest

# Expected CSV columns (headers required): external_id, customer_name,
# customer_phone_e164, service_type, completed_at (ISO-8601).
# Optional: customer_external_id, customer_consent, tech_name, amount_cents,
# customer_timezone.
CSV_REQUIRED = {
    "external_id",
    "customer_name",
    "customer_phone_e164",
    "service_type",
    "completed_at",
}


class WebhookCRMAdapter(CRMAdapter):
    """No-op poll: jobs arrive as webhook events via the API."""

    def fetch_completed_jobs(self, since: datetime) -> list[JobIngest]:
        return []


class CSVAdapter(CRMAdapter):
    """Read completed jobs from a CSV file (onboarding without API access)."""

    def __init__(self, path: str):
        self.path = path

    def fetch_completed_jobs(self, since: datetime) -> list[JobIngest]:
        jobs: list[JobIngest] = []
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = CSV_REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"CSV missing required columns: {sorted(missing)}")
            for row in reader:
                completed_at = datetime.fromisoformat(row["completed_at"])
                if completed_at < since:
                    continue
                amount = row.get("amount_cents")
                jobs.append(
                    JobIngest(
                        external_id=row["external_id"],
                        customer_external_id=row.get("customer_external_id") or None,
                        customer_name=row["customer_name"],
                        customer_phone_e164=row["customer_phone_e164"],
                        customer_consent=row.get("customer_consent") or None,
                        service_type=row["service_type"],
                        tech_name=row.get("tech_name") or None,
                        amount_cents=int(amount) if amount else None,
                        completed_at=completed_at,
                        customer_timezone=row.get("customer_timezone") or None,
                    )
                )
        return jobs


class ServiceTitanAdapter(CRMAdapter):
    """ServiceTitan API v2: GET /v2/tenant/{tenant_id}/jobs with a completed filter.

    Auth: OAuth2 client-credentials against the ServiceTitan identity endpoint.
    Raises CredentialError when client id/secret (or the ServiceTitan tenant id)
    are missing; raises on HTTP errors otherwise. Never returns fabricated data.
    """

    TOKEN_URL = "https://auth.servicetitan.com/connect/token"
    API_BASE = "https://api.servicetitan.com"

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        st_tenant_id: str | int | None,
    ):
        if not client_id or not client_secret:
            raise CredentialError(
                "ServiceTitanAdapter needs SERVICETITAN_CLIENT_ID and "
                "SERVICETITAN_CLIENT_SECRET; refusing to fabricate data"
            )
        if not st_tenant_id:
            raise CredentialError(
                "ServiceTitanAdapter needs the ServiceTitan tenant id "
                "(tenant setting 'servicetitan_tenant_id')"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.st_tenant_id = st_tenant_id

    def _token(self) -> str:
        resp = httpx.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def fetch_completed_jobs(self, since: datetime) -> list[JobIngest]:
        token = self._token()
        resp = httpx.get(
            f"{self.API_BASE}/v2/tenant/{self.st_tenant_id}/jobs",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "completedOnOrAfter": since.isoformat(),
                "pageSize": 500,
            },
            timeout=30,
        )
        resp.raise_for_status()
        jobs = []
        for item in resp.json().get("data", []):
            jobs.append(
                JobIngest(
                    external_id=str(item["id"]),
                    customer_external_id=str(item.get("customerId") or "") or None,
                    customer_name=item.get("customerName") or "Customer",
                    customer_phone_e164=item.get("customerPhone") or "",
                    service_type=item.get("jobType") or item.get("summary") or "Service",
                    tech_name=(item.get("technician") or {}).get("name"),
                    completed_at=datetime.fromisoformat(item["completedOn"])
                    if item.get("completedOn")
                    else None,
                )
            )
        return jobs


class SmartMovingAdapter(CRMAdapter):
    """SmartMoving read-only API: completed-jobs endpoint.

    Requires an API key (tenant setting 'smartmoving_api_key' or the
    SMARTMOVING_API_KEY env var). The endpoint path is configurable via the
    'smartmoving_jobs_path' tenant setting; the default targets the documented
    completed-jobs listing. Raises CredentialError without a key; raises on
    HTTP errors otherwise. Never returns fabricated data.
    """

    DEFAULT_BASE_URL = "https://api.smartmoving.com"
    DEFAULT_JOBS_PATH = "/v1/jobs"

    def __init__(
        self, api_key: str | None, base_url: str | None = None, jobs_path: str | None = None
    ):
        if not api_key:
            raise CredentialError(
                "SmartMovingAdapter needs an API key (SMARTMOVING_API_KEY or tenant "
                "setting 'smartmoving_api_key'); refusing to fabricate data"
            )
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.jobs_path = jobs_path or self.DEFAULT_JOBS_PATH

    def fetch_completed_jobs(self, since: datetime) -> list[JobIngest]:
        resp = httpx.get(
            f"{self.base_url}{self.jobs_path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"status": "completed", "completed_since": since.isoformat()},
            timeout=30,
        )
        resp.raise_for_status()
        jobs = []
        for item in resp.json().get("jobs", resp.json().get("data", [])):
            jobs.append(
                JobIngest(
                    external_id=str(item["id"]),
                    customer_external_id=str(item.get("customer_id") or "") or None,
                    customer_name=item.get("customer_name") or "Customer",
                    customer_phone_e164=item.get("customer_phone") or "",
                    service_type=item.get("service_type") or "Move",
                    tech_name=item.get("crew_lead") or item.get("tech_name"),
                    completed_at=datetime.fromisoformat(item["completed_at"])
                    if item.get("completed_at")
                    else None,
                )
            )
        return jobs
