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
from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID, ensure_marketplace_tenant
from aiuditor_service.package import witness_uses_memory_persist
from aiuditor_service.role import AiuditorRole, resolve_role

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


def check_witness_schema(url):
    """A SQL witness validates an adopted schema without acquiring write authority."""
    entry = json.loads((ROOT / 'owner.json').read_text())
    engine = migrations._engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            tables = set(inspect(connection).get_table_names(schema='public'))
            if entry['version_table'] not in tables:
                raise ValueError('witness: provision the owner schema before startup')
            revisions = connection.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all()
            if revisions != [owner_head()]:
                raise ValueError('witness: provision the candidate owner revision before startup')
            missing = sorted(set(entry['tables']) - tables)
            if missing:
                raise ValueError(f'witness: schema is missing required owner tables: {missing}')
    finally:
        engine.dispose()


def check_runtime_schema(url):
    """Check an explicitly provisioned owner without migration or tenant writes."""
    entry = json.loads((ROOT / 'owner.json').read_text())
    engine = migrations._engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            if connection.scalar(text('SELECT current_schema()')) != 'public':
                raise ValueError('runtime: public owner schema must be explicitly provisioned')
            tables = set(inspect(connection).get_table_names(schema='public'))
            required = set(entry['tables']) | {entry['version_table'], 'tenants'}
            if required - tables:
                raise ValueError('runtime: provision all required owner tables before startup')
            revisions = connection.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all()
            if revisions != [owner_head()]:
                raise ValueError('runtime: provision the exact image owner revision before startup')
            # An actor may SET one role and then inherit a different owner whose
            # membership forbids SET. Follow both edge kinds transitively.
            roles = connection.execute(text(
                "WITH RECURSIVE reachable(oid) AS ("
                "SELECT oid FROM pg_roles WHERE rolname IN (current_user,session_user) "
                "UNION SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member=r.oid "
                "WHERE m.inherit_option OR m.set_option) "
                "SELECT rolname,(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication) "
                "FROM pg_roles WHERE oid IN (SELECT oid FROM reachable) LIMIT 1001"
            )).all()
            if not roles or len(roles) > 1000:
                raise ValueError('runtime: database role authority is unavailable')
            for role, privileged in roles:
                if privileged or connection.scalar(text(
                    "SELECT has_schema_privilege(:role,'public','CREATE') "
                    "OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='public' "
                    "AND nspowner=(SELECT oid FROM pg_roles WHERE rolname=:role)) "
                    "OR EXISTS(SELECT 1 FROM pg_database WHERE datname=current_database() "
                    "AND datdba=(SELECT oid FROM pg_roles WHERE rolname=:role))"
                ), {'role': role}):
                    raise ValueError('runtime: migration authority is forbidden')
                for table in ('tenants', entry['version_table']):
                    if connection.scalar(text(
                        "SELECT has_table_privilege(:role,:table,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                        "OR has_any_column_privilege(:role,:table,'INSERT,UPDATE,REFERENCES')"
                    ), {'role': role, 'table': 'public.' + table}):
                        raise ValueError('runtime: tenant and migration metadata must be read only')
                if connection.scalar(text(
                    "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=:role) "
                    "AND c.relname = ANY(:tables))"
                ), {'role': role, 'tables': sorted(required)}):
                    raise ValueError('runtime: owner table authority is forbidden')
                if connection.scalar(text(
                    "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.prosecdef AND has_function_privilege(:role,p.oid,'EXECUTE'))"
                ), {'role': role}):
                    raise ValueError('runtime: security definer authority is forbidden')
            active = connection.scalar(text('SELECT is_active FROM public.tenants WHERE id=:id'), {'id': MARKETPLACE_TENANT_ID})
            if active is not True:
                raise ValueError('runtime: configured marketplace tenant must already be active')
    finally:
        engine.dispose()


def main() -> int:
    role = resolve_role()
    url = os.environ.get('DATABASE_URL', '').strip()
    if role is AiuditorRole.WITNESS:
        if not witness_uses_memory_persist(role, url):
            check_witness_schema(url)
        return 0
    if not url:
        raise ValueError('DATABASE_URL is required for owner schema startup')
    mode = os.environ.get('AIUDITOR_SCHEMA_STARTUP', 'upgrade')
    if mode == 'verify':
        check_runtime_schema(url)
    elif mode == 'upgrade':
        upgrade_schema(url)
    else:
        raise ValueError('AIUDITOR_SCHEMA_STARTUP must be upgrade or verify')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
