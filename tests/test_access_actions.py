import hashlib
import json
import unittest
from uuid import uuid4
from aiuditor_service.access_actions import ActionReview,evaluate_action
from aiuditor_service.access_preview import Policy
import test_access_preview as fixtures


class TypedActionsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AccessPreviewTests();self.f.setUp()
    def body(self,operation,target,args):
        raw=self.f.body.model_dump(mode='json')
        encoded=json.dumps(args,separators=(',',':'))
        raw['action'].update(operation=operation,target=target,arguments_json=encoded,arguments_sha256=hashlib.sha256(encoded.encode()).hexdigest())
        raw['template'][0].update(operation=operation,target=target)
        self.f.policy_data['organizations'][0]['rules'][0].update(operation=operation,target=target)
        return ActionReview.model_validate(raw)
    def review(self,body):return evaluate_action(body,self.f.fact,Policy.model_validate(self.f.policy_data),now=100)
    def network(self):
        body=self.body('https','https://example.test/api',dict(method='GET',addresses=['8.8.8.8'],body_sha256=hashlib.sha256(b'').hexdigest(),body_bytes=0))
        self.f.policy_data['organizations'][0]['rules'][0]['method']='GET'
        return body
    def test_network_method_addresses_and_digest_are_bound(self):
        body=self.network();self.assertTrue(self.review(body)['permitted'])
        self.f.policy_data['organizations'][0]['rules'][0]['method']='POST'
        self.assertFalse(self.review(body)['permitted'])
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:8.8.8.8','224.0.0.1']:
            body=self.body('https','https://example.test/api',dict(method='GET',addresses=[address],body_sha256=hashlib.sha256(b'').hexdigest(),body_bytes=0))
            with self.subTest(address=address),self.assertRaises(ValueError):self.review(body)
        body=self.network();body.action.arguments_json+=' '
        with self.assertRaises(ValueError):self.review(body)
    def test_write_binds_content_and_project_and_limits(self):
        args=dict(content_sha256='a'*64,content_bytes=12,expected_object_sha256='b'*64,parent_object_sha256='c'*64)
        self.assertTrue(self.review(self.body('write','/project/file',args))['permitted'])
        for target in ['/outside/file','/project/../file','//file']:
            with self.subTest(target=target),self.assertRaises(ValueError):self.review(self.body('write',target,args))
        args['content_bytes']=2048
        with self.assertRaises(ValueError):self.review(self.body('write','/project/file',args))
    def test_named_service_requires_explicit_executable_policy(self):
        body=self.body('service_restart','example.service',dict(verb='restart',executable='/usr/bin/systemctl',executable_sha256='a'*64,unit='example.service',uid=1000,controller_binding_sha256='b'*64))
        self.assertFalse(self.review(body)['permitted'])
        self.f.policy_data['organizations'][0]['rules'][0]['executable_sha256']='a'*64
        self.assertTrue(self.review(body)['permitted'])
        body.action.target='other.service'
        with self.assertRaises(ValueError):self.review(body)
    def test_advisory_deny_and_revision_still_govern(self):
        body=self.network();rule=self.f.policy_data['organizations'][0]['rules'][0]
        rule['effect']='advisory';self.assertTrue(self.review(body)['permitted'])
        rule['no_always_allow']=True;self.assertFalse(self.review(body)['learning_eligible'])
        rule['effect']='deny';self.assertFalse(self.review(body)['permitted'])
        rule['effect']='allow';body.expected_policy_revision=uuid4();self.assertFalse(self.review(body)['permitted'])
    def test_read_or_unknown_parameters_cannot_authorize_typed_actions(self):
        body=self.network();body.action.operation='read';body.action.target='/project/file'
        with self.assertRaises(ValueError):self.review(body)
        body=self.network();raw=json.loads(body.action.arguments_json);raw['headers']={'Authorization':'secret'}
        body.action.arguments_json=json.dumps(raw);body.action.arguments_sha256=hashlib.sha256(body.action.arguments_json.encode()).hexdigest()
        with self.assertRaises(ValueError):self.review(body)
