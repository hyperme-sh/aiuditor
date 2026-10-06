import uuid
import httpx
import pytest
from aiuditor_service.crawl import ArtifactMeta, StaticMetadataSource, HuggingFaceMetadataSource
from aiuditor_service.intake import run_intake
from aiuditor_service.store import CandidateStore, EnvelopeStore

@pytest.mark.asyncio
async def test_daily_discovery_never_requests_downloads():
    class Plane:
        async def queue_lab_soak(self, *args, **kwargs):
            pytest.fail("discovery scheduled a download")
    report = await run_intake(
        tenant_id=uuid.uuid4(), candidates=CandidateStore(), envelopes=EnvelopeStore(), lab_hop=Plane(),
        source=StaticMetadataSource([ArtifactMeta(repo="bartowski/model", revision="a"*40,
            file_name="model-Q4_K_M.gguf", size_bytes=4*2**30, license="mit", quant="Q4_K_M",
            file_hash="sha256:"+"b"*64, publisher="bartowski")]),
    )
    assert report.stored > 0
    assert report.queued == 0
    assert report.errors == ()

@pytest.mark.asyncio
async def test_crawl_resolves_branch_before_listing_artifact():
    commit = "a" * 40
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if "/revision/" in request.url.path:
            return httpx.Response(200, json={"sha": commit})
        if "/tree/" in request.url.path:
            assert request.url.path.endswith(commit)
            return httpx.Response(200, json=[{"type":"file", "path":"model-Q4_K_M.gguf", "size":123,
                "lfs":{"oid":"b"*64}}])
        return httpx.Response(200, json={"cardData":{"license":"mit"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await HuggingFaceMetadataSource(["bartowski/model"], client=client).list_artifacts()
    assert rows[0].revision == commit
    assert rows[0].source_ref == f"bartowski/model@{commit}/model-Q4_K_M.gguf"
    assert all("/resolve/" not in path for path in calls)
