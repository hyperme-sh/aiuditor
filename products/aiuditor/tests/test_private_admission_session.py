"""Human approval must not reserve a second connection while middleware holds one."""

import uuid

import pytest
from aiuditor_service.private_models import routes
from controlplane_tenancy.db_session import reset_db_session, set_db_session
from fastapi import HTTPException, Response
from test_machine_identity_session import RecordingSession


async def test_approval_reuses_request_session_and_rolls_back_denials(monkeypatch):
    def second_connection():
        raise AssertionError("approval attempted a second pooled connection")

    monkeypatch.setattr(routes, "get_session_maker", second_connection)
    session = RecordingSession()
    token = set_db_session(session)
    try:
        response = Response()
        async with routes.transaction(uuid.uuid4(), response) as used:
            assert used is session
        assert response.headers["cache-control"] == "no-store"
        assert session.calls == ["tenant", "commit", "tenant"]
        session.calls.clear()
        with pytest.raises(HTTPException):
            async with routes.transaction(uuid.uuid4(), Response()):
                raise HTTPException(403)
        assert session.calls == ["tenant", "rollback"]
    finally:
        reset_db_session(token)
