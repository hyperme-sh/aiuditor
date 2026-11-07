"""Upgrade only AIuditor's already-adopted schema from its packaged owner bundle."""
import json
import logging
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from controlplane_db import migrations
from controlplane_db.startup import _revision_missing_from_image, _require_ahead_of_image
from controlplane_tenancy.marketplace import ensure_marketplace_tenant

OWNER = 'aiuditor'
ROOT = Path(__file__).with_name('migration_bundle')


def owner_config(connection=None):
    config = Config()
    config.set_main_option('script_location', str(ROOT))
    config.set_main_option('path_separator', 'os')
    config.set_main_option('version_locations', str(ROOT / 'versions'))
    config.attributes['version_table'] = 'alembic_version_aiuditor'
    if connection is not None:
        config.attributes['connection'] = connection
    return config


def owner_head():
    return ScriptDirectory.from_config(owner_config()).get_current_head()


def upgrade_schema(url):
    entry = json.loads((ROOT / 'owner.json').read_text())
    engine = migrations._engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('controlplane-owner-migrations'))"))
            tables = set(inspect(connection).get_table_names(schema='public'))
            if entry['version_table'] not in tables:
                raise ValueError(f'{OWNER}: provision the owner schema before service startup')
            revisions = connection.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all()
            if len(revisions) != 1:
                raise ValueError(f'{OWNER}: exactly one adopted owner revision is required')
            script = ScriptDirectory.from_config(owner_config())
            if _revision_missing_from_image(script, revisions[0]):
                _require_ahead_of_image(OWNER, revisions[0], script.get_current_head(), entry['tables'], tables)
                logging.getLogger(__name__).warning('Skipping unknown newer AIuditor revision %s', revisions[0])
            else:
                command.upgrade(owner_config(connection), 'head')
            missing = sorted(set(entry['tables']) - set(inspect(connection).get_table_names(schema='public')))
            if missing:
                raise ValueError(f'{OWNER}: schema is missing required owner tables: {missing}')
            ensure_marketplace_tenant(connection)
    finally:
        engine.dispose()


def main() -> int:
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        raise ValueError('DATABASE_URL is required for owner schema startup')
    upgrade_schema(url)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
