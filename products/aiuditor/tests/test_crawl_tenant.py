"""Daily crawl uses the marketplace tenant. Witness does not crawl."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiuditor_service.hypermesh import ClosedPlaneHop
from aiuditor_service.intake import MARKETPLACE_TENANT_ID, IntakeReport, resolve_crawl_tenant
from aiuditor_service.intake_loop import run_crawl_loop
from aiuditor_service.role import AiuditorRole


def test_unset_tenant_is_the_hypermesh_marketplace_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIUDITOR_CRAWL_TENANT_ID", raising=False)
    assert resolve_crawl_tenant() == MARKETPLACE_TENANT_ID
    assert MARKETPLACE_TENANT_ID == uuid.UUID("8f8e8d8c-7b6a-4a59-8c3d-2e1f0a9b8c7d")


def test_default_matches_hypermesh_seed() -> None:
    src = Path(__file__).resolve().parents[2] / "hypermesh" / "src"
    sys.path.insert(0, str(src))
    from hypermesh_service.seed import MARKETPLACE_TENANT_ID as seeded

    assert seeded == MARKETPLACE_TENANT_ID


def test_invalid_tenant_does_not_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIUDITOR_CRAWL_TENANT_ID", "not-a-uuid")
    assert resolve_crawl_tenant() is None


def _app(role: AiuditorRole) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            aiuditor_role=role,
            plane_hop=ClosedPlaneHop(),
            candidate_store=None,
            envelope_store=None,
        )
    )


@pytest.mark.asyncio
async def test_writer_crawls_the_marketplace_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, uuid.UUID] = {}

    stop = asyncio.Event()

    async def fake_intake(**kwargs):
        seen["tenant"] = kwargs["tenant_id"]
        stop.set()
        return IntakeReport(0, 0, 0, 0, 0)

    monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", fake_intake)
    monkeypatch.delenv("AIUDITOR_CRAWL_TENANT_ID", raising=False)
    await run_crawl_loop(_app(AiuditorRole.GLOBAL), stop=stop)
    assert seen["tenant"] == MARKETPLACE_TENANT_ID
    seen.clear()
    stop.clear()
    await run_crawl_loop(_app(AiuditorRole.OPERATOR), stop=stop)
    assert seen["tenant"] == MARKETPLACE_TENANT_ID


@pytest.mark.asyncio
async def test_witness_does_not_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []

    async def fake_intake(**kwargs):
        called.append(1)
        return IntakeReport(0, 0, 0, 0, 0)

    monkeypatch.setattr("aiuditor_service.intake_loop.run_intake", fake_intake)
    monkeypatch.setenv("AIUDITOR_CRAWL_TENANT_ID", str(MARKETPLACE_TENANT_ID))
    stop = asyncio.Event()
    await run_crawl_loop(_app(AiuditorRole.WITNESS), stop=stop)
    assert called == []
