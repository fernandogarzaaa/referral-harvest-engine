"""Shared test fixtures: migrated temp SQLite DB, per-test rolled-back session."""

import os
import sys
from datetime import UTC, datetime

# Point the app at an isolated SQLite file BEFORE importing referral_harvest
# (settings are lru-cached at first import). The API-test DB is migrated by
# the `app_db` fixture below; the scheduler stays off in tests.
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/rh_test_app.db")
os.environ.setdefault("SKIP_MIGRATIONS", "1")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("BCRYPT_ROUNDS", "4")

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from alembic import command

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from referral_harvest import models  # noqa: F401  (register tables)
from referral_harvest.models import Customer, Job, Rating, Tenant
from referral_harvest.security import make_api_key
from referral_harvest.util import utcnow


def _migrate(url: str) -> None:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def db_url(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    url = f"sqlite:///{path}"
    _migrate(url)
    return url


@pytest.fixture()
def db(db_url):
    """A session wrapped in a transaction that rolls back after each test."""
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.close()

    conn = engine.connect()
    tx = conn.begin()
    session = sessionmaker(bind=conn, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        tx.rollback()
        conn.close()
        engine.dispose()


@pytest.fixture()
def tenant(db):
    t = Tenant(name="Test Shop", slug="test-shop")
    db.add(t)
    db.flush()
    return t


@pytest.fixture()
def api_key(db, tenant):
    """A (raw_key, tenant) pair; raw_key is what the X-API-Key header carries."""
    raw, record = make_api_key(tenant.id, name="test-key")
    db.add(record)
    db.flush()
    return raw, tenant


def aware(dt: datetime) -> datetime:
    """Attach UTC tzinfo to a naive-UTC datetime from the DB."""
    return dt.replace(tzinfo=UTC)


def seed_customer(db, tenant, name="Mike Smith", phone=None, consent="opted_in", **kw):
    from referral_harvest.models import Consent

    c = Customer(
        tenant_id=tenant.id,
        name=name,
        phone_e164=phone or unique_phone(),
        consent=Consent(consent),
        **kw,
    )
    db.add(c)
    db.flush()
    return c


_counter = [0]


_MISSING = object()


def seed_job(db, tenant, customer, completed_at=_MISSING, external_id=None, **kw):
    _counter[0] += 1
    if completed_at is _MISSING:
        completed_at = utcnow()
    j = Job(
        tenant_id=tenant.id,
        customer_id=customer.id,
        external_id=external_id or f"job-{_counter[0]:04d}",
        service_type=kw.pop("service_type", "AC Repair"),
        completed_at=completed_at,
        **kw,
    )
    db.add(j)
    db.flush()
    return j


def seed_rating(db, job, score=5.0, scale=5, received_at=None):
    r = Rating(
        job_id=job.id,
        score=score,
        scale=scale,
        received_at=received_at or utcnow(),
    )
    db.add(r)
    db.flush()
    return r


# --- API-test fixtures (TestClient against the real app) --------------------

APP_DB_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="session", autouse=True)
def _migrated_app_db():
    """Migrate the app DB file once per test session."""
    path = APP_DB_URL.replace("sqlite:///", "")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    _migrate(APP_DB_URL)
    yield


@pytest.fixture()
def app_db(_migrated_app_db):
    """Direct session on the app DB (for seeding tenants/keys in API tests)."""
    from referral_harvest.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


_api_tenant_counter = [0]
_phone_counter = [0]


def unique_phone() -> str:
    """A fresh E.164 phone per call (the app DB is shared across API tests)."""
    _phone_counter[0] += 1
    return f"+1555000{1000 + _phone_counter[0]:04d}"


@pytest.fixture()
def app_tenant(app_db):
    """A tenant + raw API key in the app DB; unique slug per test. Committed."""
    from referral_harvest.models import Tenant

    _api_tenant_counter[0] += 1
    n = _api_tenant_counter[0]
    tenant = Tenant(name=f"API Shop {n}", slug=f"api-shop-{n}")
    app_db.add(tenant)
    app_db.flush()
    raw, record = make_api_key(tenant.id, name="test")
    app_db.add(record)
    app_db.commit()
    return raw, tenant


@pytest.fixture()
def client(_migrated_app_db):
    """TestClient for the real FastAPI app (lifespan runs, scheduler off)."""
    from fastapi.testclient import TestClient

    from referral_harvest.api.app import create_app

    with TestClient(create_app(), raise_server_exceptions=True) as c:
        yield c
