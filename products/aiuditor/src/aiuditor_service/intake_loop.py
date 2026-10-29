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

_MAX_LOGGED_ERRORS = 10


def _log_crawl_errors(errors: tuple[str, ...]) -> None:
    """Warning log of crawl failures. Bounded. No headers or tokens."""

    if not errors:
        return
    shown = list(errors[:_MAX_LOGGED_ERRORS])
    rest = len(errors) - len(shown)
    logger.warning(
        "aiuditor crawl errors count=%s rest=%s errors=%s",
        len(errors),
        rest,
        shown,
    )


def _repos_from_env() -> tuple[str, ...]:
    raw = (os.environ.get("AIUDITOR_CRAWL_REPOS") or "").strip()
    if not raw:
        return default_seed_repos()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


async def _finish_lease(lease, error_count):
    """A canceled await must not close a connection still used by its worker."""
    worker = asyncio.create_task(asyncio.to_thread(lease.finish, error_count))
    cancelled = None
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception:
            break
    if cancelled is not None:
        # Retrieve a worker failure, but preserve shutdown cancellation.
        if not worker.cancelled() and worker.exception() is not None:
            logger.warning("aiuditor crawl completion failed during shutdown")
        raise cancelled
    worker.result()


async def run_crawl_loop(app, *, stop: asyncio.Event, sleep=asyncio.sleep) -> None:
    """Re-crawl daily on the global or operator writer. Witness returns immediately.

    The tenant is AIUDITOR_CRAWL_TENANT_ID, or the marketplace tenant the
    hypermesh seed already uses when that variable is unset.
    """

    role = getattr(app.state, "aiuditor_role", None)
    tenant_id = resolve_crawl_tenant()
    if role is AiuditorRole.WITNESS or tenant_id is None:
        return
    from aiuditor_service.crawl_schedule import resolve_crawl_schedule

    schedule = getattr(app.state, "crawl_schedule", None)
    if schedule is None:
        schedule = resolve_crawl_schedule(getattr(app.state, "aiuditor_persist", None))
        app.state.crawl_schedule = schedule
    every = resolve_crawl_every_s()
    while not stop.is_set():
        lease = None
        try:
            acquiring = asyncio.create_task(asyncio.to_thread(schedule.claim_due, tenant_id, every))
            try:
                decision = await asyncio.shield(acquiring)
            except asyncio.CancelledError:
                # Thread cancellation cannot cancel SQL acquisition. Release any
                # claim it obtained so shutdown cannot strand a pooled lock.
                try:
                    decision = await acquiring
                    if decision.lease is not None:
                        await asyncio.shield(asyncio.to_thread(decision.lease.close))
                except Exception:
                    logger.warning("aiuditor crawl acquisition cleanup failed")
                raise
            if decision.lease is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=decision.wait_s)
                except TimeoutError:
                    pass
                continue
            lease = decision.lease
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
            await _finish_lease(lease, len(report.errors))
            _log_crawl_errors(report.errors)
        except Exception as exc:  # noqa: BLE001
            logger.warning("aiuditor crawl failed: %s", exc)
            if lease is not None:
                try:
                    await _finish_lease(lease, 1)
                except Exception:
                    logger.warning("aiuditor crawl completion could not be recorded")
        finally:
            if lease is not None:
                await asyncio.shield(asyncio.to_thread(lease.close))
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
            break
        except TimeoutError:
            continue
        except Exception:
            await sleep(every)
