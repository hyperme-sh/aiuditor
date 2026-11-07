"""Parent ownership, complete-snapshot deletion and atomic memory reconciliation."""
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from fastapi import HTTPException
from aiuditor_service.domain.models import ApprovalPolicy, HydrateSnapshot, PolicyDisposition, ClassEnvelope
from aiuditor_service.parent import apply_hydrate
from aiuditor_service.persist import MemoryPersistSession
from aiuditor_service.store import new_stores

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def policy(tenant, **changes):
    return ApprovalPolicy(id=uuid.uuid4(), tenant_id=tenant, purpose='renter',
        disposition=PolicyDisposition.ALLOW, created_at=T0, **changes)


def snapshot(tenant, *, tick=0, policies=(), **tables):
    return HydrateSnapshot(tenant_id=tenant, role='global', writer='market',
        exported_at=T0 + timedelta(seconds=tick), connections=[],
        policies=[p.model_dump(mode='json') for p in policies], contracts=[],
        violations=[], approvals=[], catalog=[], **tables)


def test_empty_snapshot_removes_parent_allow_and_preserves_local_other_tenant():
    tenant, other = uuid.uuid4(), uuid.uuid4()
    stores = new_stores()
    local, foreign, remote = policy(tenant), policy(other), policy(tenant)
    stores.policy_store.put(local)
    stores.policy_store.put(foreign)
    first = snapshot(tenant, policies=[remote])
    apply_hydrate(stores, first)
    assert {r.id for r in stores.policy_store.list_for(tenant)} == {local.id, remote.id}
    # A fresh bundle over the same persist session retains provenance.
    restored = new_stores(stores.persist)
    apply_hydrate(restored, snapshot(tenant, tick=1))
    assert restored.policy_store.list_for(tenant) == [local]
    assert restored.policy_store.list_for(other) == [foreign]


@pytest.mark.parametrize('bad', ['malformed', 'foreign', 'duplicate', 'wrong_tenant'])
def test_invalid_later_tables_cannot_partially_delete_or_add_authority(bad):
    tenant = uuid.uuid4()
    stores = new_stores()
    remote = policy(tenant)
    apply_hydrate(stores, snapshot(tenant, policies=[remote]))
    before = stores.persist.snapshot()
    receipt = dict(stores.persist._parent_snapshots)
    candidate = snapshot(tenant, tick=1, policies=[policy(tenant)])
    if bad == 'malformed':
        candidate = candidate.model_copy(update={'admissions': [{'not': 'an admission'}]})
    elif bad == 'foreign':
        candidate = snapshot(tenant, tick=1, policies=[policy(uuid.uuid4())])
    elif bad == 'duplicate':
        candidate = snapshot(tenant, tick=1, policies=[remote, remote])
    with pytest.raises(HTTPException) as exc:
        apply_hydrate(stores, candidate, tenant_id=uuid.uuid4() if bad == 'wrong_tenant' else tenant)
    assert exc.value.status_code == 400
    assert stores.persist.snapshot() == before
    assert stores.persist._parent_snapshots == receipt


def test_local_collision_rolls_back_earlier_parent_deletion_and_receipt():
    tenant = uuid.uuid4()
    stores = new_stores()
    remote, local = policy(tenant), policy(tenant)
    apply_hydrate(stores, snapshot(tenant, policies=[remote]))
    stores.policy_store.put(local)
    before = stores.persist.snapshot()
    with pytest.raises(HTTPException) as conflict:
        apply_hydrate(stores, snapshot(tenant, tick=1, policies=[local]))
    assert conflict.value.status_code == 409
    assert stores.persist.snapshot() == before
    # Failed updates must not advance the receipt timestamp.
    apply_hydrate(stores, snapshot(tenant, tick=1))
    assert stores.policy_store.list_for(tenant) == [local]


def test_old_or_conflicting_snapshot_cannot_restore_deleted_authority():
    tenant = uuid.uuid4()
    stores = new_stores()
    remote = policy(tenant)
    old = snapshot(tenant, policies=[remote])
    apply_hydrate(stores, old)
    empty = snapshot(tenant, tick=1)
    apply_hydrate(stores, empty)
    apply_hydrate(stores, empty)  # exact replay
    for candidate in (old, snapshot(tenant, tick=1, policies=[remote])):
        with pytest.raises(HTTPException) as conflict:
            apply_hydrate(stores, candidate)
        assert conflict.value.status_code == 409
    assert stores.policy_store.list_for(tenant) == []


