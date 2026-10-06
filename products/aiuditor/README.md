# AIuditor

Enterprise grant plane on the existing Panopticon control plane. Copied from [`products/template/`](../template/) the way [`hypermesh/`](../hypermesh/) and [`tennessee-windage/`](../tennessee-windage/) were.

**AIuditor** is connections, contracts, approvals, and the **model cert program** (class envelopes, core cert suite, admission verdicts). **Hypermesh** stays the measurement and lease store: classes, devices, scorecards, catalog stamps (`certified` / `tokens_per_sec`), and payout. The **AIuditor door** stays [`products/hypermesh-gateway/`](../hypermesh-gateway/). Site inbox stays `/api/v1/auditor` on hypermesh-service. Do not merge AImmune. Do not put this on hyperme.sh. Do not fork Keycloak or Stripe.

Edge prefix: `/api/v1/aiuditor`. Wire prefix of the door (`hm_gw_`) is unchanged.

## What this cut is

| Piece | Location |
|-------|----------|
| Connection | `kind` host\|provider, `purpose` lease\|auditor, `device_id` or HTTPS `endpoint`, `credential_ref` (hash), `status` |
| Host assign | `kind=host` looks up the enrolled Hypermesh device (`HYPERMESH_SERVICE_URL`). Stores `device_id` only. Device secret and the standing WG peer stay on Hypermesh / the door. Unset URL fail-closes. |
| Use | `POST …/connections/{id}/assign` binds a purpose=lease host to a Hypermesh renter/service lease. `POST …/ticket` mints a purpose=ir ticket on a purpose=auditor host. `POST …/classify` is an AIuditor-owned HTTPS hop on a purpose=auditor provider (hash-only credential; optional one-shot `X-Provider-Authorization`). |
| Class envelope | `POST/GET /api/v1/aiuditor/envelopes` — one row per artifact per `class_id` (file hash, quant, runtime, context, fit, `decode_tok_s`, thermal). Written from lab soaks. Hypermesh still stamps the catalog row from a green scorecard. |
| Core cert suite | `POST/GET /api/v1/aiuditor/suite` (and `POST …/suite/seed`) — which catalog/pack ids form the compact host cert. Seed starts from `pack-v1` / `llama-3.1-8b-q4` for `agx-large` and `nx-volume`. Compact-cert jobs live on Hypermesh. |
| Admission | `POST/GET /api/v1/aiuditor/admissions`, `POST …/admissions/{id}/sandbox`, `POST …/admissions/{id}/verdict` — upload submit runs static gate → lab quarantine soak (with `admission_id`) → sandbox (egress fail-closed; missing report does not pass). Named human allow may promote an uncertified catalog row only after soak + explicit egress_ok. Deny never promotes. LLM is never `resolved_by`. Process note: `hypermesh-docs/model-admission.md`. |
| Daily crawl | `POST /api/v1/aiuditor/crawl/run`, `GET …/crawl/candidates` — HF + allowlisted GGUF publisher **metadata only** (no weight download). Fit-filter drops artifacts that cannot fit the class after OS reserve + KV headroom. Queues one lab soak via Hypermesh `POST /lab/soaks`. Re-soak only on hash/quant/runtime change or retention age. |
| Router | `GET/POST /api/v1/aiuditor/connections`, `GET /api/v1/aiuditor/connections/{id}` |
| Allowed set | `GET /api/v1/aiuditor/allowed` — every purpose=lease host that is not disabled. Does not allocate. `POST /catalog/{id}/allocate` still binds one host. |
| Desk | Connections registry and violations desk on `/dashboard/aiuditor`. Not hyperme.sh. |
| Approval inbox | `POST/GET /api/v1/aiuditor/approvals`, `POST …/approvals/{id}/resolve` — hold an AI-use ask until a named human allow-once / allow-session / deny. Timeout is deny. Not `/api/v1/auditor`. LLM is never `resolved_by`. |
| Tenant | Bound JWT (tenant-session or purpose=service exchange). `X-Tenant-ID` is a cross-check. Header-only and Hypermesh machine prefixes are 401. |
| Event | `aiuditor.connection.created.v1` on `controlplane.events.v1`. Lock fields only. No raw credential. |
| Export | `GET /api/v1/aiuditor/export` — contracts, violations, approvals. No prompt bodies. Derived copy, not the SoT. |

