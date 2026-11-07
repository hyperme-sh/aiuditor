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
    monkeypatch.setattr(schema_startup.migrations, '_engine', lambda *_: pytest.fail('memory witness opened a database'))
    assert schema_startup.main() == 0


def test_invalid_role_is_refused_before_opening_database(monkeypatch):
    from aiuditor_service.role import RoleUnavailable
    monkeypatch.setenv('AIUDITOR_ROLE', 'unrecognized')
    monkeypatch.setattr(schema_startup.migrations, '_engine', lambda *_: pytest.fail('invalid role opened a database'))
    with pytest.raises(RoleUnavailable):
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
    assert script.get_current_head() == 'aiuditor_0060_admission_reports'
    entry = json.loads((schema_startup.ROOT / 'owner.json').read_text())
    assert hashlib.sha256((schema_startup.ROOT / 'versions/baseline.sql').read_bytes()).hexdigest() == entry['baseline_sha256']
    assert 'aiuditor_parent_snapshots' in entry['tables']
    assert all(table.startswith('aiuditor_') for table in entry['tables'])
    assert not list(schema_startup.ROOT.glob('**/hypermesh*'))
