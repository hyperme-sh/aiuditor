"""Keep repeated suite transitions distinct from delivery retries."""
from alembic import op
revision = 'aiuditor_0064_suite_revision'
down_revision = 'aiuditor_0063_accounting_binding'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE public.aiuditor_suite_members ADD COLUMN revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0)')


def downgrade():
    op.execute('ALTER TABLE public.aiuditor_suite_members DROP COLUMN revision')
