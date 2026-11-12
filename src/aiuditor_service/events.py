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
CONTRACT_FULFILLED_V1 = "aiuditor.contract.fulfilled.v1"
CONTRACT_VIOLATED_V1 = "aiuditor.contract.violated.v1"
CATALOG_SERVICE_CREATED_V1 = "aiuditor.catalog.service.created.v1"
CATALOG_SERVICE_ALLOCATED_V1 = "aiuditor.catalog.service.allocated.v1"
ENVELOPE_RECORDED_V1 = "aiuditor.envelope.recorded.v1"
SUITE_MEMBER_RECORDED_V1 = "aiuditor.suite.member.recorded.v1"
ADMISSION_SUBMITTED_V1 = "aiuditor.admission.submitted.v1"
ADMISSION_RESOLVED_V1 = "aiuditor.admission.resolved.v1"


async def publish_contract(publisher, *, tenant_id, event_type, contract):
    """Legacy rail grants retain their v1 wire; neutral grants use v2."""
    from aiuditor_service.domain.models import LegacyContractEvent
    if contract.rail is not None:
        payload = LegacyContractEvent.model_validate(contract.model_dump()).to_event_payload()
    else:
        event_type = event_type.removesuffix('.v1') + '.v2'
        payload = contract.to_event_payload()
    return await publisher.publish(tenant_id=tenant_id, event_type=event_type, payload=payload)
