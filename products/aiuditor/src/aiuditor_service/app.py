from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.common.exceptions import ForbiddenException, UnauthorizedException
from api.common.logging import configure_logging
from api.common.utils import get_logger
from api.core.config import settings
from api.infrastructure.messaging.tenant_event_publisher import (
    install_tenant_event_publisher,
    set_tenant_event_publisher,
    shutdown_tenant_event_publisher,
)
from api.infrastructure.persistence.db_session import configure_session_maker
from api.infrastructure.service_cors import add_optional_service_cors
from api.interfaces.api_controllers.health_endpoint import router as health_router
from controlplane_db.session import normalize_database_url
from aiuditor_service.hypermesh import (
    HostLookup,
    PlaneHop,
    resolve_host_lookup,
    resolve_plane_hop,
)
from aiuditor_service.interfaces.api.routes import router as aiuditor_router
from aiuditor_service.middleware import AiuditorServiceDbMiddleware
from aiuditor_service.package import (
    resolve_hydrate_tenant,
    witness_uses_memory_persist,
)
from aiuditor_service.parent import ParentHop, resolve_parent_hop, run_hydrate_loop
from aiuditor_service.provider import ProviderHop, resolve_provider_hop
from aiuditor_service.persist import (
    PERSIST_UNAVAILABLE,
    PersistSession,
    PersistUnavailable,
    resolve_persist,
)
from aiuditor_service.role import AiuditorRole, resolve_role, writer_for
from aiuditor_service.store import new_stores
from aiuditor_service.intake_loop import run_crawl_loop

logger = get_logger(__name__)


def create_app(
    *,
    test_event_sink: list | None = None,
    enable_db_middleware: bool = True,
    service_name: str | None = None,
    host_lookup: HostLookup | None = None,
    plane_hop: PlaneHop | None = None,
    provider_hop: ProviderHop | None = None,
    persist_session: PersistSession | None = None,
    role: AiuditorRole | None = None,
    parent_hop: ParentHop | None = None,
) -> FastAPI:
    svc_name = service_name or settings.service_name
    resolved_role = role if role is not None else resolve_role()
    hydrate_tenant_id = resolve_hydrate_tenant(resolved_role)
    use_sql = enable_db_middleware
    if witness_uses_memory_persist(resolved_role):
        use_sql = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        persist = persist_session or resolve_persist(
            use_sql, settings.database_url, role=resolved_role
        )
        stores = new_stores(persist)
        app.state.aiuditor_persist = persist
        app.state.aiuditor_role = resolved_role
        app.state.aiuditor_writer = writer_for(resolved_role)
        app.state.hydrate_tenant_id = hydrate_tenant_id
        app.state.connection_store = stores.connection_store
        app.state.approval_store = stores.approval_store
        app.state.policy_store = stores.policy_store
        app.state.contract_store = stores.contract_store
        app.state.violation_store = stores.violation_store
        app.state.catalog_store = stores.catalog_store
        app.state.envelope_store = stores.envelope_store
        app.state.suite_store = stores.suite_store
        app.state.admission_store = stores.admission_store
        app.state.candidate_store = stores.candidate_store
        logger.info(
            "AIuditor persist backend: %s role=%s writer=%s",
            type(persist).__name__,
            resolved_role.value,
            writer_for(resolved_role),
        )
        app.state.host_lookup = resolve_host_lookup(host_lookup)
        app.state.plane_hop = resolve_plane_hop(plane_hop, role=resolved_role.value)
        app.state.provider_hop = resolve_provider_hop(provider_hop)
        app.state.parent_hop = resolve_parent_hop(parent_hop)
        engine = None
        if use_sql:
            engine = create_async_engine(normalize_database_url(settings.database_url))
            maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            configure_session_maker(maker)
            app.state.async_engine = engine

        if test_event_sink is not None:

            class _ListSink:
                async def publish(
                    self,
                    *,
                    tenant_id,
                    event_type: str,
                    payload: dict,
                    correlation_id: str | None = None,
                ) -> None:
                    test_event_sink.append(
                        {
                            "tenant_id": tenant_id,
                            "event_type": event_type,
                            "payload": payload,
                            "correlation_id": correlation_id,
                        }
                    )

            set_tenant_event_publisher(_ListSink())
        else:
            await install_tenant_event_publisher(service_name=svc_name)

        hydrate_stop = asyncio.Event()
        app.state.hydrate_stop = hydrate_stop
        hydrate_task = asyncio.create_task(run_hydrate_loop(app, stop=hydrate_stop))
        app.state.hydrate_task = hydrate_task
        crawl_stop = asyncio.Event()
        app.state.crawl_stop = crawl_stop
        crawl_task = asyncio.create_task(run_crawl_loop(app, stop=crawl_stop))
        app.state.crawl_task = crawl_task

        yield

        crawl_stop.set()
        crawl_task.cancel()
        try:
            await crawl_task
        except asyncio.CancelledError:
            pass
        hydrate_stop.set()
        hydrate_task.cancel()
        try:
            await hydrate_task
        except asyncio.CancelledError:
            pass
        await shutdown_tenant_event_publisher()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        lifespan=lifespan,
        title="AIuditor",
        description=(
            "Enterprise grant plane: connections, approval inbox, org policy, "
            "spine §D contracts, service catalog, and the model cert program "
            "(class envelopes, core suite, admission). "
            "Edge prefix /api/v1/aiuditor. Hypermesh stays measurement, leases, "
            "and catalog stamps. Site inbox stays /api/v1/auditor. "
            "The door stays hypermesh-gateway."
        ),
        version="0.1.0",
    )
    app.state.aiuditor_role = resolved_role
    app.state.aiuditor_writer = writer_for(resolved_role)

    @app.exception_handler(UnauthorizedException)
    async def _unauthorized(_: Request, ex: UnauthorizedException) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": ex.message},
        )

    @app.exception_handler(ForbiddenException)
    async def _forbidden(_: Request, ex: ForbiddenException) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": ex.message},
        )

    @app.exception_handler(PersistUnavailable)
    async def _persist_unavailable(_: Request, ex: PersistUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": str(ex) or PERSIST_UNAVAILABLE},
        )

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description or "",
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})[
            "BearerAuth"
        ] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Tenant-session or purpose=service exchange JWT. X-Tenant-ID is a cross-check.",
        }
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi

    if use_sql:
        app.add_middleware(AiuditorServiceDbMiddleware)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(aiuditor_router, prefix="/api/v1")
    add_optional_service_cors(app, env_var="AIUDITOR_SERVICE_CORS_ORIGINS")
    logger.info(
        "AIuditor: health + connections + inbox + policy + contracts + catalog + optional CORS"
    )
    from api.infrastructure.metrics import install_http_metrics

    install_http_metrics(app)
    return app


app = create_app()
