"""Bind legacy upload evidence to a durable assigned lab job."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
revision = "aiuditor_0060_admission_reports"
down_revision = "aiuditor_0059_parent_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("aiuditor_admissions", sa.Column("lab_assignment", JSONB, nullable=True))
    op.add_column("aiuditor_admissions", sa.Column("sandbox_report_sha256", sa.String(64), nullable=True))


def downgrade():
    raise RuntimeError("Preserve admission evidence; use a reviewed restore for rollback")
