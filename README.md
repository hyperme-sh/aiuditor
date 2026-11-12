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

## Policy class selectors

Policy `prompt_class` values are case-sensitive identifiers of 1–64 ASCII
characters: an initial letter or digit followed by letters, digits, `_`, `.`,
or `-`. Names such as `prompt_injection` and `content_filter` remain exact
class selectors. Invalid values, including blank strings and `*`, return 400
without creating a policy. Omit the field or use JSON `null` to intentionally
match every class. Supplied identifiers are neither trimmed nor truncated.

Database read failures return HTTP 503 rather than an empty snapshot or a
missing-record response. Cached tenant rows remain intact while a read is
unavailable; a successful empty query still reconciles deleted rows.

See [the accounting interface](docs/accounting-interface.md) for signed commits,
backend settlement capabilities and the neutral allocation response.

## Runtime audit rules

The package includes a public audit-rule baseline and supports tenant-private
overlays outside the image. See [rule learning](docs/rule-learning.md) for the
reviewed prompt-log/dream feedback contract, offline reward learning, validation
and explicit private activation. Raw prompts never belong in policy records.

## Development

Use Python 3.13 and uv 0.11.26. All pinned source dependencies are public;
package and container builds require no dependency-read credential.

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
using public dependencies. No human approval gate is introduced.

The canonical AIuditor migration bundle ships in the runtime wheel under
`aiuditor_service/migration_bundle`; `migrations/aiuditor` retains review copies.
Its frozen owner baseline remains byte-identical. The pinned shared database
package supplies generic helpers, not this service's new migration resources. Reconcile any existing monorepo database separately;
never stamp an unknown revision to force adoption of the standalone chain.
Container, deployed RLS/witness behavior, hydration and consumer cutover remain
functional acceptance checks. The GHCR TEST image is built separately from deployment.

Set `AIUDITOR_SCHEMA_STARTUP=verify` when a separate owner migration operation
has already installed the exact image revision. This mode performs only a
read-only startup check. It requires the existing public-schema owner bundle,
its complete tables and an already active configured marketplace tenant; it
neither adopts an empty database nor creates tenant metadata. Keep the immutable
baseline unchanged and provision only this owner's chain and configured tenant
reference metadata in its reserved namespace. User identity, organizations and
authorization remain with the User HTTP authority.

Use a separate runtime login with business-table permissions and read access to
the version table and tenant references. Verify mode refuses superuser, database,
schema and table ownership, schema creation, metadata writes (including column
grants), reachable `SET ROLE`/inherited repair authority and ADMIN membership options and executable public security
definer functions. Unknown startup modes fail closed. Existing `upgrade` mode
remains the default for previously adopted deployments; witness startup retains
its separate read-only behavior.

The additive `aiuditor_0061_owned_outbox` revision installs this owner's
`service_outbox` and tenant delivery inventory. The installed event factory uses
the owned outbox by default; delivery uses the Events HTTP relay with AIuditor's
runtime URL and a dedicated producer token. It never opens User, Payment or the
Events archive's SQL. Failed delivery retains the same UUID and facts for retry.
The frozen baseline is unchanged. Provisioning also needs the configured tenant
reference rows, whose write authority stays outside ordinary runtime. Verify
mode additionally refuses PostgreSQL server-file and server-program roles.

PostgreSQL qualification builds only the packaged AIuditor chain and tenant
references in fresh databases. Explicit local identity-directory conversion
cases install their own temporary test tables; canonical provisioning does not
create them or adopt the retired mixed migration registry. Lifecycle-safe identity-bridge
credential handling stays on its separately pinned revision.

Uploaded-model admissions persist the queue owner's exact tenant/admission/job/device/
artifact assignment. Sandbox reports require the dedicated `AIUDITOR_LAB_REPORT_KEY`
(minimum 32 characters, different from generic internal/model tokens), HMAC-SHA256
with purpose `aiuditor.admission-sandbox.v1\n`, and the exact signed body. A tenant,
IR/service exchange, or generic internal token grants no measurement authority.
Authentication expires after five minutes; new evidence must be measured within
24 hours and after assignment. Identical evidence retries are idempotent across
restarts; changed evidence conflicts. A passing report enters human hold and never
certifies or promotes. Old unassigned rows remain closed to reports. An auditor can retry scheduling
through `POST /admissions/{id}/quarantine` with a `source_ref`; artifact metadata
comes from the persisted admission. Plane cancels an exact matching unbound run
and creates a fresh bound assignment instead of trusting old evidence. Plane's companion implementation must be
deployed before new assignments are available. Private admissions remain separate.
One-use approvals use a tenant-scoped conditional database update. Live caller,
PostgreSQL/RLS deployment and consumer cutover acceptance remain tracked in #3/#7.


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

