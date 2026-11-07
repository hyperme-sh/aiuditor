import pytest
from aiuditor_service import schema_startup


def test_startup_requires_explicit_database_url(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(ValueError, match='DATABASE_URL is required'):
        schema_startup.main()


def test_packaged_bundle_retains_frozen_baseline_and_only_owner_chain():
    import hashlib, json
    from alembic.script import ScriptDirectory
    config = schema_startup.owner_config()
    script = ScriptDirectory.from_config(config)
    assert script.get_base() == 'aiuditor_0053_baseline'
    assert script.get_current_head() == 'aiuditor_0059_parent_snapshots'
    entry = json.loads((schema_startup.ROOT / 'owner.json').read_text())
    assert hashlib.sha256((schema_startup.ROOT / 'versions/baseline.sql').read_bytes()).hexdigest() == entry['baseline_sha256']
    assert 'aiuditor_parent_snapshots' in entry['tables']
    assert all(table.startswith('aiuditor_') for table in entry['tables'])
    assert not list(schema_startup.ROOT.glob('**/hypermesh*'))
