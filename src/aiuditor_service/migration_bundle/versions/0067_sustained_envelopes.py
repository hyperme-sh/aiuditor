"""Store sustained decode references independently from legacy envelopes."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "aiuditor_0067_sustained_decode"
down_revision = "aiuditor_0066_access_dreams"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aiuditor_sustained_envelopes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", sa.String(64), nullable=False),
        sa.Column("catalog_id", sa.String(64), nullable=False),
        sa.Column("file_hash", sa.String(256), nullable=False),
        sa.Column("quant", sa.String(32)),
        sa.Column("runtime", sa.String(64), nullable=False),
        sa.Column("context", sa.Integer()),
        sa.Column("fit", sa.Boolean(), nullable=False),
        sa.Column("decode_tok_s", sa.Float()),
        sa.Column("thermal_c", sa.Float()),
        sa.Column("lab_job_id", UUID(as_uuid=True)),
        sa.Column("lab_device_id", UUID(as_uuid=True)),
        sa.Column("lab_origin", sa.String(32)),
        sa.Column("binding_sha256", sa.String(64)),
        sa.Column("report_sha256", sa.String(64)),
        sa.Column("measured_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measurement_protocol", sa.String(32), nullable=False),
        sa.Column("measurement", JSONB, nullable=False),
        sa.UniqueConstraint("tenant_id", "class_id", "catalog_id", name="uq_aiuditor_sustained_envelope"),
        sa.CheckConstraint("measurement_protocol = 'sustained-decode-v2'", name="ck_aiuditor_sustained_protocol"),
    )
    op.execute("ALTER TABLE aiuditor_sustained_envelopes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aiuditor_sustained_envelopes FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON aiuditor_sustained_envelopes "
        "USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')) "
        "WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))"
    )


def downgrade():
    raise RuntimeError("Preserve accepted sustained-decode measurements")
