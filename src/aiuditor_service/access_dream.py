"""Bounded owner-side dreaming; no execution or organization-admin authority."""
import hashlib
import json
import time
import uuid
from typing import Literal
from pydantic import Field
from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from aiuditor_service.access_preview import Strict, Action, ReadReview, evaluate_read
from aiuditor_service.access_actions import TypedAction, ActionReview, evaluate_action
from aiuditor_service.access_policy_state import PolicyMutation, Restriction, restriction_key
from aiuditor_service import access_policy_store as store
from aiuditor_service.models import AiuditorAccessDream
from aiuditor_service.access_routes import router, machine_context, load_policy, slots
from aiuditor_service.machine_identity import transaction
from aiuditor_service.role import require_grant_write, role_from_request
import asyncio

class Record(Strict):
    rule_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    policy_revision: uuid.UUID
    action: TypedAction | Action
    last_use: float = Field(gt=0, allow_inf_nan=False)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    review_pending: bool = Field(strict=True)

class Dream(Strict):
    version: Literal[1]
    request_id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    bot_id: uuid.UUID
    assignment_id: uuid.UUID
    session_id: uuid.UUID
    project: str = Field(min_length=1,max_length=4096)
    records: list[Record] = Field(min_length=1,max_length=8)


def review(body, fact, policy, now):
    results=[]
    if len({r.rule_hash for r in body.records}) != len(body.records):
        raise ValueError('Duplicate rule')
    for record in body.records:
        if record.last_use > now:raise ValueError('Future use')
        common={k:getattr(body,k) for k in ('version','request_id','tenant_id','device_id','bot_id','assignment_id','session_id','project')}
        common.update(template_version=1,template=[{'operation':record.action.operation,'target':record.action.target,'limits':record.action.limits}],action=record.action)
        if record.action.operation=='read':
            request=ReadReview(**common);decision=evaluate_read(request,fact,policy,now)
        else:
            request=ActionReview(**common);decision=evaluate_action(request,fact,policy,now)
        rules=[r for org in policy.organizations if str(org.organization_id)==fact['organization_id'] and org.tenant_id==body.tenant_id for r in org.rules if r.operation==record.action.operation and r.target==record.action.target]
        protected=any(r.dream_protected for r in rules)
        outcome={'rule_hash':record.rule_hash,'kind':'retain','status':'applied',
                 'reason':'Useful scoped allow remains within current policy',
                 'before':record.action.model_dump(mode='json'),'after':record.action.model_dump(mode='json')}
        if record.policy_revision != policy.revision:
            outcome.update(status='superseded',reason='Policy changed; fresh joint verification required')
        elif not decision['permitted']:
            outcome.update(kind='revoke',after=None,reason='Current owner policy prohibits this action')
        elif record.expires_at <= now:
            outcome.update(kind='expire',after=None,reason='Scoped allow expired')
        elif not decision['learning_eligible'] or (record.review_pending and not protected):
            outcome.update(kind='narrow',after=None,reason='Fresh per-use verification replaces reusable learning')
        results.append(outcome)
    return results


@router.post('/dream')
async def dreaming(body:Dream, request:Request, response:Response):
    require_grant_write(role_from_request(request))
    response.headers['Cache-Control']='no-store'
    token=request.headers.get('Authorization','')
    if not token.startswith('Bearer hm_msi_') or len(token)>4096:
        raise HTTPException(401,'Enrolled machine identity required')
    if request.headers.get('X-Tenant-ID')!=str(body.tenant_id):
        raise HTTPException(403,'Tenant differs')
    try:
        async with asyncio.timeout(8), slots:
            fact=await machine_context(body,token[7:],active=True)
            async with transaction(body.tenant_id) as session:
                row=await store.locked(session,body.tenant_id,uuid.UUID(fact['organization_id']),load_policy)
                binding=hashlib.sha256(json.dumps({'request':body.model_dump(mode='json'),'principal':fact['principal'],'organization':fact['organization_id']},sort_keys=True,separators=(',',':')).encode()).hexdigest()
                prior=await session.scalar(select(AiuditorAccessDream).where(AiuditorAccessDream.tenant_id==body.tenant_id,AiuditorAccessDream.request_id==body.request_id))
                if prior is not None:
                    if prior.binding_sha256!=binding:raise HTTPException(409,'Review ID already used')
                    return prior.result
                outcomes=review(body,fact,store.current(row,fact['principal']),time.time())
                for record,outcome in zip(body.records,outcomes):
                    if outcome['status']=='applied' and outcome['kind']=='narrow':
                        mutation=PolicyMutation(organization_id=fact['organization_id'],expected_revision=row.state['revision'],scope='personal',restriction=Restriction(operation=record.action.operation,target=record.action.target,mode='per_use',reason='Dreaming review: fresh per-use verification'))
                        existing=row.state['restrictions'].get(restriction_key(fact['principal'],mutation.restriction))
                        if existing and existing['mode']=='limited':
                            outcome.update(status='failed',after=outcome['before'],reason='Retained explicit personal limits; combined narrowing is unsupported')
                        else:
                            store.mutate(session,row,mutation,fact['principal'],False)
                result={'version':1,'request_id':str(body.request_id),'project':body.project,
                        'identity':{k:fact[k] for k in ('tenant_id','owner_id','principal','organization_id','device_id','bot_id','assignment_id','session_id')},
                        'policy_revision':row.state['revision'],'outcomes':outcomes}
                session.add(AiuditorAccessDream(tenant_id=body.tenant_id,request_id=body.request_id,binding_sha256=binding,result=result))
            return result
    except HTTPException:raise
    except (ValueError,KeyError,TypeError,TimeoutError):
        raise HTTPException(503,'Permission review unavailable') from None
