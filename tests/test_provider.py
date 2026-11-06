from __future__ import annotations

import httpx
import pytest

from aiuditor_service.provider import (
    PROVIDER_ENDPOINT_INVALID,
    PROVIDER_HOP_FAILED,
    FakeProviderHop,
    HttpProviderHop,
    ProviderUnavailable,
    _completions_url,
    resolve_provider_hop,
)


def test_completions_url_appends_and_rejects_http():
    assert (
        _completions_url("https://llm.example/v1")
        == "https://llm.example/v1/chat/completions"
    )
    assert (
        _completions_url("https://llm.example/v1/chat/completions")
        == "https://llm.example/v1/chat/completions"
    )
    with pytest.raises(ProviderUnavailable) as exc:
        _completions_url("http://llm.example/v1")
    assert str(exc.value) == PROVIDER_ENDPOINT_INVALID


def test_resolve_provider_defaults_to_http():
    assert isinstance(resolve_provider_hop(), HttpProviderHop)


@pytest.mark.asyncio
async def test_fake_provider_can_fail():
    hop = FakeProviderHop(error=PROVIDER_HOP_FAILED)
    with pytest.raises(ProviderUnavailable):
        await hop.complete(
            "https://llm.example/v1",
            messages=[],
            model=None,
            authorization=None,
        )


@pytest.mark.asyncio
async def test_http_provider_posts_without_operator_jwt(monkeypatch, public_provider):
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None, extensions=None):
            captured["url"] = str(url)
            captured["extensions"] = extensions
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    status = await HttpProviderHop().complete(
        "https://llm.example/v1",
        messages=[{"role": "user", "content": "secret"}],
        model="test",
        authorization="Bearer one-shot",
    )
    assert status == 200
    assert captured["url"] == "https://93.184.216.34/v1/chat/completions"
    assert captured["extensions"] == {"sni_hostname": "llm.example"}
    assert captured["headers"] == {"Authorization": "Bearer one-shot", "Host": "llm.example"}
    assert captured["json"]["model"] == "test"


@pytest.mark.asyncio
async def test_http_provider_network_fail_closes(monkeypatch, public_provider):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None, extensions=None):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(ProviderUnavailable) as exc:
        await HttpProviderHop().complete(
            "https://llm.example/v1",
            messages=[],
            model=None,
            authorization=None,
        )
    assert str(exc.value) == PROVIDER_HOP_FAILED


@pytest.fixture
def public_provider(monkeypatch):
    from aiuditor_service import provider
    monkeypatch.setenv("AIUDITOR_PROVIDER_ALLOWED_HOSTS", "llm.example")
    async def resolve(host, timeout_s):
        assert host == "llm.example"
        return "93.184.216.34"
    monkeypatch.setattr(provider, "_resolve_public_address", resolve)


@pytest.mark.parametrize("endpoint", [
    "https://llm.example?x=1", "https://llm.example/#fragment",
    "https://llm.example?", "https://llm.example#", "https://user@llm.example/v1",
    "https://@llm.example/v1", "https://llm.example:8443/v1",
    "https://llm.example/v1%3fquery", "https://llm.example\\evil/v1",
    "https://llm.example\n/v1", " https://llm.example/v1",
    "https://llm.example./v1", "https://llm..example/v1",
    "https://[fe80::1%eth0]/v1", "https://llm.example:bad/v1",
])
def test_endpoint_rejects_authority_and_suffix_ambiguities(endpoint):
    with pytest.raises(ProviderUnavailable):
        _completions_url(endpoint)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", ["", "other.example", "*.example", "llm.example.evil"])