Merged main publishes the moving GHCR `runtime:test` container. No Actions
artifacts, package releases or test tags are published. Production promotion
requires explicit authorization for its target. Historical releases stay intact.

## Standalone runtime image

Build the installed production dependency graph from this repository's lock:

```sh
docker build \
  --build-arg SOURCE_SHA="$(git rev-parse HEAD)" \
  --build-arg BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" -t aiuditor:local .
```

The runtime runs as UID/GID 65532, contains installed distributions rather than
source checkouts, and receives no dependency credential. The startup command
applies the installed owner schema and marketplace tenancy provisioning before
starting the service. Supply runtime database and service configuration separately.
The build imports the application with networking disabled. PRs run package and
owner-contract checks without registry write access. Main builds check the runtime
container and push the same image to `ghcr.io/hyperme-sh/aiuditor/runtime:test`.
Deployments follow this moving channel without version or digest pins. No stable
promotion is implied. Existing-database adoption still requires schema compatibility.


### Owner-scoped schema startup

The image entrypoint runs `aiuditor_service.schema_startup` before serving
requests. It upgrades only the installed `aiuditor` chain under the shared
migration lock, then provisions marketplace tenancy through the tenancy owner.
An absent or ambiguous owner revision stops startup; first-time platform/schema
provisioning remains an explicit deployment operation. It never stamps a mixed
history, bootstraps an empty database, or validates/upgrades another service's
schema. The installed AIuditor package supplies its own migration resources and retains
the locked database helper's guarded handling of an unknown newer owner revision. That guard permits
startup only; existing-data compatibility still requires clone qualification.

PostgreSQL regressions reproduce a native payment revision with retired development
tables absent. The former all-owner entrypoint refuses that schema; the scoped
entrypoint upgrades this owner's earlier adopted revision, preserves every
foreign head and leaves the retired table absent. Empty-database refusal is also
exercised. This does not qualify financial-owner migration or live callers.

## Witness owner qualification

The container entrypoint resolves the role before touching PostgreSQL. A
database-free witness skips owner migrations. A witness with a database checks
the adopted AIuditor revision and required tables in a read-only transaction;
an owner deployment must provision or upgrade that schema first. Global and
operator deployments retain the scoped owner migration path.

The PostgreSQL integration lane includes the source #716 witness refusal and
loopback HTTP framing cases. See [the security note](docs/witness-violation-security.md)
for tested boundaries and the remaining deployed acceptance. These cases run in
`integration/`, separately from the in-memory package suite.

The source #715 PostgreSQL authority cases additionally qualify one successful
consumer among four concurrent attempts, consumed state after reconnect,
foreign-tenant invisibility under a non-superuser/non-BYPASSRLS role, refusal of
expired/denied/session/already-consumed approvals, and current auditor/admin
membership checks after role removal or user deactivation. These are owner-fixture
checks with locally signed tenant sessions; they do not qualify a live identity
provider or production policy settings.

### Package preparation

`bash scripts/check-package.sh --fixtures` runs the existing source, fresh-wheel
and rebuilt-source-archive consumers without private Git credentials. Fixtures
are built from the exact clean Git revisions already pinned by this package;
the manifest records their digests and source provenance. The verifier checks
root metadata, the complete active lock graph and transitive wheel metadata,
including the explicitly declared uv database override. This is qualification of
the effective uv graph; it does not establish portable installer resolution of
conflicting transitive database pins without that override.
Explicit private extras and unrecognized conditional pins fail closed.

The read-only pull-request workflow discards temporary package checks after
validation. It uploads no distribution files and publishes no releases.

Pull requests validate source and installed package forms using verified
public dependency-wheel fixtures. Main builds use the pinned public source
dependencies without dependency-read credentials. See the retained
[release policy](docs/release-policy.md) for compatibility, artifact provenance,
and publication authority, and [AGENTS.md](AGENTS.md) for contributor guidance.

### Replacing a class's qualification model

