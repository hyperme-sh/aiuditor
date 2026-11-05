import pytest
from aiuditor_service import schema_startup


def test_startup_requires_explicit_database_url(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(ValueError, match='DATABASE_URL is required'):
        schema_startup.main()
