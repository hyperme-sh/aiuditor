"""Persist AIuditor metadata crawl cadence across writer restarts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "aiuditor_0056_crawl_state"
down_revision = "aiuditor_0055_private_admission"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aiuditor_crawl_state",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("error_count >= 0", name="ck_aiuditor_crawl_errors"),
    )
    op.execute("ALTER TABLE aiuditor_crawl_state ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE aiuditor_crawl_state FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON aiuditor_crawl_state
        USING (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))
        WITH CHECK (tenant_id::text = nullif(current_setting('app.current_tenant_id', true), ''))""")


def downgrade():
    raise RuntimeError("Retain crawl history; disable crawling for rollback")
