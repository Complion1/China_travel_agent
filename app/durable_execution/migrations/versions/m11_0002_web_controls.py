"""Add persistent web-control evidence to existing runs."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.durable_execution.errors import DurableExecutionConfigurationError


revision = "m11_0002"
down_revision = "m11_0001"
branch_labels = None
depends_on = None

SCHEMA = "durable_execution"


def upgrade() -> None:
    op.add_column("runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.add_column("runs", sa.Column("cancel_terminal_bytes", sa.LargeBinary(), nullable=True), schema=SCHEMA)
    op.add_column("runs", sa.Column("cancel_terminal_digest", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("runs", sa.Column("cancel_base_checkpoint_id", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("runs", sa.Column("cancel_base_checkpoint_digest", sa.Text(), nullable=True), schema=SCHEMA)

    op.create_check_constraint(
        "ck_runs_cancel_terminal_fields",
        "runs",
        "(cancel_terminal_bytes IS NULL AND cancel_terminal_digest IS NULL AND "
        "cancel_base_checkpoint_id IS NULL AND cancel_base_checkpoint_digest IS NULL) OR "
        "(cancel_terminal_bytes IS NOT NULL AND cancel_terminal_digest IS NOT NULL AND "
        "cancel_base_checkpoint_id IS NOT NULL AND cancel_base_checkpoint_digest IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_runs_cancel_terminal_lifecycle",
        "runs",
        "cancel_terminal_bytes IS NULL OR "
        "(cancel_requested_at IS NOT NULL AND lifecycle = 'closed' AND finalized_at IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_runs_cancel_closed_requires_terminal",
        "runs",
        "cancel_requested_at IS NULL OR lifecycle <> 'closed' OR cancel_terminal_bytes IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_runs_cancel_terminal_bytes",
        "runs",
        "cancel_terminal_bytes IS NULL OR octet_length(cancel_terminal_bytes) BETWEEN 1 AND 1048576",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_runs_cancel_terminal_digest",
        "runs",
        "cancel_terminal_digest IS NULL OR cancel_terminal_digest ~ '^sha256:[0-9a-f]{64}$'",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_runs_cancel_base_checkpoint_id",
        "runs",
        "cancel_base_checkpoint_id IS NULL OR char_length(cancel_base_checkpoint_id) BETWEEN 1 AND 200",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_runs_cancel_base_checkpoint_digest",
        "runs",
        "cancel_base_checkpoint_digest IS NULL OR cancel_base_checkpoint_digest ~ '^sha256:[0-9a-f]{64}$'",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_runs_created_at_run_id_desc",
        "runs",
        [sa.text("created_at DESC"), sa.text("run_id DESC")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    raise DurableExecutionConfigurationError() from None
