"""Add tenant-isolated workspace connector grants and decision receipts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "aiuditor_0069_workspace_tools"
down_revision = "aiuditor_0068_sustained_series"
branch_labels = None
depends_on = None

CONNECTIONS = "aiuditor_workspace_connections"
GRANTS = "aiuditor_workspace_grants"
DECISIONS = "aiuditor_workspace_decisions"
SCOPE = ("tenant_id", "workspace_id", "connection_id")


def upgrade():
    op.create_table(
        CONNECTIONS,
        sa.Column("tenant_id", UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("workspace_id", UUID(), primary_key=True),
        sa.Column("connection_id", UUID(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("disabled_at", sa.BigInteger()),
    )
    op.create_table(
        GRANTS,
        *(sa.Column(field, UUID(), primary_key=True) for field in SCOPE),
        sa.Column("grant_id", UUID(), primary_key=True),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("operations", JSONB(), nullable=False),
        sa.Column("created_by", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger()),
        sa.Column("revoked_by", sa.String(512)),
        sa.ForeignKeyConstraint(SCOPE, [f"{CONNECTIONS}.{field}" for field in SCOPE], ondelete="CASCADE"),
    )
    op.create_table(
        DECISIONS,
        sa.Column("tenant_id", UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("request_id", UUID(), primary_key=True),
        sa.Column("workspace_id", UUID(), nullable=False),
        sa.Column("connection_id", UUID(), nullable=False),
        sa.Column("grant_id", UUID(), nullable=False),
        sa.Column("run_id", UUID()),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("decided_at", sa.BigInteger(), nullable=False),
    )
    for table in (CONNECTIONS, GRANTS, DECISIONS):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        tenant = "tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')"
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({tenant}) WITH CHECK ({tenant})")


def downgrade():
    raise RuntimeError("Preserve workspace grant revocations and decision receipts")
