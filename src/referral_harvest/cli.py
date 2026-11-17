"""Operator CLI: bootstrap tenants and API keys, run migrations.

Usage:
    python -m referral_harvest.cli upgrade
    python -m referral_harvest.cli create-tenant --name "Acme HVAC" --slug acme
    python -m referral_harvest.cli create-api-key --tenant acme --name default
"""

import argparse
import sys

from sqlalchemy import select

from referral_harvest.db import SessionLocal
from referral_harvest.migrate import upgrade_head
from referral_harvest.models import Tenant
from referral_harvest.security import make_api_key


def _find_tenant(db, slug: str) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        print(f"unknown tenant slug: {slug}", file=sys.stderr)
        raise SystemExit(2)
    return tenant


def cmd_upgrade(_args) -> None:
    upgrade_head()
    print("migrations applied")


def cmd_create_tenant(args) -> None:
    upgrade_head()
    db = SessionLocal()
    try:
        if db.scalar(select(Tenant).where(Tenant.slug == args.slug)):
            print(f"tenant slug already exists: {args.slug}", file=sys.stderr)
            raise SystemExit(2)
        tenant = Tenant(name=args.name, slug=args.slug)
        db.add(tenant)
        db.commit()
        print(f"tenant created: id={tenant.id} slug={tenant.slug}")
    finally:
        db.close()


def cmd_create_api_key(args) -> None:
    upgrade_head()
    db = SessionLocal()
    try:
        tenant = _find_tenant(db, args.tenant)
        raw, record = make_api_key(tenant.id, name=args.name)
        db.add(record)
        db.commit()
        print(f"api key for tenant '{tenant.slug}':")
        print(raw)
        print("Store it now; only the hash is kept in the database.")
    finally:
        db.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="referral-harvest")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("upgrade", help="apply Alembic migrations to head")

    p_tenant = sub.add_parser("create-tenant", help="create a tenant")
    p_tenant.add_argument("--name", required=True)
    p_tenant.add_argument("--slug", required=True)

    p_key = sub.add_parser("create-api-key", help="mint an API key for a tenant")
    p_key.add_argument("--tenant", required=True, help="tenant slug")
    p_key.add_argument("--name", default="default")

    args = parser.parse_args(argv)
    {
        "upgrade": cmd_upgrade,
        "create-tenant": cmd_create_tenant,
        "create-api-key": cmd_create_api_key,
    }[args.command](args)


if __name__ == "__main__":
    main()
