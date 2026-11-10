"""Owner startup must not adopt or validate another service's migration chain."""
import uuid
import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from machine_postgres import provision_owner
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


def owner_revision(url):
    engine = create_engine(url)
    try:
        with engine.connect() as db:
            return db.scalar(text('SELECT version_num FROM public.alembic_version_aiuditor'))
    finally:
        engine.dispose()


def test_owner_startup_ignores_retired_foreign_tables(isolated_owner_db, monkeypatch):
    url = isolated_owner_db
    provision_owner(url, revision='aiuditor_0057_lab_reports')
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            db.execute(text('CREATE TABLE foreign_owner_marker(revision text NOT NULL)'))
            db.execute(text("INSERT INTO foreign_owner_marker VALUES('unrelated-newer-owner')"))
        monkeypatch.setenv('DATABASE_URL', url)
        assert schema_startup.main() == 0
        assert schema_startup.main() == 0
        assert owner_revision(url) == schema_startup.owner_head()
        with engine.connect() as db:
            assert db.scalar(text('SELECT revision FROM foreign_owner_marker')) == 'unrelated-newer-owner'
        assert not inspect(engine).has_table('onramp_sessions')
        assert not inspect(engine).has_table('users')
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
    provision_owner(url)
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
            before = owner_revision(url)
            schema_startup.upgrade_schema(url)
            assert store.get(tenant, original.id) == original
            after = owner_revision(url)
            assert after == schema_startup.owner_head()
            assert before == after
            if ahead:
                with engine.begin() as cx:
                    cx.execute(text("UPDATE alembic_version_aiuditor SET version_num='aiuditor_9999_future'"))
                schema_startup.upgrade_schema(url)
                assert owner_revision(url) == 'aiuditor_9999_future'
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
        provision_owner(url, revision='aiuditor_0053_baseline' if state == 'outdated' else 'head')
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
        monkeypatch.setattr(schema_startup, '_engine', lambda _: readonly)
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


