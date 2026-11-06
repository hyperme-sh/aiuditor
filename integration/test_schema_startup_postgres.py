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
        assert after['aiuditor'] == migrations.chain_heads()['aiuditor']
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
