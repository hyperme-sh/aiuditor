"""Owner transactions for derivative events and their business writes."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import inspect
import json
import uuid

from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.contracts import validate_event_envelope
from controlplane_events.publisher import refuse_forbidden_payload
from aiuditor_service.persist import MemoryPersistSession, _SCOPE

_CURRENT = ContextVar('aiuditor_event_operation', default=None)
_MEMORY_ROWS = ContextVar('aiuditor_operation_rows', default=None)


def memory_rows(persist, fallback):
    scoped = _MEMORY_ROWS.get()
    return scoped[1] if scoped is not None and scoped[0] is persist else fallback


def _commit_memory(persist, before, after):
    # Apply only this operation's changed keys. Unrelated writes can complete
    # while an upstream is awaited; conflicting writes must be retried.
    missing = object()
    changes = []
    with persist._parent_lock:
        for table in before.keys() | after.keys():
            for tenant in before.get(table, {}).keys() | after.get(table, {}).keys():
                old = before.get(table, {}).get(tenant, {})
                new = after.get(table, {}).get(tenant, {})
                current = persist._rows.get(table, {}).get(tenant, {})
                for key in old.keys() | new.keys():
                    if old.get(key, missing) == new.get(key, missing):
                        continue
                    if current.get(key, missing) != old.get(key, missing):
                        raise ValueError('Concurrent owner operation changed the same row')
                    changes.append((table, tenant, key, new.get(key, missing)))
        for table, tenant, key, value in changes:
            bucket = persist._rows.setdefault(table, {}).setdefault(tenant, {})
            if value is missing:
                bucket.pop(key, None)
            else:
                bucket[key] = deepcopy(value)


@asynccontextmanager
async def operation(persist):
    current = _CURRENT.get()
    if current is not None:
        if current['persist'] is not persist:
            raise ValueError('An event operation cannot span owner stores')
        yield
        return
    state = {'persist': persist, 'events': []}
    token = _CURRENT.set(state)
    try:
        if isinstance(persist, MemoryPersistSession):
            before = deepcopy(persist.rows)
            staged = deepcopy(before)
            rows_token = _MEMORY_ROWS.set((persist, staged))
            try:
                yield
                _commit_memory(persist, before, staged)
            finally:
                _MEMORY_ROWS.reset(rows_token)
        else:
            if _SCOPE.get() is not None:
                raise ValueError('Event operation must own its outer transaction')
            with persist.candidate_scope():
                yield
        sink = getattr(persist, 'event_sink', None)
        if sink is not None:
            sink.extend(state['events'])
    finally:
        _CURRENT.reset(token)


def atomic_events(function):
    signature = inspect.signature(function, eval_str=True)
    @wraps(function)
    async def wrapped(*args, **kwargs):
        request = signature.bind(*args, **kwargs).arguments['request']
        persist = request.app.state.aiuditor_persist
        async with operation(persist):
            return await function(*args, **kwargs)
    wrapped.__signature__ = signature
    return wrapped


async def publish_owned_event(request, *, tenant_id, event_type, payload):
    state = _CURRENT.get()
    persist = request.app.state.aiuditor_persist
    if state is None or state['persist'] is not persist:
        raise ValueError('Event write requires its owner transaction')
    tenant_id = uuid.UUID(str(tenant_id))
    refuse_forbidden_payload(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    identity = f'aiuditor:{tenant_id}:{event_type}:{hashlib.sha256(canonical.encode()).hexdigest()}'
    event_id = uuid.uuid5(uuid.NAMESPACE_URL, identity)
    envelope = ControlPlaneEventEnvelope(service='aiuditor', tenant_id=tenant_id,
        event_type=event_type, payload=payload, correlation_id=str(event_id))
    validate_event_envelope(envelope)
    encoded = envelope.model_dump(mode='json')
    if len(json.dumps({'event_id':str(event_id),'envelope':encoded}).encode()) > 262144:
        raise ValueError('Owner event exceeds delivery limit')
    if isinstance(persist, MemoryPersistSession):
        events = persist.rows.setdefault('service_outbox', {}).setdefault(str(tenant_id), {})
        previous = events.get(str(event_id))
        if previous is None:
            events[str(event_id)] = encoded
    else:
        from sqlalchemy import text, bindparam
        from sqlalchemy.dialects.postgresql import JSONB
        session = _SCOPE.get()
        session.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {'tenant':str(tenant_id)})
        session.execute(text('INSERT INTO service_tenants(tenant_id) VALUES (:tenant) ON CONFLICT DO NOTHING'), {'tenant':tenant_id})
        inserted = session.scalar(text('''INSERT INTO service_outbox(id,tenant_id,envelope)
            VALUES (:id,:tenant,:envelope) ON CONFLICT DO NOTHING RETURNING id''').bindparams(bindparam('envelope',type_=JSONB)),
            {'id':event_id,'tenant':tenant_id,'envelope':encoded})
        previous = None if inserted is not None else session.scalar(text('SELECT envelope FROM service_outbox WHERE id=:id'), {'id':event_id})
        if inserted is None and previous is None:
            raise ValueError('Owner event identity belongs to a different tenant')
    if previous is not None:
        if any(previous.get(key) != encoded.get(key) for key in ('service','tenant_id','event_type','schema_version','payload')):
            raise ValueError('Owner event identity binds different facts')
        return event_id
    state['events'].append({'tenant_id':tenant_id,'event_type':event_type,'payload':payload,'correlation_id':str(event_id)})
    return event_id
