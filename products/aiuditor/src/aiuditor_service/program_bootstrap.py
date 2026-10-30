"""Provision documented marketplace suite membership, never certification evidence."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from controlplane_tenancy.marketplace import MARKETPLACE_TENANT_ID
from aiuditor_service.domain.models import SuiteMemberCreate
from aiuditor_service.program import CORE_SUITE_SEED, put_suite_member
from aiuditor_service.role import AiuditorRole
from aiuditor_service.store import SuiteStore

logger = logging.getLogger(__name__)


def bootstrap_core_suite(store: SuiteStore, tenant_id: uuid.UUID) -> int:
    """Fill empty default classes; retain an operator's entire existing suite."""
    count = 0
    with store.persist.program_bootstrap_scope(tenant_id):
        for class_id, catalog_id, pack_id in CORE_SUITE_SEED:
            if store.list_for(tenant_id, class_id=class_id):
                continue
            put_suite_member(store, tenant_id, SuiteMemberCreate(
                class_id=class_id, catalog_id=catalog_id, pack_id=pack_id,
            ))
            count += 1
    return count


async def run_program_bootstrap(app, *, stop: asyncio.Event, retry_s: float = 5) -> None:
    """Retry tenant creation races without withholding the service's read APIs."""
    if (app.state.aiuditor_role is not AiuditorRole.GLOBAL
            or os.environ.get("AIUDITOR_BOOTSTRAP_CORE_SUITE", "").lower() != "true"):
        return
    while not stop.is_set():
        try:
            count = await asyncio.to_thread(
                bootstrap_core_suite, SuiteStore(persist=app.state.suite_store.persist),
                MARKETPLACE_TENANT_ID,
            )
            logger.info("core suite initialized tenant=%s created=%s",
                        MARKETPLACE_TENANT_ID, count)
            return
        except Exception as exc:
            # DB errors can include connection details; report only the type.
            logger.warning("core suite initialization pending tenant=%s error=%s",
                           MARKETPLACE_TENANT_ID, type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=retry_s)
        except TimeoutError:
            continue
