"""Preserve complete failed lab attempts before an explicit person requests a retry."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
revision = "aiuditor_0062_admission_attempts"
down_revision = "aiuditor_0061_owned_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("aiuditor_admissions", sa.Column("lab_attempt_history", JSONB,
        nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade():
    raise RuntimeError("Preserve admission attempt receipts; use a reviewed restore for rollback")
