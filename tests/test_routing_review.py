import json
import logging
import uuid
import pytest
from pydantic import ValidationError
from aiuditor_service.domain.models import ApprovalPolicyCreate
from aiuditor_service.routing_contracts import ReviewEnvelope, RouteConstraint
from aiuditor_service.routing_review import review, matches
from aiuditor_service.policies import put_policy
from aiuditor_service.store import PolicyStore
from aiuditor_service.persist import sql_row, dump_for_persist, row_from_payload

LOCAL = dict(tier='cheap', model_id='local-small', provider='local-engine', locality='local')
REMOTE = dict(tier='frontier', model_id='remote-large', provider='vendor', locality='remote')
MESH = dict(tier='mesh', model_id='mesh-model', provider='hypermesh', locality='remote')


def envelope(refusal=False, candidates=None):
    return ReviewEnvelope.model_validate(dict(request_id='request-123',
        event='upstream_refusal' if refusal else 'review',
        request=dict(protocol='openai.chat', method='POST', path='/v1/chat/completions',
                     body={'messages':[{'role':'user','content':'private prompt'}]}),
        routing=REMOTE | {'job_class':'parse'},
        candidates=candidates if candidates is not None else ([LOCAL, MESH] if refusal else [REMOTE, LOCAL, MESH]),
        refusal={'model':REMOTE, 'reason':'policy refusal'} if refusal else None))


def policy(rules=None, disposition='allow'):
    store, tenant = PolicyStore(), uuid.uuid4()
    row = put_policy(store, tenant, ApprovalPolicyCreate(purpose='renter', prompt_class='parse',
        disposition=disposition, routing_policy=rules))
    return store, tenant, row


def test_conductor_reference_changes_choice_with_provider_constraint(caplog):
    store, tenant, _ = policy({'route':{'deny_models':[{'provider':'vendor'}]}})
    env = envelope()
    with caplog.at_level(logging.INFO):
        verdict = review(store, tenant, env)
    # Reference consumer preserves candidate order and never manufactures a model.
    chosen = next(c for c in env.candidates if matches(c, verdict.route.allow_models))
    assert chosen.model_id == 'local-small'
    assert verdict.action == 'allow'
    assert 'private prompt' not in caplog.text
    record = json.loads(caplog.records[-1].message.split(' ', 1)[1])
    assert record['request_id'] == env.request_id and len(record['request_sha256']) == 64


@pytest.mark.parametrize('disposition', ['deny','hold'])
def test_refusal_never_overrides_hold_or_deny(disposition):
    store, tenant, _ = policy(disposition=disposition)
    assert review(store, tenant, envelope(True)).action == 'deny'


def test_default_reroute_stays_local_or_hypermesh():
    store, tenant, _ = policy()
    other = REMOTE | {'model_id':'other-vendor-model'}
    result = review(store, tenant, envelope(True, [other, MESH, LOCAL]))
    assert result.allowed_models == ['mesh-model','local-small']
    assert result.action == 'reroute'
    assert review(store, tenant, envelope(True, [other])).action == 'deny'


def test_external_reroute_requires_explicit_permission_and_respects_avoidance():
    store, tenant, _ = policy({'allow_external_reroute':True, 'avoid_providers':['vendor']})
    other = REMOTE | {'model_id':'other-vendor-model'}
    assert review(store, tenant, envelope(True, [other])).action == 'deny'
    store, tenant, _ = policy({'allow_external_reroute':True})
    assert review(store, tenant, envelope(True, [other])).allowed_models == ['other-vendor-model']


def test_no_matching_policy_and_foreign_tenant_deny():
    store, tenant, _ = policy()
    assert review(store, uuid.uuid4(), envelope()).action == 'deny'
    assert review(PolicyStore(), tenant, envelope()).action == 'deny'


def test_policy_survives_sql_projection_and_memory_restart():
    store, tenant, row = policy({'route':{'allow_models':[{'model_id':'local-small'}]}})
    rebuilt = row_from_payload('policies', sql_row('policies', dump_for_persist(row)))
    assert rebuilt.routing_policy == row.routing_policy
    restarted = PolicyStore(persist=store.persist)
    assert review(restarted, tenant, envelope()).route.allow_models[0].model_id == 'local-small'


@pytest.mark.parametrize('constraint', [{}, {'allow_models':[], 'deny_models':[]},
    {'allow_models':[{}]}, {'deny_models':[{'model_id':'a','provider':'b'}]}])
def test_ambiguous_constraints_refused(constraint):
    with pytest.raises(ValidationError): RouteConstraint.model_validate(constraint)


def test_empty_allow_list_denies_all():
    store, tenant, _ = policy({'route':{'allow_models':[]}})
    assert review(store, tenant, envelope()).action == 'deny'


def test_refusal_rejects_repeated_or_misattributed_candidate():
    with pytest.raises(ValidationError): envelope(True, [REMOTE])
    with pytest.raises(ValidationError): envelope(False, [LOCAL])


async def test_http_review_requires_internal_credential(http_client, tenant_a, monkeypatch):
    monkeypatch.setenv('AIUDITOR_CONDUCTOR_TOKEN', 'r'*32)
    monkeypatch.setenv('AIUDITOR_CONDUCTOR_TENANT_ID', str(tenant_a))
    body=envelope().model_dump(mode='json')
    response=await http_client.post('/api/v1/aiuditor/review', json=body, headers={'X-Tenant-ID':str(tenant_a)})
    assert response.status_code == 401
    response=await http_client.post('/api/v1/aiuditor/review', json=body,
        headers={'X-Tenant-ID':str(tenant_a),'X-Internal-Token':'r'*32})
    assert response.status_code == 200 and response.json()['action'] == 'deny'

    response=await http_client.post('/api/v1/aiuditor/review', json=body,
        headers={'X-Tenant-ID':str(uuid.uuid4()),'X-Internal-Token':'r'*32})
    assert response.status_code == 403


def test_absent_routing_policy_binds_as_sql_null():
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
    from aiuditor_service.models import AiuditorPolicy
    dialect = PGDialect_psycopg(dbapi=__import__("psycopg"))
    kind = AiuditorPolicy.__table__.c.routing_policy.type.dialect_impl(dialect)
    bind = kind.bind_processor(dialect)
    assert bind(None) is None
    assert bind({'allow_external_reroute':False}).obj == {'allow_external_reroute':False}
