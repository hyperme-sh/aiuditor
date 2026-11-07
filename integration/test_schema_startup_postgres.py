"""Owner startup must not adopt or validate another service's migration chain."""
import uuid
import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from controlplane_db import migrations
from aiuditor_service import schema_startup
pytest_plugins = ['machine_postgres']

@pytest.fixture
def isolated_owner_db(postgres_sync_url):
    name = 'scoped_startup_' + uuid.uuid4().hex
    admin = create_engine(postgres_sync_url, isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = make_url(postgres_sync_url).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_owner_startup_ignores_retired_foreign_tables(isolated_owner_db, monkeypatch):
    url = isolated_owner_db
    migrations.upgrade(url, baseline_only=True)
    migrations.prepare_event_source_index(url)
    migrations.upgrade(url, chains=tuple(name for name in migrations._ORDER if name != 'aiuditor'))
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            command.upgrade(migrations._config(migrations._ROOT, 'aiuditor', connection), 'aiuditor_0057_lab_reports')
            connection.execute(text("UPDATE alembic_version_payment_service SET version_num='payment_service_0080_fin_state'"))
            connection.execute(text('DROP TABLE IF EXISTS onramp_sessions CASCADE'))
        before = migrations.current_heads(url)
        # The pinned DB owner already recognizes the retired table as optional.
        # Qualify this service's scoped startup directly rather than asserting
        # obsolete behavior in another owner's compatibility helper.
        monkeypatch.setenv('DATABASE_URL', url)
        assert schema_startup.main() == 0
        assert schema_startup.main() == 0
        after = migrations.current_heads(url)
        assert after['aiuditor'] == schema_startup.owner_head()
        assert {name: value for name, value in after.items() if name != 'aiuditor'} == {
            name: value for name, value in before.items() if name != 'aiuditor'}
        assert not inspect(engine).has_table('onramp_sessions')
    finally:
        engine.dispose()


def test_service_startup_refuses_unadopted_database(isolated_owner_db, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', isolated_owner_db)
    with pytest.raises(ValueError, match='provision the owner schema'):
        schema_startup.main()
    engine = create_engine(isolated_owner_db)
    try:
        assert inspect(engine).get_table_names(schema='public') == []
    finally:
        engine.dispose()


@pytest.mark.parametrize('ahead', [False, True])
def test_service_bundle_upgrades_adopted_rows_and_refuses_missing_receipt_table(isolated_owner_db, ahead):
    from aiuditor_service.persist import SqlPersistSession
    from aiuditor_service.store import PolicyStore
    from test_parent_reconciliation import policy
    url = isolated_owner_db
    migrations.upgrade(url)
    tenant = uuid.uuid4()
    engine = create_engine(url)
    try:
        with engine.begin() as cx:
            cx.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),
                       {'id': tenant, 'name': str(tenant)})
        persist = SqlPersistSession.from_url(url)
        try:
            store = PolicyStore(persist=persist)
            original = policy(tenant)
            store.put(original)
            before = migrations.current_heads(url)
            schema_startup.upgrade_schema(url)
            assert store.get(tenant, original.id) == original
            after = migrations.current_heads(url)
            assert after['aiuditor'] == schema_startup.owner_head()
            assert {k:v for k,v in after.items() if k != 'aiuditor'} == {k:v for k,v in before.items() if k != 'aiuditor'}
            if ahead:
                with engine.begin() as cx:
                    cx.execute(text("UPDATE alembic_version_aiuditor SET version_num='aiuditor_9999_future'"))
                schema_startup.upgrade_schema(url)
                assert migrations.current_heads(url)['aiuditor'] == 'aiuditor_9999_future'
            # A claimed current head with a missing table is corruption, not an
            # authorization to silently recreate state or reset provenance.
            with engine.begin() as cx:
                cx.execute(text('DROP TABLE aiuditor_parent_snapshots'))
            with pytest.raises(ValueError, match='missing.*tables'):
                schema_startup.upgrade_schema(url)
        finally:
            persist._session_factory.kw['bind'].dispose()
    finally:
        engine.dispose()


@pytest.mark.parametrize('state', ['ready', 'outdated', 'missing_table', 'unprovisioned'])
def test_witness_checks_schema_with_select_only_role(isolated_owner_db, monkeypatch, state):
    """An installed witness cannot stamp, upgrade, seed, or repair the owner DB."""
    import json
    url = isolated_owner_db
    if state != 'unprovisioned':
        migrations.upgrade(url)
        if state != 'outdated':
            schema_startup.upgrade_schema(url)
    admin = create_engine(url)
    role = 'witness_read_' + uuid.uuid4().hex
    try:
        with admin.begin() as db:
            if state == 'missing_table':
                db.execute(text('DROP TABLE aiuditor_parent_snapshots'))
            db.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
            db.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            entry = json.loads((schema_startup.ROOT / 'owner.json').read_text())
            tables = set(inspect(db).get_table_names(schema='public'))
            before = sorted(tables)
            for table in sorted((set(entry['tables']) | {entry['version_table']}) & tables):
                db.execute(text(f'GRANT SELECT ON public."{table}" TO "{role}"'))
            revision_before = db.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all() if entry['version_table'] in tables else None
        readonly = create_engine(url, connect_args={
            'options': f'-c role={role} -c default_transaction_read_only=on'})
        with readonly.connect() as db:
            assert db.scalar(text('SELECT current_user')) == role
            assert db.scalar(text('SHOW transaction_read_only')) == 'on'
            assert db.scalar(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')) is False
        monkeypatch.setattr(migrations, '_engine', lambda _: readonly)
        monkeypatch.setenv('AIUDITOR_ROLE', 'witness')
        monkeypatch.setenv('DATABASE_URL', url)
        if state == 'ready':
            assert schema_startup.main() == 0
        else:
            with pytest.raises(ValueError, match='provision|missing required owner tables'):
                schema_startup.main()
        with admin.connect() as db:
            assert sorted(inspect(db).get_table_names(schema='public')) == before
            if revision_before is not None:
                assert db.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all() == revision_before
    finally:
        with admin.begin() as db:
            db.execute(text(f'DROP OWNED BY "{role}"'))
            db.execute(text(f'DROP ROLE "{role}"'))
        admin.dispose()
