# Authenticated lab envelope reporting

This is the current pre-v0 contract, not a compatibility or migration API.

Envelope writes require the dedicated measurement-owner authority, not a tenant,
service, IR, or generic internal token. Both POST endpoints use the same signed
protocol. Witness nodes refuse writes. Ordinary authenticated reads are unchanged.

Hypermesh holds `AIUDITOR_LAB_REPORT_KEY` only in its service process and builds
reports from its stored, assigned, completed lab job or reviewed qualification run.
For PATH_B, queue-time artifact and environment pins must still match the completed
sustained measurement. Existing unbound jobs need a newly queued run. Qualification
publishes the frozen reviewed manifest and actual completion time. The key is never
returned to a host or frontend. AIuditor trusts this measurement-owner attestation;
it does not query another product's database. Compromise of the signer is outside
the ordinary tenant credential boundary.

Configure a unique secret of at least 32 characters in both service processes.
Reusing a generic internal or admission credential is refused. An unset key keeps
reporting closed. Supply it only to the independently deployed AIuditor and Hypermesh workloads. Install both
service-owned migration tails from the pinned database package before enabling the publisher. Deploy the consumer and publisher
together; unsigned callers receive 401 or 422. No unverified envelope is backfilled
with invented provenance: unverified rows require a new soak. To replace one, use
a fresh authorized measurement and its current revision.

The HTTP body is the version 1 report (tenant, origin, job/run UUID, device UUID,
binding SHA256, measured/issued UTC times, measurement, and create/replace intent).
`X-Lab-Signature` is lowercase HMAC-SHA256 over the exact request bytes prefixed by
`aiuditor.lab-envelope.v1\n`. `X-Tenant-ID` must equal the signed tenant. Authentication
expires after five minutes; a newly accepted measurement must be at most 24 hours
old. Refreshing authentication never refreshes observation time.

Create uses `/envelopes/initial` with `If-None-Match: *` or `/envelopes` in create
mode. Replacement requires the signed expected revision and matching quoted
`If-Match`. Per-run receipts bind immutable evidence; an identical replay returns
its original receipt without mutating the current envelope. Its event obligation
is retained or recovered exactly once. Reusing the run with changed evidence is rejected. A replay after a later
replacement returns the original result and cannot roll state back. Competing
replacements serialize and only one matching revision succeeds. Receipts, the current envelope and an owner-local outbox obligation commit
together under tenant RLS, surviving process restarts. Event identity and occurrence
time are bound to the accepted measurement. Replay can recover a missing event
from an interrupted TEST publication; it preserves a uniquely matching stored
uncorrelated event and refuses ambiguous stored events. Delivery retries retain
the same UUID and facts through the owner relay.

Qualification covers ordinary credential refusal, tampering, freshness, replay,
CAS replacement, caller binding, concurrent PostgreSQL consumers, restart, and RLS.
These are protocol tests, not physical hardware measurement acceptance.
