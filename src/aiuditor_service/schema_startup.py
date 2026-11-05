"""Upgrade this service's already-adopted owner schema before serving requests.

The locked database package supplies the migration graph and guarded ahead-head
handling. A service never bootstraps a mixed database or migrates another owner.
"""
import os
from sqlalchemy import inspect, text
from controlplane_db import migrations
from controlplane_db.startup import _upgrade_owner_chain
from controlplane_tenancy.marketplace import ensure_marketplace_tenant

OWNER = "aiuditor"


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise ValueError("DATABASE_URL is required for owner schema startup")
    root = migrations._ROOT
    entry = migrations._registry(root)["chains"][OWNER]
    engine = migrations._engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('controlplane-owner-migrations'))"))
            tables = set(inspect(connection).get_table_names(schema="public"))
            if entry["version_table"] not in tables:
                raise ValueError(f"{OWNER}: provision the owner schema before service startup")
            revisions = connection.execute(text("SELECT version_num FROM public.alembic_version_aiuditor")).scalars().all()
            if len(revisions) != 1:
                raise ValueError(f"{OWNER}: exactly one adopted owner revision is required")
            _upgrade_owner_chain(root, OWNER, connection, revisions[0], entry["tables"], tables)
            ensure_marketplace_tenant(connection)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
