"""Real PostgreSQL witness refusal and socket-level framing acceptance."""
import json
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import create_engine, text
from aiuditor_service.app import create_app
from aiuditor_service.hypermesh import HttpPlaneHop
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.role import AiuditorRole
from aiuditor_service.store import new_stores
from conftest import tenant_session_headers
from test_violations import _signed
pytest_plugins = ["machine_postgres"]

@pytest.fixture(autouse=True)
def static_metadata(monkeypatch):
    monkeypatch.setenv("AIUDITOR_CRAWL_SOURCE", "static")

@pytest.mark.integration
@pytest.mark.parametrize("path", ["violations", "evaluate"])
@pytest.mark.parametrize("bound", [False, True])
async def test_persisted_witness_refusal(migrated_postgres, tenant_a, path, bound):
    engine = create_engine(migrated_postgres)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true) ON CONFLICT DO NOTHING"), {"id": tenant_a, "name": str(tenant_a)})
    engine.dispose()
    persist = SqlPersistSession.from_url(migrated_postgres)
    stores = new_stores(persist)
    row = _signed(stores.contract_store, tenant_a, until=datetime(2020,1,1,tzinfo=UTC), now=datetime(2019,1,1,tzinfo=UTC)).model_copy(update={"lease_id": uuid.uuid4() if bound else None})
    stores.contract_store.put(row)
    class RefuseHop:
        async def get(self, *args, **kwargs):
            pytest.fail("witness attempted host lookup")
        async def get_lease(self, *args, **kwargs):
            pytest.fail("witness attempted lease lookup")
        async def apply_violation(self, *args, **kwargs):
            pytest.fail("witness attempted enforcement")
    events = []
    app = create_app(enable_db_middleware=False, persist_session=persist, role=AiuditorRole.WITNESS, host_lookup=RefuseHop(), plane_hop=RefuseHop(), test_event_sink=events)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://witness.test") as client:
            response = await client.post(f"/api/v1/aiuditor/contracts/{row.id}/{path}", headers=tenant_session_headers(tenant_a), **({"json":{"kind":"abuse"}} if path == "violations" else {}))
            assert response.status_code == 403
    restarted = new_stores(SqlPersistSession.from_url(migrated_postgres))
    assert restarted.contract_store.get(tenant_a, row.id) == row
    assert restarted.violation_store.list_for(tenant_a) == []
    assert events == []

@pytest.mark.parametrize("mode", ["violations", "evaluate"])
async def test_authorized_violation_real_http_framing(app, http_client, tenant_a, tenant_headers, monkeypatch, mode):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, dict(self.headers.items()), body))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        def do_GET(self):
            seen.append((self.path, dict(self.headers.items()), b""))
            body = json.dumps({"id": str(row.lease_id), "status": "active", "device_id": None, "paid": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.delenv("HYPERMESH_INTERNAL_TOKEN", raising=False)
    try:
        app.state.plane_hop = HttpPlaneHop(f"http://127.0.0.1:{server.server_port}")
        row = _signed(app.state.contract_store, tenant_a, until=datetime(2020,1,1,tzinfo=UTC), now=datetime(2019,1,1,tzinfo=UTC)).model_copy(update={"lease_id": uuid.uuid4()})
        app.state.contract_store.put(row)
        response = await http_client.post(f"/api/v1/aiuditor/contracts/{row.id}/{mode}", headers={**tenant_headers,"Host":"inbound.example","Cookie":"private=secret","X-Untrusted":"no"}, **({"json":{"kind":"abuse","reason":"reviewed"}} if mode == "violations" else {}))
        assert response.status_code == (201 if mode == "violations" else 200), response.text
        assert len(seen) == (1 if mode == "violations" else 2)
        for _, forwarded, _ in seen:
            forwarded = {k.lower(): v for k,v in forwarded.items()}
            assert forwarded["host"] == f"127.0.0.1:{server.server_port}"
            assert "cookie" not in forwarded and "x-untrusted" not in forwarded
        path, headers, body = seen[-1]
        headers = {k.lower():v for k,v in headers.items()}
        assert path == "/api/v1/hypermesh/internal/lease-violate"
        assert headers["host"] == f"127.0.0.1:{server.server_port}"
        assert int(headers["content-length"]) == len(body)
        assert headers["authorization"] == tenant_headers["Authorization"]
        assert headers["x-tenant-id"] == str(tenant_a)
        assert "cookie" not in headers and "x-untrusted" not in headers
        assert json.loads(body)["action"] == ("stop" if mode == "violations" else "refund")
        assert json.loads(body)["lease_id"] == str(row.lease_id)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
