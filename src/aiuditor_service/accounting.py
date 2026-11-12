"""Accounting consumer port: committed grants and optional settlement status.

Ledger, balances, money conversion and provider operations belong to the backend.
"""
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Literal, Protocol
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from aiuditor_service.accounting_contracts import BackendName, Capabilities, SettlementStatus
from aiuditor_service.domain.models import ContractFulfill


class CommitTerms(ContractFulfill):
    model_config = ConfigDict(extra='forbid')


class Committed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['accounting.committed.v1']
    id: uuid.UUID
    tenant_id: uuid.UUID
    accounting_backend: BackendName
    accounting_ref: str = Field(min_length=1, max_length=256)
    contract: CommitTerms


def selected_backend():
    value = os.environ.get('ACCOUNTING_BACKEND', 'internal')
    if value not in {'internal','none','http'}:
        raise HTTPException(503, 'Accounting backend is misconfigured')
    return value


async def authenticated_commit(request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
            raise HTTPException(413, 'Accounting callback is too large')
        raw.extend(chunk)
    key = os.environ.get('AIUDITOR_ACCOUNTING_CALLBACK_KEY', '')
    if len(key) < 32:
        raise HTTPException(503, 'Accounting callback key is unconfigured')
    if key in {os.environ.get('AIUDITOR_INTERNAL_TOKEN'), os.environ.get('AIUDITOR_PAID_CHECKOUT_TOKEN')}:
        raise HTTPException(503, 'Accounting callback requires a dedicated signing key')
    stamp = request.headers.get('X-Accounting-Timestamp', '')
    if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12 or abs(time.time() - int(stamp)) > 300:
        raise HTTPException(401, 'Accounting signature timestamp is invalid')
    message = b'aiuditor/accounting.committed.v1\n' + stamp.encode() + b'\n' + bytes(raw)
    wanted = 'v1=' + hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    supplied = request.headers.get('X-Accounting-Signature', '')
    if not re.fullmatch(r'v1=[0-9a-f]{64}', supplied) or not hmac.compare_digest(wanted, supplied):
        raise HTTPException(401, 'Accounting signature is invalid')
    try:
        event = Committed.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, 'Accounting commit payload is invalid') from None
    try:
        bound_tenant = uuid.UUID(os.environ.get('AIUDITOR_ACCOUNTING_TENANT_ID', ''))
    except ValueError:
        raise HTTPException(503, 'Accounting tenant is unconfigured') from None
    if event.tenant_id != bound_tenant or (request.headers.get('X-Tenant-ID') not in {None, str(bound_tenant)}):
        raise HTTPException(403, 'Accounting tenant mismatch')
    if event.accounting_backend != selected_backend():
        raise HTTPException(403, 'Accounting backend mismatch')
    request.state.tenant_id = bound_tenant
    return event


def apply_commit(store, event):
    from aiuditor_service.contracts import fulfill_checkout_contract
    from aiuditor_service.domain.models import ContractStatus
    digest = hashlib.sha256(json.dumps(event.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    # One tenant-scoped lock serializes reference and event replay across leases.
    key = uuid.uuid5(event.tenant_id, 'accounting-commit')
    with store.persist.checkout_contract_scope(event.tenant_id, key, None):
        for prior in store.list_for(event.tenant_id):
            if prior.accounting_backend != event.accounting_backend:
                continue
            same_event = prior.accounting_commit_id == event.id
            same_ref = prior.accounting_ref == event.accounting_ref
            if same_ref and prior.accounting_commit_id is None and event.contract.contract_id == prior.id:
                continue
            if same_event or same_ref:
                if not (same_event and same_ref and prior.accounting_commit_hash == digest):
                    raise HTTPException(409, 'Accounting commit conflicts with existing authority')
                # A replay never recreates, extends or reactivates a grant.
                return prior
        existing = store.lease_for(event.tenant_id, event.contract.lease_id)
        if existing is None and event.contract.contract_id is not None:
            existing = store.get(event.tenant_id, event.contract.contract_id)
        if existing is not None:
            if existing.accounting_backend != event.accounting_backend or existing.accounting_ref not in {None, event.accounting_ref}:
                raise HTTPException(409, 'Contract accounting binding differs')
            if existing.status != ContractStatus.SIGNED:
                raise HTTPException(409, 'Contract is not signed')
        row = fulfill_checkout_contract(store, event.tenant_id, event.contract)
        row = row.model_copy(update={'accounting_backend':event.accounting_backend,
            'accounting_ref':event.accounting_ref, 'accounting_commit_id':event.id,
            'accounting_commit_hash':digest})
        store.put(row)
        return row


class AccountingBackend(Protocol):
    async def capabilities(self) -> Capabilities: ...
    async def settlement_status(self, tenant_id: uuid.UUID, accounting_ref: str) -> SettlementStatus: ...


class InternalAccounting:
    """No external settlement; this consumer does not grant credits or keep a ledger."""
    async def capabilities(self):
        return Capabilities(supports_settlement=False)

    async def settlement_status(self, tenant_id, accounting_ref):
        raise HTTPException(503, 'Backend does not support settlement')


class HttpAccounting:
    def __init__(self, url, token, *, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or len(token) < 32
            or (parsed.scheme == 'http' and parsed.hostname not in {'localhost','127.0.0.1','::1'})):
            raise HTTPException(503, 'Accounting HTTP backend is misconfigured')
        self.url, self.token, self.transport = url.rstrip('/'), token, transport

    async def _get(self, path, params=None):
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                async with client.stream('GET', self.url + path, params=params,
                    headers={'Authorization':'Bearer ' + self.token}) as response:
                    if response.status_code != 200:
                        raise ValueError('Accounting backend rejected the request')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 65536:
                            raise ValueError('Accounting backend response too large')
                        raw.extend(chunk)
                    return json.loads(raw)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, 'Accounting backend unavailable') from None

    async def capabilities(self):
        try:
            return Capabilities.model_validate(await self._get('/capabilities'))
        except ValidationError:
            raise HTTPException(503, 'Accounting capabilities are invalid') from None

    async def settlement_status(self, tenant_id, accounting_ref):
        try:
            result = SettlementStatus.model_validate(await self._get('/settlement-status',
                {'tenant_id':str(tenant_id), 'accounting_ref':accounting_ref}))
        except ValidationError:
            raise HTTPException(503, 'Accounting settlement status is invalid') from None
        if result.tenant_id != tenant_id or result.accounting_ref != accounting_ref:
            raise HTTPException(503, 'Accounting settlement scope mismatch')
        return result


def backend_for(contract):
    # Stored bindings are authoritative; changing a default never migrates them.
    if contract.accounting_backend in {'internal','none'}:
        return InternalAccounting()
    if contract.accounting_backend == 'http':
        return HttpAccounting(os.environ.get('ACCOUNTING_URL',''), os.environ.get('ACCOUNTING_TOKEN',''))
    raise HTTPException(503, 'Unknown contract accounting backend')


async def settlement_overdue(contract, backend=None):
    source = backend if backend is not None else backend_for(contract)
    capabilities = await source.capabilities()
    if not capabilities.supports_settlement:
        return False
    if not contract.accounting_ref:
        raise HTTPException(503, 'Contract accounting reference is missing')
    status = await source.settlement_status(contract.tenant_id, contract.accounting_ref)
    if status.tenant_id != contract.tenant_id or status.accounting_ref != contract.accounting_ref:
        raise HTTPException(503, 'Accounting settlement scope mismatch')
    return status.overdue