def test_parent_switch_cannot_claim_another_parents_rows():
    tenant = uuid.uuid4()
    stores = new_stores()
    remote = policy(tenant)
    first = snapshot(tenant, policies=[remote])
    apply_hydrate(stores, first, parent_id='parent-one')
    with pytest.raises(HTTPException) as conflict:
        apply_hydrate(stores, first, parent_id='parent-two')
    assert conflict.value.status_code == 409
    assert stores.policy_store.list_for(tenant) == [remote]


def test_omitted_optional_table_preserves_rows_until_explicit_empty():
    tenant = uuid.uuid4()
    stores = new_stores()
    # An approval-independent model envelope is a parent-owned optional table.
    envelope = ClassEnvelope(id=uuid.uuid4(), tenant_id=tenant, class_id='agx-large',
        catalog_id='test-model', file_hash='sha256:test', runtime='test-runtime',
        fit=True, created_at=T0, updated_at=T0)
    apply_hydrate(stores, snapshot(tenant, envelopes=[envelope.model_dump(mode='json')]))
    apply_hydrate(stores, snapshot(tenant, tick=1))
    assert stores.envelope_store.list_for(tenant) == [envelope]
    apply_hydrate(stores, snapshot(tenant, tick=2, envelopes=[]))
    assert stores.envelope_store.list_for(tenant) == []


@pytest.mark.asyncio
async def test_http_witness_reconciles_deletions_and_refuses_foreign_response(
    tenant_a, tenant_b, tenant_headers, event_sink, host_lookup, plane_hop, provider_hop,
):
    from test_role import _role_app, _client
    from aiuditor_service.parent import FakeParentHop
    from aiuditor_service.role import AiuditorRole
    remote = policy(tenant_a)
    parent = FakeParentHop(snapshot=snapshot(tenant_a, policies=[remote]))
    app = _role_app(event_sink, host_lookup, plane_hop, provider_hop,
                    AiuditorRole.WITNESS, parent_hop=parent)
    async with _client(app) as client:
        local = await client.post('/api/v1/aiuditor/policies', headers=tenant_headers,
                                  json={'purpose': 'renter', 'prompt_class': 'local_only', 'disposition': 'hold'})
        assert local.status_code == 201
        first = await client.post('/api/v1/aiuditor/hydrate/pull', headers=tenant_headers)
        assert first.status_code == 200, first.text
        listed = await client.get('/api/v1/aiuditor/policies', headers=tenant_headers)
        assert {row['id'] for row in listed.json()} == {str(remote.id), local.json()['id']}
        parent.snapshot = snapshot(tenant_a, tick=1)
        deleted = await client.post('/api/v1/aiuditor/hydrate/pull', headers=tenant_headers)
        assert deleted.status_code == 200, deleted.text
        assert (await client.get('/api/v1/aiuditor/policies', headers=tenant_headers)).json() == [local.json()]
        class ForeignParent:
            async def hydrate(self, *args, **kwargs):
                return snapshot(tenant_b, policies=[policy(tenant_b)])
        app.state.parent_hop = ForeignParent()
        refused = await client.post('/api/v1/aiuditor/hydrate/pull', headers=tenant_headers)
        assert refused.status_code == 400
        assert app.state.policy_store.list_for(tenant_b) == []
        write = await client.post('/api/v1/aiuditor/contracts/fulfill', headers=tenant_headers,
            json={'lease_id': str(uuid.uuid4()), 'renter_subject': 'renter', 'host_subject': 'host'})
        assert write.status_code == 403


@pytest.mark.asyncio
async def test_http_parent_refuses_foreign_tenant_at_transport_boundary():
    import httpx
    from aiuditor_service.parent import HttpParentHop, ParentUnavailable
    tenant, other = uuid.uuid4(), uuid.uuid4()
    peer = httpx.MockTransport(lambda request: httpx.Response(200,
        json=snapshot(other).model_dump(mode='json')))
    with pytest.raises(ParentUnavailable):
        await HttpParentHop('http://parent.test', transport=peer).hydrate(tenant)


def test_parent_identity_is_stable_across_credentials_and_default_port():
    from aiuditor_service.parent import HttpParentHop, parent_identity
    one = parent_identity(HttpParentHop('https://first:fixture@PARENT.example/base?token=one'))
    two = parent_identity(HttpParentHop('https://second:other@parent.example:443/base?token=two'))
    assert one == two
    assert one != parent_identity(HttpParentHop('https://different.example/base'))
    assert one != parent_identity(HttpParentHop('https://parent.example/other'))
