"""Create the Module 11 durable execution tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.durable_execution.errors import DurableExecutionConfigurationError


revision = "m11_0001"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = "durable_execution"
SAFE_INTEGER_MAX = 2**53 - 1
MAX_PAYLOAD_BYTES = 1_048_576

_PHASES = (
    "requirements",
    "hotel_selection",
    "candidate_research",
    "itinerary_planning",
    "validation",
    "budget_evaluation",
    "output_formatting",
)
_BLOCKED_REASONS = (
    "contract_mismatch",
    "budget_mismatch",
    "checkpoint_invalid",
    "checkpoint_lineage_invalid",
    "journal_invalid",
    "artifact_invalid",
    "pending_command_invalid",
    "node_classification_invalid",
    "non_recoverable_dispatch",
    "operation_indeterminate",
    "recursion_limit_exceeded",
    "state_invalid",
)
_JOURNAL_STATES = (
    "execution_permitted",
    "budget_rejected",
    "artifact_confirmed",
    "checkpoint_confirmed",
    "completed",
    "indeterminate",
)
_JOURNAL_REASONS = (
    "external_result_unknown",
    "cancelled_after_permission",
    "lease_lost_after_permission",
    "artifact_missing_after_permission",
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value + "'" for value in values)


def upgrade() -> None:
    op.create_table(
        "capacity_state",
        sa.Column("singleton_id", sa.SmallInteger(), nullable=False, autoincrement=False),
        sa.Column("max_active_runs", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("max_stored_runs", sa.Integer(), nullable=False, server_default="16384"),
        sa.Column("max_managed_operations_per_run", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("max_journal_transitions_per_operation", sa.SmallInteger(), nullable=False, server_default="4"),
        sa.Column("max_journal_rows_per_run", sa.Integer(), nullable=False, server_default="16384"),
        sa.Column("max_payload_bytes", sa.Integer(), nullable=False, server_default="1048576"),
        sa.Column("max_lineage_checkpoints", sa.Integer(), nullable=False, server_default="256"),
        sa.Column("recursion_limit", sa.Integer(), nullable=False, server_default="256"),
        sa.Column("lease_duration_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("lease_renew_interval_seconds", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("active_run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stored_run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("singleton_id", name="pk_capacity_state"),
        sa.CheckConstraint("singleton_id = 1", name="ck_capacity_state_singleton"),
        sa.CheckConstraint("max_active_runs BETWEEN 1 AND 4096", name="ck_capacity_state_max_active_runs"),
        sa.CheckConstraint("max_stored_runs BETWEEN max_active_runs AND 16384", name="ck_capacity_state_max_stored_runs"),
        sa.CheckConstraint("max_managed_operations_per_run BETWEEN 1 AND 4096", name="ck_capacity_state_max_operations"),
        sa.CheckConstraint("max_journal_transitions_per_operation = 4", name="ck_capacity_state_max_transitions"),
        sa.CheckConstraint("max_journal_rows_per_run = max_managed_operations_per_run * max_journal_transitions_per_operation", name="ck_capacity_state_max_journal_rows"),
        sa.CheckConstraint("max_journal_rows_per_run BETWEEN 1 AND 16384", name="ck_capacity_state_journal_rows_range"),
        sa.CheckConstraint(f"max_payload_bytes BETWEEN 1 AND {MAX_PAYLOAD_BYTES}", name="ck_capacity_state_max_payload"),
        sa.CheckConstraint("max_lineage_checkpoints BETWEEN 1 AND 256", name="ck_capacity_state_max_lineage"),
        sa.CheckConstraint("recursion_limit = 256", name="ck_capacity_state_recursion"),
        sa.CheckConstraint("lease_duration_seconds = 60", name="ck_capacity_state_lease_duration"),
        sa.CheckConstraint("lease_renew_interval_seconds = 15 AND lease_renew_interval_seconds < lease_duration_seconds", name="ck_capacity_state_lease_renew"),
        sa.CheckConstraint("active_run_count BETWEEN 0 AND max_active_runs", name="ck_capacity_state_active_count"),
        sa.CheckConstraint("stored_run_count BETWEEN active_run_count AND max_stored_runs", name="ck_capacity_state_stored_count"),
        schema=SCHEMA,
    )

    op.create_table(
        "runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle", sa.String(length=8), nullable=False),
        sa.Column("bootstrap_state", sa.String(length=24), nullable=False),
        sa.Column("contract_id", sa.String(length=128), nullable=False),
        sa.Column("contract_schema_version", sa.String(length=16), nullable=False),
        sa.Column("contract_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("contract_digest", sa.CHAR(length=71), nullable=False),
        sa.Column("contract_size_bytes", sa.Integer(), nullable=False),
        sa.Column("latest_root_checkpoint_ns", sa.Text(), nullable=True),
        sa.Column("latest_root_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("latest_root_checkpoint_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("pause_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("managed_operation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("journal_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_reason_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_runs"),
        sa.CheckConstraint("lifecycle IN ('open', 'closed', 'blocked')", name="ck_runs_lifecycle"),
        sa.CheckConstraint("bootstrap_state IN ('bootstrap_pending', 'bootstrap_checkpointed')", name="ck_runs_bootstrap_state"),
        sa.CheckConstraint("contract_id ~ '^[a-z][a-z0-9_.-]{0,127}$'", name="ck_runs_contract_id"),
        sa.CheckConstraint("contract_schema_version = '1.0'", name="ck_runs_contract_version"),
        sa.CheckConstraint(f"octet_length(contract_bytes) BETWEEN 1 AND {MAX_PAYLOAD_BYTES}", name="ck_runs_contract_bytes_range"),
        sa.CheckConstraint("contract_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runs_contract_digest"),
        sa.CheckConstraint("contract_size_bytes = octet_length(contract_bytes)", name="ck_runs_contract_size"),
        sa.CheckConstraint("pause_generation >= 0", name="ck_runs_pause_generation"),
        sa.CheckConstraint("managed_operation_count BETWEEN 0 AND 4096", name="ck_runs_operation_count"),
        sa.CheckConstraint("journal_row_count BETWEEN 0 AND 16384", name="ck_runs_journal_count"),
        sa.CheckConstraint("blocked_reason_code IS NULL OR blocked_reason_code IN (" + _in(_BLOCKED_REASONS) + ")", name="ck_runs_blocked_reason"),
        sa.CheckConstraint("(lifecycle = 'blocked') = (blocked_reason_code IS NOT NULL)", name="ck_runs_blocked_reason_lifecycle"),
        sa.CheckConstraint("(lifecycle = 'open') = (finalized_at IS NULL)", name="ck_runs_finalized_at"),
        sa.CheckConstraint("(latest_root_checkpoint_ns IS NULL AND latest_root_checkpoint_id IS NULL AND latest_root_checkpoint_digest IS NULL) OR (latest_root_checkpoint_ns = '' AND latest_root_checkpoint_id ~ '^.{1,200}$' AND latest_root_checkpoint_digest ~ '^sha256:[0-9a-f]{64}$')", name="ck_runs_checkpoint_pointer"),
        sa.CheckConstraint("bootstrap_state = 'bootstrap_pending' OR (latest_root_checkpoint_ns = '' AND latest_root_checkpoint_id IS NOT NULL AND latest_root_checkpoint_digest IS NOT NULL)", name="ck_runs_bootstrap_pointer"),
        sa.CheckConstraint("lifecycle <> 'closed' OR (latest_root_checkpoint_ns = '' AND latest_root_checkpoint_id IS NOT NULL AND latest_root_checkpoint_digest IS NOT NULL)", name="ck_runs_closed_pointer"),
        schema=SCHEMA,
    )

    op.create_table(
        "run_leases",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("lease_owner", sa.Uuid(), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_run_leases"),
        sa.ForeignKeyConstraint(("run_id",), (f"{SCHEMA}.runs.run_id",), name="fk_run_leases_run", ondelete="RESTRICT"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_run_leases_fencing_token"),
        sa.CheckConstraint("(lease_owner IS NULL AND expires_at IS NULL AND renewed_at IS NULL) OR (lease_owner IS NOT NULL AND expires_at IS NOT NULL AND renewed_at IS NOT NULL)", name="ck_run_leases_fields"),
        schema=SCHEMA,
    )

    op.create_table(
        "budget_limits",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=65), nullable=False),
        sa.Column("phase_code", sa.String(length=32), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=False),
        sa.Column("consumed_value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("activation_ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("activation_digest", sa.CHAR(length=71), nullable=False),
        sa.Column("activation_checkpoint_ns", sa.Text(), nullable=True),
        sa.Column("activation_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("activation_checkpoint_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("run_id", "scope", "name", "phase_code", name="pk_budget_limits"),
        sa.ForeignKeyConstraint(("run_id",), (f"{SCHEMA}.runs.run_id",), name="fk_budget_limits_run", ondelete="RESTRICT"),
        sa.CheckConstraint("scope IN ('run', 'phase')", name="ck_budget_limits_scope"),
        sa.CheckConstraint("name ~ '^[a-z][a-z0-9_]{0,31}\\.[a-z][a-z0-9_]{0,31}$'", name="ck_budget_limits_name"),
        sa.CheckConstraint("(scope = 'run' AND phase_code = '') OR (scope = 'phase' AND phase_code IN (" + _in(_PHASES) + "))", name="ck_budget_limits_scope_phase"),
        sa.CheckConstraint("(scope = 'run' AND phase_code = '' AND activation_ordinal = 0) OR (scope = 'phase' AND ((phase_code = 'requirements' AND activation_ordinal = 0) OR (phase_code = 'hotel_selection' AND activation_ordinal = 1) OR (phase_code = 'candidate_research' AND activation_ordinal = 2) OR (phase_code = 'itinerary_planning' AND activation_ordinal = 3) OR (phase_code = 'validation' AND activation_ordinal = 4) OR (phase_code = 'budget_evaluation' AND activation_ordinal = 5) OR (phase_code = 'output_formatting' AND activation_ordinal = 6)))", name="ck_budget_limits_activation_phase"),
        sa.CheckConstraint(f"limit_value BETWEEN 0 AND {SAFE_INTEGER_MAX}", name="ck_budget_limits_limit"),
        sa.CheckConstraint("consumed_value BETWEEN 0 AND limit_value", name="ck_budget_limits_consumed"),
        sa.CheckConstraint("activation_ordinal BETWEEN 0 AND 6", name="ck_budget_limits_activation_ordinal"),
        sa.CheckConstraint("activation_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_budget_limits_activation_digest"),
        sa.CheckConstraint("(activation_ordinal = 0 AND activation_checkpoint_ns IS NULL AND activation_checkpoint_id IS NULL AND activation_checkpoint_digest IS NULL) OR (activation_ordinal BETWEEN 1 AND 6 AND activation_checkpoint_ns = '' AND activation_checkpoint_id ~ '^.{1,200}$' AND activation_checkpoint_digest ~ '^sha256:[0-9a-f]{64}$')", name="ck_budget_limits_activation_pointer"),
        schema=SCHEMA,
    )

    op.create_table(
        "budget_operations",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("operation_phase", sa.String(length=32), nullable=False),
        sa.Column("operation_logical_step", sa.String(length=64), nullable=False),
        sa.Column("budget_scope", sa.String(length=8), nullable=False),
        sa.Column("budget_name", sa.String(length=65), nullable=False),
        sa.Column("budget_phase_code", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("snapshot_limit", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_consumed", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_remaining", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("run_id", "operation_phase", "operation_logical_step", name="pk_budget_operations"),
        sa.ForeignKeyConstraint(("run_id",), (f"{SCHEMA}.runs.run_id",), name="fk_budget_operations_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(("run_id", "budget_scope", "budget_name", "budget_phase_code"), (f"{SCHEMA}.budget_limits.run_id", f"{SCHEMA}.budget_limits.scope", f"{SCHEMA}.budget_limits.name", f"{SCHEMA}.budget_limits.phase_code"), name="fk_budget_operations_limit", ondelete="RESTRICT"),
        sa.CheckConstraint("operation_phase IN (" + _in(_PHASES) + ")", name="ck_budget_operations_phase"),
        sa.CheckConstraint("operation_logical_step ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_budget_operations_logical_step"),
        sa.CheckConstraint("budget_scope IN ('run', 'phase')", name="ck_budget_operations_scope"),
        sa.CheckConstraint("budget_name ~ '^[a-z][a-z0-9_]{0,31}\\.[a-z][a-z0-9_]{0,31}$'", name="ck_budget_operations_name"),
        sa.CheckConstraint("(budget_scope = 'run' AND budget_phase_code = '') OR (budget_scope = 'phase' AND budget_phase_code IN (" + _in(_PHASES) + "))", name="ck_budget_operations_scope_phase"),
        sa.CheckConstraint(f"amount BETWEEN 1 AND {SAFE_INTEGER_MAX}", name="ck_budget_operations_amount"),
        sa.CheckConstraint("outcome IN ('consumed', 'exceeded')", name="ck_budget_operations_outcome"),
        sa.CheckConstraint(f"snapshot_limit BETWEEN 0 AND {SAFE_INTEGER_MAX}", name="ck_budget_operations_snapshot_limit"),
        sa.CheckConstraint("snapshot_consumed BETWEEN 0 AND snapshot_limit", name="ck_budget_operations_snapshot_consumed"),
        sa.CheckConstraint("snapshot_remaining = snapshot_limit - snapshot_consumed", name="ck_budget_operations_snapshot_remaining"),
        schema=SCHEMA,
    )

    op.create_table(
        "operation_journal",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("operation_phase", sa.String(length=32), nullable=False),
        sa.Column("operation_logical_step", sa.String(length=64), nullable=False),
        sa.Column("transition_ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=False),
        sa.Column("journal_state", sa.String(length=24), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_type", sa.String(length=65), nullable=True),
        sa.Column("artifact_schema_version", sa.String(length=16), nullable=True),
        sa.Column("artifact_content_hash", sa.CHAR(length=71), nullable=True),
        sa.Column("artifact_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checkpoint_ns", sa.Text(), nullable=True),
        sa.Column("checkpoint_id", sa.Text(), nullable=True),
        sa.Column("checkpoint_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("run_id", "operation_phase", "operation_logical_step", "transition_ordinal", name="pk_operation_journal"),
        sa.UniqueConstraint("run_id", "operation_phase", "operation_logical_step", "journal_state", name="uq_operation_journal_state"),
        sa.ForeignKeyConstraint(("run_id",), (f"{SCHEMA}.runs.run_id",), name="fk_operation_journal_run", ondelete="RESTRICT"),
        sa.CheckConstraint("operation_phase IN (" + _in(_PHASES) + ")", name="ck_operation_journal_phase"),
        sa.CheckConstraint("operation_logical_step ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_operation_journal_logical_step"),
        sa.CheckConstraint("transition_ordinal BETWEEN 1 AND 4", name="ck_operation_journal_ordinal"),
        sa.CheckConstraint("node_name ~ '^.{1,128}$' AND node_name !~ '[[:cntrl:]]'", name="ck_operation_journal_node_name"),
        sa.CheckConstraint("journal_state IN (" + _in(_JOURNAL_STATES) + ")", name="ck_operation_journal_state"),
        sa.CheckConstraint("(artifact_id IS NULL AND artifact_type IS NULL AND artifact_schema_version IS NULL AND artifact_content_hash IS NULL AND artifact_created_at IS NULL AND artifact_size_bytes IS NULL) OR (artifact_id IS NOT NULL AND artifact_type IS NOT NULL AND artifact_schema_version IS NOT NULL AND artifact_content_hash IS NOT NULL AND artifact_created_at IS NOT NULL AND artifact_size_bytes IS NOT NULL)", name="ck_operation_journal_artifact_fields"),
        sa.CheckConstraint("artifact_type IS NULL OR artifact_type ~ '^[a-z][a-z0-9_]{0,31}\\.[a-z][a-z0-9_]{0,31}$'", name="ck_operation_journal_artifact_type"),
        sa.CheckConstraint("artifact_schema_version IS NULL OR artifact_schema_version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'", name="ck_operation_journal_artifact_version"),
        sa.CheckConstraint("artifact_content_hash IS NULL OR artifact_content_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_operation_journal_artifact_hash"),
        sa.CheckConstraint(f"artifact_size_bytes IS NULL OR artifact_size_bytes BETWEEN 1 AND {SAFE_INTEGER_MAX}", name="ck_operation_journal_artifact_size"),
        sa.CheckConstraint("(checkpoint_ns IS NULL AND checkpoint_id IS NULL AND checkpoint_digest IS NULL) OR (checkpoint_ns = '' AND checkpoint_id ~ '^.{1,200}$' AND checkpoint_digest ~ '^sha256:[0-9a-f]{64}$')", name="ck_operation_journal_checkpoint_fields"),
        sa.CheckConstraint("reason_code IS NULL OR reason_code IN (" + _in(_JOURNAL_REASONS) + ")", name="ck_operation_journal_reason"),
        sa.CheckConstraint("(journal_state = 'indeterminate') = (reason_code IS NOT NULL)", name="ck_operation_journal_reason_state"),
        sa.CheckConstraint("(journal_state = 'artifact_confirmed' AND artifact_id IS NOT NULL AND artifact_type IS NOT NULL AND artifact_schema_version IS NOT NULL AND artifact_content_hash IS NOT NULL AND artifact_created_at IS NOT NULL AND artifact_size_bytes IS NOT NULL) OR (journal_state IN ('execution_permitted', 'budget_rejected', 'indeterminate') AND artifact_id IS NULL AND artifact_type IS NULL AND artifact_schema_version IS NULL AND artifact_content_hash IS NULL AND artifact_created_at IS NULL AND artifact_size_bytes IS NULL) OR (journal_state IN ('checkpoint_confirmed', 'completed') AND ((artifact_id IS NULL AND artifact_type IS NULL AND artifact_schema_version IS NULL AND artifact_content_hash IS NULL AND artifact_created_at IS NULL AND artifact_size_bytes IS NULL) OR (artifact_id IS NOT NULL AND artifact_type IS NOT NULL AND artifact_schema_version IS NOT NULL AND artifact_content_hash IS NOT NULL AND artifact_created_at IS NOT NULL AND artifact_size_bytes IS NOT NULL)))", name="ck_operation_journal_artifact_state"),
        sa.CheckConstraint("(journal_state IN ('checkpoint_confirmed', 'completed') AND checkpoint_ns IS NOT NULL AND checkpoint_id IS NOT NULL AND checkpoint_digest IS NOT NULL) OR (journal_state IN ('execution_permitted', 'budget_rejected', 'artifact_confirmed', 'indeterminate') AND checkpoint_ns IS NULL AND checkpoint_id IS NULL AND checkpoint_digest IS NULL)", name="ck_operation_journal_checkpoint_state"),
        schema=SCHEMA,
    )

    op.create_table(
        "pending_commands",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("pause_generation", sa.Integer(), nullable=False),
        sa.Column("command_kind", sa.String(length=8), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=False),
        sa.Column("command_type_id", sa.String(length=128), nullable=True),
        sa.Column("command_type_version", sa.String(length=16), nullable=True),
        sa.Column("command_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("command_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("command_size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pause_token_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("pause_context_type", sa.String(length=64), nullable=True),
        sa.Column("pause_context_version", sa.String(length=16), nullable=True),
        sa.Column("pause_context_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("pause_context_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("pause_context_size_bytes", sa.Integer(), nullable=True),
        sa.Column("pre_checkpoint_ns", sa.Text(), nullable=True),
        sa.Column("pre_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("pre_checkpoint_digest", sa.CHAR(length=71), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpointed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", "pause_generation", name="pk_pending_commands"),
        sa.ForeignKeyConstraint(("run_id",), (f"{SCHEMA}.runs.run_id",), name="fk_pending_commands_run", ondelete="RESTRICT"),
        sa.CheckConstraint("pause_generation >= 0", name="ck_pending_commands_generation"),
        sa.CheckConstraint("command_kind IN ('initial', 'resume')", name="ck_pending_commands_kind"),
        sa.CheckConstraint("node_name ~ '^.{1,128}$' AND node_name !~ '[[:cntrl:]]'", name="ck_pending_commands_node_name"),
        sa.CheckConstraint("command_type_id IS NULL OR command_type_id ~ '^[a-z][a-z0-9_.-]{0,127}$'", name="ck_pending_commands_type_id"),
        sa.CheckConstraint("command_type_version IS NULL OR command_type_version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'", name="ck_pending_commands_type_version"),
        sa.CheckConstraint("(command_type_id IS NULL AND command_type_version IS NULL AND command_digest IS NULL AND command_size_bytes IS NULL) OR (command_type_id IS NOT NULL AND command_type_version IS NOT NULL AND command_digest IS NOT NULL AND command_size_bytes IS NOT NULL)", name="ck_pending_commands_type_fields"),
        sa.CheckConstraint(f"command_bytes IS NULL OR octet_length(command_bytes) BETWEEN 1 AND {MAX_PAYLOAD_BYTES}", name="ck_pending_commands_bytes"),
        sa.CheckConstraint("command_digest IS NULL OR command_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_pending_commands_digest"),
        sa.CheckConstraint(f"command_size_bytes IS NULL OR command_size_bytes BETWEEN 1 AND {MAX_PAYLOAD_BYTES}", name="ck_pending_commands_size"),
        sa.CheckConstraint("command_bytes IS NULL OR command_size_bytes = octet_length(command_bytes)", name="ck_pending_commands_command_size"),
        sa.CheckConstraint("status IN ('awaiting_input', 'accepted', 'dispatch_started', 'checkpointed', 'cleared')", name="ck_pending_commands_status"),
        sa.CheckConstraint("pause_token_digest IS NULL OR pause_token_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_pending_commands_token_digest"),
        sa.CheckConstraint("pause_context_type IS NULL OR pause_context_type IN ('requirement_subgraph.pause', 'durable_execution.root_pause')", name="ck_pending_commands_context_type"),
        sa.CheckConstraint("pause_context_version IS NULL OR pause_context_version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'", name="ck_pending_commands_context_version"),
        sa.CheckConstraint("(pause_context_type IS NULL AND pause_context_version IS NULL) OR (pause_context_type = 'requirement_subgraph.pause' AND pause_context_version = '1.0') OR (pause_context_type = 'durable_execution.root_pause' AND pause_context_version = '1.0')", name="ck_pending_commands_context_contract"),
        sa.CheckConstraint("(pause_context_type IS NULL AND pause_context_version IS NULL AND pause_context_digest IS NULL AND pause_context_size_bytes IS NULL) OR (pause_context_type IS NOT NULL AND pause_context_version IS NOT NULL AND pause_context_digest IS NOT NULL AND pause_context_size_bytes IS NOT NULL)", name="ck_pending_commands_context_fields"),
        sa.CheckConstraint(f"pause_context_bytes IS NULL OR octet_length(pause_context_bytes) BETWEEN 1 AND {MAX_PAYLOAD_BYTES}", name="ck_pending_commands_context_bytes"),
        sa.CheckConstraint("pause_context_digest IS NULL OR pause_context_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_pending_commands_context_digest"),
        sa.CheckConstraint(f"pause_context_size_bytes IS NULL OR pause_context_size_bytes BETWEEN 1 AND {MAX_PAYLOAD_BYTES}", name="ck_pending_commands_context_size"),
        sa.CheckConstraint("pause_context_bytes IS NULL OR pause_context_size_bytes = octet_length(pause_context_bytes)", name="ck_pending_commands_context_size_match"),
        sa.CheckConstraint("(pre_checkpoint_ns IS NULL AND pre_checkpoint_id IS NULL AND pre_checkpoint_digest IS NULL) OR (pre_checkpoint_ns = '' AND pre_checkpoint_id ~ '^.{1,200}$' AND pre_checkpoint_digest ~ '^sha256:[0-9a-f]{64}$')", name="ck_pending_commands_pre_checkpoint"),
        sa.CheckConstraint("(pause_generation = 0 AND command_kind = 'initial' AND node_name = '__start__' AND status IN ('accepted', 'dispatch_started', 'checkpointed', 'cleared') AND pause_token_digest IS NULL AND pause_context_type IS NULL AND pause_context_version IS NULL AND pause_context_bytes IS NULL AND pause_context_digest IS NULL AND pause_context_size_bytes IS NULL AND pre_checkpoint_ns IS NULL AND pre_checkpoint_id IS NULL AND pre_checkpoint_digest IS NULL) OR (pause_generation > 0 AND command_kind = 'resume' AND node_name <> '__start__' AND pause_token_digest IS NOT NULL AND pause_context_type IS NOT NULL AND pause_context_version IS NOT NULL AND pause_context_digest IS NOT NULL AND pause_context_size_bytes IS NOT NULL AND pre_checkpoint_ns = '' AND pre_checkpoint_id IS NOT NULL AND pre_checkpoint_digest IS NOT NULL)", name="ck_pending_commands_generation_contract"),
        sa.CheckConstraint("(status = 'awaiting_input' AND command_type_id IS NULL AND command_type_version IS NULL AND command_digest IS NULL AND command_size_bytes IS NULL) OR (status IN ('accepted', 'dispatch_started', 'checkpointed') AND command_type_id IS NOT NULL AND command_type_version IS NOT NULL AND command_digest IS NOT NULL AND command_size_bytes IS NOT NULL) OR (status = 'cleared' AND ((command_type_id IS NULL AND command_type_version IS NULL AND command_digest IS NULL AND command_size_bytes IS NULL) OR (command_type_id IS NOT NULL AND command_type_version IS NOT NULL AND command_digest IS NOT NULL AND command_size_bytes IS NOT NULL)))", name="ck_pending_commands_command_metadata_lifecycle"),
        sa.CheckConstraint("(status IN ('accepted', 'dispatch_started') AND command_bytes IS NOT NULL) OR (status IN ('awaiting_input', 'checkpointed', 'cleared') AND command_bytes IS NULL)", name="ck_pending_commands_command_lifecycle"),
        sa.CheckConstraint("(status = 'awaiting_input' AND accepted_at IS NULL AND dispatch_started_at IS NULL AND checkpointed_at IS NULL AND cleared_at IS NULL) OR (status = 'accepted' AND accepted_at IS NOT NULL AND dispatch_started_at IS NULL AND checkpointed_at IS NULL AND cleared_at IS NULL) OR (status = 'dispatch_started' AND accepted_at IS NOT NULL AND dispatch_started_at IS NOT NULL AND checkpointed_at IS NULL AND cleared_at IS NULL) OR (status = 'checkpointed' AND accepted_at IS NOT NULL AND dispatch_started_at IS NOT NULL AND checkpointed_at IS NOT NULL AND cleared_at IS NULL) OR (status = 'cleared' AND checkpointed_at IS NULL AND cleared_at IS NOT NULL AND (dispatch_started_at IS NULL OR accepted_at IS NOT NULL) AND ((command_type_id IS NULL AND command_type_version IS NULL AND command_digest IS NULL AND command_size_bytes IS NULL AND accepted_at IS NULL) OR (command_type_id IS NOT NULL AND command_type_version IS NOT NULL AND command_digest IS NOT NULL AND command_size_bytes IS NOT NULL AND accepted_at IS NOT NULL)))", name="ck_pending_commands_timestamps"),
        sa.CheckConstraint("(pause_generation = 0 AND pause_context_bytes IS NULL) OR (pause_generation > 0 AND status IN ('awaiting_input', 'accepted', 'dispatch_started') AND pause_context_bytes IS NOT NULL) OR (pause_generation > 0 AND status IN ('checkpointed', 'cleared') AND pause_context_bytes IS NULL)", name="ck_pending_commands_context_lifecycle"),
        schema=SCHEMA,
    )

    op.create_index("ix_runs_lifecycle", "runs", ["lifecycle"], schema=SCHEMA)
    op.create_index(
        "ix_run_leases_expires_at",
        "run_leases",
        ["expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("lease_owner IS NOT NULL"),
    )
    op.create_index(
        "ix_operation_journal_operation",
        "operation_journal",
        ["run_id", "operation_phase", "operation_logical_step", sa.text("transition_ordinal DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pending_commands_run_generation",
        "pending_commands",
        ["run_id", sa.text("pause_generation DESC")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    raise DurableExecutionConfigurationError() from None
