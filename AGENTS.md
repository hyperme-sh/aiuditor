# AIuditor development

Read README.md. Preserve verified tenant binding, human approval authority,
witness write refusal and model-admission boundaries. ORM ownership is here;
no Hypermesh ORM imports or cross-product foreign keys. Run bash scripts/check-package.sh.
Use mock upstreams and memory persistence for package checks; qualify PostgreSQL,
owner migrations and real witnesses separately before deployment.

## Package publication permission boundary

Source/package validation keeps its existing runner policy and read-only contents.
The separate `.github/workflows/release.yml` publication job is the narrow exception:
it may use `contents: write` and `actions: read` on disposable `ubuntu-24.04` workers
solely through the exact-pinned shared package publisher. It must not check out or
execute this repository's code, install its packages, or receive private dependency
or deployment credentials. This hosted publication exception does not authorize
moving secret-bearing validation to another runner group. Stable publication must
retain any configured release-admission prerequisite for the exact tag/commit.