Alembic `0025` / `0029` / `0030` `aiuditor_*` tables with tenant RLS on the shared `controlplane` Postgres. Production hydrates and writes through `SqlPersistSession` (RLS GUC per put) and fail-closes if `DATABASE_URL` is missing, dummy, or unreachable. Tests keep `MemoryPersistSession` (`enable_db_middleware=False`).

`AIUDITOR_ROLE` is `global` (Azure marketplace SoT), `operator` (private plane), or `witness` (host/door package). Same image. Witness fail-closes on fulfill, sign, bind, allocate, ticket mint, and program writes (`require_grant_write`). `GET /meta` advertises `writer` (`market` / `private` / `none`). Hypermesh checkout GETs that before fulfill and refuses a non-market writer unless `AIUDITOR_REQUIRED_WRITER` is `private` or `any`. Witness `POST /hydrate/pull` copies a tenant snapshot from `AIUDITOR_PARENT_URL` (connections, policies, contracts, violations, approvals, catalog, **envelopes**, **suite members**, **admissions**). A host sidecar also pulls on a timer (`AIUDITOR_HYDRATE_TENANT_ID`, `AIUDITOR_HYDRATE_EVERY`). Witness binds `127.0.0.1` and uses memory persist unless `DATABASE_URL` is set. Handoff (spine D) is not this path. Package: [`deploy/witness/`](deploy/witness/). Do not put this on hyperme.sh.

## Boundary with Hypermesh

| Fact | Owner |
|------|--------|
| What gets measured (class envelopes, core suite, admission allow/deny, crawl candidates) | **AIuditor** |
| Timed samples, scorecards, Path B, lab soak queue, catalog `certified` / `tokens_per_sec`, leases | **Hypermesh** |
| Compact-cert job execution on enrolled hosts | **Hypermesh** (reads suite membership from AIuditor / witness hydrate) |
| Workload jail / WG-only lease publish | **hypermesh-host** |

AIuditor does **not** own Path B or scorecards. The lease catalog remains Hypermesh. The program that decides what gets measured and which pack proves a box still matches its class lives here.

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

### Core suite deployment initialization

Compose enables `AIUDITOR_BOOTSTRAP_CORE_SUITE=true` on the global AIuditor.
At startup it initializes the documented `pack-v1` / `llama-3.1-8b-q4` suite for
empty `agx-large` and `nx-volume` classes in `MARKETPLACE_TENANT_ID`. This restores
the program configuration after a test database rebuild without tying it to a
particular host. Existing class suites, including custom catalogs and packs,
remain unchanged. Set the flag to `false` when deliberately managing an empty
program; private operators and witnesses never run this initialization.

Initialization retries if the marketplace tenant has not been created yet.
PostgreSQL serializes concurrent initializer replicas and commits both classes
in one transaction. Logs report `core suite initialized` or the redacted
`core suite initialization pending` state. Normal suite reads and witness
hydration use the persisted rows.

This provisions membership only: it does not approve hardware intake, invent
class envelopes or benchmark measurements, activate an agent, or enable selling.
A registered host reporting `missing_suite` needs this server-side configuration,
not another device login. Remaining hardware intake and certification gates
require operator approval and measured evidence through their existing workflows.

### Conditional first-reference publication

`POST /api/v1/aiuditor/envelopes` accepts `If-None-Match: *` for initial references.
The write uses the tenant/class/catalog unique constraint with an atomic insert;
if any writer has already stored that reference, it returns 409 without changing
it or emitting a new envelope event. Omitting the header retains ordinary lab
upsert behavior. Automatic onboarding publication uses the conditional form even
when its earlier missing-reference lookup succeeded.

`POST /api/v1/aiuditor/envelopes/initial` always applies this condition. Automatic
onboarding uses this dedicated endpoint so an older AIuditor rejects the request
instead of ignoring a new conditional header during a rolling deployment.
