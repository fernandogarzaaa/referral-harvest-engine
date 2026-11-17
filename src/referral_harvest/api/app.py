"""FastAPI application factory.

Lifespan: `alembic upgrade head` (unless SKIP_MIGRATIONS), then start the
APScheduler loop (unless SCHEDULER_ENABLED=false).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from referral_harvest.admin import routes as admin_routes
from referral_harvest.api.routes import (
    asks,
    events,
    health,
    redeem,
    referrals,
    reports,
    rewards,
    webhooks,
)
from referral_harvest.config import Settings, get_settings
from referral_harvest.migrate import upgrade_head

__version__ = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.skip_migrations:
            upgrade_head(settings.database_url)
        scheduler = None
        if settings.scheduler_enabled:
            from referral_harvest.scheduler.jobs import start_scheduler

            scheduler = start_scheduler()
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Referral Harvest Engine", version=__version__, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(asks.router)
    app.include_router(events.router)
    app.include_router(redeem.router)
    app.include_router(referrals.router)
    app.include_router(rewards.router)
    app.include_router(reports.router)
    app.include_router(webhooks.router)
    app.include_router(admin_routes.router)
    return app


app = create_app()
