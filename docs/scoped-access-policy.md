# Scoped access policy changes

P4 adds authenticated personal narrowing and organization administration to the
existing access policy owner. User supplies current organization read/admin
checks; AIuditor stores changes in its normal tenant database transactions.
The configured policy file seeds each organization once. Thereafter the durable
row is authoritative, so changing or rolling back that file cannot erase a
revocation. Policy rows are checked against retained change history.

GET `/api/v1/aiuditor/access/policy/{organization_id}` returns the revision,
base rules, applicable personal/organization restrictions and editable scopes.
PUT `/api/v1/aiuditor/access/policy` requires `expected_revision`, the selected
scope and one exact restriction. Modes are deny, per-use, limited, and inherited.
Personal rules can only narrow the organization policy. Per-use requires fresh
automatic verification and disables remembered allows; it does not add a human
prompt. Inherited entries remain as tombstones with immutable change history.
Stale concurrent edits return 409, and cross-organization requests are checked
by User before opening policy state.

`read-admit` and `action-admit` repeat current active identity and policy checks,
consume an action ID, and commit under the same organization-row lock used by
mutations. That commit orders admission against revocation. A review alone is
not permission to execute. A later policy change cannot undo an action already
admitted; unused reviews must pass admission against their expected revision.
Consumed IDs are never deleted by allow-cache expiry or policy editing.

The Conductor and Visor consumers are required for the complete P4 behavior.
The service adapter alone is not completion of AIuditor #92.
