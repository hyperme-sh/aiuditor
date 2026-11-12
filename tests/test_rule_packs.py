import hashlib
import json
import uuid
from pathlib import Path
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from aiuditor_service.rules import RulePack, constraints, public_pack
from aiuditor_service.rules.learning import Feedback, propose, activate


def feedback(tenant, *, validation=10, reward=1, action='hold'):
    rows=[]
    for split,count in [('train',4),('validation',validation)]:
        for i in range(count):
            rows.append(Feedback(objective='audit_safety_rbac',review_id=uuid.uuid4(),tenant_id=tenant,
                source_sha256=hashlib.sha256(f'{split}:{i}'.encode()).hexdigest(),
                source='dream' if split=='train' else 'prompt_log',
                purpose='service',prompt_class='private_operation',action=action,
                reward=reward,propensity=1,split=split))
    return rows


def test_public_pack_contains_no_tenant_or_free_text():
    pack=public_pack()
    assert pack.audience=='public' and pack.tenant_id is None
    assert constraints(uuid.uuid4(),purpose='service',prompt_class='authorization_bypass')=={'deny'}


def test_private_rules_are_scoped_additive_and_configuration_failure_is_closed(tmp_path,monkeypatch):
    tenant=uuid.uuid4()
    monkeypatch.setenv('AIUDITOR_PRIVATE_RULES_DIR',str(tmp_path))
    with pytest.raises(HTTPException) as absent:
        constraints(tenant,purpose='service',prompt_class='anything')
    assert absent.value.status_code==503
    pack=RulePack(audience='private',tenant_id=tenant,revision=1,rules=[
        dict(id='local-business',prompt_class='private_operation',action='hold')])
    (tmp_path/f'{tenant}.json').write_text(pack.model_dump_json())
    assert constraints(tenant,purpose='service',prompt_class='private_operation')=={'hold'}
    assert constraints(tenant,purpose='service',prompt_class='authorization_bypass')=={'deny'}
    (tmp_path/f'{tenant}.json').write_text(pack.model_copy(update={'tenant_id':uuid.uuid4()}).model_dump_json())
    with pytest.raises(HTTPException): constraints(tenant,purpose='service',prompt_class='anything')


def test_feedback_cannot_carry_bodies_or_rewards_outside_the_contract():
    row=feedback(uuid.uuid4())[0].model_dump()
    for change in [dict(prompt='private text'),dict(reward=float('nan')),dict(propensity=0),dict(action='allow'),dict(action='local_only'),dict(objective='routing_cost'),dict(objective='market_pricing')]:
        with pytest.raises(ValidationError): Feedback.model_validate(dict(row,**change))


def test_proposals_are_inert_and_activation_is_additive_and_idempotent(tmp_path):
    tenant=uuid.uuid4(); rows=feedback(tenant)
    proposal=propose(rows,tenant)
    assert proposal['eligible'] and not list(tmp_path.iterdir())
    pack=activate(rows,tenant,tmp_path)
    assert pack.audience=='private' and pack.revision==1
    assert activate(rows,tenant,tmp_path)==pack
    path=tmp_path/f'{tenant}.json'
    assert path.stat().st_mode & 0o077 == 0
    before=path.read_bytes()
    with pytest.raises(ValueError,match='replace'):
        activate(feedback(tenant,action='deny'),tenant,tmp_path)
    assert path.read_bytes()==before


@pytest.mark.parametrize('bad',['dream-validation','duplicate','foreign','negative','sparse'])
def test_learning_refuses_contamination_and_weak_validation(tmp_path,bad):
    tenant=uuid.uuid4();rows=feedback(tenant)
    if bad=='dream-validation': rows[-1]=rows[-1].model_copy(update={'source':'dream'})
    if bad=='duplicate': rows[-1]=rows[0]
    if bad=='foreign': rows[-1]=rows[-1].model_copy(update={'tenant_id':uuid.uuid4()})
    if bad=='negative': rows=feedback(tenant,reward=-1)
    if bad=='sparse': rows=feedback(tenant,validation=2)
    with pytest.raises(ValueError): activate(rows,tenant,tmp_path)
    assert not (tmp_path/f'{tenant}.json').exists()


def test_runtime_pack_constraints_intersect_tenant_policy(tmp_path,monkeypatch):
    from test_routing_review import policy, envelope
    from aiuditor_service.routing_review import review
    store,tenant,_=policy({'allow_external_reroute':True})
    pack=RulePack(audience='private',tenant_id=tenant,revision=1,rules=[
        dict(id='private-local',prompt_class='parse',action='hold')])
    (tmp_path/f'{tenant}.json').write_text(pack.model_dump_json())
    monkeypatch.setenv('AIUDITOR_PRIVATE_RULES_DIR',str(tmp_path))
    verdict=review(store,tenant,envelope())
    assert verdict.action=='deny' and verdict.route is None
    pack=pack.model_copy(update={'rules':[]})
    (tmp_path/f'{tenant}.json').write_text(pack.model_dump_json())
    env=envelope();env.routing.job_class='authorization_bypass'
    assert review(store,tenant,env).action=='deny'


def test_low_effective_sample_size_cannot_validate(tmp_path):
    tenant=uuid.uuid4();rows=feedback(tenant)
    rows[-1]=rows[-1].model_copy(update={'propensity':0.01})
    assert not propose(rows,tenant)['eligible']
    with pytest.raises(ValueError): activate(rows,tenant,tmp_path)
