# AIuditor

Policy, approvals, connections, contracts and model-admission service.
Package `aiuditor-service` 0.1.0; factory `aiuditor_service.app.create_app`;
entry point `python -m aiuditor_service.main`. Runtime routes are described in
`edge-routes.json`; model-admission guidance is in `docs/private-model-admission.md`.

Tenant operations require bound JWT credentials; X-Tenant-ID is a cross-check.
Approvals require named human authority. Global/operator/witness roles retain
their existing write boundaries; witness hydration does not grant write authority.
ORM models are owned by `aiuditor_service.models`. Hypermesh integration uses
owner HTTP contracts and opaque IDs, not cross-product SQL/FKs.

## Development

Use Python 3.13 and uv 0.11.26 with read access to the seven pinned private
repositories named in `.github/workflows/package.yml`.

    uv sync --frozen
    bash scripts/check-package.sh

Package tests run from source, a fresh installed wheel outside the checkout,
and a rebuilt source distribution. Wheel runtime bytes must match source.
Upstreams are mocked and persistence is in-memory. The shared marketplace setting
check uses installed dependencies instead of searching neighboring source trees.

`integration/` retains PostgreSQL and migration-dependent tests plus their helper.
They require the owner test harness and migrated database and are not part of
the package result. `migrations/aiuditor` retains the extracted owner chain
unchanged; it is not an independently configured migration runner. Do not apply
these scripts without source reconciliation and owner-chain qualification.
Container, database RLS, witness deployment, hydration and consumer cutover
remain separate acceptance gates. No image or deployment is published here.

The main-only hosted workflow uses pinned actions, read-only contents and exact
repository credential headers. Actions remains disabled pending scoped
`DEPENDENCY_READ_TOKEN` access and hosted-worker policy.
