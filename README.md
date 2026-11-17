# Referral Harvest Engine

> Independent prototype by Fernando Garza. Not affiliated with any company. All demo data is synthetic and fictional.

Turn completed home-service jobs into referred jobs. A single FastAPI service:
ingest job/rating events, ask happy customers for referrals by SMS, track the
referral through booking and completion, fulfill the referrer's reward, and report
unit economics against your paid CAC.

## Quickstart

```bash
cp .env.example .env   # set ADMIN_TOKEN
docker compose up --build -d
curl http://localhost:8000/healthz
```

Or without Docker:

```bash
python -m venv .venv && .venv/bin/pip install -e .
alembic upgrade head
uvicorn referral_harvest.api.app:create_app --factory --port 8000
```

**Your team starts here:** [`INTEGRATION.md`](INTEGRATION.md) — the absorb-in-an-afternoon
guide: webhooks, SMS choices, redemption, reward fulfillment, CRM polling, economics.

## What it does

- `POST /v1/events/job-completed` + `POST /v1/events/rating-received` → the ask engine
  schedules an SMS ask for satisfied, opted-in customers (cooldown, quiet hours,
  expiry enforced; all per-tenant settings).
- Scheduler sends due asks every minute, expires stale ones hourly, polls CRM
  adapters every 5 minutes, retries webhook reward fulfillment.
- `POST /v1/redeem` (rate-limited) → book → complete mints and advances the reward
  (manual approval or webhook fulfillment).
- `GET /v1/reports/unit-economics` → cost per referred job vs your paid CAC.
- Operator dashboard at `/admin` (Jinja2, `ADMIN_TOKEN` cookie, CSRF on every
  mutating form): economics cards, funnel, asks, referrals, rewards queue,
  tenant settings.

## Layout

```
src/referral_harvest/
  api/          FastAPI app, routes, deps (auth, rate limiting)
  admin/        Jinja2 operator dashboard (/admin)
  engine/       ask evaluation, code generation, unit economics
  adapters/     CRM / SMS / reward-fulfiller interfaces + implementations
  scheduler/    APScheduler jobs (poll, send, expire, retry)
  models.py     SQLAlchemy models + transition maps
  transitions.py single choke point for status changes
  ingest.py     one intake path for webhook API and scheduler poll
  redeem.py     code redemption
  cli.py        upgrade / create-tenant / create-api-key
alembic/        migrations
tests/          pytest suite (unit + API + full loop)
```

Multi-tenant throughout: every row is tenant-scoped, API keys are `rh_`-prefixed
and bcrypt-hashed. SQLite (WAL) by default, Postgres via `DATABASE_URL`.
All timestamps are naive UTC.

## Verify

```bash
.venv/bin/python -m pytest
python3 -m ruff check src/ tests/ && python3 -m ruff format --check src/ tests/
~/workspace/bin/testteeth run --base <spec-sha> --fail-under 80
```

## Roadmap

- Phase 2: Snap-Diagnose (photo-based instant estimate).
- Out of scope by design: billing, multi-region, mobile apps.
