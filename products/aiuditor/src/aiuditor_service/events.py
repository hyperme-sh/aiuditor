"""AIuditor event_type strings on the shared control-plane topic."""

# Publish on controlplane.events.v1 only. Do not add a per-product topic.
CONNECTION_CREATED_V1 = "aiuditor.connection.created.v1"
CONNECTION_ASSIGNED_V1 = "aiuditor.connection.assigned.v1"
APPROVAL_HELD_V1 = "aiuditor.approval.held.v1"
APPROVAL_RESOLVED_V1 = "aiuditor.approval.resolved.v1"
