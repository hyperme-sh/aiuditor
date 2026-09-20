# AIuditor

Enterprise grant plane on the existing Panopticon control plane. Copied from [`products/template/`](../template/) the way [`hypermesh/`](../hypermesh/) and [`tennessee-windage/`](../tennessee-windage/) were.

**AIuditor** is connections, later contracts and approvals. **Hypermesh** stays classes, devices, host certs, and payout. The **AIuditor door** stays [`products/hypermesh-gateway/`](../hypermesh-gateway/). Site inbox stays `/api/v1/auditor` on hypermesh-service. Do not merge AImmune. Do not put this on hyperme.sh. Do not fork Keycloak or Stripe.

Edge prefix: `/api/v1/aiuditor`. Wire prefix of the door (`hm_gw_`) is unchanged.

## What this cut is

| Piece | Location |
|-------|----------|
| Connection | `kind` host\|provider, `purpose` lease\|auditor, `device_id` or HTTPS `endpoint`, `credential_ref` (hash), `status` |
| Host assign | `kind=host` looks up the enrolled Hypermesh device (`HYPERMESH_SERVICE_URL`). Stores `device_id` only. Device secret and the standing WG peer stay on Hypermesh / the door. Unset URL fail-closes. |
| Use | `POST …/connections/{id}/assign` binds a purpose=lease host to a Hypermesh renter/service lease. `POST …/ticket` mints a purpose=ir ticket on a purpose=auditor host. `POST …/classify` is an AIuditor-owned HTTPS hop on a purpose=auditor provider (hash-only credential; optional one-shot `X-Provider-Authorization`). |
| Router | `GET/POST /api/v1/aiuditor/connections`, `GET /api/v1/aiuditor/connections/{id}` |
| Approval inbox | `POST/GET /api/v1/aiuditor/approvals`, `POST …/approvals/{id}/resolve` — hold an AI-use ask until a named human allow-once / allow-session / deny. Timeout is deny. Not `/api/v1/auditor`. LLM is never `resolved_by`. |
| Tenant | Bound JWT (tenant-session or purpose=service exchange). `X-Tenant-ID` is a cross-check. Header-only and Hypermesh machine prefixes are 401. |
| Event | `aiuditor.connection.created.v1` on `controlplane.events.v1`. Lock fields only. No raw credential. |

In-memory store. Persistence (table + RLS + Alembic) is follow-up when assignment and contracts need it.

Not in this product: Hypermesh classes, devices, certs, leases, catalog. Not a second door. Not a second IdP.

## Run locally

From the repo root (after `uv lock` if you just copied):

```bash
docker compose up -d --build aiuditor-service
```

- Health: http://localhost:8015/api/v1/health/
- OpenAPI: http://localhost:8015/docs
- Gateway merge: `CPEDGE_AIUDITOR_SERVICE_URL` (compose sets it). Merged paths are prefixed `/_upstream/aiuditor/…` on `GET http://localhost:8000/v1/openapi.json`.

Without Docker:

```bash
cd products/aiuditor
uv run pytest -q
uv run python -m aiuditor_service.main
```

## Tests

```bash
cd products/aiuditor && uv run pytest --cov=aiuditor_service --cov-report=term-missing -q
```

HTTP tests disable DB middleware so they run without Docker. Compose still enables GUC middleware against Postgres.
