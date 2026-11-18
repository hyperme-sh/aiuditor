"""Transactional access policy state using the service's tenant session."""
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from aiuditor_service.access_preview import Policy
from aiuditor_service.access_policy_state import changed, effective
from aiuditor_service.models import AiuditorAccessPolicy, AiuditorAccessPolicyEvent, AiuditorAccessDispatch


async def locked(session, tenant, organization, seed):
    match = (AiuditorAccessPolicy.tenant_id == tenant,
             AiuditorAccessPolicy.organization_id == organization)
    row = await session.scalar(select(AiuditorAccessPolicy).where(*match).with_for_update())
    latest = await session.scalar(select(AiuditorAccessPolicyEvent).where(
        AiuditorAccessPolicyEvent.tenant_id == tenant,
        AiuditorAccessPolicyEvent.organization_id == organization)
        .order_by(AiuditorAccessPolicyEvent.sequence.desc()).limit(1))
    if row is not None:
        if (row.state['tenant_id'] != str(tenant) or row.state['organization_id'] != str(organization)
            or (latest is None and row.state['sequence'] != 0)
            or (latest is not None and (latest.sequence != row.state['sequence']
                or latest.change['revision'] != row.state['revision']))):
            raise HTTPException(503, 'Policy history differs; restore consistent owner state')
        return row
    if latest is not None:
        raise HTTPException(503, 'Policy history exists; refusing to recreate prior permissions')
    base = seed()
    org = next((o for o in base.organizations if o.tenant_id == tenant and o.organization_id == organization), None)
    if org is None:
        raise HTTPException(403, 'No configured organization policy')
    # Seed only once. A later config-file rollback cannot replace durable rules.
    policy = Policy(version=1, revision=base.revision, organizations=[org])
    state = {'tenant_id':str(tenant), 'organization_id':str(organization),
             'revision':str(uuid.uuid4()), 'sequence':0, 'restrictions':{},
             'base':policy.model_dump(mode='json')}
    await session.execute(insert(AiuditorAccessPolicy).values(tenant_id=tenant,
        organization_id=organization, state=state).on_conflict_do_nothing())
    return await session.scalar(select(AiuditorAccessPolicy).where(*match).with_for_update())


def current(row, principal):
    return effective(Policy.model_validate(row.state['base']), row.state, principal)


def mutate(session, row, body, subject, admin):
    state, event = changed(row.state, body, subject, admin=admin)
    row.state = state
    session.add(AiuditorAccessPolicyEvent(tenant_id=row.tenant_id,
        organization_id=row.organization_id, sequence=event['sequence'], change=event))
    return state


async def consume(session, row, body, decision):
    """Call under the policy row lock, then commit before returning admission.

    This commit orders dispatch against revocation. Later revocation cannot
    undo an admitted operation; unconsumed reviews have no execution authority.
    """
    if not decision['permitted']:
        return decision
    raw=json.dumps({'identity':decision['identity'],'project':body.project,
                    'action':decision['action']},sort_keys=True,separators=(',',':')).encode()
    inserted=await session.scalar(insert(AiuditorAccessDispatch).values(
        tenant_id=row.tenant_id, request_id=body.request_id, organization_id=row.organization_id,
        policy_revision=uuid.UUID(decision['policy_revision']),
        binding_sha256=hashlib.sha256(raw).hexdigest()).on_conflict_do_nothing().returning(AiuditorAccessDispatch.request_id))
    if inserted is None:
        raise HTTPException(409,'Action ID already consumed; do not retry')
    return {**decision,'dispatch_admitted':True}
