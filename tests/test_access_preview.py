import unittest
from uuid import uuid4
from pydantic import ValidationError
from aiuditor_service.access_preview import Preview,Policy,evaluate


class AccessPreviewTests(unittest.TestCase):
    def setUp(self):
        self.ids={k:str(uuid4()) for k in ('request_id','tenant_id','device_id','bot_id','assignment_id','session_id','organization_id','owner_id')}
        self.limits={'max_bytes':1024,'timeout_seconds':10}
        self.rule={'operation':'read','target':'/project/file','limits':self.limits}
        self.body=Preview.model_validate({'version':1,**{k:v for k,v in self.ids.items() if k not in ('organization_id','owner_id')},'project':'/project','template_version':1,'template':[self.rule],'action':{**self.rule,'arguments_sha256':'a'*64}})
        self.fact={'version':1,**self.ids,'principal':'owner','expires_at':110}
        self.policy_data={'version':1,'revision':str(uuid4()),'organizations':[{'tenant_id':self.ids['tenant_id'],'organization_id':self.ids['organization_id'],'rules':[{**self.rule,'effect':'allow','reason':'Project file read','no_always_allow':False,'category':'ordinary'}]}]}
    def result(self):return evaluate(self.body,self.fact,Policy.model_validate(self.policy_data),now=100)
    def test_owner_allow_is_preview_only_and_bounded(self):
        result=self.result()
        self.assertTrue(result['preview_only']);self.assertTrue(result['permitted'])
        self.assertEqual(result['decision'],'allowed');self.assertTrue(result['learning_eligible'])
        self.assertNotIn('grant',result);self.assertEqual(result['expires_at'],110)
    def test_advisory_does_not_deny_and_no_always_allow_wins(self):
        rule=self.policy_data['organizations'][0]['rules'][0]
        rule.update(effect='advisory',no_always_allow=True)
        result=self.result();self.assertEqual(result['decision'],'advisory')
        self.assertTrue(result['permitted']);self.assertFalse(result['learning_eligible'])
    def test_org_deny_and_extreme_override_personal_allow(self):
        rule=self.policy_data['organizations'][0]['rules'][0]
        rule['effect']='deny';self.assertFalse(self.result()['permitted'])
        rule.update(effect='extreme',category='credential_exfiltration')
        self.assertEqual(self.result()['decision'],'extreme')
        rule['category']='ordinary'
        with self.assertRaises(ValueError):self.result()
    def test_missing_policy_is_unavailable_and_missing_rule_prohibited(self):
        self.policy_data['organizations']=[];self.assertEqual(self.result()['decision'],'unavailable')
        self.setUp();self.policy_data['organizations'][0]['rules']=[]
        self.assertEqual(self.result()['decision'],'prohibited')
    def test_cross_scope_and_expired_identity_refused(self):
        for key in ('tenant_id','device_id','bot_id','assignment_id','session_id'):
            previous=self.fact[key];self.fact[key]=str(uuid4())
            with self.subTest(key=key),self.assertRaises(ValueError):self.result()
            self.fact[key]=previous
        self.fact['expires_at']=100
        with self.assertRaises(ValueError):self.result()
    def test_template_cannot_expand_org_limits(self):
        self.body.action.limits.max_bytes=2048
        self.body.template[0].limits.max_bytes=4096
        result=self.result();self.assertFalse(result['permitted']);self.assertEqual(result['limits']['max_bytes'],1024)
    def test_invalid_targets_versions_and_risk_labels_refused(self):
        raw=self.body.model_dump(mode='json');raw['version']=True
        with self.assertRaises(ValidationError):Preview.model_validate(raw)
        raw=self.body.model_dump(mode='json');raw['action']['risk']='safe'
        with self.assertRaises(ValidationError):Preview.model_validate(raw)
        self.body.action.target='/project/../secret'
        with self.assertRaises(ValueError):self.result()
