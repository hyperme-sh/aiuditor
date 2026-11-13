import pytest
from aiuditor_service import schema_startup


@pytest.mark.parametrize('role', ['global', 'operator'])
def test_startup_requires_explicit_database_url(monkeypatch, role):
    monkeypatch.setenv('AIUDITOR_ROLE', role)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(ValueError, match='DATABASE_URL is required'):
        schema_startup.main()


@pytest.mark.parametrize('url', ['', 'postgresql://unused@localhost:1/unused'])
def test_memory_witness_entrypoint_never_opens_database(monkeypatch, url):
    monkeypatch.setenv('AIUDITOR_ROLE', 'witness')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setattr(schema_startup, '_engine', lambda *_: pytest.fail('memory witness opened a database'))
    assert schema_startup.main() == 0


def test_invalid_role_is_refused_before_opening_database(monkeypatch):
    from aiuditor_service.role import RoleUnavailable
    monkeypatch.setenv('AIUDITOR_ROLE', 'unrecognized')
    monkeypatch.setattr(schema_startup, '_engine', lambda *_: pytest.fail('invalid role opened a database'))
    with pytest.raises(RoleUnavailable):
        schema_startup.main()


@pytest.mark.parametrize('role', ['global', 'operator'])
def test_explicit_verify_mode_never_runs_migrations_or_tenant_seed(monkeypatch, role):
    checked = []
    monkeypatch.setenv('AIUDITOR_ROLE', role)
    monkeypatch.setenv('AIUDITOR_SCHEMA_STARTUP', 'verify')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://runtime@localhost/owner')
    monkeypatch.setattr(schema_startup, 'check_runtime_schema', checked.append)
    monkeypatch.setattr(schema_startup, 'upgrade_schema', lambda *_: pytest.fail('runtime ran migration'))
    monkeypatch.setattr(schema_startup, 'ensure_marketplace_tenant', lambda *_: pytest.fail('runtime seeded tenant'))
    assert schema_startup.main() == 0
    assert checked == ['postgresql://runtime@localhost/owner']


def test_unknown_schema_mode_cannot_fall_back_to_upgrade(monkeypatch):
    monkeypatch.setenv('AIUDITOR_ROLE', 'operator')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused@localhost/owner')
    monkeypatch.setenv('AIUDITOR_SCHEMA_STARTUP', 'repair')
    monkeypatch.setattr(schema_startup, '_engine', lambda *_: pytest.fail('invalid mode opened database'))
    with pytest.raises(ValueError, match='AIUDITOR_SCHEMA_STARTUP'):
        schema_startup.main()


def test_container_startup_module_accepts_database_free_witness(monkeypatch):
    import os
    import subprocess
    import sys
    monkeypatch.setenv('AIUDITOR_ROLE', 'witness')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    result = subprocess.run([sys.executable, '-m', 'aiuditor_service.schema_startup'],
                            env=dict(os.environ), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_packaged_bundle_retains_frozen_baseline_and_only_owner_chain():
    import hashlib, json
    from alembic.script import ScriptDirectory
    config = schema_startup.owner_config()
    script = ScriptDirectory.from_config(config)
    assert script.get_base() == 'aiuditor_0053_baseline'
    assert script.get_current_head() == 'aiuditor_0064_suite_revision'
    entry = json.loads((schema_startup.ROOT / 'owner.json').read_text())
    assert hashlib.sha256((schema_startup.ROOT / 'versions/baseline.sql').read_bytes()).hexdigest() == entry['baseline_sha256']
    assert 'aiuditor_parent_snapshots' in entry['tables']
    assert all(table.startswith('aiuditor_') or table in {'service_tenants', 'service_outbox'} for table in entry['tables'])
    assert not list(schema_startup.ROOT.glob('**/hypermesh*'))


def test_installed_event_factory_uses_owned_outbox_without_a_legacy_database():
    from api.infrastructure.messaging.tenant_event_publisher import resolve_event_publisher
    from controlplane_events.publisher import OwnedOutboxPublisher
    publisher = resolve_event_publisher(service_name='aiuditor', service_bus_connection_string=None,
                                        database_url='postgresql://unused@localhost/owner')
    assert isinstance(publisher, OwnedOutboxPublisher)
