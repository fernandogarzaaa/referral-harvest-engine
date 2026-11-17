"""Programmatic Alembic upgrades (used by app lifespan and the CLI)."""

import os

from alembic.config import Config

from alembic import command
from referral_harvest.config import get_settings


def _config(database_url: str) -> Config:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    cfg = Config(os.path.join(root, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_head(database_url: str | None = None) -> None:
    url = database_url or get_settings().database_url
    command.upgrade(_config(url), "head")
