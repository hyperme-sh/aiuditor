"""AIuditor persist session. A bounce must not lose grant rows.

SQLAlchemy + Alembic + tenant_id + RLS live in controlplane-db (revision 0025).
Tests use MemoryPersistSession so they run without Postgres. Production hydrates
from SQL when DATABASE_URL is a real server. Prompt bodies, HMAC tickets, and
sibling TURN/oracle/payment rows stay off these tables.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Protocol

from aiuditor_service.domain.models import (
    Approval,
    ApprovalPolicy,
    CatalogService,
    Connection,
    Contract,
    ContractViolation,
)

logger = logging.getLogger(__name__)

PERSIST_UNAVAILABLE = "AIuditor persist requires a real DATABASE_URL"


class PersistUnavailable(RuntimeError):
    """Compose/VM must have a reachable DATABASE_URL. Tests set enable_sql=False."""

TABLES = (
    "connections",
    "approvals",
    "policies",
    "contracts",
    "violations",
    "catalog",
)

_DOMAIN = {
    "connections": Connection,
    "approvals": Approval,
    "policies": ApprovalPolicy,
    "contracts": Contract,
    "violations": ContractViolation,
    "catalog": CatalogService,
}

_BANNED = ("credential", "prompt", "ticket", "messages", "hmac")

_ALLOWED = {
    "connections": {
        "id",
        "tenant_id",
        "kind",
        "purpose",
        "device_id",
        "endpoint",
        "credential_ref",
        "lease_id",
        "status",
        "created_at",
    },
    "approvals": {
        "id",
        "tenant_id",
        "lease_id",
        "connection_id",
        "purpose",
        "who",
        "why",
        "prompt_class",
        "status",
        "decision",
        "resolved_by",
        "created_at",
        "expires_at",
        "resolved_at",
        "consumed_at",
    },
    "policies": {
        "id",
        "tenant_id",
        "purpose",
        "prompt_class",
        "connection_id",
        "disposition",
        "created_at",
    },
    "contracts": {
        "id",
        "tenant_id",
        "parties",
        "meters",
        "rail",
        "until",
        "dispute_tip",
        "connection_id",
        "lease_id",
        "status",
        "signed_by",
        "created_at",
        "signed_at",
    },
    "violations": {
        "id",
        "tenant_id",
        "contract_id",
        "lease_id",
        "kind",
        "action",
        "reason",
        "created_at",
    },
    "catalog": {
        "id",
        "tenant_id",
        "title",
        "backend",
        "class_id",
        "catalog_id",
        "connection_id",
        "meters",
        "created_at",
    },
}


def _parse_uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _plain(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, list):
        return [_plain(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, (str, uuid.UUID, datetime)):
        return enum_value
    return value


def dump_for_persist(obj: Any) -> dict[str, Any]:
    data = obj.model_dump()
    for banned in _BANNED:
        data.pop(banned, None)
    return data


def row_from_payload(table: str, payload: dict[str, Any]) -> Any:
    return _DOMAIN[table].model_validate(payload)


def sql_row(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    for banned in _BANNED:
        data.pop(banned, None)
    if table == "contracts":
        data["parties"] = _plain(data.get("parties") or [])
        data["meters"] = list(data.get("meters") or [])
    if table == "catalog":
        data["meters"] = list(data.get("meters") or [])
    for key in ("kind", "purpose", "status", "why", "decision", "disposition", "rail", "backend", "action"):
        if key in data and data[key] is not None:
            data[key] = _plain(data[key])
    allowed = _ALLOWED[table]
    return {key: data[key] for key in allowed if key in data}


class PersistSession(Protocol):
    def hydrate(self, stores: Any) -> None: ...

    def upsert(self, table: str, tenant_id: uuid.UUID, key: object, payload: dict[str, Any]) -> None: ...

    def delete(self, table: str, tenant_id: uuid.UUID, key: object) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...

    def load_rows(self, table: str, tenant_id: uuid.UUID) -> dict[str, dict[str, Any]]: ...


class MemoryPersistSession:
    """In-process row store. A new store on the same session reloads rows."""

    def __init__(self, rows: dict[str, dict[str, dict[str, Any]]] | None = None) -> None:
        self.rows: dict[str, dict[str, dict[str, Any]]] = rows or {table: {} for table in TABLES}

    def snapshot(self) -> dict[str, Any]:
        return {
            table: {tid: dict(items) for tid, items in by_tenant.items()}
            for table, by_tenant in self.rows.items()
        }

    def upsert(self, table: str, tenant_id: uuid.UUID, key: object, payload: dict[str, Any]) -> None:
        bucket = self.rows.setdefault(table, {}).setdefault(str(tenant_id), {})
        body = dict(payload)
        for banned in _BANNED:
            body.pop(banned, None)
        bucket[str(key)] = body

    def delete(self, table: str, tenant_id: uuid.UUID, key: object) -> None:
        bucket = self.rows.setdefault(table, {}).setdefault(str(tenant_id), {})
        bucket.pop(str(key), None)

    def hydrate(self, stores: Any) -> None:
        apply_snapshot(stores, self.rows)

    def load_rows(self, table: str, tenant_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        bucket = self.rows.get(table, {}).get(str(tenant_id), {})
        return {key: dict(payload) for key, payload in bucket.items()}


def apply_snapshot(stores: Any, rows: dict[str, Any]) -> None:
    mapping = (
        ("connections", "connection_store"),
        ("approvals", "approval_store"),
        ("policies", "policy_store"),
        ("contracts", "contract_store"),
        ("violations", "violation_store"),
        ("catalog", "catalog_store"),
    )
    for table, attr in mapping:
        store = getattr(stores, attr, None)
        if store is None:
            continue
        store._hydrating = True
        try:
            for _tid, items in rows.get(table, {}).items():
                for payload in items.values():
                    store.put(row_from_payload(table, payload))
        finally:
            store._hydrating = False


def _sql_models():
    from controlplane_db.models import (
        AiuditorApproval,
        AiuditorCatalog,
        AiuditorConnection,
        AiuditorContract,
        AiuditorPolicy,
        AiuditorViolation,
    )

    return {
        "connections": AiuditorConnection,
        "approvals": AiuditorApproval,
        "policies": AiuditorPolicy,
        "contracts": AiuditorContract,
        "violations": AiuditorViolation,
        "catalog": AiuditorCatalog,
    }


def _sql_hydrate_tenant_gucs(session) -> list[str]:
    from sqlalchemy import select

    from controlplane_db.models import Tenant

    gucs = [""]
    try:
        for tenant_id in session.execute(select(Tenant.id)).scalars().all():
            value = str(tenant_id)
            if value not in gucs:
                gucs.append(value)
    except Exception:
        logger.debug("aiuditor persist: tenant list unavailable during hydrate", exc_info=True)
    return gucs


class SqlPersistSession:
    """Write-through SQLAlchemy persist. Sets RLS GUC per tenant on each write."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_url(cls, url: str) -> SqlPersistSession:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        sync = url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        if sync.startswith("postgresql://") and "+" not in sync.split("://", 1)[0]:
            sync = "postgresql+psycopg://" + sync.split("postgresql://", 1)[1]
        engine = create_engine(sync)
        return cls(sessionmaker(engine, expire_on_commit=False))

    def ping(self) -> None:
        from sqlalchemy import text

        with self._session_factory() as session:
            session.execute(text("SELECT 1"))

    def snapshot(self) -> dict[str, Any]:
        memory = MemoryPersistSession()
        self._copy_sql_into(memory)
        return memory.snapshot()

    def hydrate(self, stores: Any) -> None:
        memory = MemoryPersistSession()
        self._copy_sql_into(memory)
        memory.hydrate(stores)

    def _copy_sql_into(self, memory: MemoryPersistSession) -> None:
        from sqlalchemy import select, text

        models = _sql_models()
        with self._session_factory() as session:
            for tenant_guc in _sql_hydrate_tenant_gucs(session):
                session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": tenant_guc},
                )
                for table, model in models.items():
                    try:
                        rows = session.execute(select(model)).scalars().all()
                    except Exception:
                        continue
                    for row in rows:
                        payload = {c.key: getattr(row, c.key) for c in row.__table__.columns}
                        memory.upsert(table, row.tenant_id, row.id, payload)

    def upsert(self, table: str, tenant_id: uuid.UUID, key: object, payload: dict[str, Any]) -> None:
        from sqlalchemy import text

        model = _sql_models()[table]
        body = dict(payload)
        body.setdefault("tenant_id", tenant_id)
        body.setdefault("id", key)
        row_data = sql_row(table, body)
        with self._session_factory() as session:
            session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            session.merge(model(**row_data))
            session.commit()

    def load_rows(self, table: str, tenant_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        from sqlalchemy import select, text

        model = _sql_models()[table]
        memory = MemoryPersistSession()
        with self._session_factory() as session:
            session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            try:
                rows = (
                    session.execute(select(model).where(model.tenant_id == tenant_id))
                    .scalars()
                    .all()
                )
            except Exception:
                return {}
            for row in rows:
                payload = {c.key: getattr(row, c.key) for c in row.__table__.columns}
                memory.upsert(table, row.tenant_id, row.id, payload)
        return memory.load_rows(table, tenant_id)

    def delete(self, table: str, tenant_id: uuid.UUID, key: object) -> None:
        from sqlalchemy import text

        model = _sql_models()[table]
        with self._session_factory() as session:
            session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            row = session.get(model, _parse_uuid(key))
            if row is not None:
                session.delete(row)
                session.commit()


def resolve_persist(
    enable_sql: bool,
    database_url: str | None = None,
    *,
    role: object | None = None,
) -> PersistSession:
    from aiuditor_service.package import witness_uses_memory_persist
    from aiuditor_service.role import AiuditorRole

    if not enable_sql:
        return MemoryPersistSession()
    if isinstance(role, AiuditorRole) and witness_uses_memory_persist(role, database_url):
        logger.info("aiuditor persist: MemoryPersistSession (witness copy)")
        return MemoryPersistSession()
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
    if not url or "localhost:1" in url:
        raise PersistUnavailable(PERSIST_UNAVAILABLE)
    try:
        persist = SqlPersistSession.from_url(url)
        persist.ping()
    except PersistUnavailable:
        raise
    except Exception as exc:
        raise PersistUnavailable(PERSIST_UNAVAILABLE) from exc
    logger.info("aiuditor persist: SqlPersistSession")
    return persist
