# Parent snapshot reconciliation

Witness hydration applies validated parent snapshots as one transaction. A
receipt records the tenant, configured-parent identity, export timestamp,
snapshot digest and IDs owned by that parent in each supplied table. Rows
absent from the next complete table snapshot are removed only if the receipt
previously recorded them as parent-owned. Untracked local rows and other
tenants remain unchanged. Row collisions are refused rather than adopted.

The parent identity is derived from the configured parent URL, without tokens.
Changing that URL does not transfer ownership from another parent. A SQL
witness retains receipts across process restarts. Memory mode protects the
replacement with a lock and swaps staged state after validation. PostgreSQL
uses one transaction and a nonblocking tenant advisory lock, so independent
workers cannot interleave parent claims. Failed transactions leave rows and
receipts unchanged. Readers reload from committed persistence.

Every row must match the snapshot tenant. The requested tenant is also checked
at the HTTP hop and apply boundary. Duplicate IDs and invalid rows are refused
before any writes. Older snapshots cannot restore deleted authority. An exact
replay is harmless; different content at an accepted timestamp is refused.
An older parent may omit optional envelope, suite or admission tables; omission
preserves those tables. Explicit empty lists authorize reconciliation to empty
for the parent's previously owned rows only.

The service's installed migration bundle extends its existing Alembic owner
chain with `aiuditor_0059_parent_snapshots`. The new receipt table has forced
tenant RLS. Startup still requires exactly one already-adopted AIuditor head,
takes the existing shared migration lock, preserves foreign heads and refuses
an unprovisioned database. It does not bootstrap or stamp mixed histories. The
frozen AIuditor baseline is copied unchanged into the owner-only runtime bundle;
no other service's migration history is included in that bundle.

## Existing witnesses

Historical rows have no trustworthy parent provenance. This migration does not
infer ownership, delete them, or mark them as local/parent-owned. A first new
snapshot whose IDs collide with those rows is refused. Review the witness's
derived rows and configured parent before any separate ownership adoption or
cache reset. A changed parent or reset export clock also needs explicit
reconciliation; silently resetting the receipt could revive old authority.
This PR does not perform live adoption or publish a deployment image.

Hydration remains witness-only. It does not authorize grant writes, execute
violations, approve admissions, certify a model or activate a host. Receipts
contain IDs/digests and are not included in grant exports or hydrate responses.

Existing authenticated local policy/inbox operations retain their role contract.
Ownership is by row identity: changing a parent-owned row does not silently
transfer it to local ownership. New unrelated local rows retain their own IDs.
