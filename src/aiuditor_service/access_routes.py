"""Read-only preview authenticated by current Plane machine assignment."""
import asyncio
import json
import os
from pathlib import Path
import ssl
from urllib.parse import urlsplit
import uuid
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from aiuditor_service.access_preview import Preview, Policy, ReadReview, evaluate, evaluate_read

router=APIRouter(prefix='/aiuditor/access',tags=['access-preview'])
slots=asyncio.Semaphore(8)


def load_policy():
    path=Path(os.environ.get('AIUDITOR_ACCESS_POLICY_FILE',''))
    if not path.is_absolute():raise ValueError('Access policy unavailable')
    with path.open('rb') as source:
        raw=source.read(1048577)
    if len(raw)>1048576:raise ValueError('Access policy too large')
    return Policy.model_validate_json(raw).validate_rules()


async def machine_context(body,token,*,active=False):
    origin=os.environ.get('AIUDITOR_ACCESS_PLANE_URL','').rstrip('/')
    parsed=urlsplit(origin)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Plane authority unavailable')
    route='access-context' if active else 'context'
    nonce=str(uuid.uuid4())
    payload={key:str(getattr(body,key)) for key in ('device_id','assignment_id','session_id')}
    payload.update(version=1,request_id=nonce)
    tls=ssl.create_default_context(cafile=os.environ.get('AIUDITOR_ACCESS_CA_FILE') or None)
    async with httpx.AsyncClient(timeout=5,follow_redirects=False,trust_env=False,verify=tls) as client:
        async with client.stream('POST',origin+f'/api/v1/hypermesh/bots/{body.bot_id}/machine/{route}',
            json=payload,headers={'Authorization':'Bearer '+token,'X-Tenant-ID':str(body.tenant_id)}) as reply:
            if reply.status_code in (401,403,409,410):raise HTTPException(403,'Machine preview context refused')
            reply.raise_for_status()
            raw=b''
            async for chunk in reply.aiter_bytes():
                raw+=chunk
                if len(raw)>8192:raise ValueError('Context too large')
    fact=json.loads(raw)
    if not isinstance(fact,dict) or fact.get('request_id')!=nonce:raise ValueError('Context binding differs')
    for field in ('owner_id','organization_id'):
        if str(uuid.UUID(fact[field]))!=fact[field] or uuid.UUID(fact[field]).int==0:raise ValueError('Invalid owner identity')
    return fact


@router.post('/preview')
async def preview(body:Preview,request:Request,response:Response):
    response.headers['Cache-Control']='no-store'
    authorization=request.headers.get('Authorization','')
    if not authorization.startswith('Bearer hm_msi_') or len(authorization)>4096:
        raise HTTPException(401,'Enrolled machine identity required')
    if request.headers.get('X-Tenant-ID')!=str(body.tenant_id):
        raise HTTPException(403,'Preview tenant mismatch')
    try:
        async with asyncio.timeout(8):
            async with slots:
                fact=await machine_context(body,authorization[7:])
                policy=await asyncio.to_thread(load_policy)
                return evaluate(body,fact,policy)
    except HTTPException:
        raise
    except (OSError,ValueError,KeyError,TypeError,TimeoutError,httpx.HTTPError):
        raise HTTPException(503,'Permission preview unavailable') from None


@router.post('/read-review')
async def read_review(body:ReadReview,request:Request,response:Response):
    response.headers['Cache-Control']='no-store'
    authorization=request.headers.get('Authorization','')
    if not authorization.startswith('Bearer hm_msi_') or len(authorization)>4096:
        raise HTTPException(401,'Enrolled machine identity required')
    if request.headers.get('X-Tenant-ID')!=str(body.tenant_id):
        raise HTTPException(403,'Read tenant mismatch')
    try:
        async with asyncio.timeout(8):
            async with slots:
                fact=await machine_context(body,authorization[7:],active=True)
                policy=await asyncio.to_thread(load_policy)
                return evaluate_read(body,fact,policy)
    except HTTPException:
        raise
    except (OSError,ValueError,KeyError,TypeError,TimeoutError,httpx.HTTPError):
        raise HTTPException(503,'Permission read review unavailable') from None
