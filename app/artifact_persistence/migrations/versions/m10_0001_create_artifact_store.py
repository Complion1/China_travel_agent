"""Create the Module 10 artifact tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.artifact_persistence.errors import ArtifactPersistenceConfigurationError


revision = "m10_0001"
down_revision = None
branch_labels = None
depends_on = None


_SAFE_INTEGER_MAX = 2**53 - 1


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column(
            "artifact_id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("logical_step", sa.String(length=64), nullable=False),
        sa.Column("artifact_type", sa.String(length=65), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.CHAR(length=71), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_artifacts"),
        sa.UniqueConstraint(
            "run_id",
            "phase",
            "logical_step",
            name="uq_artifacts_operation_id",
        ),
        sa.CheckConstraint(
            "phase IN ('requirements', 'hotel_selection', 'candidate_research', "
            "'itinerary_planning', 'validation', 'budget_evaluation', 'output_formatting')",
            name="ck_artifacts_phase",
        ),
        sa.CheckConstraint(
            "logical_step ~ '^[a-z][a-z0-9_]*$'",
            name="ck_artifacts_logical_step_format",
        ),
        sa.CheckConstraint(
            "char_length(logical_step) BETWEEN 1 AND 64",
            name="ck_artifacts_logical_step_length",
        ),
        sa.CheckConstraint(
            "artifact_type ~ '^[a-z][a-z0-9_]{0,31}\\.[a-z][a-z0-9_]{0,31}$'",
            name="ck_artifacts_artifact_type",
        ),
        sa.CheckConstraint(
            "schema_version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'",
            name="ck_artifacts_schema_version",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_artifacts_content_hash",
        ),
        sa.CheckConstraint(
            f"size_bytes BETWEEN 1 AND {_SAFE_INTEGER_MAX}",
            name="ck_artifacts_size_bytes_range",
        ),
        sa.CheckConstraint(
            "size_bytes = octet_length(canonical_bytes)",
            name="ck_artifacts_size_bytes_matches_content",
        ),
        schema="artifact_store",
    )

    op.create_table(
        "capacity_state",
        sa.Column(
            "singleton_id",
            sa.SmallInteger(),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("max_artifact_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_artifacts", sa.BigInteger(), nullable=False),
        sa.Column("max_total_content_bytes", sa.BigInteger(), nullable=False),
        sa.Column("artifact_count", sa.BigInteger(), nullable=False),
        sa.Column("total_content_bytes", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("singleton_id", name="pk_capacity_state"),
        sa.CheckConstraint("singleton_id = 1", name="ck_capacity_state_singleton"),
        sa.CheckConstraint(
            f"max_artifact_bytes BETWEEN 1 AND {_SAFE_INTEGER_MAX}",
            name="ck_capacity_state_max_artifact_bytes",
        ),
        sa.CheckConstraint(
            f"max_artifacts BETWEEN 1 AND {_SAFE_INTEGER_MAX}",
            name="ck_capacity_state_max_artifacts",
        ),
        sa.CheckConstraint(
            f"max_total_content_bytes BETWEEN 1 AND {_SAFE_INTEGER_MAX}",
            name="ck_capacity_state_max_total_content_bytes",
        ),
        sa.CheckConstraint(
            "max_total_content_bytes >= max_artifact_bytes",
            name="ck_capacity_state_total_at_least_single",
        ),
        sa.CheckConstraint(
            "artifact_count BETWEEN 0 AND max_artifacts",
            name="ck_capacity_state_artifact_count",
        ),
        sa.CheckConstraint(
            "total_content_bytes BETWEEN 0 AND max_total_content_bytes",
            name="ck_capacity_state_total_content_bytes",
        ),
        schema="artifact_store",
    )


def downgrade() -> None:
    raise ArtifactPersistenceConfigurationError() from None
