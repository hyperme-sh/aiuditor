"""The installed event factory writes this owner and retains failed deliveries."""
from datetime import UTC, datetime
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from aiuditor_service.events import POLICY_CREATED_V1
from api.infrastructure.messaging.tenant_event_publisher import resolve_event_publisher
from controlplane_events.publisher import OwnedOutboxPublisher
from controlplane_events.outbox import relay_batch
from aiuditor_service.domain.models import ApprovalPolicy
from machine_postgres import provision_owner
from test_schema_startup_postgres import isolated_owner_db

pytest_plugins = ['machine_postgres']


def test_owned_event_factory_rollback_rls_and_retry(isolated_owner_db):
    url = isolated_owner_db
    provision_owner(url)
    admin = create_engine(url)
    role = 'outbox_' + uuid.uuid4().hex
    tenant = uuid.uuid4()
    different = uuid.uuid4()
    source = None
    try:
        with admin.begin() as db:
            db.execute(text('INSERT INTO public.tenants(id,name,is_active) VALUES(:id,:name,true)'), {'id': tenant, 'name': str(tenant)})
            db.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT'))
            db.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            db.execute(text(f'GRANT SELECT,INSERT,UPDATE,DELETE ON public.service_outbox,public.service_tenants TO "{role}"'))
        assert 'users' not in inspect(admin).get_table_names(schema='public')
        assert 'controlplane_events' not in inspect(admin).get_table_names(schema='public')
        source = create_engine(url, connect_args={'options': '-c role=' + role + ' -c search_path=public'})
        publisher = resolve_event_publisher(service_name='aiuditor', service_bus_connection_string=None, database_url=url)
        assert isinstance(publisher, OwnedOutboxPublisher)
        policy = ApprovalPolicy(id=uuid.uuid4(), tenant_id=tenant, purpose='chat', disposition='hold', created_at=datetime.now(UTC))
        with Session(source) as db:
            db.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"), {'tid': str(tenant)})
            publisher.publish_in_session(db, tenant_id=tenant, event_type=POLICY_CREATED_V1, payload=policy.to_event_payload())
            db.rollback()
        with admin.connect() as db:
            assert db.scalar(text('SELECT count(*) FROM public.service_outbox')) == 0
            assert db.scalar(text('SELECT count(*) FROM public.service_tenants')) == 0
        with Session(source) as db, db.begin():
            db.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"), {'tid': str(tenant)})
            event_id = publisher.publish_in_session(db, tenant_id=tenant, event_type=POLICY_CREATED_V1, payload=policy.to_event_payload())
        with source.begin() as db:
            db.execute(text("SELECT set_config('app.current_tenant_id',:tid,true)"), {'tid': str(different)})
            assert db.scalar(text('SELECT count(*) FROM public.service_outbox')) == 0
        attempts = []
        def delivery(event, envelope):
            attempts.append((event, envelope.model_dump(mode='json')))
            if len(attempts) == 1:
                raise OSError('receiver unavailable')
        with pytest.raises(OSError, match='receiver unavailable'):
            relay_batch(source, tenant, delivery)
        with admin.connect() as db:
            assert db.scalar(text('SELECT delivered_at FROM public.service_outbox WHERE id=:id'), {'id': event_id}) is None
            assert db.execute(text('SELECT tenant_id FROM public.service_tenants')).scalars().all() == [tenant]
        assert relay_batch(source, tenant, delivery) == 1
        assert relay_batch(source, tenant, delivery) == 0
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert attempts[0][0] == event_id
        with admin.connect() as db:
            assert db.scalar(text('SELECT delivered_at IS NOT NULL FROM public.service_outbox WHERE id=:id'), {'id': event_id}) is True
    finally:
        if source is not None:source.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP OWNED BY "{role}"'))
            db.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()
