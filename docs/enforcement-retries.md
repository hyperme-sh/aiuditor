# Violation enforcement retries

Local contract suspension and upstream enforcement are different facts. Every
violation carries a durable attempt count and nullable enforcement completion time.
Its UUID is the upstream operation identity. Filing the same contract/kind/action/
normalized reason reuses the same identity; changing the decision creates a new one.
The local decision and suspension commit together before any remote call.

Evaluating a suspended contract retries its unfinished violations instead of
assuming suspension proves that Hypermesh stopped or refunded the lease. Repeating
filing also retries that same decision. No background scheduler is introduced;
operators or existing evaluators must repeat either authenticated API call. Writer,
tenant and actor checks still run before retry. Witness replicas cannot enforce.

A bounded lock-connection pool, separate from the progress writer pool, refuses
excess work immediately. A nonblocking per-operation PostgreSQL advisory lock prevents two AIuditor workers
from dispatching the same operation concurrently. A busy worker returns 503 for a
later retry. Progress commits independently of the lock connection: a process exit
releases exclusion but leaves an unfinished operation available to the next worker.
Completion is recorded only after Hypermesh acknowledges success.

Hypermesh serializes violation calls per tenant/lease and stores a tenant-scoped
operation receipt binding the operation to lease, action and reason. Different
parameters cannot reuse an accepted identity. The existing lease owner performs
revocation, durable stop-job insertion and payment settlement. Stop insertion already
reuses a pending or completed stop. Payment settlement uses its existing durable
billing-record replay/refund identity, including an ambiguous payment HTTP outcome;
this adds no second payment or refund implementation. A terminal lease or matching
completed receipt returns current lease state without repeating those effects.

Install both owner migrations and deploy the Hypermesh receiver before the new
AIuditor sender. Existing violations are intentionally pending rather than assigned
invented completion evidence; reevaluation reconciles them against the lease owner.
Legacy internal callers without operation_id receive a deterministic lease/action/
reason identity. Rollback retains receipts and progress. This change does not add a
durable event outbox or guarantee delivery of the existing post-write events.

Qualification covers first-call failure, suspended reevaluation, repeated filing,
stable identity, process/store restart, independent PostgreSQL consumers, concurrent
worker refusal and lost upstream acknowledgements. Real payment/provider acceptance
remains governed by the payment owner's existing qualification gates.
