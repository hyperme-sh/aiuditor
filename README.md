# AIuditor

Policy, approvals, connections, contracts and model-admission service.
Package `aiuditor-service` 0.1.0; factory `aiuditor_service.app.create_app`;
entry point `python -m aiuditor_service.main`. Runtime routes are described in
`edge-routes.json`; model-admission guidance is in `docs/private-model-admission.md`.

Tenant operations require bound JWT credentials; X-Tenant-ID is a cross-check.
Approval resolution, contract signing and admission verdicts require a verified
interactive human session and current auditor/admin tenant membership. Global/operator/witness roles retain
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

The main-only shared-runner workflow uses pinned actions, read-only contents and exact
repository credential headers. Actions remains disabled pending scoped
`DEPENDENCY_READ_TOKEN` access and shared-runner qualification.

Legacy upload-admission sandbox reporting returns 503 until authenticated lab
job/device/artifact binding is implemented (#7). Tenant sessions and generic
service/internal credentials cannot supply measurement authority. This disables
that upload promotion path; it does not qualify the separate private-admission
contract. One-use approvals are consumed with a tenant-scoped conditional
database update; PostgreSQL/RLS acceptance remains in #3.


## Single-operator test-plane approval policy

The default requires administrator-verified `account_type=human`, interactive
AMR, a frontend tenant session and current tenant auditor/admin membership.
The test-plane owner may explicitly set
`AIUDITOR_TEST_ALLOW_UNCLASSIFIED_APPROVERS=true` to accept an interactive
approver whose signed session has no account classification. This is the
single-operator test-system policy, not evidence of verified human identity.
Explicit service classifications, exchange/internal/smoke credentials, missing
interactive AMR, revoked membership and witness writes remain refused. This
setting creates no tenant membership or account classification and remains
false unless explicitly configured. Remove the setting and recreate AIuditor
to restore classified-human approval requirements. Do not enable it in production.
