"""Lease hosts AIuditor grants. Does not allocate and does not open a lease."""

from __future__ import annotations

from aiuditor_service.domain.models import (
    Connection,
    ConnectionKind,
    ConnectionPurpose,
    ConnectionStatus,
)


def allowed_host_set(rows: list[Connection]) -> list[Connection]:
    """Host connections with purpose=lease that are not disabled.

    Order is device id, then created time. One-host allocate is a different call.
    """

    chosen = [
        row
        for row in rows
        if row.kind is ConnectionKind.HOST
        and row.purpose is ConnectionPurpose.LEASE
        and row.status is not ConnectionStatus.DISABLED
        and row.device_id is not None
    ]
    chosen.sort(key=lambda row: (str(row.device_id), row.created_at, str(row.id)))
    return chosen