@pytest.mark.parametrize('state', ['ready', 'superuser', 'database_owner', 'schema_create', 'table_owner', 'version_write', 'tenant_column_write', 'set_role', 'inherit_owner', 'mixed_owner', 'admin_owner', 'server_program', 'server_read', 'server_write', 'session_superuser', 'definer', 'inactive', 'missing_tenant', 'outdated', 'missing_table', 'wrong_search_path'])
def test_explicit_runtime_verification_uses_fresh_owner_and_no_metadata_writes(isolated_owner_db, monkeypatch, state):
    """Real LOGIN roles qualify frozen fresh DDL and refuse reachable repair authority."""
    import json
    import secrets
    from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID
    admin = create_engine(isolated_owner_db)
    role = 'runtime_' + uuid.uuid4().hex
    extra = 'repair_' + uuid.uuid4().hex
    bridge = 'bridge_' + uuid.uuid4().hex
    password = secrets.token_hex(32)
    runtime = None
    try:
        with admin.begin() as db:
            db.execute(text('CREATE TABLE public.tenants(id uuid PRIMARY KEY,name text NOT NULL,is_active boolean NOT NULL)'))
            # Use the actual immutable standalone owner chain, with no mixed DB baseline.
            command.upgrade(schema_startup.owner_config(db), 'head')
            if state != 'missing_tenant':
                db.execute(text('INSERT INTO public.tenants VALUES(:id,:name,:active)'), {'id': MARKETPLACE_TENANT_ID, 'name': 'Configured TEST', 'active': state != 'inactive'})
            db.execute(text(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\' NOSUPERUSER NOINHERIT NOCREATEDB NOCREATEROLE NOBYPASSRLS'))
            db.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
            db.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            entry = json.loads((schema_startup.ROOT / 'owner.json').read_text())
            for table in entry['tables']:
                db.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON public."{table}" TO "{role}"'))
            db.execute(text(f'GRANT SELECT ON public.tenants,public.alembic_version_aiuditor TO "{role}"'))
            if state == 'superuser':db.execute(text(f'ALTER ROLE "{role}" SUPERUSER'))
            if state == 'database_owner':db.execute(text(f'ALTER DATABASE "{make_url(isolated_owner_db).database}" OWNER TO "{role}"'))
            if state == 'schema_create':db.execute(text(f'GRANT CREATE ON SCHEMA public TO "{role}"'))
            if state == 'table_owner':db.execute(text(f'ALTER TABLE public.aiuditor_policies OWNER TO "{role}"'))
            if state == 'version_write':db.execute(text(f'GRANT UPDATE ON public.alembic_version_aiuditor TO "{role}"'))
            if state == 'tenant_column_write':db.execute(text(f'GRANT UPDATE(is_active) ON public.tenants TO "{role}"'))
            if state == 'set_role':
                db.execute(text(f'CREATE ROLE "{extra}" NOLOGIN NOINHERIT'))
                db.execute(text(f'GRANT UPDATE ON public.tenants TO "{extra}"'))
                db.execute(text(f'GRANT "{extra}" TO "{role}" WITH INHERIT FALSE, SET TRUE'))
            if state in ('inherit_owner', 'mixed_owner', 'admin_owner'):
                db.execute(text(f'CREATE ROLE "{extra}" NOLOGIN NOINHERIT'))
                db.execute(text(f'ALTER TABLE public.aiuditor_policies OWNER TO "{extra}"'))
                if state == 'inherit_owner':
                    db.execute(text(f'GRANT "{extra}" TO "{role}" WITH INHERIT TRUE, SET FALSE'))
                elif state == 'admin_owner':
                    db.execute(text(f'GRANT "{extra}" TO "{role}" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE'))
                else:
                    db.execute(text(f'CREATE ROLE "{bridge}" NOLOGIN NOINHERIT'))
                    db.execute(text(f'GRANT "{extra}" TO "{bridge}" WITH INHERIT TRUE, SET FALSE'))
                    db.execute(text(f'GRANT "{bridge}" TO "{role}" WITH INHERIT FALSE, SET TRUE'))
            if state in ('server_program', 'server_read', 'server_write'):
                server_role = {'server_program': 'pg_execute_server_program', 'server_read': 'pg_read_server_files', 'server_write': 'pg_write_server_files'}[state]
                db.execute(text(f'GRANT "{server_role}" TO "{role}" WITH INHERIT TRUE, SET TRUE'))
            if state == 'definer':
                db.execute(text("CREATE FUNCTION public.fixture_repair() RETURNS void LANGUAGE SQL SECURITY DEFINER AS $$ UPDATE public.tenants SET is_active=true $$"))
            if state == 'outdated':db.execute(text("UPDATE public.alembic_version_aiuditor SET version_num='aiuditor_0053_baseline'"))
            if state == 'missing_table':db.execute(text('DROP TABLE public.aiuditor_parent_snapshots'))
        runtime_url = make_url(isolated_owner_db).set(username=role, password=password).render_as_string(hide_password=False)
        runtime = create_engine(runtime_url, connect_args={'options': '-c search_path=' + ('pg_catalog' if state == 'wrong_search_path' else 'public')})
        if state == 'admin_owner':
            # Prove the login can recover SET authority despite having neither
            # effective inheritance nor SET beforehand; roll the grant back.
            with runtime.connect() as db:
                transaction = db.begin()
                assert db.scalar(text('SELECT pg_has_role(current_user,:owner,\'SET\')'), {'owner': extra}) is False
                db.execute(text(f'GRANT "{extra}" TO "{role}" WITH SET TRUE'))
                assert db.scalar(text('SELECT pg_has_role(current_user,:owner,\'SET\')'), {'owner': extra}) is True
                transaction.rollback()
        if state == 'session_superuser':
            runtime.dispose()
            runtime = create_engine(isolated_owner_db, connect_args={'options': '-c role=' + role + ' -c search_path=public'})
        monkeypatch.setattr(schema_startup, '_engine', lambda _: runtime)
        monkeypatch.setenv('AIUDITOR_ROLE', 'operator')
        monkeypatch.setenv('AIUDITOR_SCHEMA_STARTUP', 'verify')
        monkeypatch.setenv('DATABASE_URL', runtime_url)
        with admin.connect() as db:
            before = db.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all()
            tenants_before = db.execute(text('SELECT id,name,is_active FROM public.tenants ORDER BY id')).all()
        if state == 'ready':
            assert schema_startup.main() == 0
            from sqlalchemy.orm import sessionmaker
            from aiuditor_service.persist import SqlPersistSession, dump_for_persist
            from sqlalchemy.exc import ProgrammingError
            from aiuditor_service.store import new_stores
            from test_parent_reconciliation import policy
            with admin.connect() as db:
                tenant = db.scalar(text('SELECT id FROM tenants WHERE is_active LIMIT 1'))
            original = policy(tenant)
            SqlPersistSession(sessionmaker(admin)).upsert(
                'policies', tenant, original.id,
                dump_for_persist(original),
            )
            persist = SqlPersistSession(sessionmaker(runtime))
            stores = new_stores(persist)
            assert stores.policy_store.policies[tenant][original.id] == original
            assert persist.snapshot() is not None
            # A failed table or registry read must not silently replace a
            # previously hydrated cache with a partial/empty snapshot.
            for table in ('aiuditor_policies', 'tenants'):
                with admin.begin() as db:
                    db.execute(text(f'REVOKE SELECT ON public."{table}" FROM "{role}"'))
                try:
                    with pytest.raises(ProgrammingError):
                        persist.hydrate(stores)
                    assert stores.policy_store.policies[tenant][original.id] == original
                finally:
                    with admin.begin() as db:
                        db.execute(text(f'GRANT SELECT ON public."{table}" TO "{role}"'))

        else:
            with pytest.raises(ValueError, match='runtime:'):
                schema_startup.main()
        with admin.connect() as db:
            assert db.execute(text('SELECT version_num FROM public.alembic_version_aiuditor')).scalars().all() == before
            assert db.execute(text('SELECT id,name,is_active FROM public.tenants ORDER BY id')).all() == tenants_before
    finally:
        if runtime is not None:runtime.dispose()
        # Fixture-only teardown: no shared VM, application or configured accounts.
        with admin.begin() as db:
            if state == 'database_owner':db.execute(text(f'ALTER DATABASE "{make_url(isolated_owner_db).database}" OWNER TO CURRENT_USER'))
            db.execute(text(f'DROP OWNED BY "{role}" CASCADE'))
            if state in ('set_role', 'inherit_owner', 'mixed_owner', 'admin_owner'):db.execute(text(f'DROP OWNED BY "{extra}" CASCADE'))
            if state == 'mixed_owner':db.execute(text(f'DROP OWNED BY "{bridge}" CASCADE'))
            db.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
            if state in ('set_role', 'inherit_owner', 'mixed_owner', 'admin_owner'):db.execute(text(f'DROP ROLE IF EXISTS "{extra}"'))
            if state == 'mixed_owner':db.execute(text(f'DROP ROLE IF EXISTS "{bridge}"'))
        admin.dispose()


