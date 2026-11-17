# INTEGRATION.md — absorb this in an afternoon

Referral Harvest Engine is a single service that turns completed jobs into referred jobs.
Your team inserts its systems at five seams. Nothing here requires our code to change.

## The loop

```
job-completed event → rating → ask (SMS) → code redeemed → booked → completed
→ reward approved → reward fulfilled → unit-economics report
```

## 0. Run it

```bash
cp .env.example .env   # set ADMIN_TOKEN to a long random value
docker compose up --build -d
curl http://localhost:8000/healthz   # {"ok": true}
```

Without Docker:

```bash
python -m venv .venv && .venv/bin/pip install -e .
alembic upgrade head
uvicorn referral_harvest.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

## 1. Point your job-completed webhook here

Create a tenant and an API key:

```bash
python -m referral_harvest.cli create-tenant --name "Acme HVAC" --slug acme-hvac
python -m referral_harvest.cli create-api-key --tenant acme-hvac
# -> rh_...  (store it now; only the hash is kept)
```

Every completed job:

```bash
curl -X POST http://localhost:8000/v1/events/job-completed \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
    "external_id": "job-123",
    "customer": {
      "name": "Mike Smith",
      "phone_e164": "+15550001111",
      "consent": "opted_in",
      "timezone": "America/Chicago"
    },
    "service_type": "AC Repair",
    "tech_name": "Sam",
    "amount_cents": 45000
  }'
# -> {"job_id": "..."}
```

Send ratings when you have them (CSAT, NPS, Google review — anything 0-5 or 0-10):

```bash
curl -X POST http://localhost:8000/v1/events/rating-received \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
    "job_external_id": "job-123",
    "score": 5,
    "scale": 5
  }'
# -> {"rating_id": "...", "ask_id": "...", "ask_scheduled": true, "ask_send_at": "..."}
```

Rules (per-tenant settings, see `/admin/tenants`): only satisfied customers get asked
(`min_rating`, default 4/5 normalized), one ask per customer per `ask_cooldown_days`
(default 90), no asks without `opted_in` consent, no sends during `quiet_hours`
(default 20:00-09:00 recipient-local). Asks expire after `ask_expiry_days` (default 30).

## 2. Choose SMS

Three senders, per-tenant `sms_sender` setting (default from `DEFAULT_SMS_SENDER`):

| Sender | Use when | Needs |
|---|---|---|
| `console` | dev, demos | nothing (logs to stdout) |
| `twilio` | production SMS | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` |
| `webhook` | your own gateway | tenant setting `sms_webhook_url` (POSTs `{to, body}`) |

The ask text is the tenant's `ask_template`:

```
Hi {name}, glad we could help with your {service}! Know anyone who needs a great
{trade} pro? Share code {code} - they get $25 off their first visit and you get
$25 when their job's done. {link}
```

Inbound SMS (Twilio webhook → `POST /v1/webhooks/twilio`): STOP/START update consent,
HELP replies with program info, a bare code redeems it.

Cost accounting: every outbound message records `SMS_COST_USD_PER_MESSAGE`
(default $0.013) on its `SmsMessage` row; the economics report sums and rounds it.

## 3. Redeem, book, complete

Your booking page or CSR tool calls:

```bash
# Friend redeems Mike's code
curl -X POST http://localhost:8000/v1/redeem \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
    "code": "MIKE-QKOP",
    "referee_name": "Jane Doe",
    "referee_phone_e164": "+15550002222"
  }'
# -> {"referral_id": "...", "code": "MIKE-QKOP", "reward_terms": {"amount_cents": 2500, ...}}

# Booked in your system
curl -X POST http://localhost:8000/v1/referrals/<referral_id>/book \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{}'

# Job done -> mints the referrer's reward (pending -> approved via ManualFulfiller)
curl -X POST http://localhost:8000/v1/referrals/<referral_id>/complete \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{}'
# -> {"status": "completed", "reward": {"id": "...", "status": "approved"}}
```

The redeem endpoint is rate-limited (10/min per client IP + key) against code guessing.

## 4. Choose reward fulfillment

Per-tenant `reward_fulfiller` setting:

- `manual` (default): reward goes pending → approved on completion. Your team pays
  out, then marks it fulfilled in the dashboard rewards queue or via
  `POST /v1/rewards/{id}/fulfill`.
- `webhook`: we POST `{reward_id, referral_id, kind, amount_cents}` to the tenant's
  `reward_webhook_url`; a 2xx marks it fulfilled. Failures stay pending/approved and
  the scheduler retries every 5 minutes.

## 5. Prefer polling over webhooks?

Set the tenant's `crm_adapter` and we pull completed jobs every 5 minutes through the
same ingest path as the webhook:

- `servicetitan`: needs `SERVICETITAN_CLIENT_ID`/`SERVICETITAN_CLIENT_SECRET`
  (real ServiceTitan API v2 shape; raises without creds, never fakes).
- `smartmoving`: needs `SMARTMOVING_API_KEY`.
- `csv`: point it at a file or upload for onboarding without API access.
- `webhook` (default): no-op poll; events arrive via the API.

## 6. Read the economics

```bash
curl "http://localhost:8000/v1/reports/unit-economics" -H "X-API-Key: $KEY"
```

```json
{
  "jobs_completed": 1,
  "asks_sent": 1,
  "referrals_claimed": 1,
  "referrals_booked": 1,
  "referrals_completed": 1,
  "sms_cost_cents": 1,
  "rewards_fulfilled_cents": 2500,
  "total_cost_cents": 2501,
  "cost_per_referred_job_cents": 2501.0,
  "paid_cac_cents": 30000,
  "savings_vs_cac_cents": 27499.0
}
```

Formula: `sms_cost_cents` = sum of per-message costs, rounded to whole cents;
`rewards_fulfilled_cents` = fulfilled rewards in the period;
`cost_per_referred_job_cents` = (sms + rewards) / completed referrals.
`paid_cac_cents` is your benchmark (tenant setting, default $300.00).

The operator dashboard at `http://localhost:8000/admin` (login with `ADMIN_TOKEN`)
shows the same numbers as cards plus the funnel, asks, referrals, the rewards queue
(approve / mark-fulfilled buttons), and per-tenant settings.

## Reference

- Full API surface: `GET /openapi.json` (or `/docs`).
- Every env var: `.env.example`.
- Schema changes: `alembic/` migrations; `alembic upgrade head` runs on boot
  unless `SKIP_MIGRATIONS=true`.
- Multi-tenancy: every row is tenant-scoped; API keys are `rh_`-prefixed,
  bcrypt-hashed, revocable (`revoked_at`).
