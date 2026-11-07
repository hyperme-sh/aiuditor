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

The canonical AIuditor migration bundle ships in the runtime wheel under
`aiuditor_service/migration_bundle`; `migrations/aiuditor` retains review copies.
Its frozen owner baseline remains byte-identical. The pinned shared database
package supplies generic helpers, not this service's new migration resources. Reconcile any existing monorepo database separately;
never stamp an unknown revision to force adoption of the standalone chain.
Container, deployed RLS/witness behavior, hydration and consumer cutover remain
functional acceptance checks. No image or live deployment is published here.

Legacy upload admissions persist the queue owner's exact tenant/admission/job/device/
artifact assignment. Sandbox reports require the dedicated `AIUDITOR_LAB_REPORT_KEY`
(minimum 32 characters, different from generic internal/model tokens), HMAC-SHA256
with purpose `aiuditor.admission-sandbox.v1\n`, and the exact signed body. A tenant,
IR/service exchange, or generic internal token grants no measurement authority.
Authentication expires after five minutes; new evidence must be measured within
24 hours and after assignment. Identical evidence retries are idempotent across
restarts; changed evidence conflicts. A passing report enters human hold and never
certifies or promotes. Old unassigned rows remain closed to reports. An auditor can retry scheduling
through `POST /admissions/{id}/quarantine` with a `source_ref`; artifact metadata
comes from the persisted admission. Plane cancels an exact matching legacy run
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

Main validation retains its exact qualified files in Actions artifacts. It does
not create GitHub releases or tags. Historical `test-<commit>` prereleases remain
qualification evidence and do not establish an accepted stable release.

Stable publication requires an explicit `vMAJOR.MINOR.PATCH` tag, the exact
successful main validation artifact, compatible declared contracts and an actual
immutable stable baseline. The publisher independently verifies that evidence;
successful caller jobs alone cannot establish acceptance. Missing evidence and
first-stable bootstrap remain closed. Package qualification, coordinated consumer
acceptance and runtime deployment remain separate.

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

PostgreSQL regressions reproduce a native payment revision with retired legacy
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

The read-only pull-request workflow retains tested wheels, source archives and
an artifact-derived runtime inventory as Actions artifacts. It does not publish
releases. These are preparation evidence, not an accepted synchronized dependency
baseline: package version alignment, dependency adoption and comparison against
a qualified prior baseline remain separate work. Existing owner test boundaries
and live/provider qualification requirements continue to apply.

Pull requests run the complete package suite using verified dependency wheel
fixtures on disposable hosted workers without private credentials. Main checks
retain the shared runner and use a short-lived App token restricted to their
dependency repositories. The App key is available only to main. Fixture checks
preserve exact source provenance and do not constitute stable release acceptance.
## Test qualification and stable acceptance

GitHub `test-<commit>` prereleases are development snapshots. Their embedded
package version does not reserve a stable version. Artifact and consumer evidence
belongs under `releases/qualification`. Owning-package and artifact validation
remain separate from stable release admission. Future test receipts must carry
an explicit non-stable classification.

`releases/accepted` and `SPANOPTICON_RELEASE_BASELINE_SHA` are reserved for an
actual immutable stable GitHub release. The pinned shared guard verifies the live
release channel, stable tag source and asset digests; a test snapshot cannot
become accepted through a provenance label. No stable baseline is recorded until
that release exists. Stable publication remains closed without a valid baseline;
first stable bootstrap and coordinated release qualification remain separate.
No stable version, approval prerequisite or deployment is introduced here.

### Actions qualification and stable publication

Main merges and workflow completion do not publish GitHub test releases. Exact
qualification files remain in Actions artifacts with finite retention. The pinned
publisher independently verifies a real immutable stable baseline and refuses
qualification snapshots, even if a caller job reports success. Existing valid
stable records are retained; no release, version change, deployment or human
approval prerequisite is introduced by this correction.

### Replacing a class's qualification model

Catalog entries are class-scoped. The default NX suite uses
`llama-3.1-8b-q4-nx`, while AGX retains `llama-3.1-8b-q4`; their artifact pins can
match without relabeling either hardware class. Existing suites are preserved
at startup. A program operator can move an existing suite member with
`PUT /api/v1/aiuditor/suite/{member_id}` using `class_id`, `catalog_id`, `pack_id`,
`expected_catalog_id`, and `expected_pack_id`. The member ID and class remain
unchanged; stale expected configuration or another member for the target model
returns 409. The operation serializes with other program edits and bootstrap.

This changes program configuration only. Legacy envelopes remain recorded under
their original model IDs; no measurements, host certification or activation are
granted. Fresh assigned native qualification must establish the new reference.

Generic `purpose=service` keys cannot add or replace reviewed suite members,
even when they carry self-requested operator scope strings. Their compatibility
seed endpoint initializes only empty default classes and preserves any existing
operator-managed workload and pack. Qualification program changes require the
existing authenticated tenant-session `operator:program` authority.

### CI artifact retention

PR preparation archives and temporary main image-transfer archives expire after
one day. Main package archives expire after seven days. Published registry
images and explicitly published release assets have separate retention; these
CI settings do not delete them. Preserve source revisions, artifact digests and
validation results in the run/PR evidence. A release needing an expired package
archive must requalify its exact source; it must not silently rebuild or accept
a different artifact.

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

Main runtime publication does not transfer Docker archives through Actions.
Validation and the isolated publisher use the existing `shared-local` runner
group, verify the exact Docker image configuration and source label, and push
those already tested bytes to the registry. A short-lived local package archive
is bound by its SHA-256 job output; the publisher checks file types, names,
size bounds and digest without checking out or executing application code.
Successful publication removes its job-owned image alias and local archive;
failed publishers retain them for retry. Optional one-day `tested-dist` retention
can fail without blocking runtime publication. If the quota prevents retention,
versioned test-bundle publication explicitly skips; deployment remains on its
previous reviewed bundle until one is available. Stable release paths are unchanged.
