"""Daily metadata crawl loop. Control-plane only — not a pull on every host."""

from __future__ import annotations

import asyncio
import logging
import os

from aiuditor_service.crawl import (
    HuggingFaceMetadataSource,
    StaticMetadataSource,
    default_seed_repos,
)
from aiuditor_service.intake import resolve_crawl_every_s, resolve_crawl_tenant, run_intake
from aiuditor_service.hypermesh import ClosedPlaneHop
from aiuditor_service.role import AiuditorRole

logger = logging.getLogger(__name__)


def _repos_from_env() -> tuple[str, ...]:
    raw = (os.environ.get("AIUDITOR_CRAWL_REPOS") or "").strip()
    if not raw:
        return default_seed_repos()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


async def run_crawl_loop(app, *, stop: asyncio.Event, sleep=asyncio.sleep) -> None:
    """Re-crawl daily on the global or operator writer. Witness returns immediately.

    The tenant is AIUDITOR_CRAWL_TENANT_ID, or the marketplace tenant the
    hypermesh seed already uses when that variable is unset.
    """

    role = getattr(app.state, "aiuditor_role", None)
    tenant_id = resolve_crawl_tenant()
    if role is AiuditorRole.WITNESS or tenant_id is None:
        return
    every = resolve_crawl_every_s()
    while not stop.is_set():
        try:
            plane = getattr(app.state, "plane_hop", None)
            lab_hop = None if isinstance(plane, ClosedPlaneHop) else plane
            source_name = (os.environ.get("AIUDITOR_CRAWL_SOURCE") or "hf").strip().lower()
            if source_name == "static":
                source = StaticMetadataSource([])
            else:
                source = HuggingFaceMetadataSource(_repos_from_env())
            report = await run_intake(
                tenant_id=tenant_id,
                source=source,
                candidates=app.state.candidate_store,
                envelopes=app.state.envelope_store,
                lab_hop=lab_hop,
            )
            logger.info(
                "aiuditor crawl tenant=%s crawled=%s queued=%s dropped=%s skipped=%s",
                tenant_id,
                report.crawled,
                report.queued,
                report.dropped_no_fit,
                report.skipped_fresh,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("aiuditor crawl failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
            break
        except TimeoutError:
            continue
        except Exception:
            await sleep(every)
