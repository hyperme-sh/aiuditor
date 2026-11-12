# Runtime image references

New admission targets and sandbox reports may use `runtime_image` with an
explicit tag or moving channel. Configure `AIUDITOR_RUNTIME_IMAGE_REPOSITORIES`
as a JSON list of exact approved registry/repository names through the owning
deployment configuration. Missing or invalid policy fails closed for channels.
No image is pulled or resolved by AIuditor, and the submitted reference remains
unchanged. Shared reference vectors cover missing tags, digests, credentials,
whitespace and unapproved repositories.

The older `runtime_image_digest` field also accepts configured channels. Input
normalizes to that existing field in stored targets and responses, preserving
the current canonical target hash and report-comparison contract. Supplying
both field names is rejected. Existing V1 immutable targets retain their prior
digest syntax for migration and historical report replay; new `runtime_image`
requests use the tag-only contract. This compatibility path does not select or
pin a running service and does not convert a digest into a guessed channel.

Plane and host can adopt the field spelling independently: both report shapes
normalize to the same scoped target, while tenant, assigned job/device, model
file hash, configuration hash and human approval checks remain enforced. Model
artifact hashes and package provenance remain separate from runtime references.

The `audchannel` cases in the existing owner PostgreSQL harness check channel
creation, old/new host report replay and cross-tenant refusal. They run in the
central nightly batch. Combined registry advancement and independently deployed
peers are tracked in Infra #922; these owner cases do not claim deployed readiness.
Accounting behavior and authority are unchanged.
