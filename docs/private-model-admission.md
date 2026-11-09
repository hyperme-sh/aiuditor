# Scoped private model admission

AIuditor owns the private admission verdict and human audit. Hypermesh owns upload,
scan/inspection, lab selection, immutable job inputs, host delivery and leases.
This authority slice is a dependency for that integration; it grants no placement
by itself and does not mark MS-03 complete.

Private records use `aiuditor_private_model_admissions`, separate from the uploaded-model
public catalog promotion path. An allow verdict never publishes or certifies the
revision. Migration `aiuditor_0055_private_admission` adds forced tenant RLS and
retains verdict evidence on rollback.

## V1 service contract

Internal routes below `/api/v1/aiuditor/internal/private-model-admissions` require
`X-Model-Admission-Token`, matching a dedicated random secret of at least 32
characters in `AIUDITOR_MODEL_ADMISSION_TOKEN`. `X-Tenant-ID` is a cross-check within
that trusted service hop. This credential authenticates no browser/verdict route
or unrelated AIuditor API. Witness deployments cannot write these records.

- `POST /`: idempotent `admission_id`, exact `target`, `lab_job_id`, `lab_device_id`.
- `GET /{admission_id}`: current authoritative scope, evidence, status and audit.
- `POST /{admission_id}/sandbox`: matching job/device/class, target hash, file hash,
  backend, OCI image and configuration hash, plus measured soak and explicit egress
  observation. A report is write-once; exact retries succeed.

The target binds revision UUID, tenant-owner subject, complete sealed-manifest and
inspection hashes, GGUF content hash/size, intended class/backend, pinned runtime
image, reviewed configuration and its hash, and license. V0's first adapter is GGUF
on `cuda-jetson` / `nvidia`; this is a contract scope, not a claim of deployment or
hardware validation. The existing license allowlist applies. More than
50,000,000,000 bytes is refused and stays v1+.

Hashes use SHA-256 over UTF-8 sorted compact JSON. Configuration consists of
`context_tokens`, `jinja` and `timeout_seconds`; unknown fields are refused. The
service recomputes the configuration hash. Every change of revision, manifest,
inspection, class, backend, runtime image or configuration creates a distinct
validation target. Hypermesh must construct it from trusted persisted evidence,
never forward browser claims as measured evidence.

A positive matching soak and explicit positive egress result opens a **human hold**.
Missing/false egress or failed soak yields `failed`. Changed identities return 409.
There is no automatic allow, promotion or model-output resolver.

## Human decisions

`GET /api/v1/aiuditor/private-model-admissions` lists the latest 100 tenant records
for authorized reviewers. `POST /{admission_id}/verdict` below that prefix accepts
`allow` or `deny` plus a bounded reason slug. `POST /{admission_id}/revoke` revokes an
allowed target while retaining its original resolution audit.

These routes require a verified `controlplane-frontend` tenant-session JWT, an
active subject-bound tenant user with live `auditor` or `admin` membership, and a
named human subject. Setup, smoke, API-key exchange, service credentials and model
subjects cannot resolve. Membership is rechecked for every request. Internal
service tokens cannot resolve or revoke. The request body supplies no resolver.
Allow requires the persisted positive scoped evidence; the verdict records the
verified human subject and time. Responses use `no-store`.

Hypermesh must read this authority before issuing renter grants, compare the
complete target, and refuse unavailable/revoked/denied/incomplete verdicts. Runtime
shutdown, credential expiry and cleanup belong to the placement/operations slices;
revoking this record alone does not claim an already-running host has stopped.

## Validation

PostgreSQL HTTP tests exercise real middleware, concurrent idempotent creation,
changed class/configuration/image/job/hash refusal, missing egress, human hold,
service/browser/setup/model separation, live membership revocation, tenant isolation
and verdict revocation. Existing admission/promotion tests remain unchanged. Owner
migration tests verify RLS and preserve all frozen baseline tables.
