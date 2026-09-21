"""AIuditor event_type strings on the shared control-plane topic."""

# Publish on controlplane.events.v1 only. Do not add a per-product topic.
CONNECTION_CREATED_V1 = "aiuditor.connection.created.v1"
CONNECTION_ASSIGNED_V1 = "aiuditor.connection.assigned.v1"
APPROVAL_HELD_V1 = "aiuditor.approval.held.v1"
APPROVAL_RESOLVED_V1 = "aiuditor.approval.resolved.v1"
POLICY_CREATED_V1 = "aiuditor.policy.created.v1"
POLICY_DELETED_V1 = "aiuditor.policy.deleted.v1"
CONTRACT_CREATED_V1 = "aiuditor.contract.created.v1"
CONTRACT_SIGNED_V1 = "aiuditor.contract.signed.v1"
CONTRACT_BOUND_V1 = "aiuditor.contract.bound.v1"
