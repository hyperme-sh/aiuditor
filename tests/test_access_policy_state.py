import copy
import uuid
import pytest
from aiuditor_service.access_policy_state import PolicyMutation, changed, effective
from aiuditor_service.access_preview import Policy, evaluate
from test_access_preview import AccessPreviewTests


def fixture():
    f=AccessPreviewTests();f.setUp()
    state={'tenant_id':f.ids['tenant_id'],'organization_id':f.ids['organization_id'],
           'revision':str(uuid.uuid4()),'sequence':0,'restrictions':{}}
    return f,state


def mutation(state, mode, scope='personal', **extra):
    return PolicyMutation(organization_id=state['organization_id'],expected_revision=state['revision'],
        scope=scope,restriction={'operation':'read','target':'/project/file','mode':mode,'reason':'Owner choice',**extra})


def result(f,state,principal='owner'):
    fact={**f.fact,'principal':principal}
    return evaluate(f.body,fact,effective(Policy.model_validate(f.policy_data),state,principal),now=100)


def test_personal_changes_cannot_relax_organization_deny():
    f,s=fixture();s,_=changed(s,mutation(s,'deny','organization'),'admin',admin=True)
    s,_=changed(s,mutation(s,'inherited'),'owner',admin=False)
    assert not result(f,s)['permitted']
    assert not result(f,s,'another')['permitted']
    with pytest.raises(PermissionError):changed(s,mutation(s,'inherited','organization'),'owner',admin=False)


def test_per_use_is_automatic_fresh_verification_not_human_prompt():
    f,s=fixture();s,_=changed(s,mutation(s,'per_use'),'owner',admin=False)
    r=result(f,s);assert r['permitted'] and not r['learning_eligible']
    assert result(f,s,'another')['learning_eligible']


def test_limits_never_broaden_and_unmatched_restrictions_never_allow():
    f,s=fixture();s,_=changed(s,mutation(s,'limited',limits={'max_bytes':32,'timeout_seconds':1}),'owner',admin=False)
    assert not result(f,s)['permitted']
    f.body.action.limits.max_bytes=32;f.body.action.limits.timeout_seconds=1
    assert result(f,s)['permitted']
    f.policy_data['organizations'][0]['rules']=[]
    assert not result(f,s)['permitted']


def test_stale_concurrent_edit_cross_org_and_restart_tombstone():
    f,s=fixture();stale=mutation(s,'per_use');s,event=changed(s,mutation(s,'deny'),'owner',admin=False)
    assert event['sequence']==1 and event['previous_revision']!=event['revision']
    with pytest.raises(ValueError):changed(s,stale,'owner',admin=False)
    wrong=mutation(s,'inherited');wrong.organization_id=uuid.uuid4()
    with pytest.raises(ValueError):changed(s,wrong,'owner',admin=False)
    s,event=changed(s,mutation(s,'inherited'),'owner',admin=False)
    restored=copy.deepcopy(s)
    assert len(restored['restrictions'])==1 and next(iter(restored['restrictions'].values()))['mode']=='inherited'
    assert restored['sequence']==2 and result(f,restored)['permitted']
    with pytest.raises(ValueError):changed(restored,stale,'owner',admin=False)


def test_personal_capacity_cannot_exhaust_organization_revocation_capacity():
    f,s=fixture()
    for i in range(4096):
        s['restrictions'][str(i)]={'subject':f'member-{i//128}','operation':'read','target':f'/project/{i}','mode':'inherited','reason':'Prior edit','limits':None}
    s,_=changed(s,mutation(s,'deny','organization'),'admin',admin=True)
    assert not result(f,s)['permitted']
    with pytest.raises(ValueError):changed(s,mutation(s,'deny'),'new-member',admin=False)
