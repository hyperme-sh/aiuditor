import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch
from fastapi import Response, HTTPException
from aiuditor_service.access_preview import ReadReview, Policy, evaluate_read
from aiuditor_service import access_routes as routes
import test_access_preview as preview_fixture
import test_access_routes as route_fixture


class ReadPolicyTests(unittest.TestCase):
    def setUp(self):
        fixture=preview_fixture.AccessPreviewTests();fixture.setUp()
        self.fixture=fixture
        self.body=ReadReview.model_validate(fixture.body.model_dump())
    def result(self):
        return evaluate_read(self.body,self.fixture.fact,Policy.model_validate(self.fixture.policy_data),now=100)
    def test_exact_read_and_current_revision(self):
        result=self.result()
        self.assertTrue(result['permitted'])
        self.assertFalse(result['preview_only'])
        self.body.expected_policy_revision=uuid4()
        self.assertFalse(self.result()['permitted'])
        self.body.expected_policy_revision=Policy.model_validate(self.fixture.policy_data).revision
        self.assertTrue(self.result()['permitted'])
    def test_project_and_operation_cannot_expand(self):
        for operation,target in [('read','/project-other/file'),('read','/project'),('read','/project//file'),('read','/project/../file'),('write','/project/file'),('https','https://example.test/')]:
            self.body.action.operation=operation;self.body.action.target=target
            with self.subTest(target=target),self.assertRaises(ValueError):self.result()
    def test_advisory_learns_but_prohibition_and_no_always_allow_win(self):
        rule=self.fixture.policy_data['organizations'][0]['rules'][0]
        rule['effect']='advisory'
        self.assertTrue(self.result()['permitted']);self.assertTrue(self.result()['learning_eligible'])
        rule['no_always_allow']=True
        self.assertTrue(self.result()['permitted']);self.assertFalse(self.result()['learning_eligible'])
        rule['effect']='deny'
        self.assertFalse(self.result()['permitted'])
        rule.update(effect='extreme',category='credential_exfiltration')
        self.assertFalse(self.result()['permitted'])


class ReadRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=route_fixture.AccessRouteTests();self.f.setUp()
        self.body=ReadReview.model_validate(self.f.f.body.model_dump())
    async def test_active_owner_context_required_and_policy_reloaded(self):
        with patch.object(routes,'machine_context',AsyncMock(return_value=self.f.f.fact)) as context,patch.object(routes,'load_policy',return_value=Policy.model_validate(self.f.f.policy_data)) as policy,patch('aiuditor_service.access_preview.time.time',return_value=100):
            response=Response()
            self.assertTrue((await routes.read_review(self.body,self.f.request(),response))['permitted'])
            context.assert_awaited_once_with(self.body,'hm_msi_verified',active=True)
            self.assertEqual(response.headers['cache-control'],'no-store')
            changed=Policy.model_validate(self.f.f.policy_data)
            changed.organizations[0].rules[0].effect='deny'
            policy.return_value=changed
            self.assertFalse((await routes.read_review(self.body,self.f.request(),Response()))['permitted'])
            self.assertEqual(policy.call_count,2)
    async def test_auth_and_upstream_failure_refuse(self):
        with patch.object(routes,'machine_context',AsyncMock(side_effect=ValueError('private upstream'))) as context:
            for request in (self.f.request(token='renter'),self.f.request(tenant='other')):
                with self.assertRaises(HTTPException):await routes.read_review(self.body,request,Response())
            context.assert_not_awaited()
            with self.assertRaises(HTTPException) as caught:
                await routes.read_review(self.body,self.f.request(),Response())
            self.assertEqual(caught.exception.detail,'Permission read review unavailable')
