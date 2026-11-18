"""Authenticated personal narrowing and organization policy administration."""
import uuid
from fastapi import APIRouter, HTTPException, Request, Response
from aiuditor_service.approval_authority import require_authority
from aiuditor_service.private_models.routes import human
from aiuditor_service.machine_identity import transaction
from aiuditor_service.access_policy_state import PolicyMutation
from aiuditor_service import access_policy_store as store

router=APIRouter(prefix='/aiuditor/access',tags=['access-policy'])


def seed():
    from aiuditor_service.access_routes import load_policy
    return load_policy()


def snapshot(row, subject, admin):
    state=row.state
    return {'version':1,'organization_id':state['organization_id'],'revision':state['revision'],
            'sequence':state['sequence'],'rules':state['base']['organizations'][0]['rules'],
            'restrictions':[entry for entry in state['restrictions'].values()
                            if entry['subject'] in ('',subject)],
            'subject':subject,'can_edit_personal':True,'can_edit_organization':admin}


@router.get('/policy/{organization_id}')
async def get_policy(organization_id:uuid.UUID,request:Request,response:Response):
    response.headers['Cache-Control']='no-store'
    tenant,subject=human(request,clients=frozenset({"controlplane-frontend","hypermesh-native"}))
    await require_authority(request,tenant,subject,'organization.read',organization_id)
    admin=True
    try:
        await require_authority(request,tenant,subject,'organization.admin',organization_id)
    except HTTPException as error:
        if error.status_code != 403:raise
        admin=False
    async with transaction(tenant) as session:
        row=await store.locked(session,tenant,organization_id,seed)
        result=snapshot(row,subject,admin)
    return result


@router.put('/policy')
async def change_policy(body:PolicyMutation,request:Request,response:Response):
    response.headers['Cache-Control']='no-store'
    tenant,subject=human(request,clients=frozenset({"controlplane-frontend","hypermesh-native"}))
    admin=body.scope=='organization'
    await require_authority(request,tenant,subject,
        'organization.admin' if admin else 'organization.read',body.organization_id)
    async with transaction(tenant) as session:
        row=await store.locked(session,tenant,body.organization_id,seed)
        try:
            store.mutate(session,row,body,subject,admin)
        except PermissionError:
            raise HTTPException(403,'Organization administration required') from None
        except ValueError:
            raise HTTPException(409,'Policy changed or scope limit reached; reload before editing') from None
        result=snapshot(row,subject,admin)
    return result
