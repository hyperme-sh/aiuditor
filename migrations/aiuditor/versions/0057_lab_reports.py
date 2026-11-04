"""Authenticate lab provenance and retain immutable replay receipts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "aiuditor_0057_lab_reports"
down_revision = "aiuditor_0056_crawl_state"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_ in [("lab_job_id", UUID(as_uuid=True)), ("lab_device_id", UUID(as_uuid=True)),
                        ("lab_origin", sa.String(32)), ("binding_sha256", sa.String(64)),
                        ("report_sha256", sa.String(64)), ("measured_at", sa.DateTime(timezone=True))]:
        op.add_column("aiuditor_envelopes", sa.Column(name, type_, nullable=True))
    # Old envelopes deliberately remain unverified; no historical evidence is invented.
    op.create_table("aiuditor_envelope_reports",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("origin", sa.String(32), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("accepted_envelope", JSONB, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("origin IN ('path_b','qualification')", name="ck_lab_report_origin"))
    op.execute("ALTER TABLE aiuditor_envelope_reports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aiuditor_envelope_reports FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON aiuditor_envelope_reports
        USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))
        WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))""")


def downgrade():
    raise RuntimeError("Retain lab receipts; disable reporting for rollback")
