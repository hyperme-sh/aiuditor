from __future__ import annotations

from controlplane_db.models import (
    AiuditorCatalog,
    AiuditorCandidate,
    AiuditorConnection,
    AiuditorContract,
    AiuditorEnvelope,
    AiuditorSuiteMember,
    AiuditorAdmission,
    HypermeshLease,
)


def test_orm_tables_are_grant_store_not_siblings():
    names = {
        AiuditorConnection.__tablename__,
        AiuditorCatalog.__tablename__,
        AiuditorContract.__tablename__,
        AiuditorEnvelope.__tablename__,
        AiuditorSuiteMember.__tablename__,
        AiuditorAdmission.__tablename__,
        AiuditorCandidate.__tablename__,
    }
    assert names == {
        "aiuditor_connections",
        "aiuditor_catalog",
        "aiuditor_contracts",
        "aiuditor_envelopes",
        "aiuditor_suite_members",
        "aiuditor_admissions",
        "aiuditor_candidates",
    }
    assert "contract_id" in HypermeshLease.__table__.c
    assert not any("turn" in name or "oracle" in name or "payment" in name for name in names)
    assert "prompt" not in AiuditorContract.__table__.c
    assert "ticket" not in AiuditorContract.__table__.c
    assert "decode_tok_s" in AiuditorEnvelope.__table__.c
    assert "pack_id" in AiuditorSuiteMember.__table__.c
    assert "verdict" in AiuditorAdmission.__table__.c
    assert "size_bytes" in AiuditorAdmission.__table__.c
    assert "repo" in AiuditorCandidate.__table__.c


def test_contract_row_is_spine_d_only():
    cols = set(AiuditorContract.__table__.c.keys())
    assert {
        "parties",
        "meters",
        "rail",
        "until",
        "dispute_tip",
        "tenant_id",
        "status",
    } <= cols
    assert "handoff" not in cols
