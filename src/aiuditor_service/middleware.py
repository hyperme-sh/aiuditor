"""Per-request DB session + RLS GUC from the bound credential, not the header alone."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from controlplane_tenancy.db_session import (
    get_session_maker,
    reset_db_session,
    set_db_session,
)
from controlplane_tenancy.tenant_guc import set_current_tenant_id_guc

from aiuditor_service.tenant import rls_tenant_id


class AiuditorServiceDbMiddleware(BaseHTTPMiddleware):
    """Sets app.current_tenant_id from the bound credential for RLS."""

    async def dispatch(self, request: Request, call_next):
        # These purpose-authenticated routes own their transaction/GUC. Their
        # service credential cannot authenticate any other AIuditor endpoint.
        if request.url.path.startswith("/api/v1/aiuditor/internal/private-model-admissions"):
            return await call_next(request)
        maker = get_session_maker()
        async with maker() as session:
            token = set_db_session(session)
            try:
                await set_current_tenant_id_guc(session, None)
                try:
                    tid = rls_tenant_id(request)
                except HTTPException as exc:
                    from starlette.responses import JSONResponse

                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"error": exc.detail},
                    )
                if tid is not None:
                    await set_current_tenant_id_guc(session, tid)
                request.state.tenant_id = tid

                response = await call_next(request)
                await session.commit()
                return response
            except Exception:
                await session.rollback()
                raise
            finally:
                reset_db_session(token)
