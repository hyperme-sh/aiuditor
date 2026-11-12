"""Tenant-owned model routing constraints."""
from alembic import op
revision = 'aiuditor_0062_routing_policy'
down_revision = 'aiuditor_0061_owned_outbox'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE public.aiuditor_policies ADD COLUMN routing_policy jsonb')
    op.execute("ALTER TABLE public.aiuditor_policies ADD CONSTRAINT routing_policy_object CHECK (routing_policy IS NULL OR jsonb_typeof(routing_policy) = 'object')")


def downgrade():
    raise RuntimeError('Retain operator routing constraints; restore a reviewed backup')
