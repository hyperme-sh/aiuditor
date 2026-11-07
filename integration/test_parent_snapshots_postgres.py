"""Atomic deletion ownership, fresh SQL sessions, replay ordering and tenant RLS."""
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from aiuditor_service.domain.models import Connection, ConnectionKind, ConnectionPurpose, ConnectionStatus
from aiuditor_service.parent import apply_hydrate
from aiuditor_service.persist import SqlPersistSession
from aiuditor_service.store import new_stores
from test_parent_reconciliation import T0, policy, snapshot

pytest_plugins = ['machine_postgres']
pytestmark = pytest.mark.integration


def seed(url, *tenants):
    engine = create_engine(url)
    with engine.begin() as cx:
        for tenant in tenants:
            cx.execute(text('INSERT INTO tenants(id,name,is_active) VALUES(:id,:name,true)'),
                       {'id': tenant, 'name': str(tenant)})
    engine.dispose()


def test_restart_deletes_only_parent_rows_and_metadata_is_rls_scoped(migrated_postgres):
    tenant, other = uuid.uuid4(), uuid.uuid4()
    seed(migrated_postgres, tenant, other)
    persist = SqlPersistSession.from_url(migrated_postgres)
    stores = new_stores(persist)
    local, foreign, remote = policy(tenant), policy(other), policy(tenant)
    stores.policy_store.put(local)
    stores.policy_store.put(foreign)
    apply_hydrate(stores, snapshot(tenant, policies=[remote]))
    apply_hydrate(stores, snapshot(other))
    persist._session_factory.kw['bind'].dispose()
    restarted = SqlPersistSession.from_url(migrated_postgres)
    try:
        recovered = new_stores(restarted)
        apply_hydrate(recovered, snapshot(tenant, tick=1))
        assert recovered.policy_store.list_for(tenant) == [local]
        assert recovered.policy_store.list_for(other) == [foreign]
        engine = restarted._session_factory.kw['bind']
        role = 'parent_reader_' + uuid.uuid4().hex
        with engine.begin() as cx:
            assert cx.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='aiuditor_parent_snapshots'")).one() == (True, True)
            cx.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            try:
                cx.exec_driver_sql(f'GRANT SELECT ON aiuditor_parent_snapshots TO "{role}"')
                cx.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                for value, expected in [('', []), (str(tenant), [tenant]), (str(other), [other])]:
                    cx.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"), {'tid': value})
                    assert cx.execute(text('SELECT tenant_id FROM aiuditor_parent_snapshots')).scalars().all() == expected
            finally:
                cx.exec_driver_sql('RESET ROLE')
                cx.exec_driver_sql(f'DROP OWNED BY "{role}"')
                cx.exec_driver_sql(f'DROP ROLE "{role}"')
    finally:
        restarted._session_factory.kw['bind'].dispose()


def test_sql_collision_rolls_back_earlier_deletes_and_receipt(migrated_postgres):
    tenant = uuid.uuid4()
    seed(migrated_postgres, tenant)
    persist = SqlPersistSession.from_url(migrated_postgres)
    try:
        stores = new_stores(persist)
        remote = policy(tenant)
        connection = Connection(id=uuid.uuid4(), tenant_id=tenant, kind=ConnectionKind.HOST,
            purpose=ConnectionPurpose.LEASE, device_id=uuid.uuid4(), status=ConnectionStatus.READY,
            created_at=T0)
        first = snapshot(tenant, policies=[remote]).model_copy(update={
            'connections': [connection.model_dump(mode='json')]})
        apply_hydrate(stores, first)
        local = policy(tenant)
        stores.policy_store.put(local)
        with pytest.raises(HTTPException) as conflict:
            apply_hydrate(stores, snapshot(tenant, tick=1, policies=[local]))
        assert conflict.value.status_code == 409
        assert stores.connection_store.get(tenant, connection.id) == connection
        assert {r.id for r in stores.policy_store.list_for(tenant)} == {remote.id, local.id}
        # The failed transaction cannot consume the next snapshot timestamp.
        apply_hydrate(stores, snapshot(tenant, tick=1))
        assert stores.connection_store.get(tenant, connection.id) is None
        assert stores.policy_store.list_for(tenant) == [local]
    finally:
        persist._session_factory.kw['bind'].dispose()


def test_concurrent_receipts_cannot_reintroduce_stale_allow(migrated_postgres):
    tenant = uuid.uuid4()
    seed(migrated_postgres, tenant)
    remote = policy(tenant)
    first = snapshot(tenant, policies=[remote])
    older, newest = snapshot(tenant, tick=1, policies=[remote]), snapshot(tenant, tick=2)
    def apply(item):
        persist = SqlPersistSession.from_url(migrated_postgres)
        try:
            try:
                apply_hydrate(new_stores(persist), item)
                return 200
            except HTTPException as error:
                assert error.status_code == 409
                return 409
        finally:
            persist._session_factory.kw['bind'].dispose()
    assert apply(first) == 200
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(apply, [older, newest]))
    assert 200 in results
    assert apply(newest) == 200
    assert apply(older) == 409
    persist = SqlPersistSession.from_url(migrated_postgres)
    try:
        assert new_stores(persist).policy_store.list_for(tenant) == []
    finally:
        persist._session_factory.kw['bind'].dispose()


def test_absent_row_still_cannot_be_claimed_by_another_parent(migrated_postgres):
    tenant = uuid.uuid4()
    seed(migrated_postgres, tenant)
    persist = SqlPersistSession.from_url(migrated_postgres)
    try:
        stores = new_stores(persist)
        remote = policy(tenant)
        apply_hydrate(stores, snapshot(tenant, policies=[remote]), parent_id='original')
        stores.policy_store.delete(tenant, remote.id)
        with pytest.raises(HTTPException) as conflict:
            apply_hydrate(stores, snapshot(tenant, tick=1, policies=[remote]), parent_id='replacement')
        assert conflict.value.status_code == 409
        assert stores.policy_store.list_for(tenant) == []
        apply_hydrate(stores, snapshot(tenant, tick=1), parent_id='original')
        apply_hydrate(stores, snapshot(tenant, tick=2, policies=[remote]), parent_id='replacement')
        assert stores.policy_store.list_for(tenant) == [remote]
    finally:
        persist._session_factory.kw['bind'].dispose()
