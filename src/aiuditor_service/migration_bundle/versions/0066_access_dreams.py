"""Retain idempotent permission dreaming results."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID
revision = 'aiuditor_0066_access_dreams'
down_revision = 'aiuditor_0065_access_policies'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('aiuditor_access_dreams',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('request_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('binding_sha256', sa.String(64), nullable=False),
        sa.Column('result', JSONB, nullable=False))
    op.execute('ALTER TABLE aiuditor_access_dreams ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE aiuditor_access_dreams FORCE ROW LEVEL SECURITY')
    op.execute("CREATE POLICY tenant_isolation ON aiuditor_access_dreams USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), '')) WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))")

def downgrade():
    raise RuntimeError('Preserve completed permission reviews')
