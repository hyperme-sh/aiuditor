import unittest
from unittest.mock import AsyncMock,patch
from fastapi import Request,Response,HTTPException
from aiuditor_service import access_routes as routes
from aiuditor_service.access_preview import Policy
import test_access_preview as fixtures


class AccessRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.AccessPreviewTests();self.f.setUp()
    def request(self,token='hm_msi_verified',tenant=None):
        return Request({'type':'http','headers':[(b'authorization',('Bearer '+token).encode()),(b'x-tenant-id',(tenant or self.f.ids['tenant_id']).encode())]})
    async def test_owner_response_without_mutation(self):
        with patch.object(routes,'machine_context',AsyncMock(return_value=self.f.fact)) as context,patch.object(routes,'policy_decision',side_effect=current_policy_fixture),patch.object(routes,'load_policy',return_value=Policy.model_validate(self.f.policy_data)),patch('aiuditor_service.access_preview.time.time',return_value=100):
            response=Response();result=await routes.preview(self.f.body,self.request(),response)
            self.assertTrue(result['preview_only']);self.assertEqual(result['decision'],'allowed')
            self.assertEqual(response.headers['cache-control'],'no-store');context.assert_awaited_once()
    async def test_wrong_tenant_and_non_machine_cannot_reach_owner(self):
        with patch.object(routes,'machine_context',AsyncMock()) as context:
            for request in (self.request(token='renter'),self.request(tenant='other')):
                with self.assertRaises(HTTPException):await routes.preview(self.f.body,request,Response())
            context.assert_not_awaited()
    async def test_owner_failure_has_bounded_public_error(self):
        with patch.object(routes,'machine_context',AsyncMock(side_effect=ValueError('upstream private material'))):
            with self.assertRaises(HTTPException) as caught:await routes.preview(self.f.body,self.request(),Response())
            self.assertEqual(caught.exception.status_code,503)
            self.assertEqual(caught.exception.detail,'Permission preview unavailable')


async def current_policy_fixture(body,fact,evaluator,**kwargs):
    return evaluator(body,fact,routes.load_policy())
