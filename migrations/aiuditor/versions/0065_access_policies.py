"""Durable access revisions, scoped changes and consumed dispatch IDs."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID
revision = 'aiuditor_0065_access_policies'
down_revision = 'aiuditor_0064_suite_revision'
branch_labels = None
depends_on = None


def tenant():
    return sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)


def upgrade():
    op.create_table('aiuditor_access_policies', tenant(),
        sa.Column('organization_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('state', JSONB, nullable=False))
    op.create_table('aiuditor_access_policy_events', tenant(),
        sa.Column('organization_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('sequence', sa.BigInteger, primary_key=True),
        sa.Column('change', JSONB, nullable=False))
    op.create_table('aiuditor_access_dispatches', tenant(),
        sa.Column('request_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', UUID(as_uuid=True), nullable=False),
        sa.Column('policy_revision', UUID(as_uuid=True), nullable=False),
        sa.Column('binding_sha256', sa.String(64), nullable=False))
    for table in ('aiuditor_access_policies', 'aiuditor_access_policy_events', 'aiuditor_access_dispatches'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))
            WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))""")


def downgrade():
    raise RuntimeError('Preserve access revocations and consumed dispatch IDs')
