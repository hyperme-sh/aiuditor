"""Tenant-scoped parent snapshot ownership and monotonic reconciliation receipts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID
revision = 'aiuditor_0059_parent_snapshots'
down_revision = 'aiuditor_0058_enforcement'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('aiuditor_parent_snapshots',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('parent_id', sa.String(64), primary_key=True),
        sa.Column('exported_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('snapshot_sha256', sa.String(64), nullable=False),
        sa.Column('owned_rows', JSONB, nullable=False))
    op.execute('ALTER TABLE aiuditor_parent_snapshots ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE aiuditor_parent_snapshots FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY tenant_isolation ON aiuditor_parent_snapshots
        USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))
        WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))""")


def downgrade():
    raise RuntimeError('Preserve parent ownership receipts; use a reviewed restore for rollback')
