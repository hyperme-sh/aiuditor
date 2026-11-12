# AIuditor contributor and agent guidance

Read README.md and [the release policy](docs/release-policy.md) before changing
code, build configuration, or publication workflows. Keep this file in public
source distributions as contributor guidance.

## Service boundaries

Preserve verified tenant binding, human approval authority, witness write
refusal, and model-admission boundaries. ORM ownership belongs to this service;
do not import another product's ORM or add cross-product foreign keys. Use owner
HTTP contracts and opaque identifiers. Never retain prompts or credentials in
policy or contract records.

## Validation and issue completion

Use mock upstreams and memory persistence for package checks. The package command
is `bash scripts/check-package.sh`; PR work runs unit and contract checks.
Integration, end-to-end, PostgreSQL, owner-migration and deployed checks belong
in CI, including the scheduled main batch. Never run those suites ad hoc on hosts
or shared resources, or manually start or repeat the nightly batch.

The full nightly suite uses one concurrency group with cancellation of superseded
runs. Configuration changes do not trigger the full suite. Existing authorized
CI smoke, owner-contract and test-publication workflows may continue running.

Close an implemented issue after its PR merges with green CI and clean review.
Nightly results are not issue-closing or Product Sign-off prerequisites. Nightly
failures become new issues, linking the original issue when applicable.

## Releases and permissions

Do not advance package versions, move release lines, create or change tags,
publish releases, or promote production without explicit maintainer authorization
for the specific action and target. Existing automatic test publication remains
within its configured authorization; PR authorization does not authorize manual
tag operations or production promotion. Preserve historical releases and tags.

Development validation must work without a prior stable release. Use immutable
source revisions, fresh artifacts and consistent dependency locks. Never invent
baseline digests or substitute a development snapshot for an accepted stable
release. See the release policy for the existing automated admission contract.

Do not add required reviewers, branch protections, approval rulesets, environment
approvals, CODEOWNERS approval requirements, or release/deployment approval gates
unless explicitly requested. Automated checks and advisory reviews are welcome.
Agent instructions are not access controls for credentials shared with humans.

## CI and artifact handling

Keep validation permissions read-only unless a specific job needs more. Use
pinned actions and public dependencies without dependency-read credentials.
Preserve the currently configured runner policy: private repositories use their
approved private runner pool; public repositories use hosted runners. Never let
untrusted pull-request code receive publication or deployment credentials.

Keep privileged publication isolated from application-code execution. A publisher
must verify the exact tested artifact, source revision and digest; it must not
check out or execute the application or install its dependencies. Preserve
existing automated release-admission checks for the exact tag and commit.

Use exact versions and digests for downloadable bundles and artifact provenance.
Running test services follow their moving test channel without manual version or
digest pins, and peer services remain patch-compatible. Production follows its
explicit promotion policy. Deployment configuration and private operator details
are maintained separately from this public contributor guidance.