async def test_unapproved_host_never_resolves_or_connects(monkeypatch, configured):
    from aiuditor_service import provider
    monkeypatch.setenv("AIUDITOR_PROVIDER_ALLOWED_HOSTS", configured)
    async def unexpected(*args, **kwargs):
        pytest.fail("disallowed host reached DNS")
    monkeypatch.setattr(provider, "_resolve_public_address", unexpected)
    with pytest.raises(ProviderUnavailable):
        await HttpProviderHop().complete("https://llm.example/v1", messages=[], model=None, authorization=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [
    [], ["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["192.168.1.1"],
    ["100.64.0.1"], ["0.0.0.0"], ["240.0.0.1"], ["224.0.0.1"],
    ["::1"], ["fc00::1"], ["fe80::1"], ["ff02::1"],
    ["::ffff:8.8.8.8"], ["::ffff:127.0.0.1"], ["2002:7f00:1::"],
    ["64:ff9b::7f00:1"], ["93.184.216.34", "127.0.0.1"],
    ["127.0.0.1", "93.184.216.34"], ["not-an-address"],
])
async def test_every_dns_answer_must_be_public_before_client_creation(monkeypatch, addresses):
    import asyncio, socket
    monkeypatch.setenv("AIUDITOR_PROVIDER_ALLOWED_HOSTS", "llm.example")
    async def dns(*args, **kwargs):
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM,
                 socket.IPPROTO_TCP, '', (ip, 443)) for ip in addresses]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    def client(*args, **kwargs):
        pytest.fail("non-public DNS answer reached a client")
    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(ProviderUnavailable):
        await HttpProviderHop().complete("https://llm.example/v1", messages=[], model=None, authorization=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("public_ip", ["93.184.216.34", "2606:4700:4700::1111"])
async def test_pins_one_resolution_preserves_tls_host_and_disables_redirect_proxy(monkeypatch, public_ip):
    import asyncio, socket
    monkeypatch.setenv("AIUDITOR_PROVIDER_ALLOWED_HOSTS", "llm.example")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9999")
    count = 0
    async def dns(host, port, **kwargs):
        nonlocal count
        count += 1
        assert count == 1, "the original DNS name must never be resolved twice"
        return [(socket.AF_INET6 if ':' in public_ip else socket.AF_INET,
                 socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (public_ip, port))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    calls = []
    async def handler(request):
        calls.append(request)
        assert request.url.host == public_ip
        assert request.headers['host'] == 'llm.example'
        assert request.headers['authorization'] == 'Bearer one-shot'
        assert request.extensions['sni_hostname'] == 'llm.example'
        return httpx.Response(307, headers={'Location': 'http://127.0.0.1/private'})
    real_client = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        assert kwargs['verify'] is True
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    status = await HttpProviderHop().complete("https://llm.example/v1", messages=[], model=None, authorization='Bearer one-shot')
    assert status == 307 and count == 1 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("certificate_host", ["llm.example", "wrong.example"])
async def test_pinned_connection_uses_real_tls_hostname_verification(monkeypatch, tmp_path, certificate_host):
    import asyncio, socket, ssl
    from datetime import UTC, datetime, timedelta
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from httpcore._backends.anyio import AnyIOBackend
    from httpx._transports import default as transport

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_host)])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path = tmp_path / 'certificate.pem'
    key_path = tmp_path / 'key.pem'
    cert_path.write_bytes(pem)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server_names = []
    server_context.set_servername_callback(lambda sock, server_name, ctx: server_names.append(server_name))
    requests = []
    async def handle(reader, writer):
        try:
            requests.append(await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(handle, '127.0.0.1', 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv('AIUDITOR_PROVIDER_ALLOWED_HOSTS', 'llm.example')
    loop = asyncio.get_running_loop()
    real_dns = loop.getaddrinfo
    lookups = []
    async def dns(host, requested_port, **kwargs):
        if host == 'llm.example':
            lookups.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', 443))]
        return await real_dns(host, requested_port, **kwargs)
    monkeypatch.setattr(loop, 'getaddrinfo', dns)
    real_connect = AnyIOBackend.connect_tcp
    connections = []
    async def connect(self, host, requested_port=None, **kwargs):
        # Test-only socket routing. Production DNS validation and HTTP/TLS paths
        # run unchanged, and the actual connection target must already be pinned.
        original_port = kwargs.pop('port', requested_port)
        connections.append((host, original_port))
        assert (host, original_port) == ('93.184.216.34', 443)
        return await real_connect(self, host='127.0.0.1', port=port, **kwargs)
    monkeypatch.setattr(AnyIOBackend, 'connect_tcp', connect)
    real_context = transport.create_ssl_context
    def context(*args, **kwargs):
        assert kwargs.get('verify') is True and kwargs.get('trust_env') is False
        ctx = real_context(*args, **kwargs)
        ctx.load_verify_locations(cadata=pem.decode())
        assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
        return ctx
    monkeypatch.setattr(transport, 'create_ssl_context', context)
    async with server:
        hop = HttpProviderHop(timeout_s=3)
        if certificate_host == 'llm.example':
            assert await hop.complete('https://llm.example/v1', messages=[], model=None, authorization=None) == 200
            assert b'Host: llm.example\r\n' in requests[0]
        else:
            with pytest.raises(ProviderUnavailable, match=PROVIDER_HOP_FAILED):
                await hop.complete('https://llm.example/v1', messages=[], model=None, authorization=None)
            assert requests == []
    assert lookups == ['llm.example']
    assert connections == [('93.184.216.34', 443)]
    assert server_names == ['llm.example']


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError, TimeoutError])
async def test_dns_failure_refuses_provider_before_client(monkeypatch, error):
    import asyncio
    monkeypatch.setenv('AIUDITOR_PROVIDER_ALLOWED_HOSTS', 'llm.example')
    async def dns(*args, **kwargs):
        raise error('private resolver diagnostics')
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', dns)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: pytest.fail('failed DNS reached client'))
    with pytest.raises(ProviderUnavailable) as exc:
        await HttpProviderHop().complete('https://llm.example/v1', messages=[], model=None, authorization=None)
    assert 'private resolver diagnostics' not in str(exc.value)
