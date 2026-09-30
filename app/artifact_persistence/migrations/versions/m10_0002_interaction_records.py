"""Dedicated diagnostic records; business tables are unchanged (M10 §23)."""
from alembic import op
import sqlalchemy as sa
from app.artifact_persistence.errors import ArtifactPersistenceConfigurationError

revision = "m10_0002"
down_revision = "m10_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interaction_records",
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("record_kind", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.CHAR(71), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("record_id", name="pk_interaction_records"),
        sa.CheckConstraint("record_kind IN ('request','response','transport_failure','validation','tool_result','call_summary','run_summary')", name="ck_interaction_records_kind"),
        sa.CheckConstraint("content_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_interaction_records_hash"),
        sa.CheckConstraint("size_bytes = octet_length(content_bytes) AND size_bytes BETWEEN 1 AND 8388608", name="ck_interaction_records_size"),
        schema="artifact_store",
    )
    op.create_index("ix_interaction_records_run", "interaction_records", ["run_id", "captured_at", "record_id"], schema="artifact_store")


def downgrade() -> None:
    # No deletion path is authorized for diagnosis or old business test data.
    raise ArtifactPersistenceConfigurationError()
