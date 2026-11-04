"""aiuditor schema-creating baseline at mixed-chain 0053."""
from pathlib import Path
from alembic import op

revision = "aiuditor_0053_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_name("baseline.sql").read_text()
    op.get_bind().exec_driver_sql(sql, execution_options={"no_parameters": True})


def downgrade():
    raise RuntimeError("Owner baselines cannot be downgraded; use a reviewed database restore.")
