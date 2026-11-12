import importlib.util
import io
from pathlib import Path
from unittest.mock import patch
import urllib.error
import pytest

spec=importlib.util.spec_from_file_location('public_container',Path(__file__).parents[1]/'scripts/check-public-container.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_uses_anonymous_token_even_when_publisher_credentials_exist(monkeypatch):
    monkeypatch.setenv('GH_TOKEN','publisher-secret')
    requests=[]
    def open_url(request,timeout):
        requests.append(request)
        if len(requests)==1:
            assert isinstance(request,str) and 'scope=repository%3Aexample%2Fservice%2Fruntime%3Apull' in request
            return io.BytesIO(b'{"token":"anonymous-only"}')
        assert request.headers['Authorization']=='Bearer anonymous-only'
        response=io.BytesIO(b'{}');response.status=200
        return response
    with patch.object(module.urllib.request,'urlopen',side_effect=open_url): module.check('Example/Service')
    assert len(requests)==2


@pytest.mark.parametrize('failure_at',[1,2])
def test_private_registry_response_fails(failure_at):
    count=0
    def open_url(request,timeout):
        nonlocal count
        count+=1
        if count==failure_at: raise urllib.error.HTTPError('https://ghcr.io',401,'denied',{},None)
        return io.BytesIO(b'{"token":"anonymous-only"}')
    with patch.object(module.urllib.request,'urlopen',side_effect=open_url):
        with pytest.raises(urllib.error.HTTPError): module.check('example/service')
