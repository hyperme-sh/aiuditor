"""Durable upstream enforcement completion, independent of the local decision."""
from alembic import op
import sqlalchemy as sa
revision = 'aiuditor_0058_enforcement'
down_revision = 'aiuditor_0057_lab_reports'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('aiuditor_violations', sa.Column('enforcement_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('aiuditor_violations', sa.Column('enforcement_attempts', sa.Integer(), nullable=False, server_default='0'))

def downgrade():
    raise RuntimeError('Retain enforcement progress; roll back the publisher instead')
