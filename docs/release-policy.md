# Release policy

This policy applies to public and private distributions of AIuditor. The package
version is declared in `pyproject.toml`; `release-policy.json` identifies the
component, release line and version files. The current configured line is `0.1`.
Do not infer the service version from another component or deployment channel.
Patch releases must preserve accepted API contracts.

## Validation and provenance

Package CI checks source, a fresh installed wheel outside the checkout, and a
wheel rebuilt from the source archive. Published artifacts must be the exact
validated bytes. Record their checksums and source revision; never replace
artifacts already published under the same version.

PR unit and contract checks and clean review determine issue completion. Full
integration checks remain in scheduled CI; failures become new issues rather
than holding implemented issues open. Package validation and development builds
must work without a prior stable release.

## Automated release admission

The existing workflow checks `release-policy.json` and compares release
candidates with an independently selected stable baseline. Before an authorized
stable publication:

1. Verify the accepted stable bundle at `releases/accepted/release.json` and its
   immutable artifact references. `SPANOPTICON_RELEASE_BASELINE_SHA` selects the
   trusted baseline commit; candidate data cannot select its own baseline.
2. Generate `releases/candidate/release.json` from the validated artifacts. Its
   component, configured line, package version and requested tag must agree.
3. Pass the existing automated compatibility and artifact checks. A missing or
   invalid stable baseline refuses publication; first-release bootstrap is a
   separately authorized operation, not permission to fabricate a baseline.
4. Publish only the admitted artifacts for that exact source revision and tag.

A development build or test prerelease is not an accepted stable baseline.
Actions artifacts have finite retention; an unavailable artifact must be rebuilt
and validated before a later release. A green build does not establish deployment
or production acceptance.

## Publication authority

Keep package-version changes, manual tags, manual releases and production
promotion explicitly authorized for their specific targets. Already configured
automatic test publication retains its existing authorization. It does not grant
authority to move a stable tag, change package versions or promote production.

Publication jobs use only the permissions they need and remain isolated from
application-code execution. They verify source identity and checksums before
publishing validated bytes. Do not introduce required reviewers, branch
protections, environment approvals or other human approval prerequisites unless
explicitly requested. Maintainer authorization for an operation is distinct from
adding GitHub approval settings.

Public contributor documentation describes these contracts and workflows.
Private deployment channels, infrastructure topology and business release plans
belong in the private operator documentation, not in this repository's policy.