def test_admission_history_upgrade_preserves_existing_denial(isolated_owner_db):
    import json
    url=isolated_owner_db
    provision_owner(url,revision='aiuditor_0061_owned_outbox')
    engine=create_engine(url);tenant=uuid.uuid4();admission=uuid.uuid4()
    try:
        with engine.begin() as c:
            c.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),{'id':tenant,'name':str(tenant)})
            c.execute(text("""INSERT INTO aiuditor_admissions
                (id,tenant_id,file_hash,format,license,intended_class,status,verdict,reason,
                 resolved_by,sandbox_soak_ok,sandbox_egress_ok,sandbox_report_sha256,created_at,resolved_at)
                VALUES(:id,:tenant,:hash,'gguf','mit','agx-large','denied','deny','soak failed',
                 'sandbox',false,true,:receipt,now(),now())"""),
                {'id':admission,'tenant':tenant,'hash':'sha256:'+'a'*64,'receipt':'b'*64})
            before=c.scalar(text('SELECT row_to_json(a)::text FROM aiuditor_admissions a WHERE id=:id'),{'id':admission})
        schema_startup.upgrade_schema(url)
        schema_startup.upgrade_schema(url)
        with engine.connect() as c:
            after=json.loads(c.scalar(text('SELECT row_to_json(a)::text FROM aiuditor_admissions a WHERE id=:id'),{'id':admission}))
        assert after.pop('lab_attempt_history')==[]
        assert after==json.loads(before)
        assert owner_revision(url)==schema_startup.owner_head()
    finally:engine.dispose()
