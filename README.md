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

`integration/` qualifies PostgreSQL using this service's installed, immutable
`controlplane-db` dependency. Set `TEST_DATABASE_URL` to a disposable administrative
PostgreSQL database and run `bash scripts/check-postgres.sh`. The fixture uses the
standalone owner runner; it never searches or imports the retiring monorepo.
The main workflow automatically qualifies package forms and PostgreSQL contracts
with a scoped dependency-read credential. No human approval gate is introduced.

The service-owned migration copies are in `migrations/aiuditor`; their matching
reviewed resources ship in the pinned database package. Frozen mixed/baseline
resources remain unchanged. Reconcile any existing monorepo database separately;
never stamp an unknown revision to force adoption of the standalone chain.
Container, deployed RLS/witness behavior, hydration and consumer cutover remain
functional acceptance checks. No image or live deployment is published here.

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

## Release channels

Successful main-push validation retains the validated Python wheel and source distribution and publishes
an immutable `test-<commit>` GitHub prerelease. Push a `vMAJOR.MINOR.PATCH`
tag at that tested commit to promote the exact files to a stable release,
without rebuilding. Repository visibility applies to all package assets.
Package metadata versions are preserved; these are GitHub distribution snapshots,
not automatic PyPI publication or service deployment.

Promotion requires the original Actions artifact (90-day requested retention;
organization policy may shorten it). Expired evidence fails closed: validate a
new main revision. The publisher verifies the run, source, workflow and artifact
digest independently of release metadata. Repository immutable releases must be
enabled. See the [shared release contract](https://github.com/hyperme-sh/.github/blob/7749591fccd30d46ba9b0adabe82114001c9d0de/actions/release-channel/README.md).

## Standalone runtime image

Build the installed production dependency graph from this repository's lock:

```sh
docker build --secret id=dependency_read_token,env=DEPENDENCY_READ_TOKEN \
  --build-arg SOURCE_SHA="$(git rev-parse HEAD)" \
  --build-arg BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" -t aiuditor:local .
```

The runtime runs as UID/GID 65532, contains installed distributions rather than
source checkouts, and receives no dependency credential. The startup command
applies the installed owner schema and marketplace tenancy provisioning before
starting the service. Supply runtime database and service configuration separately.
The build imports the application with networking disabled; same-repository PRs
and main run the package/PostgreSQL suite and build/import the runtime image.
Fork PRs receive no dependency credential. Successful main pushes retain the tested image and publish it without rebuilding
as `ghcr.io/hyperme-sh/aiuditor/runtime:test-<commit>`. The isolated hosted
publisher never checks out or executes application code and receives no private
dependency or deployment credential. Deployments must select the resulting
registry digest. Existing test tags cannot be overwritten by reruns. This test
channel is not stable promotion or live acceptance. Existing-database adoption must independently
qualify schema compatibility; startup's newer-revision skip is insufficient.
