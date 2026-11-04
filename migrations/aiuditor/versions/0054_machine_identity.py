"""AIuditor owns durable host keys, replay state and network token hashes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "aiuditor_0054_machine_identity"
down_revision = "aiuditor_0053_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "aiuditor_connections",
        sa.Column(
            "control_plane_id", sa.String(128), nullable=False, server_default="local"
        ),
    )
    op.create_table(
        "aiuditor_machine_identities",
        sa.Column("authority_id", sa.String(128), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("control_plane_id", sa.String(128), primary_key=True),
        sa.Column("device_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.execute("ALTER TABLE aiuditor_machine_identities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aiuditor_machine_identities FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON aiuditor_machine_identities USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"
    )


def downgrade():
    raise RuntimeError(
        "Revoking machine identities requires an explicit re-enrollment plan"
    )
