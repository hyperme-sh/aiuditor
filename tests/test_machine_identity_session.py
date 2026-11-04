"""Machine identity routes share the request session instead of opening another."""

import uuid

import pytest
from fastapi import HTTPException

from aiuditor_service import machine_identity as service


class RecordingSession:
    def __init__(self):
        self.calls = []

    async def execute(self, *args, **kwargs):
        self.calls.append("tenant")

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")


async def test_transaction_reuses_the_request_session(monkeypatch):
    from controlplane_tenancy.db_session import reset_db_session, set_db_session

    def second_session():
        raise AssertionError("opened a second pooled session")

    monkeypatch.setattr(service, "get_session_maker", second_session)
    session = RecordingSession()
    token = set_db_session(session)
    try:
        async with service.transaction(uuid.uuid4()) as used:
            assert used is session
        assert session.calls == ["tenant", "commit", "tenant"]
        session.calls.clear()
        with pytest.raises(HTTPException):
            async with service.transaction(uuid.uuid4()):
                raise HTTPException(401)
        assert session.calls == ["tenant", "rollback"]
    finally:
        reset_db_session(token)
