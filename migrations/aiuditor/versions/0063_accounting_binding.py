"""Provider-neutral contract binding; preserve legacy contracts verbatim."""
from alembic import op
revision = 'aiuditor_0063_accounting_binding'
down_revision = 'aiuditor_0062_routing_policy'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE public.aiuditor_contracts ALTER COLUMN rail DROP NOT NULL')
    op.execute("ALTER TABLE public.aiuditor_contracts ADD COLUMN accounting_backend varchar(32) NOT NULL DEFAULT 'internal', ADD COLUMN accounting_ref varchar(256), ADD COLUMN accounting_commit_id uuid, ADD COLUMN accounting_commit_hash varchar(64)")
    op.execute("UPDATE public.aiuditor_contracts SET accounting_backend='http' WHERE rail IS NOT NULL")
    op.execute("ALTER TABLE public.aiuditor_contracts ADD CONSTRAINT accounting_backend_valid CHECK (accounting_backend IN ('internal','none','http'))")
    op.execute('CREATE UNIQUE INDEX aiuditor_accounting_commit_unique ON public.aiuditor_contracts(tenant_id,accounting_backend,accounting_commit_id) WHERE accounting_commit_id IS NOT NULL')
    op.execute('CREATE UNIQUE INDEX aiuditor_accounting_ref_unique ON public.aiuditor_contracts(tenant_id,accounting_backend,accounting_ref) WHERE accounting_ref IS NOT NULL')


def downgrade():
    raise RuntimeError('Preserve committed accounting bindings; restore a reviewed backup')
