# Retrying admission catalog promotion

An allowed admission is the durable human verdict, not proof that the remote
catalog call completed. An upstream outage or lost response may be retried by
reposting an allow verdict through the existing authenticated auditor endpoint.
Current tenant membership, interactive approval authority and witness write
refusal still apply. Sandbox reports require the dedicated measurement-owner signature and the
persisted assignment. The authenticated report route is enabled; ordinary tenant,
service and internal credentials cannot submit measurements.

A retry reads the persisted tenant-scoped admission and preserves its original
verdict, reason, resolver and resolution timestamp. It requires the original
successful soak and explicit egress pass. Denied decisions, changed verdicts,
changed supplied reasons and inconsistent decision records are refused. The
reason can be omitted for retry without changing its persisted value.

Delivery reuses the deterministic class/hash catalog ID and original admission
ID. The plane owns tenant/catalog upsert idempotency. Repeated delivery, including
a response lost after an upsert, may make additional HTTP attempts but creates
one catalog row. AIuditor preserves the stored human decision on every retry;
HTTP delivery may repeat. This also recovers previously allowed admissions
whose first upstream promotion failed; no schema backfill or completion-marker
assumption is needed. Concurrent remote delivery still depends on the owning
plane's idempotent contract.

The owning PostgreSQL/socket regressions exercise outage and lost-response
recovery after constructing a fresh SQL persistence session, stable request
identity and preserved decision metadata. The loopback peer implements the
plane contract; these checks do not establish deployed plane, lab or provider
acceptance. Catalog promotion does not confer certification.
