"""AIuditor owns revision/class/backend/runtime scoped private verdicts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "aiuditor_0055_private_admission"
down_revision = "aiuditor_0054_machine_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aiuditor_private_model_admissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_sub", sa.String(512), nullable=False),
        sa.Column("target", JSONB(), nullable=False),
        sa.Column("target_sha256", sa.String(64), nullable=False),
        sa.Column("lab_job_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lab_device_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("audit", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('sandboxing', 'hold', 'failed', 'allowed', 'denied', 'revoked')",
            name="ck_aud_private_admission_status",
        ),
    )
    op.create_index(
        "ix_aud_private_admission_tenant",
        "aiuditor_private_model_admissions",
        ["tenant_id", "created_at"],
    )
    op.execute(
        "ALTER TABLE aiuditor_private_model_admissions ENABLE ROW LEVEL SECURITY"
    )
    op.execute("ALTER TABLE aiuditor_private_model_admissions FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON aiuditor_private_model_admissions
        USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))
        WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))""")


def downgrade():
    raise RuntimeError(
        "Retain scoped verdict evidence; disable new admissions for rollback"
    )
