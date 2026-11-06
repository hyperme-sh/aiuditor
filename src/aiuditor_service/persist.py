"""AIuditor persist session. A bounce must not lose grant rows.

SQLAlchemy + Alembic + tenant_id + RLS live in controlplane-db (0025 + 0029).
Tests use MemoryPersistSession so they run without Postgres. Production hydrates
from SQL when DATABASE_URL is a real server. Prompt bodies, HMAC tickets, and
sibling TURN/oracle/payment rows stay off these tables.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Protocol

from aiuditor_service.domain.models import (
    Admission,
    Approval,
    ApprovalPolicy,
    CatalogService,
    ClassEnvelope,
    Connection,
    Contract,
    ContractViolation,
    CrawlCandidate,
    SuiteMember,
)

logger = logging.getLogger(__name__)

PERSIST_UNAVAILABLE = "AIuditor persist requires a real DATABASE_URL"

# Task-local session so one candidate's writes share a transaction without
# leaking into another request that awaits on the same process.
_SCOPE: ContextVar[Any] = ContextVar("aiuditor_persist_scope", default=None)


class PersistUnavailable(RuntimeError):
    """Compose/VM must have a reachable DATABASE_URL. Tests set enable_sql=False."""

TABLES = (
    "connections",
    "approvals",
    "policies",
    "contracts",
    "violations",
    "catalog",
    "envelopes",
    "suite_members",
    "admissions",
    "candidates",
)

_DOMAIN = {
    "connections": Connection,
    "approvals": Approval,
    "policies": ApprovalPolicy,
    "contracts": Contract,
    "violations": ContractViolation,
    "catalog": CatalogService,
    "envelopes": ClassEnvelope,
    "suite_members": SuiteMember,
    "admissions": Admission,
    "candidates": CrawlCandidate,
}

_BANNED = ("credential", "prompt", "ticket", "messages", "hmac")

_ALLOWED = {
    "connections": {
        "control_plane_id",
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
        "enforcement_completed_at", "enforcement_attempts",
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
    "envelopes": {
        "id",
        "tenant_id",
        "class_id",
        "catalog_id",
        "file_hash",
        "quant",
        "runtime",
        "context",
        "fit",
        "decode_tok_s",
        "thermal_c",
        "created_at",
        "updated_at",
        "lab_job_id", "lab_device_id", "lab_origin", "binding_sha256", "report_sha256", "measured_at",
    },
    "suite_members": {
        "id",
        "tenant_id",
        "class_id",
        "catalog_id",
        "pack_id",
        "created_at",
    },
    "admissions": {
        "id",
        "tenant_id",
        "file_hash",
        "format",
        "quant",
        "license",
        "intended_class",
        "credential_ref",
        "status",
        "verdict",
        "reason",
        "resolved_by",
        "size_bytes",
        "sandbox_soak_ok",
        "sandbox_egress_ok",
        "sandbox_egress_reason",
        "created_at",
        "resolved_at",
    },
    "candidates": {
        "id",
        "tenant_id",
        "repo",
        "revision",
        "file_name",
        "size_bytes",
        "license",
        "quant",
        "file_hash",
        "publisher",
        "catalog_id",
        "class_id",
        "status",
        "soak_job_id",
        "created_at",
        "updated_at",
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
    for key in (
        "kind",
        "purpose",
        "status",
        "why",
        "decision",
        "disposition",
        "rail",
        "backend",
        "action",
        "verdict",
        "format",
    ):
        if key in data and data[key] is not None:
            data[key] = _plain(data[key])
    allowed = _ALLOWED[table]
    return {key: data[key] for key in allowed if key in data}


class PersistSession(Protocol):
    def accept_lab_report(self, report, now): ...

    def consume_approval(self, tenant_id: uuid.UUID, approval_id: uuid.UUID, now: datetime) -> bool: ...

    def hydrate(self, stores: Any) -> None: ...

    def upsert(self, table: str, tenant_id: uuid.UUID, key: object, payload: dict[str, Any]) -> None: ...

    def create_envelope(self, payload: dict[str, Any]) -> bool: ...

    def candidate_scope(self): ...

    def program_bootstrap_scope(self, tenant_id: uuid.UUID): ...

    def checkout_contract_scope(self, tenant_id: uuid.UUID, lease_id: uuid.UUID, contract_id: uuid.UUID | None): ...

    def delete(self, table: str, tenant_id: uuid.UUID, key: object) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...

    def load_rows(self, table: str, tenant_id: uuid.UUID) -> dict[str, dict[str, Any]]: ...


class MemoryPersistSession:
    """In-process row store. A new store on the same session reloads rows."""

    def accept_lab_report(self, report, now):
        from aiuditor_service.lab_reporting import persist_report
        return persist_report(self, report, now)

    def __init__(self, rows: dict[str, dict[str, dict[str, Any]]] | None = None) -> None:
        from threading import Lock
        self._approval_lock = Lock()
        self._lab_report_lock = Lock()
        self.rows: dict[str, dict[str, dict[str, Any]]] = rows or {table: {} for table in TABLES}

    def consume_approval(self, tenant_id, approval_id, now):
        from datetime import UTC
        with self._approval_lock:
            row = self.rows.get("approvals", {}).get(str(tenant_id), {}).get(str(approval_id))
            if row is None:
                return False
            approval = row_from_payload("approvals", row)
            expiry = approval.expires_at.replace(tzinfo=UTC) if approval.expires_at.tzinfo is None else approval.expires_at
            if (approval.status != "allowed" or approval.decision != "allow_once"
                    or approval.consumed_at is not None or expiry <= now):
                return False
            self.upsert("approvals", tenant_id, approval_id,
                        dump_for_persist(approval.model_copy(update={"consumed_at": now})))
            return True

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

    def create_envelope(self, payload: dict[str, Any]) -> bool:
        # Memory mode is used within the synchronous service event-loop path.
        tenant = str(payload["tenant_id"])
        bucket = self.rows.setdefault("envelopes", {}).setdefault(tenant, {})
        if any(row["class_id"] == payload["class_id"] and row["catalog_id"] == payload["catalog_id"]
               for row in bucket.values()):
            return False
        bucket[str(payload["id"])] = dict(payload)
        return True

    @contextmanager
    def candidate_scope(self):
        """In-memory writes have no SQL transaction. The crawl still isolates errors."""

        yield

    @contextmanager
    def checkout_contract_scope(self, tenant_id, lease_id, contract_id):
        # Memory mode is the synchronous event-loop test stand-in.
        import copy
        previous = copy.deepcopy(self.rows.get("contracts", {}))
        try:
            yield
        except Exception:
            self.rows["contracts"] = previous
            raise

    @contextmanager
    def program_bootstrap_scope(self, tenant_id: uuid.UUID):
        yield

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
        ("envelopes", "envelope_store"),
        ("suite_members", "suite_store"),
        ("admissions", "admission_store"),
        ("candidates", "candidate_store"),
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
    from aiuditor_service.models import (
        AiuditorAdmission,
        AiuditorApproval,
        AiuditorCatalog,
        AiuditorCandidate,
        AiuditorConnection,
        AiuditorContract,
        AiuditorEnvelope,
        AiuditorPolicy,
        AiuditorSuiteMember,
        AiuditorViolation,
    )

    return {
        "connections": AiuditorConnection,
        "approvals": AiuditorApproval,
        "policies": AiuditorPolicy,
        "contracts": AiuditorContract,
        "violations": AiuditorViolation,
        "catalog": AiuditorCatalog,
        "envelopes": AiuditorEnvelope,
        "suite_members": AiuditorSuiteMember,
        "admissions": AiuditorAdmission,
        "candidates": AiuditorCandidate,
    }


def _sql_hydrate_tenant_gucs(session) -> list[str]:
    from sqlalchemy import select

    from controlplane_tenancy.tenant_registry import tenant_ids_sync

    gucs = [""]
    try:
        for tenant_id in tenant_ids_sync(session):
            value = str(tenant_id)
            if value not in gucs:
                gucs.append(value)
    except Exception:
        logger.debug("aiuditor persist: tenant list unavailable during hydrate", exc_info=True)
    return gucs


class SqlPersistSession:
    """Write-through SQLAlchemy persist. Sets RLS GUC per tenant on each write."""

    def accept_lab_report(self, report, now):
        from aiuditor_service.lab_reporting import persist_report
        return persist_report(self, report, now)

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

    def create_envelope(self, payload: dict[str, Any]) -> bool:
        """Unique-key insertion is atomic across all writers and replicas."""
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import insert
        model = _sql_models()["envelopes"]
        with self._session_factory() as session:
            session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                            {"tid": str(payload["tenant_id"])})
            inserted = session.execute(insert(model).values(**sql_row("envelopes", payload))
                .on_conflict_do_nothing(constraint="uq_aiuditor_envelope_class_catalog")
                .returning(model.id)).scalar_one_or_none()
            session.commit()
            return inserted is not None

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

    @contextmanager
    def candidate_scope(self):
        """One transaction per crawl candidate. Each upsert is a savepoint.

        Do not share this session across tasks. The context var is task-local.
        A failed savepoint rolls back that write; the caller rolls the
        candidate back and the next candidate opens a new transaction.
        """

        with self._session_factory() as session:
            token = _SCOPE.set(session)
            try:
                yield
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                _SCOPE.reset(token)

    @contextmanager
    def program_bootstrap_scope(self, tenant_id: uuid.UUID):
        """Serialize default program initialization across writer replicas."""
        from sqlalchemy import text

        with self.candidate_scope():
            session = _SCOPE.get()
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"aiuditor-core-suite:{tenant_id}"},
            )
            yield

    @contextmanager
    def checkout_contract_scope(self, tenant_id, lease_id, contract_id):
        """Owner transaction serializes lease replay and named-contract binding."""
        from sqlalchemy import text
        keys = [f"aiuditor-checkout-lease:{tenant_id}:{lease_id}"]
        if contract_id is not None:
            keys.append(f"aiuditor-checkout-contract:{tenant_id}:{contract_id}")
        with self.candidate_scope():
            session = _SCOPE.get()
            for key in sorted(keys):
                session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
            yield

    def _apply(
        self,
        session,
        model,
        tenant_id: uuid.UUID,
        row_data: dict[str, Any],
        *,
        savepoint: bool,
    ) -> None:
        from contextlib import nullcontext

        from sqlalchemy import text

        # Inside a candidate transaction each write is a savepoint, so one
        # bad upsert does not abort the session. A lone upsert is its own
        # transaction and does not need a nested one.
        ctx = session.begin_nested() if savepoint else nullcontext()
        with ctx:
            session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            session.merge(model(**row_data))

    def consume_approval(self, tenant_id, approval_id, now):
        """One conditional database write grants at most one caller authority."""
        from sqlalchemy import update, text
        from sqlalchemy.exc import SQLAlchemyError
        model = _sql_models()["approvals"]
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)})
                result = session.execute(update(model).where(
                    model.tenant_id == tenant_id, model.id == approval_id,
                    model.status == "allowed", model.decision == "allow_once",
                    model.consumed_at.is_(None), model.expires_at > now,
                ).values(consumed_at=now))
                claimed = result.rowcount == 1
                session.commit()
                return claimed
        except SQLAlchemyError as exc:
            raise PersistUnavailable("Approval consumption unavailable") from exc

    def upsert(self, table: str, tenant_id: uuid.UUID, key: object, payload: dict[str, Any]) -> None:
        model = _sql_models()[table]
        body = dict(payload)
        body.setdefault("tenant_id", tenant_id)
        body.setdefault("id", key)
        row_data = sql_row(table, body)
        scoped = _SCOPE.get()
        if scoped is not None:
            self._apply(scoped, model, tenant_id, row_data, savepoint=True)
            return
        with self._session_factory() as session:
            self._apply(session, model, tenant_id, row_data, savepoint=False)
            session.commit()

    def load_rows(self, table: str, tenant_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        try:
            from sqlalchemy import select, text

            model = _sql_models()[table]
            memory = MemoryPersistSession()
            scoped = _SCOPE.get()
            if table in {"contracts", "violations"} and scoped is not None:
                scoped.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)})
                rows = scoped.execute(select(model).where(model.tenant_id == tenant_id)).scalars().all()
                for row in rows:
                    memory.upsert(table, row.tenant_id, row.id, {c.key: getattr(row, c.key) for c in row.__table__.columns})
                return memory.load_rows(table, tenant_id)
            with self._session_factory() as session:
                session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                rows = (
                    session.execute(select(model).where(model.tenant_id == tenant_id))
                    .scalars()
                    .all()
                )
                for row in rows:
                    payload = {c.key: getattr(row, c.key) for c in row.__table__.columns}
                    memory.upsert(table, row.tenant_id, row.id, payload)
            return memory.load_rows(table, tenant_id)
        except PersistUnavailable:
            raise
        except Exception as exc:
            raise PersistUnavailable(PERSIST_UNAVAILABLE) from exc

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
