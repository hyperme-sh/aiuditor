# Release policy

AIuditor is distributed as a GHCR container under Apache-2.0. LICENSE and NOTICE
ship in the runtime image. The source package version remains declared in
`pyproject.toml`; this change does not advance it or authorize a stable release.

## Validation and TEST distribution

PR unit and contract checks and clean review determine issue completion. Full
integration remains in scheduled CI; failures become new issues rather than
holding implemented issues open. Development checks require no prior release.

PR checks use read-only permissions. All jobs use debrunner while the repository
is private and automatically select GitHub-hosted runners when it becomes public. Merged main builds validate
source/package contracts and the runtime container, then publish the moving
`ghcr.io/hyperme-sh/aiuditor/runtime:test` channel. Only that main workflow has
registry write permission. The publication step verifies the tested image and
current main revision before updating TEST. Source revision and image identity
are diagnostic checks, not pins imposed on deployed services or their peers.

Running TEST services follow the moving channel with patch-compatible contracts.
Do not require matching peer builds or manual digest updates. No package archives,
Actions artifacts, bundle releases or test prereleases are published. Temporary
wheel/source-archive builds remain part of package validation and are discarded.

## Production and authority

A successful build or TEST publication is not deployment or production acceptance.
Production promotion, stable tags/releases and package-version changes require
explicit maintainer authorization for the specific target and action. There is
no automatic stable publication workflow. Historical tags and release records
remain intact; do not overwrite them or treat a TEST image as a stable baseline.

Do not add required reviewers, branch protections, environment approvals or other
human approval prerequisites unless explicitly requested. Automated checks and
advisory reviews are welcome. Maintainer authorization to perform a publication
is distinct from adding GitHub approval settings.

Public contributor documentation describes these contracts and workflows. Private
deployment topology and business plans belong in private operator documentation.

## Public source transition

`public-source.yml` scans the committed source and a disposable single-root-commit
copy with checksum-verified gitleaks, including dependency wheel archives. The
copy is deleted after scanning; it is not uploaded or published. This preserves
AGENTS.md, this policy, license notices and technical compatibility provenance.
Changing repository visibility or replacing existing history remains a distinct
operation against an explicitly selected repository; the cleanup workflow does
neither. Private operational documentation remains in the operator repository.
