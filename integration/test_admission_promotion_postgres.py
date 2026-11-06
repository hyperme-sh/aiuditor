"""Durable admission retry with migrated owner PostgreSQL and real HTTP transport.

The loopback fixture implements the plane's tenant/catalog upsert contract.
It is not a deployed plane, real lab measurement or provider acceptance.
"""
import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from aiuditor_service.admission import resolve_admission_async
from aiuditor_service.domain.models import AdmissionResolve, AdmissionVerdict
from aiuditor_service.hypermesh import HttpPlaneHop
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import AdmissionStore
from test_admission_promote import _passed_row

pytest_plugins = ['machine_postgres']
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_mode', ['outage', 'lost_response'])
async def test_sql_restart_and_http_promotion_retry(migrated_postgres, monkeypatch, failure_mode):
    tenant = uuid.uuid4()
    engine = create_engine(migrated_postgres)
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),
                           {'id': tenant, 'name': str(tenant)})
    engine.dispose()
    state = {'failing': True, 'requests': [], 'rows': {}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == '/api/v1/hypermesh/catalog/promote'
            assert self.headers['X-Tenant-ID'] == str(tenant)
            assert self.headers['X-Internal-Token'] == 'fixture-promotion-token'
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append(body)
            if state['failing'] and failure_mode == 'outage':
                self.send_response(503)
                self.end_headers()
                return
            key = (str(tenant), body['catalog_id'])
            if key in state['rows']:
                assert state['rows'][key] == body
            else:
                state['rows'][key] = body
            if state['failing'] and failure_mode == 'lost_response':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            payload = json.dumps({'id': body['catalog_id'], 'certified': False}).encode()
            self.send_response(201)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HYPERMESH_INTERNAL_TOKEN', 'fixture-promotion-token')
    plane = HttpPlaneHop(f'http://127.0.0.1:{server.server_port}')
    writer = SqlPersistSession.from_url(migrated_postgres)
    restarted = None
    try:
        store = AdmissionStore(persist=writer)
        row = _passed_row(store, tenant, 'sha256:' + uuid.uuid4().hex * 2)
        decision = AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason='original reviewed verdict')
        with pytest.raises(HTTPException) as failure:
            await resolve_admission_async(store, tenant, row.id, decision,
                resolved_by='original-auditor', plane=plane)
        assert failure.value.status_code == 502
        original = store.get(tenant, row.id)
        writer._session_factory.kw['bind'].dispose()
        restarted = SqlPersistSession.from_url(migrated_postgres)
        recovered = AdmissionStore(persist=restarted)
        assert recovered.get(tenant, row.id) == original
        state['failing'] = False
        for _ in range(2):
            result = await resolve_admission_async(recovered, tenant, row.id,
                AdmissionResolve(verdict=AdmissionVerdict.ALLOW),
                resolved_by='current-auditor', plane=plane)
            assert result == original
        assert len(state['rows']) == 1
        assert len(state['requests']) == 3
        assert all(body == state['requests'][0] for body in state['requests'])
        assert state['requests'][0]['admission_id'] == str(row.id)
        for wrong_tenant, body in (
            (uuid.uuid4(), AdmissionResolve(verdict=AdmissionVerdict.ALLOW)),
            (tenant, AdmissionResolve(verdict=AdmissionVerdict.DENY)),
            (tenant, AdmissionResolve(verdict=AdmissionVerdict.ALLOW, reason='changed')),
        ):
            with pytest.raises(HTTPException):
                await resolve_admission_async(recovered, wrong_tenant, row.id, body,
                    resolved_by='current-auditor', plane=plane)
        assert len(state['requests']) == 3
        assert recovered.get(tenant, row.id) == original
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        writer._session_factory.kw['bind'].dispose()
        if restarted is not None:
            restarted._session_factory.kw['bind'].dispose()
