"""Store protocol-specific sustained references in the existing table."""
from alembic import op

revision = "aiuditor_0068_sustained_series"
down_revision = "aiuditor_0067_sustained_decode"
branch_labels = None
depends_on = None

TABLE = "aiuditor_sustained_envelopes"
UNIQUE = "uq_aiuditor_sustained_envelope"
CHECK = "ck_aiuditor_sustained_protocol"


def upgrade():
    op.drop_constraint(UNIQUE, TABLE, type_="unique")
    op.create_unique_constraint(UNIQUE, TABLE, ["tenant_id", "class_id", "catalog_id", "measurement_protocol"])
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.create_check_constraint(CHECK, TABLE, "measurement_protocol IN ('sustained-decode-v2', 'sustained-decode-v3')")


def downgrade():
    raise RuntimeError("Preserve accepted sustained decode series")