Catalog entries are class-scoped. The default NX suite uses
`llama-3.1-8b-q4-nx`, while AGX retains `llama-3.1-8b-q4`; their artifact pins can
match without relabeling either hardware class. Existing suites are preserved
at startup. A program operator can move an existing suite member with
`PUT /api/v1/aiuditor/suite/{member_id}` using `class_id`, `catalog_id`, `pack_id`,
`expected_catalog_id`, and `expected_pack_id`. The member ID and class remain
unchanged; stale expected configuration or another member for the target model
returns 409. The operation serializes with other program edits and bootstrap.

This changes program configuration only. Existing envelopes remain recorded under
their original model IDs; no measurements, host certification or activation are
granted. Fresh assigned native qualification must establish the new reference.

Generic `purpose=service` keys cannot add or replace reviewed suite members,
even when they carry self-requested operator scope strings. Their compatibility
seed endpoint initializes only empty default classes and preserves any existing
operator-managed workload and pack. Qualification program changes require the
existing authenticated tenant-session `operator:program` authority.

### Container distribution

Merged main builds publish `ghcr.io/hyperme-sh/aiuditor/runtime:test`. This moving
TEST channel tracks the latest validated main build; consumers do not pin a
version or digest. The image reports its source revision for diagnostics.
PR checks are read-only. All jobs use GitHub-hosted runners. Only the merged-main container workflow
can push to GHCR. It verifies the built image and skips superseded source heads.
No Actions artifacts, package bundles or test prereleases are published. Package
checks use temporary wheel/source-archive files that are removed after validation.

### Allocating an existing host connection

Catalog allocation excludes host connections whose existing lease is already
bound to a contract in AIuditor or on the plane. It selects another eligible
connection or returns 409 before creating signed authority. Existing unbound
renter leases retain their reuse path; lease validation occurs before signing.
This is a selection precondition, not a distributed allocation transaction.

### Provider destination policy

The operator must set `AIUDITOR_PROVIDER_ALLOWED_HOSTS` to comma-separated exact
ASCII hostnames, for example `api.vendor.example,inference.vendor.example`.
Unset/empty configuration permits no provider destinations. Wildcards, suffix
matching and tenant-supplied exceptions are not supported. Configure the actual
provider hostname before enabling its connection; this change alone does not
configure or qualify a live provider.

Provider endpoints require HTTPS on port 443, with no credentials, query,
fragment, escaped path/host, whitespace or backslash. Each request resolves the
approved hostname once, rejects the entire answer set if any address is not
public (including mapped/transition IPv6), and connects to one validated numeric
address. The original Host header and TLS server name are retained with normal
certificate and hostname verification. Redirects and environment proxies are
disabled; a redirect response is returned without forwarding credentials to its
target. DNS/connect failures are not retried against another destination.

This uses HTTPcore's documented [SNI hostname extension](https://www.encode.io/httpcore/extensions/#sni_hostname)
with HTTPX [environment handling disabled](https://www.python-httpx.org/environment_variables/).
Package regressions include real loopback TLS: a trusted certificate for the
original hostname succeeds through the pinned socket, and a trusted certificate
for another hostname is refused before sending HTTP headers. This is transport
qualification, not live provider acceptance or deployment.

Runtime publication checks the built image and moving channel. Build completion
does not imply deployment or production acceptance. Operator procedures are
maintained separately from this contributor documentation.

Signed class-envelope reports commit their immutable receipt, envelope and an
owner-local event obligation in one transaction. Event identity and occurrence
time come from the accepted measurement, so retries and relay failures preserve
one UUID and facts. Replaying an older receipt can recover a missing obligation;
a uniquely matching stored uncorrelated event is preserved, including one already
delivered. Ambiguous stored events refuses reconciliation. This path uses the
owned outbox even when another publisher transport is configured; the existing
owner relay handles delivery. It does not repair fixture topology or authorize
another run's measurement.

## License

AIuditor is distributed under Apache-2.0; see LICENSE and NOTICE. Dependencies
retain their own licenses. Agent guidance and release policy remain in this repo.

## Runtime ownership

AIuditor owns audit, safety and RBAC only. Conductor owns model routing and cost.
Orchestrator owns host-market pricing and timing. AIuditor may return access
constraints to conductor, but it does not select or optimize the route. Learned
rules and rewards must remain within audit/safety/RBAC.
