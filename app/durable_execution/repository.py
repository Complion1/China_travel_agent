"""Module 11 的结构检查和私有协调事务实现。

本模块的实现对象只由 Module 11 的后续阶段使用，不从包根导出。所有业务 SQL
使用固定 schema-qualified 标识符和值参数；连接只在短数据库事务内借用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import inspect
import json
import re
import unicodedata
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID

from pydantic import TypeAdapter

from app.workflow_contracts import (
    BudgetConsumed,
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetKey,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    ExecutionBudgetSnapshot,
    OperationId,
    WorkflowPhase,
)
from app.workflow_contracts.errors import (
    BudgetConsumeConflictError,
    BudgetKeyNotConfiguredError,
    BudgetOperationScopeMismatchError,
    ExecutionBudgetCapacityExceededError,
    ExecutionBudgetManagerUsageError,
    ExecutionBudgetPlanValidationError,
    ExecutionBudgetRequestValidationError,
)

from app.durable_execution.errors import (
    DurableExecutionCapacityExceededError,
    DurableExecutionConfigurationError,
    DurableExecutionDependencyError,
    DurableExecutionError,
    DurableExecutionRunAlreadyExistsError,
    DurableExecutionRunBlockedError,
    DurableExecutionRunBusyError,
    DurableExecutionRunNotFoundError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
)
from app.durable_execution.models import (
    DurableExecutionContract,
    DurableExecutionEncodedCommand,
    DurableExecutionLimits,
    contract_canonical_bytes,
)


SCHEMA_NAME = "durable_execution"
EXPECTED_REVISION = "m11_0001"
EXPECTED_BUSINESS_TABLES = (
    "capacity_state",
    "runs",
    "run_leases",
    "budget_limits",
    "budget_operations",
    "operation_journal",
    "pending_commands",
)
EXPECTED_SAVER_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
EXPECTED_TABLES = ("alembic_version",) + EXPECTED_BUSINESS_TABLES + EXPECTED_SAVER_TABLES
EXPECTED_SAVER_MIGRATION_VERSIONS = tuple(range(10))
EXPECTED_EXPLICIT_INDEXES = (
    "ix_runs_lifecycle",
    "ix_run_leases_expires_at",
    "ix_operation_journal_operation",
    "ix_pending_commands_run_generation",
)


def migration_advisory_key(*, domain: str, payload: bytes = b"") -> int:
    """Placeholder-free deterministic key shared by the migration entry point."""

    import hashlib

    if type(domain) is not str or domain not in {"migration", "capacity", "run"}:
        raise DurableExecutionConfigurationError() from None
    if type(payload) is not bytes:
        raise DurableExecutionConfigurationError() from None
    if domain in {"migration", "capacity"} and payload != b"":
        raise DurableExecutionConfigurationError() from None
    if domain == "run":
        try:
            canonical = str(UUID(payload.decode("ascii")))
        except Exception:
            raise DurableExecutionConfigurationError() from None
        if payload != canonical.encode("ascii"):
            raise DurableExecutionConfigurationError() from None
    return int.from_bytes(
        hashlib.sha256(
            b"durable-execution-advisory-v1\x00"
            + domain.encode("ascii")
            + b"\x00"
            + payload
        ).digest()[:8],
        "big",
        signed=True,
    )


async def _fetchall(connection, query: str, params: Sequence[object] = ()) -> tuple[tuple[object, ...], ...]:
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(tuple(row) for row in rows)
    except (DurableExecutionConfigurationError, DurableExecutionDependencyError):
        raise
    except Exception:
        raise DurableExecutionDependencyError() from None


async def check_durable_execution_schema(
    connection,
    *,
    expected_revision: str = EXPECTED_REVISION,
) -> None:
    """严格拒绝缺表、额外表、版本或约束结构漂移。"""

    try:
        table_rows = await _fetchall(
            connection,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (SCHEMA_NAME,),
        )
        actual_tables = tuple(row[0] for row in table_rows)
        if actual_tables != tuple(sorted(EXPECTED_TABLES)):
            raise DurableExecutionConfigurationError() from None

        await _check_columns(connection)

        revision_rows = await _fetchall(
            connection,
            "SELECT version_num FROM durable_execution.alembic_version ORDER BY version_num",
        )
        if revision_rows != ((expected_revision,),):
            raise DurableExecutionConfigurationError() from None

        public_version = await _fetchall(
            connection,
            "SELECT to_regclass('public.alembic_version')",
        )
        if public_version != ((None,),):
            raise DurableExecutionConfigurationError() from None

        await _check_constraints(connection)
        await _check_indexes(connection)
        await _check_saver_migrations(connection)
        await _check_connection_options(connection)
    except (DurableExecutionConfigurationError, DurableExecutionDependencyError):
        raise
    except Exception:
        raise DurableExecutionDependencyError() from None


_EXPECTED_COLUMNS: dict[str, tuple[tuple[str, str, str, str, str | None], ...]] = {
    "capacity_state": (
        ("singleton_id", "smallint", "int2", "NOT_NULL", None),
        ("max_active_runs", "integer", "int4", "NOT_NULL", "4096"),
        ("max_stored_runs", "integer", "int4", "NOT_NULL", "16384"),
        ("max_managed_operations_per_run", "integer", "int4", "NOT_NULL", "4096"),
        ("max_journal_transitions_per_operation", "smallint", "int2", "NOT_NULL", "4"),
        ("max_journal_rows_per_run", "integer", "int4", "NOT_NULL", "16384"),
        ("max_payload_bytes", "integer", "int4", "NOT_NULL", "1048576"),
        ("max_lineage_checkpoints", "integer", "int4", "NOT_NULL", "256"),
        ("recursion_limit", "integer", "int4", "NOT_NULL", "256"),
        ("lease_duration_seconds", "integer", "int4", "NOT_NULL", "60"),
        ("lease_renew_interval_seconds", "integer", "int4", "NOT_NULL", "15"),
        ("active_run_count", "integer", "int4", "NOT_NULL", "0"),
        ("stored_run_count", "integer", "int4", "NOT_NULL", "0"),
        ("created_at", "timestamp with time zone", "timestamptz", "NOT_NULL", "clock_timestamp()"),
    ),
    "runs": (
        ("run_id", "uuid", "uuid", "NOT_NULL", None),
        ("trace_id", "uuid", "uuid", "NOT_NULL", None),
        ("lifecycle", "character varying", "varchar", "NOT_NULL", None),
        ("bootstrap_state", "character varying", "varchar", "NOT_NULL", None),
        ("contract_id", "character varying", "varchar", "NOT_NULL", None),
        ("contract_schema_version", "character varying", "varchar", "NOT_NULL", None),
        ("contract_bytes", "bytea", "bytea", "NOT_NULL", None),
        ("contract_digest", "character", "bpchar", "NOT_NULL", None),
        ("contract_size_bytes", "integer", "int4", "NOT_NULL", None),
        ("latest_root_checkpoint_ns", "text", "text", "NULL", None),
        ("latest_root_checkpoint_id", "text", "text", "NULL", None),
        ("latest_root_checkpoint_digest", "character", "bpchar", "NULL", None),
        ("pause_generation", "integer", "int4", "NOT_NULL", "0"),
        ("managed_operation_count", "integer", "int4", "NOT_NULL", "0"),
        ("journal_row_count", "integer", "int4", "NOT_NULL", "0"),
        ("blocked_reason_code", "character varying", "varchar", "NULL", None),
        ("created_at", "timestamp with time zone", "timestamptz", "NOT_NULL", "clock_timestamp()"),
        ("finalized_at", "timestamp with time zone", "timestamptz", "NULL", None),
    ),
    "run_leases": (
        ("run_id", "uuid", "uuid", "NOT_NULL", None),
        ("lease_owner", "uuid", "uuid", "NULL", None),
        ("fencing_token", "bigint", "int8", "NOT_NULL", "0"),
        ("expires_at", "timestamp with time zone", "timestamptz", "NULL", None),
        ("renewed_at", "timestamp with time zone", "timestamptz", "NULL", None),
    ),
    "budget_limits": (
        ("run_id", "uuid", "uuid", "NOT_NULL", None),
        ("scope", "character varying", "varchar", "NOT_NULL", None),
        ("name", "character varying", "varchar", "NOT_NULL", None),
        ("phase_code", "character varying", "varchar", "NOT_NULL", None),
        ("limit_value", "bigint", "int8", "NOT_NULL", None),
        ("consumed_value", "bigint", "int8", "NOT_NULL", "0"),
        ("activation_ordinal", "smallint", "int2", "NOT_NULL", None),
        ("activation_digest", "character", "bpchar", "NOT_NULL", None),
        ("activation_checkpoint_ns", "text", "text", "NULL", None),
        ("activation_checkpoint_id", "text", "text", "NULL", None),
        ("activation_checkpoint_digest", "character", "bpchar", "NULL", None),
        ("created_at", "timestamp with time zone", "timestamptz", "NOT_NULL", "clock_timestamp()"),
    ),
    "budget_operations": (
        ("run_id", "uuid", "uuid", "NOT_NULL", None),
        ("operation_phase", "character varying", "varchar", "NOT_NULL", None),
        ("operation_logical_step", "character varying", "varchar", "NOT_NULL", None),
        ("budget_scope", "character varying", "varchar", "NOT_NULL", None),
        ("budget_name", "character varying", "varchar", "NOT_NULL", None),
        ("budget_phase_code", "character varying", "varchar", "NOT_NULL", None),
        ("amount", "bigint", "int8", "NOT_NULL", None),
        ("outcome", "character varying", "varchar", "NOT_NULL", None),
        ("snapshot_limit", "bigint", "int8", "NOT_NULL", None),
        ("snapshot_consumed", "bigint", "int8", "NOT_NULL", None),
        ("snapshot_remaining", "bigint", "int8", "NOT_NULL", None),
        ("created_at", "timestamp with time zone", "timestamptz", "NOT_NULL", "clock_timestamp()"),
    ),
    "operation_journal": (
        ("run_id", "uuid", "uuid", "NOT_NULL", None),
        ("operation_phase", "character varying", "varchar", "NOT_NULL", None),
        ("operation_logical_step", "character varying", "varchar", "NOT_NULL", None),
        ("transition_ordinal", "smallint", "int2", "NOT_NULL", None),
        ("node_name", "character varying", "varchar", "NOT_NULL", None),
        ("journal_state", "character varying", "varchar", "NOT_NULL", None),
        ("artifact_id", "uuid", "uuid", "NULL", None),
        ("artifact_type", "character varying", "varchar", "NULL", None),
        ("artifact_schema_version", "character varying", "varchar", "NULL", None),
        ("artifact_content_hash", "character", "bpchar", "NULL", None),
        ("artifact_created_at", "timestamp with time zone", "timestamptz", "NULL", None),
        ("artifact_size_bytes", "bigint", "int8", "NULL", None),
        ("checkpoint_ns", "text", "text", "NULL", None),
        ("checkpoint_id", "text", "text", "NULL", None),
        ("checkpoint_digest", "character", "bpchar", "NULL", None),
        ("reason_code", "character varying", "varchar", "NULL", None),
        ("created_at", "timestamp with time zone", "timestamptz", "NOT_NULL", "clock_timestamp()"),
    ),
    "pending_commands": (
        ("run_id", "uuid", "uuid", "NOT_NULL", None),
        ("pause_generation", "integer", "int4", "NOT_NULL", None),
        ("command_kind", "character varying", "varchar", "NOT_NULL", None),
        ("node_name", "character varying", "varchar", "NOT_NULL", None),
        ("command_type_id", "character varying", "varchar", "NULL", None),
        ("command_type_version", "character varying", "varchar", "NULL", None),
        ("command_bytes", "bytea", "bytea", "NULL", None),
        ("command_digest", "character", "bpchar", "NULL", None),
        ("command_size_bytes", "integer", "int4", "NULL", None),
        ("status", "character varying", "varchar", "NOT_NULL", None),
        ("pause_token_digest", "character", "bpchar", "NULL", None),
        ("pause_context_type", "character varying", "varchar", "NULL", None),
        ("pause_context_version", "character varying", "varchar", "NULL", None),
        ("pause_context_bytes", "bytea", "bytea", "NULL", None),
        ("pause_context_digest", "character", "bpchar", "NULL", None),
        ("pause_context_size_bytes", "integer", "int4", "NULL", None),
        ("pre_checkpoint_ns", "text", "text", "NULL", None),
        ("pre_checkpoint_id", "text", "text", "NULL", None),
        ("pre_checkpoint_digest", "character", "bpchar", "NULL", None),
        ("created_at", "timestamp with time zone", "timestamptz", "NOT_NULL", "clock_timestamp()"),
        ("accepted_at", "timestamp with time zone", "timestamptz", "NULL", None),
        ("dispatch_started_at", "timestamp with time zone", "timestamptz", "NULL", None),
        ("checkpointed_at", "timestamp with time zone", "timestamptz", "NULL", None),
        ("cleared_at", "timestamp with time zone", "timestamptz", "NULL", None),
    ),
}


_EXPECTED_COLUMN_FORMATS: dict[str, tuple[tuple[str, str, str, str | None], ...]] = {
    "capacity_state": (
        ("singleton_id", "smallint", "NOT_NULL", None),
        ("max_active_runs", "integer", "NOT_NULL", "4096"),
        ("max_stored_runs", "integer", "NOT_NULL", "16384"),
        ("max_managed_operations_per_run", "integer", "NOT_NULL", "4096"),
        ("max_journal_transitions_per_operation", "smallint", "NOT_NULL", "4"),
        ("max_journal_rows_per_run", "integer", "NOT_NULL", "16384"),
        ("max_payload_bytes", "integer", "NOT_NULL", "1048576"),
        ("max_lineage_checkpoints", "integer", "NOT_NULL", "256"),
        ("recursion_limit", "integer", "NOT_NULL", "256"),
        ("lease_duration_seconds", "integer", "NOT_NULL", "60"),
        ("lease_renew_interval_seconds", "integer", "NOT_NULL", "15"),
        ("active_run_count", "integer", "NOT_NULL", "0"),
        ("stored_run_count", "integer", "NOT_NULL", "0"),
        ("created_at", "timestamp with time zone", "NOT_NULL", "clock_timestamp()"),
    ),
    "runs": (
        ("run_id", "uuid", "NOT_NULL", None), ("trace_id", "uuid", "NOT_NULL", None),
        ("lifecycle", "character varying(8)", "NOT_NULL", None),
        ("bootstrap_state", "character varying(24)", "NOT_NULL", None),
        ("contract_id", "character varying(128)", "NOT_NULL", None),
        ("contract_schema_version", "character varying(16)", "NOT_NULL", None),
        ("contract_bytes", "bytea", "NOT_NULL", None),
        ("contract_digest", "character(71)", "NOT_NULL", None),
        ("contract_size_bytes", "integer", "NOT_NULL", None),
        ("latest_root_checkpoint_ns", "text", "NULL", None),
        ("latest_root_checkpoint_id", "text", "NULL", None),
        ("latest_root_checkpoint_digest", "character(71)", "NULL", None),
        ("pause_generation", "integer", "NOT_NULL", "0"),
        ("managed_operation_count", "integer", "NOT_NULL", "0"),
        ("journal_row_count", "integer", "NOT_NULL", "0"),
        ("blocked_reason_code", "character varying(64)", "NULL", None),
        ("created_at", "timestamp with time zone", "NOT_NULL", "clock_timestamp()"),
        ("finalized_at", "timestamp with time zone", "NULL", None),
    ),
    "run_leases": (
        ("run_id", "uuid", "NOT_NULL", None), ("lease_owner", "uuid", "NULL", None),
        ("fencing_token", "bigint", "NOT_NULL", "0"),
        ("expires_at", "timestamp with time zone", "NULL", None),
        ("renewed_at", "timestamp with time zone", "NULL", None),
    ),
    "budget_limits": (
        ("run_id", "uuid", "NOT_NULL", None), ("scope", "character varying(8)", "NOT_NULL", None),
        ("name", "character varying(65)", "NOT_NULL", None), ("phase_code", "character varying(32)", "NOT_NULL", None),
        ("limit_value", "bigint", "NOT_NULL", None), ("consumed_value", "bigint", "NOT_NULL", "0"),
        ("activation_ordinal", "smallint", "NOT_NULL", None), ("activation_digest", "character(71)", "NOT_NULL", None),
        ("activation_checkpoint_ns", "text", "NULL", None), ("activation_checkpoint_id", "text", "NULL", None),
        ("activation_checkpoint_digest", "character(71)", "NULL", None), ("created_at", "timestamp with time zone", "NOT_NULL", "clock_timestamp()"),
    ),
    "budget_operations": (
        ("run_id", "uuid", "NOT_NULL", None), ("operation_phase", "character varying(32)", "NOT_NULL", None),
        ("operation_logical_step", "character varying(64)", "NOT_NULL", None), ("budget_scope", "character varying(8)", "NOT_NULL", None),
        ("budget_name", "character varying(65)", "NOT_NULL", None), ("budget_phase_code", "character varying(32)", "NOT_NULL", None),
        ("amount", "bigint", "NOT_NULL", None), ("outcome", "character varying(8)", "NOT_NULL", None),
        ("snapshot_limit", "bigint", "NOT_NULL", None), ("snapshot_consumed", "bigint", "NOT_NULL", None),
        ("snapshot_remaining", "bigint", "NOT_NULL", None), ("created_at", "timestamp with time zone", "NOT_NULL", "clock_timestamp()"),
    ),
    "operation_journal": (
        ("run_id", "uuid", "NOT_NULL", None), ("operation_phase", "character varying(32)", "NOT_NULL", None),
        ("operation_logical_step", "character varying(64)", "NOT_NULL", None), ("transition_ordinal", "smallint", "NOT_NULL", None),
        ("node_name", "character varying(128)", "NOT_NULL", None), ("journal_state", "character varying(24)", "NOT_NULL", None),
        ("artifact_id", "uuid", "NULL", None), ("artifact_type", "character varying(65)", "NULL", None),
        ("artifact_schema_version", "character varying(16)", "NULL", None), ("artifact_content_hash", "character(71)", "NULL", None),
        ("artifact_created_at", "timestamp with time zone", "NULL", None), ("artifact_size_bytes", "bigint", "NULL", None),
        ("checkpoint_ns", "text", "NULL", None), ("checkpoint_id", "text", "NULL", None),
        ("checkpoint_digest", "character(71)", "NULL", None), ("reason_code", "character varying(64)", "NULL", None),
        ("created_at", "timestamp with time zone", "NOT_NULL", "clock_timestamp()"),
    ),
    "pending_commands": (
        ("run_id", "uuid", "NOT_NULL", None), ("pause_generation", "integer", "NOT_NULL", None),
        ("command_kind", "character varying(8)", "NOT_NULL", None), ("node_name", "character varying(128)", "NOT_NULL", None),
        ("command_type_id", "character varying(128)", "NULL", None), ("command_type_version", "character varying(16)", "NULL", None),
        ("command_bytes", "bytea", "NULL", None), ("command_digest", "character(71)", "NULL", None),
        ("command_size_bytes", "integer", "NULL", None), ("status", "character varying(16)", "NOT_NULL", None),
        ("pause_token_digest", "character(71)", "NULL", None), ("pause_context_type", "character varying(64)", "NULL", None),
        ("pause_context_version", "character varying(16)", "NULL", None), ("pause_context_bytes", "bytea", "NULL", None),
        ("pause_context_digest", "character(71)", "NULL", None), ("pause_context_size_bytes", "integer", "NULL", None),
        ("pre_checkpoint_ns", "text", "NULL", None), ("pre_checkpoint_id", "text", "NULL", None),
        ("pre_checkpoint_digest", "character(71)", "NULL", None), ("created_at", "timestamp with time zone", "NOT_NULL", "clock_timestamp()"),
        ("accepted_at", "timestamp with time zone", "NULL", None), ("dispatch_started_at", "timestamp with time zone", "NULL", None),
        ("checkpointed_at", "timestamp with time zone", "NULL", None), ("cleared_at", "timestamp with time zone", "NULL", None),
    ),
    "alembic_version": (("version_num", "character varying(32)", "NOT_NULL", None),),
    "checkpoint_migrations": (("v", "integer", "NOT_NULL", None),),
    "checkpoints": (
        ("thread_id", "text", "NOT_NULL", None), ("checkpoint_ns", "text", "NOT_NULL", ""),
        ("checkpoint_id", "text", "NOT_NULL", None), ("parent_checkpoint_id", "text", "NULL", None),
        ("type", "text", "NULL", None), ("checkpoint", "jsonb", "NOT_NULL", None), ("metadata", "jsonb", "NOT_NULL", "{}"),
    ),
    "checkpoint_blobs": (
        ("thread_id", "text", "NOT_NULL", None), ("checkpoint_ns", "text", "NOT_NULL", ""),
        ("channel", "text", "NOT_NULL", None), ("version", "text", "NOT_NULL", None),
        ("type", "text", "NOT_NULL", None), ("blob", "bytea", "NULL", None),
    ),
    "checkpoint_writes": (
        ("thread_id", "text", "NOT_NULL", None), ("checkpoint_ns", "text", "NOT_NULL", ""),
        ("checkpoint_id", "text", "NOT_NULL", None), ("task_id", "text", "NOT_NULL", None),
        ("idx", "integer", "NOT_NULL", None), ("channel", "text", "NOT_NULL", None),
        ("type", "text", "NULL", None), ("blob", "bytea", "NOT_NULL", None),
        ("task_path", "text", "NOT_NULL", ""),
    ),
}


# PostgreSQL 16 pg_get_constraintdef() fingerprints; any same-name definition drift fails.
_EXPECTED_CHECK_DEFINITION_HASHES = {
    "ck_budget_limits_activation_digest": "8fe50d1785d65d08344fff9f4344b7394d2f1835fb8fd963e755452194996713",
    "ck_budget_limits_activation_ordinal": "bf9903f3d3538a553d35c007df4636360e3fa22e13d84e565feb52b22e11d564",
    "ck_budget_limits_activation_phase": "1f0677d8828458f0d9a16af848cd84f3ad48a12ba24646f5180b460448dcb60d",
    "ck_budget_limits_activation_pointer": "a814647b2e0627c65486ccb03831f79646d94f443f864793d0868a12b9ac82c4",
    "ck_budget_limits_consumed": "f3bdbef72451c3fb6d50e73827e29b14a4ded55cbda33bc1dbd4330e82d5b04e",
    "ck_budget_limits_limit": "fda4e7914c7dbace1987f34fa686708d4e72aaa98bd714e34008cc7d1a07052d",
    "ck_budget_limits_name": "9838c90f9abe4f0982f84c787320f4ffd29270206698ac932b3773025088dd00",
    "ck_budget_limits_scope": "be36981875b691ff2a43da54f43606c72d60ef9a97c98bc5d2013d62ff717adf",
    "ck_budget_limits_scope_phase": "4006fa64bbfc8a2905bff0f4fddf2125b7278c19b55ed39fbfb46582536be9eb",
    "ck_budget_operations_amount": "f9060586719a43098bad0506a6c19c088f71010433d348a88420116e98ab6fce",
    "ck_budget_operations_logical_step": "6e43f12c59ed8220de8661b253f4d66cea78f65a06172400beb3e2af5449f397",
    "ck_budget_operations_name": "c8cfae0c4d8404524bf29ba7748bb2dafb6f022668e5da1627df01e76e650894",
    "ck_budget_operations_outcome": "91801489f57fc2a61385c45e4e3374e84268108d8ce39f2cd96c7c36289337d9",
    "ck_budget_operations_phase": "9e87789dd20f86f4a39ca25f5078ed70eef74b794c7087576d799eed72a1e459",
    "ck_budget_operations_scope": "91a65d5e3f3ad4aa957a741629c2a61f08df177b18e81d78bb58b542de4ce9e2",
    "ck_budget_operations_scope_phase": "9f6999c24953c58cb8f992844e8ec841a67a958acf7e19cbb9f77c5f65d55d11",
    "ck_budget_operations_snapshot_consumed": "f63f9a30188dbce7d16ab45d198b59b875bb62eed68f2530fd65e0a0fa1bb230",
    "ck_budget_operations_snapshot_limit": "f855e21b79601e4b41d76551542e20b97814988ae38463d80b9c24f920cf1614",
    "ck_budget_operations_snapshot_remaining": "0b1d39466169fdf8502c8b96e2d3bf1adf9779d47eb6a775d3ed248786755947",
    "ck_capacity_state_active_count": "19fbadc638cea45298dde0ea6d479b5cb13c56e41ade1c2fe74e3705ee1c64f9",
    "ck_capacity_state_journal_rows_range": "b47e32501299b70a718a915954a80a707540e87046e9dbd46ac4a189fe5be180",
    "ck_capacity_state_lease_duration": "c9286302c5c7f31e3b1705df484455bd4038ed4ebc3c4b9961c52a399430041e",
    "ck_capacity_state_lease_renew": "5849226fd5a1b8c572e707f4778cafbb7cdc57803844ea3f465b84f61640037e",
    "ck_capacity_state_max_active_runs": "3c0a5942bf66beea60d83a1d9bb84b6acb8a400fab122aa10e456bb115ff8c92",
    "ck_capacity_state_max_journal_rows": "0db4156d01c84d700a282ceae5b705a8d5116e7482ac1e8e8a826ca1ee4e51b1",
    "ck_capacity_state_max_lineage": "ccf4d82dc9fcc9fe5e2bfcf7597a067f7c780c5cc0fb1976e583e31c99b9f86e",
    "ck_capacity_state_max_operations": "e1553a9a76e49c4eeafbebf68a6a8b9c2c6663042ad0b8140c1a939f4891bafa",
    "ck_capacity_state_max_payload": "b434fbe1b1091f66c5159aaafa0ca5e7b1759f42cb8eb9edae22f8ffbfb03fc5",
    "ck_capacity_state_max_stored_runs": "ce4e716883d19c01dd1fdcaf64db406f593d8feadb01f3029620ab4b3be0caa7",
    "ck_capacity_state_max_transitions": "f22d1276be137f6e24a2865f9f7a36a1951a776d6b1e63bcf41e3e9abbaa971e",
    "ck_capacity_state_recursion": "ab25c8123feefe82f31f4e803c11cb14da604fed1e427801106a4b1c7e14619b",
    "ck_capacity_state_singleton": "0447b72e04cdd4923600cbd4cff2f72a1e1c8217b97378c419eebd05f85c19c6",
    "ck_capacity_state_stored_count": "141a7fd7ead5966e92f2d076d7150f8752856974821481b9af6aa15514eebc92",
    "ck_operation_journal_artifact_fields": "c061cc7f47daed100083891ee6e96ecf0c86ae8683c0220e08f56727c535fbf9",
    "ck_operation_journal_artifact_hash": "719dc350ab9f8d7eaa9851b2ceb4e6ed005f275798d62e441056695cf03b5e45",
    "ck_operation_journal_artifact_size": "f54a99d5739e6247574f25c4055c459cccda24f75d93f5fc6112f56d49e178c3",
    "ck_operation_journal_artifact_state": "067b56839e0b631a6ef9dc592650301006d7d209215bd28fa48285cd5b05f492",
    "ck_operation_journal_artifact_type": "4a25e900f3e9ea23c31ee5dd56a3bfeb4d17d760ef738120e2f324973c3de47c",
    "ck_operation_journal_artifact_version": "d77b4ec2060e8e2a7607de0e97e556d49b46382e2e8ade92e97ab022555532ff",
    "ck_operation_journal_checkpoint_fields": "f18856031d4006b9d14d05a3e464a124712d490ae816f12efed5a457f8ea7bd6",
    "ck_operation_journal_checkpoint_state": "d642bc721a751f8456fd9279664608cd5d90c67c758311ccfccadf1b83091026",
    "ck_operation_journal_logical_step": "6e43f12c59ed8220de8661b253f4d66cea78f65a06172400beb3e2af5449f397",
    "ck_operation_journal_node_name": "4a7974a4db44d3693335d3df06e0642a2e1b01d22e3bbddbd143f555134bd2c7",
    "ck_operation_journal_ordinal": "3bfb035b331871c402aafd73442a36b1c2953cdd6789ac4fdbb81fa09672a4c8",
    "ck_operation_journal_phase": "9e87789dd20f86f4a39ca25f5078ed70eef74b794c7087576d799eed72a1e459",
    "ck_operation_journal_reason": "0904d2fdbaf876af4fd9065cb46a34e28faf0d1d0115d0dc492628651a67f162",
    "ck_operation_journal_reason_state": "4075fbc0762c17e4444c944e08efcbb310303aeeed8d8d3b4f91dc47077e9dd1",
    "ck_operation_journal_state": "15adef5afdbaf6616347da6e88622280540a8da9cb89c6f39f944a53f4270228",
    "ck_pending_commands_bytes": "315e4040a2ee7a400bc26fe4a69ed7c3a75ec893e1542317b3cb0bbbc2dca87f",
    "ck_pending_commands_command_lifecycle": "db5f076aebb6344337073711439bcec30b0c3a87b88e1e4cf469c4a43eca42fc",
    "ck_pending_commands_command_metadata_lifecycle": "9a66388da78650e52f0b8de80a42c4489e078c173bad4566ca1bf273771c9ec4",
    "ck_pending_commands_command_size": "34a9610f87de1663782cd546513a5153ae0d0bc077312a5e5a5de892817c0b6a",
    "ck_pending_commands_context_bytes": "80cc7607ffa629637bdf409583efa56428c63d561673ff30c8208748594a2a3a",
    "ck_pending_commands_context_contract": "7b9e254fbcbec2a3653fe3141ad14f246a2896bb8ffede387ff2973479b63411",
    "ck_pending_commands_context_digest": "9adae4ae6a42da621841c80cb41a98df2872b49ca4b096bc2a80e4a55abbed53",
    "ck_pending_commands_context_fields": "b999ab90cc48d64e1cf28b61b6fcb7fd8bc3d4ff1fd382786d2ad391e413596f",
    "ck_pending_commands_context_lifecycle": "1c2eff72f110c76f1586a5ce99afa561fc850e4eef9c524c5eecc216bbe86844",
    "ck_pending_commands_context_size": "d9277f1e52b13ce7c056fb2bdef7c0a3acd2a892065b9e181e0290e2052f64f3",
    "ck_pending_commands_context_size_match": "9c63ba241df643188f454dcf483534342548fc595755959af5ac785ac1e818c1",
    "ck_pending_commands_context_type": "146b922326b3d6bac2c5efcca31fbde731636f7a184694803b2c6e823b8c5583",
    "ck_pending_commands_context_version": "0b21338770c203008153da43ad072600b7a2c3fc6b8ac9c07c693dccaea2657a",
    "ck_pending_commands_digest": "72af2a47711ee9a6f008930f5b9d8f5824e363ab4eac5ec640c5dbfd187e8e47",
    "ck_pending_commands_generation": "530beda2993c1b031aef606c580344105f5ee8a25d7ae96813dd8256b80614f5",
    "ck_pending_commands_generation_contract": "df6c62fbec917412f3b6c12194cd901bae751e3f325ac9f86901e281144734f4",
    "ck_pending_commands_kind": "3279ad08881b98968a5be0f369597c760700c1f65966eff6f9dfc411bc392844",
    "ck_pending_commands_node_name": "4a7974a4db44d3693335d3df06e0642a2e1b01d22e3bbddbd143f555134bd2c7",
    "ck_pending_commands_pre_checkpoint": "c481b7e4ea2370874cb5c262d7680ef4bc8faf26263f9aa5d050d06729bb6be8",
    "ck_pending_commands_size": "fe2c639abb1b47e8fa388ebc2e29941afd8f24af447af3e59f6c2e21faa14090",
    "ck_pending_commands_status": "f6aef0e1846d8b2bb51a9391adb12ac8d1a16f16d7c548968d42b70f5ee60fbc",
    "ck_pending_commands_timestamps": "b085f4bdd3181a81d0ad03618792eb912ee05850476aa46445b6344df7616539",
    "ck_pending_commands_token_digest": "f85d95d5d33d2db40e6b451b2b2c94f228a69ac358d0d0e0d29382be3033d538",
    "ck_pending_commands_type_fields": "df8c18ec0aaa315471ac110b5cbbe3efa50eb48b039af22894d274ca72ab445b",
    "ck_pending_commands_type_id": "e2dd8f270d4cd353e2b5bddaac9660fdfed42e238ed3ba13fc2c45dc6c07c511",
    "ck_pending_commands_type_version": "f24c4493c8d3377c5dfd6700ef6a006bc6057eedfae6366eecb521f9fc13bd5e",
    "ck_run_leases_fencing_token": "0ea6604dbe15976c992bcdc7fc1c85ebcf7c306491ec2d03079d15e5b949d3a3",
    "ck_run_leases_fields": "af0c68b4ec55dfa4329506c694d687cbb0a6d42462441ddfa1c0e9dac47a9912",
    "ck_runs_blocked_reason": "a4bc75945afcbb6c7475e2095802f99d674e112a66d49080399bd62ab4bd878a",
    "ck_runs_blocked_reason_lifecycle": "4660424d5f14d6f53773988c5d002affff17d9c672559c20ca482822cc647a93",
    "ck_runs_bootstrap_pointer": "7dc5611c1375e10086072a7d70b2a77811cf0fd6056f49ca11799ed90a38f320",
    "ck_runs_bootstrap_state": "64f0646bbd0e64d2fb85118036b5ee702e238216ac58089f7cb8d9228ddc1009",
    "ck_runs_checkpoint_pointer": "067451d9cd7e3edc284a6292ee40afb87d9b7a193d9cd82ec0141373019e27d7",
    "ck_runs_closed_pointer": "eedb7dce14f523f352e705e221f64eaacb59fb816d6958f58535d88461d4dbc9",
    "ck_runs_contract_bytes_range": "abd5a19272cbde6254ea4667a8925069419f91b8261f88f16f6fd95a10bec4c8",
    "ck_runs_contract_digest": "46e868814b49f85f5f16bd5f4139a95cd9c318e2190dc8946ca485bc31877c27",
    "ck_runs_contract_id": "6180807980b8d29cdc2f139a3a45dd04fb022b1b48c3dbbc7bb096d88d97c758",
    "ck_runs_contract_size": "b78a3caf3c5b9d32c3178feb28dba8f3e2c33b8adf128f34dac56771caf328a2",
    "ck_runs_contract_version": "0a024a86f83d80afac065f2f3ec6550c61cfe870b84d4c84074e0e94140fd639",
    "ck_runs_finalized_at": "d8ecf1f12141716a8d90cd4315bc7fae4dbb02b3a2fcaf21b5d9596b3edcb66a",
    "ck_runs_journal_count": "ad25ee8ca695b45e71a2babb25268988724461ee518620c2c659bd9af135e822",
    "ck_runs_lifecycle": "b05a7d4adfc10ce14eaac4f8923308c141f17540e886e21bd979dfb9291862fa",
    "ck_runs_operation_count": "a367358ebd8509c718c5f75281c8d02da0de2b362b986e096f90578f1ce3e59b",
    "ck_runs_pause_generation": "530beda2993c1b031aef606c580344105f5ee8a25d7ae96813dd8256b80614f5",
}


_EXPECTED_INDEX_DEFINITION_HASHES = {
    "alembic_version_pkc": "ec45212701b697735e096e486ad010e3fb17515409bc345ab9f63f4291dc1982",
    "checkpoint_blobs_pkey": "128dddc8e802422e98d80d720f946c95fe64c1dd34b8f967d2880d11ce40466e",
    "checkpoint_blobs_thread_id_idx": "9b295f80c5b1e9dab9982038648a765b171fb4c545054fccc044ae4d964f7fe7",
    "checkpoint_migrations_pkey": "ec46464a816d2f645dddc3aefe5ab24c8e27af8835d3f6db6b6807a3230105d0",
    "checkpoint_writes_pkey": "9a4627d83e3817c7e9f76927627d18e4fb49a9ac9de6bf2161f0d271efcaad10",
    "checkpoint_writes_thread_id_idx": "d5c53ab8b279873c356449b32484f99e87a48113df9bbfafa45d9cbac4c91cee",
    "checkpoints_pkey": "d3934d5f12d14455d1bc4b6ad51730cb13d0d616117fcf083c90134a9fab426f",
    "checkpoints_thread_id_idx": "2e9533378ea6a6ef903700effcad5d814b331641b6cb22283a68333162afe16a",
    "ix_operation_journal_operation": "c4d71649e0c3798ca0fe0bc45ef04c3490779ba12822722e2bdc6f5378096991",
    "ix_pending_commands_run_generation": "4bff239dfdef44bbd339f8519d8f463da90b13e9d69d04014267c839f0934bba",
    "ix_run_leases_expires_at": "b80c3356232a4360f417c1e119f1f24a75294802dedb598f6632809c84448157",
    "ix_runs_lifecycle": "0aa9deff83c0e325cc7035b890cb8e313ffcd991ab74adb96118d12d7e8cda97",
    "pk_budget_limits": "38e0f37f54003bc4c22f4ec5cd03f1f3ca4b59bdccc831dde79e8562a4f00523",
    "pk_budget_operations": "ff56f214a2877b67035de74d7cdf92e698a18e019fc07282d5e8980773155ac5",
    "pk_capacity_state": "eed1c4d603581f60503d772668af5265699ff382fc39cab58fac726901e764c0",
    "pk_operation_journal": "0b07c8be57b7316de13f2ad1c88350ab23c0615a3ed6d16d8d6a7fc4c6a77c7c",
    "pk_pending_commands": "00572d6185702628dfdb7a27d768f4077cef825ecb7cab1f5f4ccd21f0363a08",
    "pk_run_leases": "a6a080fc65a2ddadb5b701b4bc275bd91882afbee079f7072d9efa4e8df33dcb",
    "pk_runs": "f1959955a1644ab840b8ed119b5cdb8d4743fa83de8389e475a667a7ae78d6d1",
    "uq_operation_journal_state": "8e698e67e594256eab4f99cb88f7d61e4e56a99718a8ff46aba77fe5a72088b2",
}


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).lower().replace(" ", "")
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    if "::" in text:
        text = text.split("::", 1)[0].strip("'")
    if text in {"4096", "16384", "4", "1048576", "256", "60", "15", "0"}:
        return text
    if text == "clock_timestamp()":
        return "clock_timestamp()"
    return text


async def _check_columns(connection) -> None:
    for table, expected in _EXPECTED_COLUMN_FORMATS.items():
        rows = await _fetchall(
            connection,
            """
            SELECT a.attname,
                   pg_catalog.format_type(a.atttypid, a.atttypmod),
                   CASE WHEN a.attnotnull THEN 'NOT_NULL' ELSE 'NULL' END,
                   pg_catalog.pg_get_expr(d.adbin, d.adrelid)
            FROM pg_catalog.pg_attribute AS a
            JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS d
              ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (SCHEMA_NAME, table),
        )
        actual = tuple(
            (str(row[0]), str(row[1]), str(row[2]), _normalize_default(row[3]))
            for row in rows
        )
        expected_normalized = tuple(expected)
        if actual != expected_normalized:
            raise DurableExecutionConfigurationError() from None


async def _check_constraints(connection) -> None:
    rows = await _fetchall(
        connection,
        """
        SELECT c.relname,
               con.contype,
               con.conname,
               pg_catalog.pg_get_constraintdef(con.oid),
               ARRAY(
                   SELECT a.attname
                   FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                   JOIN pg_catalog.pg_attribute AS a
                     ON a.attrelid = con.conrelid AND a.attnum = k.attnum
                   ORDER BY k.ord
               ),
               CASE WHEN con.contype = 'f' THEN (
                   SELECT n2.nspname || '.' || c2.relname
                   FROM pg_catalog.pg_class AS c2
                   JOIN pg_catalog.pg_namespace AS n2 ON n2.oid = c2.relnamespace
                   WHERE c2.oid = con.confrelid
               ) END,
               ARRAY(
                   SELECT a.attname
                   FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                   JOIN pg_catalog.pg_attribute AS a
                     ON a.attrelid = con.confrelid AND a.attnum = k.attnum
                   ORDER BY k.ord
               ),
               CASE WHEN con.contype = 'f' THEN con.confdeltype END
        FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
        ORDER BY c.relname, con.conname
        """,
        (SCHEMA_NAME,),
    )
    if not rows:
        raise DurableExecutionConfigurationError() from None
    names_by_table: dict[str, set[str]] = {}
    for table, _kind, name, _definition, _columns, _target, _ref_columns, _deltype in rows:
        names_by_table.setdefault(str(table), set()).add(str(name))
    expected = {
        "alembic_version": {"alembic_version_pkc"},
        "checkpoint_migrations": {"checkpoint_migrations_pkey"},
        "checkpoints": {"checkpoints_pkey"},
        "checkpoint_blobs": {"checkpoint_blobs_pkey"},
        "checkpoint_writes": {"checkpoint_writes_pkey"},
        "capacity_state": {
            "pk_capacity_state", "ck_capacity_state_singleton", "ck_capacity_state_max_active_runs",
            "ck_capacity_state_max_stored_runs", "ck_capacity_state_max_operations",
            "ck_capacity_state_max_transitions", "ck_capacity_state_max_journal_rows",
            "ck_capacity_state_journal_rows_range", "ck_capacity_state_max_payload",
            "ck_capacity_state_max_lineage", "ck_capacity_state_recursion",
            "ck_capacity_state_lease_duration", "ck_capacity_state_lease_renew",
            "ck_capacity_state_active_count", "ck_capacity_state_stored_count",
        },
        "runs": {
            "pk_runs", "ck_runs_lifecycle", "ck_runs_bootstrap_state", "ck_runs_contract_id",
            "ck_runs_contract_version", "ck_runs_contract_bytes_range", "ck_runs_contract_digest",
            "ck_runs_contract_size", "ck_runs_pause_generation", "ck_runs_operation_count",
            "ck_runs_journal_count", "ck_runs_blocked_reason", "ck_runs_blocked_reason_lifecycle",
            "ck_runs_finalized_at", "ck_runs_checkpoint_pointer", "ck_runs_bootstrap_pointer",
            "ck_runs_closed_pointer",
        },
        "run_leases": {"pk_run_leases", "fk_run_leases_run", "ck_run_leases_fencing_token", "ck_run_leases_fields"},
        "budget_limits": {
            "pk_budget_limits", "fk_budget_limits_run", "ck_budget_limits_scope", "ck_budget_limits_name",
            "ck_budget_limits_scope_phase", "ck_budget_limits_activation_phase", "ck_budget_limits_limit", "ck_budget_limits_consumed",
            "ck_budget_limits_activation_ordinal", "ck_budget_limits_activation_digest", "ck_budget_limits_activation_pointer",
        },
        "budget_operations": {
            "pk_budget_operations", "fk_budget_operations_run", "fk_budget_operations_limit",
            "ck_budget_operations_phase", "ck_budget_operations_logical_step", "ck_budget_operations_scope",
            "ck_budget_operations_name", "ck_budget_operations_scope_phase", "ck_budget_operations_amount",
            "ck_budget_operations_outcome", "ck_budget_operations_snapshot_limit",
            "ck_budget_operations_snapshot_consumed", "ck_budget_operations_snapshot_remaining",
        },
        "operation_journal": {
            "pk_operation_journal", "uq_operation_journal_state", "fk_operation_journal_run",
            "ck_operation_journal_phase", "ck_operation_journal_logical_step", "ck_operation_journal_ordinal",
            "ck_operation_journal_node_name", "ck_operation_journal_state", "ck_operation_journal_artifact_fields",
            "ck_operation_journal_artifact_type", "ck_operation_journal_artifact_version",
             "ck_operation_journal_artifact_hash", "ck_operation_journal_artifact_size",
             "ck_operation_journal_checkpoint_fields", "ck_operation_journal_reason",
             "ck_operation_journal_reason_state", "ck_operation_journal_artifact_state",
             "ck_operation_journal_checkpoint_state",
        },
        "pending_commands": {
            "pk_pending_commands", "fk_pending_commands_run", "ck_pending_commands_generation",
            "ck_pending_commands_kind", "ck_pending_commands_node_name", "ck_pending_commands_type_id",
             "ck_pending_commands_type_version", "ck_pending_commands_type_fields", "ck_pending_commands_bytes",
             "ck_pending_commands_digest", "ck_pending_commands_size", "ck_pending_commands_command_size",
             "ck_pending_commands_status",
             "ck_pending_commands_token_digest", "ck_pending_commands_context_type", "ck_pending_commands_context_version",
             "ck_pending_commands_context_contract", "ck_pending_commands_context_fields",
             "ck_pending_commands_context_bytes", "ck_pending_commands_context_digest",
             "ck_pending_commands_context_size", "ck_pending_commands_context_size_match",
             "ck_pending_commands_pre_checkpoint", "ck_pending_commands_generation_contract",
             "ck_pending_commands_command_metadata_lifecycle", "ck_pending_commands_command_lifecycle",
             "ck_pending_commands_timestamps", "ck_pending_commands_context_lifecycle",
        },
    }
    if any(names_by_table.get(table, set()) != names for table, names in expected.items()):
        raise DurableExecutionConfigurationError() from None

    expected_key_columns = {
        "pk_capacity_state": ("singleton_id",),
        "pk_runs": ("run_id",),
        "pk_run_leases": ("run_id",),
        "pk_budget_limits": ("run_id", "scope", "name", "phase_code"),
        "pk_budget_operations": ("run_id", "operation_phase", "operation_logical_step"),
        "pk_operation_journal": ("run_id", "operation_phase", "operation_logical_step", "transition_ordinal"),
        "uq_operation_journal_state": ("run_id", "operation_phase", "operation_logical_step", "journal_state"),
        "pk_pending_commands": ("run_id", "pause_generation"),
        "alembic_version_pkc": ("version_num",),
        "checkpoint_migrations_pkey": ("v",),
        "checkpoints_pkey": ("thread_id", "checkpoint_ns", "checkpoint_id"),
        "checkpoint_blobs_pkey": ("thread_id", "checkpoint_ns", "channel", "version"),
        "checkpoint_writes_pkey": ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        "fk_run_leases_run": ("run_id",),
        "fk_budget_limits_run": ("run_id",),
        "fk_budget_operations_run": ("run_id",),
        "fk_budget_operations_limit": ("run_id", "budget_scope", "budget_name", "budget_phase_code"),
        "fk_operation_journal_run": ("run_id",),
        "fk_pending_commands_run": ("run_id",),
    }
    expected_foreign_keys = {
        "fk_run_leases_run": ("durable_execution.runs", ("run_id",), "r"),
        "fk_budget_limits_run": ("durable_execution.runs", ("run_id",), "r"),
        "fk_budget_operations_run": ("durable_execution.runs", ("run_id",), "r"),
        "fk_budget_operations_limit": (
            "durable_execution.budget_limits",
            ("run_id", "scope", "name", "phase_code"),
            "r",
        ),
        "fk_operation_journal_run": ("durable_execution.runs", ("run_id",), "r"),
        "fk_pending_commands_run": ("durable_execution.runs", ("run_id",), "r"),
    }
    expected_kinds = {
        name: ("p" if name.startswith("pk_") or name.endswith("_pkey") or name == "alembic_version_pkc" else "u" if name.startswith("uq_") else "f" if name.startswith("fk_") else "c")
        for names in expected.values()
        for name in names
    }
    for _table, kind, name, definition, columns, target, ref_columns, deltype in rows:
        name = str(name)
        if str(kind) != expected_kinds.get(name) or not str(definition):
            raise DurableExecutionConfigurationError() from None
        if str(kind) == "c":
            expected_hash = _EXPECTED_CHECK_DEFINITION_HASHES.get(name)
            actual_hash = hashlib.sha256(str(definition).encode("utf-8")).hexdigest()
            if expected_hash is None or actual_hash != expected_hash:
                raise DurableExecutionConfigurationError() from None
        if name in expected_key_columns and tuple(columns or ()) != expected_key_columns[name]:
            raise DurableExecutionConfigurationError() from None
        if name in expected_foreign_keys:
            expected_target, expected_ref_columns, expected_deltype = expected_foreign_keys[name]
            if (str(target), tuple(ref_columns or ()), str(deltype)) != (
                expected_target,
                expected_ref_columns,
                expected_deltype,
            ):
                raise DurableExecutionConfigurationError() from None


async def _check_indexes(connection) -> None:
    rows = await _fetchall(
        connection,
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s
        ORDER BY tablename, indexname
        """,
        (SCHEMA_NAME,),
    )
    expected_names = {
        "alembic_version_pkc",
        "checkpoint_blobs_pkey", "checkpoint_blobs_thread_id_idx",
        "checkpoint_migrations_pkey",
        "checkpoint_writes_pkey", "checkpoint_writes_thread_id_idx",
        "checkpoints_pkey", "checkpoints_thread_id_idx",
        "pk_capacity_state", "pk_runs", "pk_run_leases", "pk_budget_limits",
        "pk_budget_operations", "pk_operation_journal", "uq_operation_journal_state",
        "pk_pending_commands", *EXPECTED_EXPLICIT_INDEXES,
    }
    definitions = {str(row[1]): str(row[2]) for row in rows}
    if set(definitions) != expected_names or set(_EXPECTED_INDEX_DEFINITION_HASHES) != expected_names:
        raise DurableExecutionConfigurationError() from None
    if any(
        hashlib.sha256(definition.encode("utf-8")).hexdigest()
        != _EXPECTED_INDEX_DEFINITION_HASHES[name]
        for name, definition in definitions.items()
    ):
        raise DurableExecutionConfigurationError() from None
    expected_fragments = {
        "ix_runs_lifecycle": "ON durable_execution.runs USING btree (lifecycle)",
        "ix_run_leases_expires_at": "ON durable_execution.run_leases USING btree (expires_at) WHERE (lease_owner IS NOT NULL)",
        "ix_operation_journal_operation": "ON durable_execution.operation_journal USING btree (run_id, operation_phase, operation_logical_step, transition_ordinal DESC)",
        "ix_pending_commands_run_generation": "ON durable_execution.pending_commands USING btree (run_id, pause_generation DESC)",
    }
    for name, fragment in expected_fragments.items():
        if fragment.lower().replace(" ", "") not in definitions[name].lower().replace(" ", ""):
            raise DurableExecutionConfigurationError() from None


async def _check_saver_migrations(connection) -> None:
    rows = await _fetchall(
        connection,
        "SELECT v FROM durable_execution.checkpoint_migrations ORDER BY v",
    )
    if tuple(row[0] for row in rows) != EXPECTED_SAVER_MIGRATION_VERSIONS:
        raise DurableExecutionConfigurationError() from None


async def _check_connection_options(connection) -> None:
    rows = await _fetchall(
        connection,
        "SELECT current_setting('search_path'), current_setting('TimeZone'), current_setting('transaction_read_only')",
    )
    if rows != (("durable_execution,pg_catalog", "UTC", "off"),):
        raise DurableExecutionConfigurationError() from None


# ---------------------------------------------------------------------------
# DE-T03 private coordination implementation
# ---------------------------------------------------------------------------

_PHASE_ORDINAL = {phase.value: ordinal for ordinal, phase in enumerate(WorkflowPhase)}
_BLOCKED_REASON_CODES = frozenset(
    {
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
    }
)
_JOURNAL_STATES = frozenset(
    {
        "execution_permitted",
        "budget_rejected",
        "artifact_confirmed",
        "checkpoint_confirmed",
        "completed",
        "indeterminate",
    }
)
_JOURNAL_REASONS = frozenset(
    {
        "external_result_unknown",
        "cancelled_after_permission",
        "lease_lost_after_permission",
        "artifact_missing_after_permission",
    }
)
_PENDING_STATES = frozenset(
    {"awaiting_input", "accepted", "dispatch_started", "checkpointed", "cleared"}
)
_PENDING_EDGES = {
    "awaiting_input": frozenset({"accepted", "cleared"}),
    "accepted": frozenset({"dispatch_started", "cleared"}),
    "dispatch_started": frozenset({"checkpointed", "cleared"}),
    "checkpointed": frozenset({"cleared"}),
    "cleared": frozenset(),
}
_SAFE_INTEGER_MAX = 2**53 - 1


class _BlockedReason(str, Enum):
    CONTRACT_MISMATCH = "contract_mismatch"
    BUDGET_MISMATCH = "budget_mismatch"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    CHECKPOINT_LINEAGE_INVALID = "checkpoint_lineage_invalid"
    JOURNAL_INVALID = "journal_invalid"
    ARTIFACT_INVALID = "artifact_invalid"
    PENDING_COMMAND_INVALID = "pending_command_invalid"
    NODE_CLASSIFICATION_INVALID = "node_classification_invalid"
    NON_RECOVERABLE_DISPATCH = "non_recoverable_dispatch"
    OPERATION_INDETERMINATE = "operation_indeterminate"
    RECURSION_LIMIT_EXCEEDED = "recursion_limit_exceeded"
    STATE_INVALID = "state_invalid"


_BLOCK_CAUSE_TOKEN = object()


class _BlockCause:
    """由上层验证器产生的封闭阻断原因证据。

    Repository 不可能在阻断事务内重新执行 contract、Graph、checkpoint、
    Artifact 或 state 验证。调用方因此只能通过这些具名构造入口提交已经
    验证的原因，不能把任意 ``_BlockedReason`` 与任意 OperationId 拼接。
    """

    __slots__ = ("reason",)

    def __init__(self, *, reason: _BlockedReason, _token: object) -> None:
        if _token is not _BLOCK_CAUSE_TOKEN or type(reason) is not _BlockedReason:
            raise DurableExecutionUsageError() from None
        self.reason = reason

    @classmethod
    def _validated(cls, reason: _BlockedReason) -> _BlockCause:
        return cls(reason=reason, _token=_BLOCK_CAUSE_TOKEN)

    @classmethod
    def contract_mismatch(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.CONTRACT_MISMATCH)

    @classmethod
    def budget_mismatch(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.BUDGET_MISMATCH)

    @classmethod
    def checkpoint_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.CHECKPOINT_INVALID)

    @classmethod
    def checkpoint_lineage_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.CHECKPOINT_LINEAGE_INVALID)

    @classmethod
    def journal_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.JOURNAL_INVALID)

    @classmethod
    def artifact_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.ARTIFACT_INVALID)

    @classmethod
    def pending_command_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.PENDING_COMMAND_INVALID)

    @classmethod
    def node_classification_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.NODE_CLASSIFICATION_INVALID)

    @classmethod
    def non_recoverable_dispatch(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.NON_RECOVERABLE_DISPATCH)

    @classmethod
    def operation_indeterminate(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.OPERATION_INDETERMINATE)

    @classmethod
    def recursion_limit_exceeded(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.RECURSION_LIMIT_EXCEEDED)

    @classmethod
    def state_invalid(cls) -> _BlockCause:
        return cls._validated(_BlockedReason.STATE_INVALID)


@dataclass(frozen=True, slots=True)
class _BlockFacts:
    """阻断时允许调用方提交的有限事实集合。

    该对象不是公开业务 reason；Repository 根据数据库中的候选 operation、lease
    和这些有限事实推导最终 reason，避免把任意调用方字符串写入生命周期账本。
    """

    lease_lost_after_permission: bool = False
    cancelled_after_permission: bool = False
    artifact_missing_after_permission: bool = False
    external_result_unknown: bool = False
    cause: _BlockCause | None = None
    operation_id: OperationId | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (
                self.lease_lost_after_permission,
                self.cancelled_after_permission,
                self.artifact_missing_after_permission,
                self.external_result_unknown,
            )
        ):
            raise DurableExecutionUsageError() from None
        if self.cause is not None and type(self.cause) is not _BlockCause:
            raise DurableExecutionUsageError() from None
        if self.operation_id is not None and type(self.operation_id) is not OperationId:
            raise DurableExecutionUsageError() from None
        result_facts = (
            self.lease_lost_after_permission,
            self.cancelled_after_permission,
            self.artifact_missing_after_permission,
            self.external_result_unknown,
        )
        if self.operation_id is not None and not any(result_facts):
            raise DurableExecutionUsageError() from None
        if self.artifact_missing_after_permission and self.operation_id is None:
            raise DurableExecutionUsageError() from None
        if self.cause is not None and self.cause.reason is _BlockedReason.OPERATION_INDETERMINATE:
            if self.operation_id is None or not any(result_facts) or self.artifact_missing_after_permission:
                raise DurableExecutionUsageError() from None

    def indeterminate_reason(self, *, lease_active: bool) -> str:
        if self.lease_lost_after_permission or not lease_active:
            return "lease_lost_after_permission"
        if self.cancelled_after_permission:
            return "cancelled_after_permission"
        if self.artifact_missing_after_permission:
            return "artifact_missing_after_permission"
        return "external_result_unknown"

    def blocked_reason_code(self, *, candidate_count: int, indeterminate_reason: str | None) -> str:
        if self.cause is not None:
            return self.cause.reason.value
        if candidate_count == 0:
            return "journal_invalid"
        if candidate_count > 1:
            return "node_classification_invalid"
        if indeterminate_reason is None:
            return "journal_invalid"
        if indeterminate_reason == "artifact_missing_after_permission":
            return "artifact_invalid"
        return "operation_indeterminate"


def _strict_copy(value: Any, model_type: type[Any], error_type: type[Exception]) -> Any:
    """做两段式 strict copy，避免把调用方的可变嵌套对象带入事务。"""

    try:
        tree = value.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        first = TypeAdapter(model_type).validate_python(tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        encoded = json.dumps(
            json_tree,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return TypeAdapter(model_type).validate_json(encoded, strict=True)
    except Exception:
        raise error_type() from None


def _operation_key(key: ExecutionBudgetKey) -> tuple[str, str, str]:
    return (
        key.scope.value,
        key.name,
        "" if key.phase is None else key.phase.value,
    )


def _plan_digest(plan: ExecutionBudgetPlan) -> str:
    payload = plan.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _fresh_encoded_command(value: DurableExecutionEncodedCommand) -> DurableExecutionEncodedCommand:
    return _strict_copy(
        value,
        DurableExecutionEncodedCommand,
        DurableExecutionUsageError,
    )


def _normalize_pointer(pointer: tuple[str, str, str] | None) -> tuple[str, str, str] | None:
    if pointer is None:
        return None
    if type(pointer) is not tuple or len(pointer) != 3:
        raise DurableExecutionStateError() from None
    namespace, checkpoint_id, digest = pointer
    if (
        type(namespace) is not str
        or namespace != ""
        or not 1 <= len(checkpoint_id) <= 200
        or type(checkpoint_id) is not str
        or type(digest) is not str
        or len(digest) != 71
        or not digest.startswith("sha256:")
    ):
        raise DurableExecutionStateError() from None
    try:
        int(digest[7:], 16)
    except ValueError:
        raise DurableExecutionStateError() from None
    return namespace, checkpoint_id, digest


def _artifact_fields(artifact: Mapping[str, object] | None) -> tuple[object, ...]:
    if artifact is None:
        return (None, None, None, None, None, None)
    if not isinstance(artifact, Mapping) or set(artifact) != {
        "artifact_id",
        "artifact_type",
        "artifact_schema_version",
        "artifact_content_hash",
        "artifact_created_at",
        "artifact_size_bytes",
    }:
        raise DurableExecutionStateError() from None
    try:
        artifact_id = artifact["artifact_id"]
        artifact_type = artifact["artifact_type"]
        schema_version = artifact["artifact_schema_version"]
        content_hash = artifact["artifact_content_hash"]
        created_at = artifact["artifact_created_at"]
        size_bytes = artifact["artifact_size_bytes"]
    except Exception:
        raise DurableExecutionStateError() from None
    if (
        not isinstance(artifact_id, UUID)
        or type(artifact_type) is not str
        or type(schema_version) is not str
        or type(content_hash) is not str
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}", artifact_type)
        or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", schema_version)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash)
        or type(created_at) is not datetime
        or created_at.tzinfo is None
        or created_at.utcoffset() != timedelta(0)
        or type(size_bytes) is not int
        or isinstance(size_bytes, bool)
        or not 1 <= size_bytes <= _SAFE_INTEGER_MAX
    ):
        raise DurableExecutionStateError() from None
    return (
        artifact_id,
        artifact_type,
        schema_version,
        content_hash,
        created_at,
        size_bytes,
    )


class _CommitUnknown(Exception):
    """测试 fault seam 使用的私有 commit-unknown 标记。"""

    __slots__ = ("mode",)

    def __init__(self, mode: str = "committed") -> None:
        if mode not in {"committed", "absent", "partial", "conflict", "unknown"}:
            raise ValueError(mode)
        self.mode = mode


@dataclass(frozen=True, slots=True)
class _BudgetIntent:
    run_id: UUID
    operation_phase: str
    logical_step: str
    scope: str
    name: str
    phase_code: str
    amount: int
    lifecycle: str
    managed_operation_count: int
    journal_row_count: int
    limit_value: int
    consumed_value: int
    operation_exists: bool
    target_journal_rows: int


@dataclass(frozen=True, slots=True)
class _BlockIntent:
    run_id: UUID
    lifecycle: str
    journal_row_count: int
    managed_operation_count: int
    active_run_count: int
    candidate_keys: tuple[tuple[str, str], ...]
    pending_rows: tuple[tuple[object, ...], ...]


class DurableExecutionRepository:
    """Module 11 私有协调 Repository。

    该类故意不从 ``app.durable_execution`` 包根导出。它只持有协调池；checkpoint
    数据由 DE-T04 的私有 decorator 借用另一个池并委托官方 Saver。
    """

    __slots__ = ("_pool", "_limits", "_fault_hook", "_loop", "_fault_connection")

    def __init__(
        self,
        *,
        coordination_pool: object,
        limits: DurableExecutionLimits,
        fault_hook: Callable[[str], object] | None = None,
    ) -> None:
        if type(coordination_pool) is type(None):
            raise DurableExecutionConfigurationError() from None
        self._pool = coordination_pool
        self._limits = _strict_copy(
            limits,
            DurableExecutionLimits,
            DurableExecutionConfigurationError,
        )
        if fault_hook is not None and not callable(fault_hook):
            raise DurableExecutionConfigurationError() from None
        self._fault_hook = fault_hook
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fault_connection: Any | None = None

    def _ensure_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise DurableExecutionUsageError() from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise DurableExecutionUsageError() from None

    async def _fault(self, point: str) -> None:
        if self._fault_hook is None:
            return
        try:
            result = self._fault_hook(point)
            if inspect.isawaitable(result):
                await result
        except _CommitUnknown:
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise DurableExecutionDependencyError() from None

    @asynccontextmanager
    async def _transaction(
        self,
        *,
        run_id: UUID | None = None,
        capacity_lock: bool = False,
        commit_exception_point: str | None = None,
        commit_unknown_point: str | None = None,
        commit_unknown: bool = False,
    ):
        self._ensure_loop()
        try:
            async with self._pool.connection() as connection:
                transaction = connection.transaction()
                await transaction.__aenter__()
                transaction_finished = False
                try:
                    if capacity_lock:
                        await connection.execute(
                            "SELECT pg_advisory_xact_lock(%s)",
                            (migration_advisory_key(domain="capacity"),),
                        )
                    if run_id is not None:
                        await connection.execute(
                            "SELECT pg_advisory_xact_lock(%s)",
                            (
                                migration_advisory_key(
                                    domain="run",
                                    payload=str(run_id).encode("ascii"),
                                ),
                            ),
                        )
                    yield connection
                    if commit_exception_point is not None:
                        self._fault_connection = connection
                        try:
                            await self._fault(commit_exception_point)
                        finally:
                            self._fault_connection = None
                    if bool(getattr(connection, "closed", False)):
                        # fault seam 可能主动关闭真实 psycopg 连接；此时
                        # transaction context 不一定再抛出异常，但提交结果
                        # 已经不可知，必须进入同一 fresh-read reconciliation。
                        transaction_finished = True
                        raise _CommitUnknown("unknown") from None
                    try:
                        await transaction.__aexit__(None, None, None)
                    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        if not commit_unknown:
                            raise
                        transaction_finished = True
                        if commit_exception_point is not None:
                            try:
                                await self._fault(f"{commit_exception_point}_observed")
                            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                                raise
                            except Exception:
                                # 观察点只能证明 transaction.__aexit__ 确实抛错，
                                # 不能覆盖原始的 commit-unknown 语义。
                                pass
                        try:
                            await connection.close()
                        finally:
                            raise _CommitUnknown("unknown") from None
                    transaction_finished = True
                    if commit_unknown_point is not None:
                        self._fault_connection = connection
                        try:
                            await self._fault(commit_unknown_point)
                        except _CommitUnknown as marker:
                            # 该 seam 位于真实 transaction context 的 commit 后；
                            # mode 只控制测试如何制造状态，不能参与
                            # reconciliation 的权威结果判断。
                            await connection.close()
                            raise _CommitUnknown(marker.mode) from None
                        finally:
                            self._fault_connection = None
                except BaseException as exc:
                    if not transaction_finished:
                        await transaction.__aexit__(type(exc), exc, exc.__traceback__)
                    raise
        except (
            _CommitUnknown,
            DurableExecutionError,
            BudgetConsumeConflictError,
            BudgetKeyNotConfiguredError,
            BudgetOperationScopeMismatchError,
            ExecutionBudgetCapacityExceededError,
            ExecutionBudgetManagerUsageError,
            ExecutionBudgetPlanValidationError,
            ExecutionBudgetRequestValidationError,
        ):
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise DurableExecutionDependencyError() from None

    @staticmethod
    async def _one(connection, query: str, params: Sequence[object] = ()) -> Mapping[str, object] | None:
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
                description = getattr(cursor, "description", None)
            if row is None:
                return None
            if isinstance(row, Mapping):
                return row
            if description is None:
                return None
            return {item.name: value for item, value in zip(description, row, strict=True)}
        except DurableExecutionError:
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise DurableExecutionDependencyError() from None

    @staticmethod
    async def _all(connection, query: str, params: Sequence[object] = ()) -> tuple[Mapping[str, object], ...]:
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()
                description = getattr(cursor, "description", None)
            if not rows:
                return ()
            if isinstance(rows[0], Mapping):
                return tuple(rows)
            if description is None:
                return ()
            return tuple(
                {item.name: value for item, value in zip(description, row, strict=True)}
                for row in rows
            )
        except DurableExecutionError:
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise DurableExecutionDependencyError() from None

    async def initialize_capacity(self) -> DurableExecutionLimits:
        """初始化或逐项核对 capacity singleton，并核对全部运行计数。"""

        async with self._transaction(capacity_lock=True) as connection:
            row = await self._one(
                connection,
                """
                SELECT max_active_runs, max_stored_runs,
                       max_managed_operations_per_run,
                       max_journal_transitions_per_operation,
                       max_journal_rows_per_run, max_payload_bytes,
                       max_lineage_checkpoints, recursion_limit,
                       lease_duration_seconds, lease_renew_interval_seconds,
                       active_run_count, stored_run_count
                FROM durable_execution.capacity_state
                WHERE singleton_id = 1
                FOR UPDATE
                """,
            )
            if row is None:
                await self._assert_first_capacity_initialization(connection)
                await connection.execute(
                    """
                    INSERT INTO durable_execution.capacity_state
                    (singleton_id, max_active_runs, max_stored_runs,
                     max_managed_operations_per_run,
                     max_journal_transitions_per_operation,
                     max_journal_rows_per_run, max_payload_bytes,
                     max_lineage_checkpoints, recursion_limit,
                     lease_duration_seconds, lease_renew_interval_seconds)
                    VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self._limits.max_active_runs,
                        self._limits.max_stored_runs,
                        self._limits.max_managed_operations_per_run,
                        self._limits.max_journal_transitions_per_operation,
                        self._limits.max_journal_rows_per_run,
                        self._limits.max_payload_bytes,
                        self._limits.max_lineage_checkpoints,
                        self._limits.recursion_limit,
                        self._limits.lease_duration_seconds,
                        self._limits.lease_renew_interval_seconds,
                    ),
                )
                row = {
                    "active_run_count": 0,
                    "stored_run_count": 0,
                }
            else:
                self._assert_limit_row(row)
            await self._assert_capacity_counts(connection, row)
        return self._limits

    async def _assert_first_capacity_initialization(self, connection) -> None:
        for table in EXPECTED_BUSINESS_TABLES:
            if table == "capacity_state":
                continue
            row = await self._one(
                connection,
                f"SELECT EXISTS (SELECT 1 FROM durable_execution.{table}) AS present",
            )
            if row is None or row["present"]:
                raise DurableExecutionConfigurationError() from None
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            row = await self._one(
                connection,
                f"SELECT EXISTS (SELECT 1 FROM durable_execution.{table}) AS present",
            )
            if row is None or row["present"]:
                raise DurableExecutionConfigurationError() from None
        migration_rows = await self._all(
            connection,
            """
            SELECT v FROM durable_execution.checkpoint_migrations
            WHERE v NOT BETWEEN 0 AND 9
            """,
        )
        if migration_rows:
            raise DurableExecutionConfigurationError() from None

    def _assert_limit_row(self, row: Mapping[str, object]) -> None:
        expected = {
            "max_active_runs": self._limits.max_active_runs,
            "max_stored_runs": self._limits.max_stored_runs,
            "max_managed_operations_per_run": self._limits.max_managed_operations_per_run,
            "max_journal_transitions_per_operation": self._limits.max_journal_transitions_per_operation,
            "max_journal_rows_per_run": self._limits.max_journal_rows_per_run,
            "max_payload_bytes": self._limits.max_payload_bytes,
            "max_lineage_checkpoints": self._limits.max_lineage_checkpoints,
            "recursion_limit": self._limits.recursion_limit,
            "lease_duration_seconds": self._limits.lease_duration_seconds,
            "lease_renew_interval_seconds": self._limits.lease_renew_interval_seconds,
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise DurableExecutionConfigurationError() from None

    async def _assert_capacity_counts(self, connection, row: Mapping[str, object]) -> None:
        counts = await self._one(
            connection,
            """
            SELECT count(*)::integer AS stored_count,
                   count(*) FILTER (WHERE lifecycle = 'open')::integer AS active_count
            FROM durable_execution.runs
            """,
        )
        if counts is None:
            raise DurableExecutionDependencyError() from None
        if (
            row.get("stored_run_count") != counts["stored_count"]
            or row.get("active_run_count") != counts["active_count"]
        ):
            raise DurableExecutionConfigurationError() from None

    async def create_run(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
        contract: DurableExecutionContract,
        start_plan: ExecutionBudgetPlan,
        initial_command: DurableExecutionEncodedCommand | None = None,
        initial_owner: UUID | None = None,
    ) -> int | None:
        if type(run_id) is not UUID or type(trace_id) is not UUID:
            raise DurableExecutionUsageError() from None
        if type(contract) is not DurableExecutionContract:
            raise DurableExecutionUsageError() from None
        fresh_plan = _strict_copy(
            start_plan,
            ExecutionBudgetPlan,
            ExecutionBudgetPlanValidationError,
        )
        if fresh_plan.run_id != run_id:
            raise BudgetOperationScopeMismatchError() from None
        if (initial_command is None) != (initial_owner is None):
            raise DurableExecutionUsageError() from None
        fresh_initial = None
        if initial_command is not None:
            fresh_initial = _fresh_encoded_command(initial_command)
            if fresh_initial.command_kind != "initial":
                raise DurableExecutionStateError() from None
            if type(initial_owner) is not UUID:
                raise DurableExecutionUsageError() from None
        self._validate_start_plan(fresh_plan)
        contract_bytes = contract_canonical_bytes(contract)
        if len(contract_bytes) > self._limits.max_payload_bytes:
            raise DurableExecutionConfigurationError() from None
        try:
            contract_id = contract.contract_id
            contract_digest = contract.contract_digest
            contract_version = contract.schema_version
        except Exception:
            raise DurableExecutionConfigurationError() from None
        async with self._transaction(capacity_lock=True) as connection:
            capacity = await self._one(
                connection,
                """
                SELECT max_active_runs, max_stored_runs,
                       active_run_count, stored_run_count
                FROM durable_execution.capacity_state
                WHERE singleton_id = 1
                FOR UPDATE
                """,
            )
            if capacity is None:
                raise DurableExecutionConfigurationError() from None
            self._assert_limit_row(
                await self._one(
                    connection,
                    """
                    SELECT max_active_runs, max_stored_runs,
                           max_managed_operations_per_run,
                           max_journal_transitions_per_operation,
                           max_journal_rows_per_run, max_payload_bytes,
                           max_lineage_checkpoints, recursion_limit,
                           lease_duration_seconds, lease_renew_interval_seconds,
                           active_run_count, stored_run_count
                    FROM durable_execution.capacity_state
                    WHERE singleton_id = 1
                    """,
                )
                or {}
            )
            existing = await self._one(
                connection,
                "SELECT run_id FROM durable_execution.runs WHERE run_id = %s",
                (run_id,),
            )
            if existing is not None:
                raise DurableExecutionRunAlreadyExistsError() from None
            if (
                capacity["active_run_count"] >= capacity["max_active_runs"]
                or capacity["stored_run_count"] >= capacity["max_stored_runs"]
            ):
                raise DurableExecutionCapacityExceededError() from None
            await connection.execute(
                """
                INSERT INTO durable_execution.runs
                (run_id, trace_id, lifecycle, bootstrap_state, contract_id,
                 contract_schema_version, contract_bytes, contract_digest,
                 contract_size_bytes)
                VALUES (%s, %s, 'open', 'bootstrap_pending', %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    trace_id,
                    contract_id,
                    contract_version,
                    contract_bytes,
                    contract_digest,
                    len(contract_bytes),
                ),
            )
            if fresh_initial is None:
                await connection.execute(
                    "INSERT INTO durable_execution.run_leases (run_id) VALUES (%s)",
                    (run_id,),
                )
            else:
                await connection.execute(
                    """
                    INSERT INTO durable_execution.run_leases
                    (run_id, lease_owner, fencing_token, expires_at, renewed_at)
                    VALUES (%s, %s, 1,
                            clock_timestamp() + (%s * interval '1 second'),
                            clock_timestamp())
                    """,
                    (run_id, initial_owner, self._limits.lease_duration_seconds),
                )
            plan_digest = _plan_digest(fresh_plan)
            for entry in fresh_plan.limits:
                scope, name, phase_code = _operation_key(entry.key)
                await connection.execute(
                    """
                    INSERT INTO durable_execution.budget_limits
                    (run_id, scope, name, phase_code, limit_value,
                     activation_ordinal, activation_digest)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        scope,
                        name,
                        phase_code,
                        entry.limit,
                        0,
                        plan_digest,
                    ),
                )
            await connection.execute(
                """
                UPDATE durable_execution.capacity_state
                SET active_run_count = active_run_count + 1,
                    stored_run_count = stored_run_count + 1
                WHERE singleton_id = 1
                """,
            )
            if fresh_initial is not None:
                await connection.execute(
                    """
                    INSERT INTO durable_execution.pending_commands
                    (run_id, pause_generation, command_kind, node_name,
                     command_type_id, command_type_version, command_bytes,
                     command_digest, command_size_bytes, status,
                     accepted_at)
                    VALUES (%s, 0, 'initial', '__start__', %s, %s, %s, %s, %s,
                            'accepted', clock_timestamp())
                    """,
                    (
                        run_id,
                        fresh_initial.type_id,
                        fresh_initial.type_version,
                        fresh_initial.canonical_bytes,
                        fresh_initial.digest,
                        fresh_initial.size_bytes,
                    ),
                )
            return 1 if fresh_initial is not None else None

    @staticmethod
    def _validate_start_plan(plan: ExecutionBudgetPlan) -> None:
        if not plan.limits:
            raise ExecutionBudgetPlanValidationError() from None
        if not any(
            entry.key.scope is ExecutionBudgetScope.PHASE
            and entry.key.phase is WorkflowPhase.REQUIREMENTS
            for entry in plan.limits
        ):
            raise ExecutionBudgetPlanValidationError() from None
        if any(
            entry.key.scope is ExecutionBudgetScope.PHASE
            and entry.key.phase is not WorkflowPhase.REQUIREMENTS
            for entry in plan.limits
        ):
            raise ExecutionBudgetPlanValidationError() from None

    async def get_run_row(self, *, run_id: UUID) -> Mapping[str, object]:
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction() as connection:
            row = await self._one(
                connection,
                """
                SELECT run_id, trace_id, lifecycle, bootstrap_state,
                       latest_root_checkpoint_ns, latest_root_checkpoint_id,
                       latest_root_checkpoint_digest, pause_generation,
                       managed_operation_count, journal_row_count,
                       blocked_reason_code, finalized_at
                FROM durable_execution.runs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            if row is None:
                raise DurableExecutionRunNotFoundError() from None
            return dict(row)

    async def get_run_contract(self, *, run_id: UUID) -> Mapping[str, object]:
        """读取 run 的不可变执行契约元数据，不返回任何业务明文。"""

        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction() as connection:
            row = await self._one(
                connection,
                """
                SELECT contract_id, contract_schema_version, contract_bytes,
                       contract_digest, contract_size_bytes
                FROM durable_execution.runs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            if row is None:
                raise DurableExecutionRunNotFoundError() from None
            return dict(row)

    async def get_pending_command(
        self,
        *,
        run_id: UUID,
        pause_generation: int | None = None,
    ) -> Mapping[str, object] | None:
        """读取最新 pending 证据；正文只在内部调用链中短暂返回。"""

        if type(run_id) is not UUID or (
            pause_generation is not None
            and (
                type(pause_generation) is not int
                or isinstance(pause_generation, bool)
                or pause_generation < 0
            )
        ):
            raise DurableExecutionUsageError() from None
        async with self._transaction() as connection:
            if pause_generation is None:
                row = await self._one(
                    connection,
                    """
                    SELECT run_id, pause_generation, command_kind, node_name,
                           command_type_id, command_type_version, command_bytes,
                           command_digest, command_size_bytes, status,
                           pause_token_digest, pause_context_type,
                           pause_context_version, pause_context_bytes,
                           pause_context_digest, pause_context_size_bytes,
                           pre_checkpoint_ns, pre_checkpoint_id,
                           pre_checkpoint_digest
                    FROM durable_execution.pending_commands
                    WHERE run_id = %s
                    ORDER BY pause_generation DESC
                    LIMIT 1
                    """,
                    (run_id,),
                )
            else:
                row = await self._one(
                    connection,
                    """
                    SELECT run_id, pause_generation, command_kind, node_name,
                           command_type_id, command_type_version, command_bytes,
                           command_digest, command_size_bytes, status,
                           pause_token_digest, pause_context_type,
                           pause_context_version, pause_context_bytes,
                           pause_context_digest, pause_context_size_bytes,
                           pre_checkpoint_ns, pre_checkpoint_id,
                           pre_checkpoint_digest
                    FROM durable_execution.pending_commands
                    WHERE run_id = %s AND pause_generation = %s
                    """,
                    (run_id, pause_generation),
                )
            return None if row is None else dict(row)

    async def get_lease_state(self, *, run_id: UUID) -> Mapping[str, object]:
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction() as connection:
            row = await self._one(
                connection,
                """
                SELECT lease_owner, fencing_token, expires_at, renewed_at,
                       (lease_owner IS NOT NULL AND expires_at > clock_timestamp()) AS active
                FROM durable_execution.run_leases
                WHERE run_id = %s
                """,
                (run_id,),
            )
            if row is None:
                raise DurableExecutionRunNotFoundError() from None
            return dict(row)

    async def get_operation_journal_evidence(
        self,
        *,
        run_id: UUID,
        operation_id: OperationId,
        node_name: str,
        owner: UUID,
        fencing_token: int,
    ) -> Mapping[str, object]:
        """在当前 fence 下读取单个受管 operation 的最新 journal 证据。"""

        if (
            type(run_id) is not UUID
            or type(operation_id) is not OperationId
            or operation_id.run_id != run_id
            or type(node_name) is not str
            or type(owner) is not UUID
            or type(fencing_token) is not int
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(
                connection,
                run_id=run_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            row = await self._one(
                connection,
                """
                SELECT EXISTS (
                           SELECT 1
                           FROM durable_execution.budget_operations
                           WHERE run_id = %s
                             AND operation_phase = %s
                             AND operation_logical_step = %s
                       ) AS operation_exists,
                       journal.journal_state AS latest_state,
                       journal.node_name AS latest_node_name,
                       journal.artifact_id AS latest_artifact_id,
                       journal.artifact_type AS latest_artifact_type,
                       journal.artifact_schema_version AS latest_artifact_schema_version,
                       journal.artifact_content_hash AS latest_artifact_content_hash,
                       journal.artifact_created_at AS latest_artifact_created_at,
                       journal.artifact_size_bytes AS latest_artifact_size_bytes
                FROM (VALUES (1)) AS seed(value)
                LEFT JOIN LATERAL (
                    SELECT journal_state, node_name, artifact_id, artifact_type,
                           artifact_schema_version, artifact_content_hash,
                           artifact_created_at, artifact_size_bytes
                    FROM durable_execution.operation_journal
                    WHERE run_id = %s
                      AND operation_phase = %s
                      AND operation_logical_step = %s
                    ORDER BY transition_ordinal DESC
                    LIMIT 1
                ) AS journal ON TRUE
                """,
                (
                    run_id,
                    operation_id.phase.value,
                    operation_id.logical_step,
                    run_id,
                    operation_id.phase.value,
                    operation_id.logical_step,
                ),
            )
            if row is None:
                raise DurableExecutionDependencyError() from None
            if row["latest_node_name"] is not None and row["latest_node_name"] not in {node_name, "__budget__"}:
                raise DurableExecutionStateError() from None
            return dict(row)

    async def get_budget_activation_rows(self, *, run_id: UUID) -> tuple[Mapping[str, object], ...]:
        """读取已持久化的 activation 定点，供显式恢复做只读复算。"""

        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            rows = await self._all(
                connection,
                """
                SELECT scope, name, phase_code, limit_value,
                       activation_ordinal, activation_digest,
                       activation_checkpoint_ns, activation_checkpoint_id,
                       activation_checkpoint_digest
                FROM durable_execution.budget_limits
                WHERE run_id = %s
                ORDER BY activation_ordinal, scope, name, phase_code
                """,
                (run_id,),
            )
            return tuple(dict(row) for row in rows)

    async def get_managed_evidence_counts(self, *, run_id: UUID) -> Mapping[str, int]:
        """读取 bootstrap preflight 所需的 Module 11 可达证据计数。"""

        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            row = await self._one(
                connection,
                """
                SELECT
                    (SELECT count(*) FROM durable_execution.budget_operations WHERE run_id = %s) AS operation_count,
                    (SELECT count(*) FROM durable_execution.operation_journal WHERE run_id = %s) AS journal_count
                """,
                (run_id, run_id),
            )
            if row is None:
                raise DurableExecutionDependencyError() from None
            return {
                "operation_count": int(row["operation_count"]),
                "journal_count": int(row["journal_count"]),
            }

    async def get_latest_operation_evidence(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
    ) -> tuple[Mapping[str, object], ...]:
        """在 fence 下读取本 run 每个预算 operation 的最后一条 journal。

        这是 Runtime 结算/恢复使用的受控投影，不是公开 history/list API；
        只返回已有预算 operation 的最新状态和其 Artifact/checkpoint 标量。
        """

        if (
            type(run_id) is not UUID
            or type(owner) is not UUID
            or type(fencing_token) is not int
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(
                connection,
                run_id=run_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            rows = await self._all(
                connection,
                """
                SELECT b.operation_phase, b.operation_logical_step,
                       j.node_name, j.journal_state,
                       j.artifact_id, j.artifact_type,
                       j.artifact_schema_version, j.artifact_content_hash,
                       j.artifact_created_at, j.artifact_size_bytes,
                       j.checkpoint_ns, j.checkpoint_id, j.checkpoint_digest,
                       j.reason_code
                FROM durable_execution.budget_operations AS b
                LEFT JOIN LATERAL (
                    SELECT node_name, journal_state,
                           artifact_id, artifact_type,
                           artifact_schema_version, artifact_content_hash,
                           artifact_created_at, artifact_size_bytes,
                           checkpoint_ns, checkpoint_id, checkpoint_digest,
                           reason_code
                    FROM durable_execution.operation_journal
                    WHERE run_id = b.run_id
                      AND operation_phase = b.operation_phase
                      AND operation_logical_step = b.operation_logical_step
                    ORDER BY transition_ordinal DESC
                    LIMIT 1
                ) AS j ON TRUE
                WHERE b.run_id = %s
                ORDER BY b.operation_phase, b.operation_logical_step
                """,
                (run_id,),
            )
            return tuple(dict(row) for row in rows)

    async def get_recovery_operation_evidence(
        self,
        *,
        run_id: UUID,
    ) -> tuple[Mapping[str, object], ...]:
        """读取恢复分类所需的本 run 精确 budget/journal 投影，不扫描 Artifact 存储。"""

        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            rows = await self._all(
                connection,
                """
                SELECT b.operation_phase, b.operation_logical_step,
                       j.node_name, j.journal_state,
                       j.artifact_id, j.artifact_type,
                       j.artifact_schema_version, j.artifact_content_hash,
                       j.artifact_created_at, j.artifact_size_bytes,
                       j.checkpoint_ns, j.checkpoint_id, j.checkpoint_digest,
                       j.reason_code
                FROM durable_execution.budget_operations AS b
                LEFT JOIN LATERAL (
                    SELECT node_name, journal_state,
                           artifact_id, artifact_type,
                           artifact_schema_version, artifact_content_hash,
                           artifact_created_at, artifact_size_bytes,
                           checkpoint_ns, checkpoint_id, checkpoint_digest,
                           reason_code
                    FROM durable_execution.operation_journal
                    WHERE run_id = b.run_id
                      AND operation_phase = b.operation_phase
                      AND operation_logical_step = b.operation_logical_step
                    ORDER BY transition_ordinal DESC LIMIT 1
                ) AS j ON TRUE
                WHERE b.run_id = %s
                ORDER BY b.operation_phase, b.operation_logical_step
                """,
                (run_id,),
            )
            return tuple(dict(row) for row in rows)

    async def advance_pause_generation(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
    ) -> int:
        """在 fence 下为一次合法等待点分配唯一下一代。"""

        if type(run_id) is not UUID or type(owner) is not UUID:
            raise DurableExecutionUsageError() from None
        if type(fencing_token) is not int or isinstance(fencing_token, bool) or fencing_token < 1:
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(
                connection,
                run_id=run_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            row = await self._one(
                connection,
                """
                UPDATE durable_execution.runs
                SET pause_generation = pause_generation + 1
                WHERE run_id = %s AND lifecycle = 'open'
                RETURNING pause_generation
                """,
                (run_id,),
            )
            if row is None:
                raise DurableExecutionStateError() from None
            return int(row["pause_generation"])

    async def acquire_lease(self, *, run_id: UUID, owner: UUID) -> int:
        if type(run_id) is not UUID or type(owner) is not UUID:
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            run = await self._one(
                connection,
                "SELECT lifecycle FROM durable_execution.runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            if run["lifecycle"] != "open":
                raise DurableExecutionStateError() from None
            lease = await self._one(
                connection,
                """
                SELECT lease_owner, fencing_token, expires_at,
                       (expires_at IS NOT NULL AND expires_at > clock_timestamp()) AS active
                FROM durable_execution.run_leases
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            if lease is None:
                raise DurableExecutionDependencyError() from None
            if lease["active"]:
                raise DurableExecutionRunBusyError() from None
            token = int(lease["fencing_token"]) + 1
            result = await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = %s,
                    fencing_token = %s,
                    expires_at = clock_timestamp() + (%s * interval '1 second'),
                    renewed_at = clock_timestamp()
                WHERE run_id = %s AND fencing_token = %s
                """,
                (
                    owner,
                    token,
                    self._limits.lease_duration_seconds,
                    run_id,
                    lease["fencing_token"],
                ),
            )
            if getattr(result, "rowcount", 1) != 1:
                raise DurableExecutionRunBusyError() from None
            return token

    async def acquire_recovery_lease(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        expected_pointer: tuple[str, str, str] | None,
        pause_generation: int,
        expected_pending_status: str,
    ) -> int:
        """在同一 run 锁内复验恢复证据并原子竞争新 fence。"""

        pointer = _normalize_pointer(expected_pointer)
        if (
            type(run_id) is not UUID
            or type(owner) is not UUID
            or (expected_pointer is not None and pointer is None)
            or type(pause_generation) is not int
            or isinstance(pause_generation, bool)
            or pause_generation < 0
            or type(expected_pending_status) is not str
            or expected_pending_status not in {"dispatch_started", "cleared"}
        ):
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            run = await self._one(
                connection,
                """
                SELECT lifecycle, latest_root_checkpoint_ns,
                       latest_root_checkpoint_id, latest_root_checkpoint_digest
                FROM durable_execution.runs WHERE run_id = %s FOR UPDATE
                """,
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            actual_pointer = (
                None
                if run["latest_root_checkpoint_id"] is None
                else (
                    str(run["latest_root_checkpoint_ns"]),
                    str(run["latest_root_checkpoint_id"]),
                    str(run["latest_root_checkpoint_digest"]),
                )
            )
            if run["lifecycle"] != "open" or actual_pointer != pointer:
                raise DurableExecutionRunBusyError() from None
            pending = await self._one(
                connection,
                """
                SELECT pause_generation, status
                FROM durable_execution.pending_commands
                WHERE run_id = %s
                ORDER BY pause_generation DESC LIMIT 1 FOR UPDATE
                """,
                (run_id,),
            )
            if (
                pending is None
                or int(pending["pause_generation"]) != pause_generation
                or pending["status"] != expected_pending_status
            ):
                raise DurableExecutionRunBusyError() from None
            lease = await self._one(
                connection,
                """
                SELECT lease_owner, fencing_token, expires_at,
                       (lease_owner IS NOT NULL AND expires_at > clock_timestamp()) AS active
                FROM durable_execution.run_leases WHERE run_id = %s FOR UPDATE
                """,
                (run_id,),
            )
            if lease is None:
                raise DurableExecutionDependencyError() from None
            if lease["active"]:
                raise DurableExecutionRunBusyError() from None
            token = int(lease["fencing_token"]) + 1
            result = await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = %s, fencing_token = %s,
                    expires_at = clock_timestamp() + (%s * interval '1 second'),
                    renewed_at = clock_timestamp()
                WHERE run_id = %s AND fencing_token = %s
                """,
                (owner, token, self._limits.lease_duration_seconds, run_id, lease["fencing_token"]),
            )
            if getattr(result, "rowcount", 1) != 1:
                raise DurableExecutionRunBusyError() from None
            return token

    async def renew_lease(self, *, run_id: UUID, owner: UUID, fencing_token: int) -> None:
        if (
            type(run_id) is not UUID
            or type(owner) is not UUID
            or type(fencing_token) is not int
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise DurableExecutionUsageError() from None

        async def renew_once() -> None:
            async with self._transaction(
                run_id=run_id,
                commit_unknown_point="lease_renew_commit_unknown",
                commit_unknown=True,
            ) as connection:
                result = await connection.execute(
                    """
                    UPDATE durable_execution.run_leases
                    SET expires_at = clock_timestamp() + (%s * interval '1 second'),
                        renewed_at = clock_timestamp()
                    WHERE run_id = %s AND lease_owner = %s AND fencing_token = %s
                      AND expires_at > clock_timestamp()
                    """,
                    (self._limits.lease_duration_seconds, run_id, owner, fencing_token),
                )
                if getattr(result, "rowcount", 1) != 1:
                    raise DurableExecutionStateError() from None

        # 连接借用/瞬时依赖失败最多立即重试一次；CAS 未命中和 commit-unknown
        # 是确定性失租，不得把它们当作普通连接失败再次续租。
        for attempt in range(2):
            try:
                await renew_once()
                return
            except _CommitUnknown:
                raise DurableExecutionStateError() from None
            except DurableExecutionDependencyError:
                if attempt == 1:
                    raise DurableExecutionStateError() from None

    async def release_lease(self, *, run_id: UUID, owner: UUID, fencing_token: int) -> bool:
        if (
            type(run_id) is not UUID
            or type(owner) is not UUID
            or type(fencing_token) is not int
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            result = await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = NULL, expires_at = NULL, renewed_at = NULL
                WHERE run_id = %s AND lease_owner = %s AND fencing_token = %s
                """,
                (run_id, owner, fencing_token),
            )
            return getattr(result, "rowcount", 1) == 1

    async def _assert_fence(self, connection, *, run_id: UUID, owner: UUID, fencing_token: int) -> None:
        row = await self._one(
            connection,
            """
            SELECT 1 AS valid
            FROM durable_execution.runs AS r
            JOIN durable_execution.run_leases AS l ON l.run_id = r.run_id
            WHERE r.run_id = %s AND r.lifecycle = 'open'
              AND l.lease_owner = %s AND l.fencing_token = %s
              AND l.expires_at > clock_timestamp()
            FOR UPDATE OF r, l
            """,
            (run_id, owner, fencing_token),
        )
        if row is None:
            raise DurableExecutionStateError() from None

    @asynccontextmanager
    async def fencing_transaction(self, *, run_id: UUID, owner: UUID, fencing_token: int):
        """供私有 checkpointer 使用；锁内只允许官方 Saver 的一次数据库委托。"""

        if type(run_id) is not UUID or type(owner) is not UUID or type(fencing_token) is not int:
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(
                connection,
                run_id=run_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            yield connection

    async def activate_phase(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
        phase: WorkflowPhase,
        plan: ExecutionBudgetPlan,
        checkpoint: tuple[str, str, str] | None = None,
    ) -> None:
        if type(phase) is not WorkflowPhase:
            raise DurableExecutionUsageError() from None
        fresh_plan = _strict_copy(plan, ExecutionBudgetPlan, ExecutionBudgetPlanValidationError)
        if fresh_plan.run_id != run_id:
            raise BudgetOperationScopeMismatchError() from None
        if phase is WorkflowPhase.REQUIREMENTS:
            self._validate_start_plan(fresh_plan)
            ordinal = 0
            expected_checkpoint = None
        else:
            ordinal = _PHASE_ORDINAL[phase.value]
            expected_checkpoint = _normalize_pointer(checkpoint)
            if expected_checkpoint is None:
                raise DurableExecutionStateError() from None
            if not fresh_plan.limits or any(
                entry.key.scope is not ExecutionBudgetScope.PHASE
                or entry.key.phase is not phase
                for entry in fresh_plan.limits
            ):
                raise ExecutionBudgetPlanValidationError() from None
        digest = _plan_digest(fresh_plan)
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            run_row = await self._one(
                connection,
                """
                SELECT latest_root_checkpoint_ns, latest_root_checkpoint_id,
                       latest_root_checkpoint_digest
                FROM durable_execution.runs
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            if run_row is None:
                raise DurableExecutionRunNotFoundError() from None
            # 后续 budgeted phase 的 activation 绑定 Runtime 在进入该阶段首 node
            # 时从官方 Saver 读取并验证过的 pre-node root checkpoint；它可能领先
            # 于尚未随 Graph 返回而前进的 runs.latest_root_checkpoint，因此这里
            # 只做结构校验与 activation 内部的冻结一致性校验，不强制等于 latest。
            all_rows = await self._all(
                connection,
                """
                SELECT scope, name, phase_code, limit_value, activation_ordinal,
                       activation_digest, activation_checkpoint_ns,
                       activation_checkpoint_id, activation_checkpoint_digest
                FROM durable_execution.budget_limits
                WHERE run_id = %s
                ORDER BY activation_ordinal, scope, name, phase_code
                FOR UPDATE
                """,
                (run_id,),
            )
            rows = tuple(row for row in all_rows if row["activation_ordinal"] == ordinal)
            expected = {
                _operation_key(entry.key): entry.limit for entry in fresh_plan.limits
            }
            # requirements/ordinal-0 只和 create_run 的原子固化结果做完整
            # 比较，绝不补插或补齐；后续 phase 首次激活才允许一次性建立
            # 自己的完整集合，已有半份集合一律拒绝。
            if ordinal == 0 and (not rows or len(rows) != len(expected)):
                raise DurableExecutionStateError() from None
            for row in all_rows:
                key = (str(row["scope"]), str(row["name"]), str(row["phase_code"]))
                if key in expected and row["activation_ordinal"] != ordinal:
                    raise DurableExecutionStateError() from None
            if ordinal > 0 and not rows:
                for (scope, name, phase_code), limit_value in sorted(expected.items()):
                    await connection.execute(
                        """
                        INSERT INTO durable_execution.budget_limits
                        (run_id, scope, name, phase_code, limit_value,
                         activation_ordinal, activation_digest,
                         activation_checkpoint_ns, activation_checkpoint_id,
                         activation_checkpoint_digest)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            run_id,
                            scope,
                            name,
                            phase_code,
                            limit_value,
                            ordinal,
                            digest,
                            expected_checkpoint[0],
                            expected_checkpoint[1],
                            expected_checkpoint[2],
                        ),
                    )
                rows = tuple(
                    {
                        "scope": scope,
                        "name": name,
                        "phase_code": phase_code,
                        "limit_value": limit_value,
                        "activation_ordinal": ordinal,
                        "activation_digest": digest,
                        "activation_checkpoint_ns": expected_checkpoint[0],
                        "activation_checkpoint_id": expected_checkpoint[1],
                        "activation_checkpoint_digest": expected_checkpoint[2],
                    }
                    for (scope, name, phase_code), limit_value in sorted(expected.items())
                )
            elif ordinal > 0 and len(rows) != len(expected):
                raise DurableExecutionStateError() from None
            required_checkpoint = (
                (None, None, None)
                if ordinal == 0
                else expected_checkpoint
            )
            for row in rows:
                key = (str(row["scope"]), str(row["name"]), str(row["phase_code"]))
                actual_checkpoint = (
                    row["activation_checkpoint_ns"],
                    row["activation_checkpoint_id"],
                    row["activation_checkpoint_digest"],
                )
                if (
                    key not in expected
                    or row["limit_value"] != expected[key]
                    or row["activation_digest"] != digest
                    or row["activation_ordinal"] != ordinal
                    or actual_checkpoint != required_checkpoint
                ):
                    raise DurableExecutionStateError() from None

    def _budget_manager(self, *, run_id: UUID, owner: UUID, fencing_token: int):
        from app.durable_execution.budget import PersistentExecutionBudgetManager

        return PersistentExecutionBudgetManager(
            repository=self,
            run_id=run_id,
            owner=owner,
            fencing_token=fencing_token,
        )

    async def _capture_budget_intent(
        self,
        *,
        request: BudgetConsumeRequest,
        owner: UUID,
        fencing_token: int,
    ) -> _BudgetIntent:
        scope, name, phase_code = _operation_key(request.key)
        async with self._transaction(run_id=request.operation_id.run_id) as connection:
            await self._assert_fence(
                connection,
                run_id=request.operation_id.run_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            row = await self._one(
                connection,
                """
                SELECT r.lifecycle, r.managed_operation_count, r.journal_row_count,
                       l.limit_value, l.consumed_value,
                       EXISTS (
                           SELECT 1 FROM durable_execution.budget_operations AS b
                           WHERE b.run_id = r.run_id
                             AND b.operation_phase = %s
                             AND b.operation_logical_step = %s
                       ) AS operation_exists,
                       (SELECT count(*) FROM durable_execution.operation_journal AS j
                        WHERE j.run_id = r.run_id
                          AND j.operation_phase = %s
                          AND j.operation_logical_step = %s) AS target_journal_rows
                FROM durable_execution.runs AS r
                JOIN durable_execution.budget_limits AS l
                  ON l.run_id = r.run_id AND l.scope = %s
                 AND l.name = %s AND l.phase_code = %s
                WHERE r.run_id = %s
                """,
                (
                    request.operation_id.phase.value,
                    request.operation_id.logical_step,
                    request.operation_id.phase.value,
                    request.operation_id.logical_step,
                    scope,
                    name,
                    phase_code,
                    request.operation_id.run_id,
                ),
            )
            if row is None:
                raise BudgetKeyNotConfiguredError() from None
            return _BudgetIntent(
                run_id=request.operation_id.run_id,
                operation_phase=request.operation_id.phase.value,
                logical_step=request.operation_id.logical_step,
                scope=scope,
                name=name,
                phase_code=phase_code,
                amount=request.amount,
                lifecycle=str(row["lifecycle"]),
                managed_operation_count=int(row["managed_operation_count"]),
                journal_row_count=int(row["journal_row_count"]),
                limit_value=int(row["limit_value"]),
                consumed_value=int(row["consumed_value"]),
                operation_exists=bool(row["operation_exists"]),
                target_journal_rows=int(row["target_journal_rows"]),
            )

    async def _consume_budget(
        self,
        *,
        request: BudgetConsumeRequest,
        owner: UUID,
        fencing_token: int,
        node_name: str = "__budget__",
        _skip_fault: bool = False,
    ) -> BudgetConsumeOutcome:
        if type(node_name) is not str or not 1 <= len(node_name) <= 128 or node_name.strip() != node_name:
            raise DurableExecutionUsageError() from None
        intent = await self._capture_budget_intent(
            request=request,
            owner=owner,
            fencing_token=fencing_token,
        )
        try:
            return await self._consume_budget_once(
                request=request,
                owner=owner,
                fencing_token=fencing_token,
                node_name=node_name,
                _skip_fault=_skip_fault,
            )
        except _CommitUnknown:
            return await self._reconcile_budget_commit(
                request=request,
                owner=owner,
                fencing_token=fencing_token,
                node_name=node_name,
                intent=intent,
            )

    async def _consume_budget_once(
        self,
        *,
        request: BudgetConsumeRequest,
        owner: UUID,
        fencing_token: int,
        node_name: str = "__budget__",
        _skip_fault: bool = False,
    ) -> BudgetConsumeOutcome:
        async with self._transaction(
            run_id=request.operation_id.run_id,
            commit_exception_point=(
                "budget_retry_commit_exception" if _skip_fault else "budget_commit_exception"
            ),
            commit_unknown_point=(
                "budget_retry_commit_unknown" if _skip_fault else "budget_commit_unknown"
            ),
            commit_unknown=True,
        ) as connection:
            await self._assert_fence(
                connection,
                run_id=request.operation_id.run_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            run = await self._one(
                connection,
                """
                SELECT managed_operation_count, journal_row_count
                FROM durable_execution.runs
                WHERE run_id = %s
                FOR UPDATE
                """,
                (request.operation_id.run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            scope, name, phase_code = _operation_key(request.key)
            operation = await self._one(
                connection,
                """
                SELECT budget_scope, budget_name, budget_phase_code, amount,
                       outcome, snapshot_limit, snapshot_consumed, snapshot_remaining
                FROM durable_execution.budget_operations
                WHERE run_id = %s AND operation_phase = %s
                  AND operation_logical_step = %s
                FOR UPDATE
                """,
                (
                    request.operation_id.run_id,
                    request.operation_id.phase.value,
                    request.operation_id.logical_step,
                ),
            )
            if operation is not None:
                if (
                    operation["budget_scope"] != scope
                    or operation["budget_name"] != name
                    or operation["budget_phase_code"] != phase_code
                    or operation["amount"] != request.amount
                ):
                    raise BudgetConsumeConflictError() from None
                return self._outcome_from_operation(operation, request, replayed=True)
            if run["managed_operation_count"] >= self._limits.max_managed_operations_per_run:
                raise ExecutionBudgetCapacityExceededError() from None
            if run["journal_row_count"] >= self._limits.max_journal_rows_per_run:
                raise ExecutionBudgetCapacityExceededError() from None
            limit_row = await self._one(
                connection,
                """
                SELECT limit_value, consumed_value, phase_code
                FROM durable_execution.budget_limits
                WHERE run_id = %s AND scope = %s AND name = %s AND phase_code = %s
                FOR UPDATE
                """,
                (request.operation_id.run_id, scope, name, phase_code),
            )
            if limit_row is None:
                raise BudgetKeyNotConfiguredError() from None
            if request.key.scope is ExecutionBudgetScope.PHASE and request.key.phase is not request.operation_id.phase:
                raise BudgetOperationScopeMismatchError() from None
            limit_value = int(limit_row["limit_value"])
            current = int(limit_row["consumed_value"])
            applied = current + request.amount <= limit_value
            next_consumed = current + request.amount if applied else current
            remaining = limit_value - next_consumed
            outcome_value = "consumed" if applied else "exceeded"
            await connection.execute(
                """
                INSERT INTO durable_execution.budget_operations
                (run_id, operation_phase, operation_logical_step, budget_scope,
                 budget_name, budget_phase_code, amount, outcome,
                 snapshot_limit, snapshot_consumed, snapshot_remaining)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request.operation_id.run_id,
                    request.operation_id.phase.value,
                    request.operation_id.logical_step,
                    scope,
                    name,
                    phase_code,
                    request.amount,
                    outcome_value,
                    limit_value,
                    next_consumed,
                    remaining,
                ),
            )
            if applied:
                await connection.execute(
                    """
                    UPDATE durable_execution.budget_limits
                    SET consumed_value = %s
                    WHERE run_id = %s AND scope = %s AND name = %s AND phase_code = %s
                    """,
                    (next_consumed, request.operation_id.run_id, scope, name, phase_code),
                )
            journal_state = "execution_permitted" if applied else "budget_rejected"
            journal_inserted = await self._append_journal_in_transaction(
                connection,
                run_id=request.operation_id.run_id,
                operation_phase=request.operation_id.phase.value,
                logical_step=request.operation_id.logical_step,
                node_name=node_name,
                journal_state=journal_state,
            )
            if not journal_inserted:
                # 新 budget operation 却对应已有 journal transition 是账本分裂，
                # 不能把它当作一次成功的精确重放。
                raise DurableExecutionStateError() from None
            counts_result = await connection.execute(
                """
                UPDATE durable_execution.runs
                SET managed_operation_count = managed_operation_count + 1,
                    journal_row_count = journal_row_count + 1
                WHERE run_id = %s
                """,
                (request.operation_id.run_id,),
            )
            if getattr(counts_result, "rowcount", 1) != 1:
                raise DurableExecutionStateError() from None
            outcome = self._outcome_from_operation(
                {
                    "budget_scope": scope,
                    "budget_name": name,
                    "budget_phase_code": phase_code,
                    "amount": request.amount,
                    "outcome": outcome_value,
                    "snapshot_limit": limit_value,
                    "snapshot_consumed": next_consumed,
                    "snapshot_remaining": remaining,
                },
                request,
                replayed=False,
            )
        return outcome

    @staticmethod
    def _outcome_from_operation(
        row: Mapping[str, object],
        request: BudgetConsumeRequest,
        *,
        replayed: bool,
    ) -> BudgetConsumeOutcome:
        key = request.key
        snapshot = ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=request.operation_id.run_id,
            key=key,
            limit=int(row["snapshot_limit"]),
            consumed=int(row["snapshot_consumed"]),
            remaining=int(row["snapshot_remaining"]),
        )
        disposition = "replayed" if replayed else ("applied" if row["outcome"] == "consumed" else "rejected")
        if row["outcome"] == "consumed":
            return BudgetConsumed(
                disposition=disposition,
                operation_id=request.operation_id,
                key=key,
                amount=request.amount,
                snapshot=snapshot,
            )
        return BudgetExceeded(
            disposition=disposition,
            operation_id=request.operation_id,
            key=key,
            amount=request.amount,
            snapshot=snapshot,
        )

    async def _reconcile_budget_commit(
        self,
        *,
        request: BudgetConsumeRequest,
        owner: UUID,
        fencing_token: int,
        intent: _BudgetIntent,
        node_name: str = "__budget__",
    ) -> BudgetConsumeOutcome:
        # 每次 transaction 都从 pool 借 fresh connection；完成一次 fresh read 后
        # 最多重试一次写入，随后再做一次最终 fresh read，绝不第三次写。
        for attempt in range(2):
            async with self._transaction(run_id=request.operation_id.run_id) as connection:
                await self._assert_fence(
                    connection,
                    run_id=request.operation_id.run_id,
                    owner=owner,
                    fencing_token=fencing_token,
                )
                row = await self._one(
                    connection,
                    """
                    SELECT b.budget_scope, b.budget_name, b.budget_phase_code, b.amount,
                           b.outcome, b.snapshot_limit, b.snapshot_consumed,
                           b.snapshot_remaining, r.managed_operation_count,
                           r.journal_row_count, l.limit_value, l.consumed_value,
                           (SELECT count(*) FROM durable_execution.budget_operations
                            WHERE run_id = b.run_id) AS operation_count,
                           (SELECT count(*) FROM durable_execution.operation_journal
                            WHERE run_id = b.run_id) AS journal_count,
                           j.journal_state, j.node_name, j.transition_ordinal,
                           j.artifact_id, j.artifact_type, j.artifact_schema_version,
                           j.artifact_content_hash, j.artifact_created_at,
                           j.artifact_size_bytes, j.checkpoint_ns, j.checkpoint_id,
                           j.checkpoint_digest, j.reason_code
                    FROM durable_execution.budget_operations
                    AS b
                    JOIN durable_execution.runs AS r ON r.run_id = b.run_id
                    JOIN durable_execution.budget_limits AS l
                      ON l.run_id = b.run_id
                     AND l.scope = b.budget_scope
                     AND l.name = b.budget_name
                     AND l.phase_code = b.budget_phase_code
                    LEFT JOIN durable_execution.operation_journal AS j
                      ON j.run_id = b.run_id
                     AND j.operation_phase = b.operation_phase
                     AND j.operation_logical_step = b.operation_logical_step
                     AND j.journal_state IN ('execution_permitted', 'budget_rejected')
                    WHERE b.run_id = %s AND b.operation_phase = %s
                      AND b.operation_logical_step = %s
                    """,
                    (
                        request.operation_id.run_id,
                        request.operation_id.phase.value,
                        request.operation_id.logical_step,
                    ),
                )
                if row is not None:
                    expected_scope, expected_name, expected_phase = _operation_key(request.key)
                    expected_outcome = "consumed" if row["outcome"] == "consumed" else "exceeded"
                    expected_journal = "execution_permitted" if expected_outcome == "consumed" else "budget_rejected"
                    if (
                        row["budget_scope"] != expected_scope
                        or row["budget_name"] != expected_name
                        or row["budget_phase_code"] != expected_phase
                        or row["amount"] != request.amount
                        or row["outcome"] not in {"consumed", "exceeded"}
                        or row["journal_state"] != expected_journal
                        or row["node_name"] not in {node_name, "__budget__"}
                        or row["artifact_id"] is not None
                        or row["artifact_type"] is not None
                        or row["artifact_schema_version"] is not None
                        or row["artifact_content_hash"] is not None
                        or row["artifact_created_at"] is not None
                        or row["artifact_size_bytes"] is not None
                        or row["checkpoint_ns"] is not None
                        or row["checkpoint_id"] is not None
                        or row["checkpoint_digest"] is not None
                        or row["reason_code"] is not None
                        or int(row["snapshot_limit"]) != int(row["limit_value"])
                        or int(row["consumed_value"]) < int(row["snapshot_consumed"])
                        or int(row["consumed_value"]) > int(row["limit_value"])
                        or int(row["snapshot_remaining"]) != (
                            int(row["snapshot_limit"]) - int(row["snapshot_consumed"])
                        )
                        or row["managed_operation_count"] != row["operation_count"]
                        or row["journal_row_count"] != row["journal_count"]
                        or int(row["operation_count"]) < 1
                        or int(row["journal_count"]) < 1
                        or row["transition_ordinal"] != 1
                    ):
                        raise DurableExecutionStateError() from None
                    return self._outcome_from_operation(row, request, replayed=False)
                precondition = await self._one(
                    connection,
                    """
                    SELECT r.lifecycle, r.managed_operation_count, r.journal_row_count,
                           l.limit_value, l.consumed_value,
                           EXISTS (
                               SELECT 1 FROM durable_execution.budget_operations AS b
                               WHERE b.run_id = r.run_id
                                 AND b.operation_phase = %s
                                 AND b.operation_logical_step = %s
                           ) AS operation_exists,
                           (SELECT count(*) FROM durable_execution.operation_journal AS j
                            WHERE j.run_id = r.run_id
                              AND j.operation_phase = %s
                              AND j.operation_logical_step = %s) AS target_journal_rows
                    FROM durable_execution.runs AS r
                    JOIN durable_execution.budget_limits AS l
                      ON l.run_id = r.run_id AND l.scope = %s
                     AND l.name = %s AND l.phase_code = %s
                    WHERE r.run_id = %s
                    """,
                    (
                        intent.operation_phase,
                        intent.logical_step,
                        intent.operation_phase,
                        intent.logical_step,
                        intent.scope,
                        intent.name,
                        intent.phase_code,
                        intent.run_id,
                    ),
                )
                if precondition is None or (
                    str(precondition["lifecycle"]) != intent.lifecycle
                    or int(precondition["managed_operation_count"]) != intent.managed_operation_count
                    or int(precondition["journal_row_count"]) != intent.journal_row_count
                    or int(precondition["limit_value"]) != intent.limit_value
                    or int(precondition["consumed_value"]) != intent.consumed_value
                    or bool(precondition["operation_exists"]) != intent.operation_exists
                    or int(precondition["target_journal_rows"]) != intent.target_journal_rows
                ):
                    raise DurableExecutionStateError() from None
            if attempt == 0:
                try:
                    await self._consume_budget_once(
                        request=request,
                        owner=owner,
                        fencing_token=fencing_token,
                        node_name=node_name,
                        _skip_fault=True,
                    )
                except _CommitUnknown:
                    # 第二次写入也必须进入最终 fresh read；这里不再递归，
                    # 从而保证整个 reconciliation 最多两次写入、不会第三次写。
                    pass
                continue
        raise DurableExecutionStateError() from None

    async def get_budget_snapshot(
        self,
        *,
        run_id: UUID,
        key: ExecutionBudgetKey,
        owner: UUID,
        fencing_token: int,
    ) -> ExecutionBudgetSnapshot:
        scope, name, phase_code = _operation_key(key)
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            row = await self._one(
                connection,
                """
                SELECT limit_value, consumed_value
                FROM durable_execution.budget_limits
                WHERE run_id = %s AND scope = %s AND name = %s AND phase_code = %s
                """,
                (run_id, scope, name, phase_code),
            )
            if row is None:
                raise BudgetKeyNotConfiguredError() from None
            return ExecutionBudgetSnapshot(
                schema_version="1.0",
                run_id=run_id,
                key=key,
                limit=int(row["limit_value"]),
                consumed=int(row["consumed_value"]),
                remaining=int(row["limit_value"]) - int(row["consumed_value"]),
            )

    async def get_all_budget_snapshots(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
    ) -> tuple[ExecutionBudgetSnapshot, ...]:
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            rows = await self._all(
                connection,
                """
                SELECT scope, name, phase_code, limit_value, consumed_value
                FROM durable_execution.budget_limits
                WHERE run_id = %s
                ORDER BY CASE scope WHEN 'run' THEN 0 ELSE 1 END,
                         phase_code, name
                """,
                (run_id,),
            )
            snapshots: list[ExecutionBudgetSnapshot] = []
            for row in rows:
                phase = None if row["scope"] == "run" else WorkflowPhase(str(row["phase_code"]))
                key = ExecutionBudgetKey(
                    scope=ExecutionBudgetScope(str(row["scope"])),
                    name=str(row["name"]),
                    phase=phase,
                )
                snapshots.append(
                    ExecutionBudgetSnapshot(
                        schema_version="1.0",
                        run_id=run_id,
                        key=key,
                        limit=int(row["limit_value"]),
                        consumed=int(row["consumed_value"]),
                        remaining=int(row["limit_value"]) - int(row["consumed_value"]),
                    )
                )
            return tuple(snapshots)

    async def append_journal_transition(
        self,
        *,
        run_id: UUID,
        operation_phase: str,
        logical_step: str,
        node_name: str,
        journal_state: str,
        owner: UUID,
        fencing_token: int,
        artifact: Mapping[str, object] | None = None,
        checkpoint: tuple[str, str, str] | None = None,
        reason_code: str | None = None,
    ) -> None:
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            inserted = await self._append_journal_in_transaction(
                connection,
                run_id=run_id,
                operation_phase=operation_phase,
                logical_step=logical_step,
                node_name=node_name,
                journal_state=journal_state,
                artifact=artifact,
                checkpoint=checkpoint,
                reason_code=reason_code,
            )
            if inserted:
                await connection.execute(
                    "UPDATE durable_execution.runs SET journal_row_count = journal_row_count + 1 WHERE run_id = %s",
                    (run_id,),
                )

    async def _append_journal_in_transaction(
        self,
        connection,
        *,
        run_id: UUID,
        operation_phase: str,
        logical_step: str,
        node_name: str,
        journal_state: str,
        artifact: Mapping[str, object] | None = None,
        checkpoint: tuple[str, str, str] | None = None,
        reason_code: str | None = None,
    ) -> bool:
        if (
            journal_state not in _JOURNAL_STATES
            or type(operation_phase) is not str
            or operation_phase not in {phase.value for phase in WorkflowPhase}
            or type(logical_step) is not str
            or not logical_step
            or len(logical_step) > 64
            or type(node_name) is not str
            or not 1 <= len(node_name) <= 128
            or node_name.strip() != node_name
            or any(unicodedata.category(char).startswith("C") for char in node_name)
        ):
            raise DurableExecutionStateError() from None
        if reason_code is not None and reason_code not in _JOURNAL_REASONS:
            raise DurableExecutionStateError() from None
        pointer = _normalize_pointer(checkpoint)
        fields = _artifact_fields(artifact)
        if journal_state == "artifact_confirmed" and fields[0] is None:
            raise DurableExecutionStateError() from None
        if journal_state == "indeterminate" and reason_code is None:
            raise DurableExecutionStateError() from None
        if journal_state != "indeterminate" and reason_code is not None:
            raise DurableExecutionStateError() from None
        if journal_state in {"execution_permitted", "budget_rejected", "indeterminate"} and any(
            value is not None for value in fields
        ):
            raise DurableExecutionStateError() from None
        if journal_state in {"execution_permitted", "budget_rejected", "indeterminate", "artifact_confirmed"} and pointer is not None:
            raise DurableExecutionStateError() from None
        run_row = await self._one(
            connection,
            """
            SELECT journal_row_count
            FROM durable_execution.runs
            WHERE run_id = %s
            FOR UPDATE
            """,
            (run_id,),
        )
        if run_row is None:
            raise DurableExecutionRunNotFoundError() from None
        rows = await self._all(
            connection,
            """
            SELECT transition_ordinal, node_name, journal_state,
                   artifact_id, artifact_type, artifact_schema_version,
                   artifact_content_hash, artifact_created_at, artifact_size_bytes,
                   checkpoint_ns, checkpoint_id, checkpoint_digest, reason_code
            FROM durable_execution.operation_journal
            WHERE run_id = %s AND operation_phase = %s AND operation_logical_step = %s
            ORDER BY transition_ordinal DESC
            FOR UPDATE
            """,
            (run_id, operation_phase, logical_step),
        )
        last = rows[0] if rows else None
        if last is not None and last["journal_state"] == journal_state:
            if self._journal_row_matches(last, node_name=node_name, artifact=fields, checkpoint=pointer, reason_code=reason_code):
                return False
            raise DurableExecutionStateError() from None
        if last is not None and last["node_name"] != node_name and not (
            last["node_name"] == "__budget__"
            and str(last["journal_state"]) in {"execution_permitted", "budget_rejected"}
        ):
            raise DurableExecutionStateError() from None
        previous = None if last is None else str(last["journal_state"])
        allowed = {
            None: frozenset({"execution_permitted", "budget_rejected"}),
            "execution_permitted": frozenset({"artifact_confirmed", "checkpoint_confirmed", "indeterminate"}),
            "artifact_confirmed": frozenset({"checkpoint_confirmed"}),
            "budget_rejected": frozenset({"checkpoint_confirmed"}),
            "checkpoint_confirmed": frozenset({"completed"}),
            "completed": frozenset(),
            "indeterminate": frozenset(),
        }
        if journal_state not in allowed.get(previous, frozenset()):
            raise DurableExecutionStateError() from None
        ordinal = 1 if last is None else int(last["transition_ordinal"]) + 1
        if ordinal > self._limits.max_journal_transitions_per_operation:
            raise DurableExecutionCapacityExceededError() from None
        if run_row["journal_row_count"] >= self._limits.max_journal_rows_per_run:
            raise DurableExecutionCapacityExceededError() from None
        if journal_state in {"checkpoint_confirmed", "completed"} and pointer is None:
            raise DurableExecutionStateError() from None
        if journal_state == "completed" and previous != "checkpoint_confirmed":
            raise DurableExecutionStateError() from None
        if journal_state == "completed" and previous == "checkpoint_confirmed":
            old_pointer = (last["checkpoint_ns"], last["checkpoint_id"], last["checkpoint_digest"])
            if pointer != old_pointer:
                raise DurableExecutionStateError() from None
        if journal_state in {"checkpoint_confirmed", "completed"} and (
            previous == "artifact_confirmed"
            or (journal_state == "completed" and previous == "checkpoint_confirmed")
        ):
            old_artifact = (
                last["artifact_id"], last["artifact_type"], last["artifact_schema_version"],
                last["artifact_content_hash"], last["artifact_created_at"], last["artifact_size_bytes"],
            )
            if fields != old_artifact:
                raise DurableExecutionStateError() from None
        elif journal_state in {"checkpoint_confirmed", "completed"} and any(
            value is not None for value in fields
        ):
            raise DurableExecutionStateError() from None
        await connection.execute(
            """
            INSERT INTO durable_execution.operation_journal
            (run_id, operation_phase, operation_logical_step, transition_ordinal,
             node_name, journal_state, artifact_id, artifact_type,
             artifact_schema_version, artifact_content_hash, artifact_created_at,
             artifact_size_bytes, checkpoint_ns, checkpoint_id, checkpoint_digest,
             reason_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                operation_phase,
                logical_step,
                ordinal,
                node_name,
                journal_state,
                *fields,
                *(pointer if pointer is not None else (None, None, None)),
                reason_code,
            ),
        )
        return True

    @staticmethod
    def _journal_row_matches(
        row: Mapping[str, object],
        *,
        node_name: str,
        artifact: tuple[object, ...],
        checkpoint: tuple[str, str, str] | None,
        reason_code: str | None,
    ) -> bool:
        existing_artifact = (
            row["artifact_id"], row["artifact_type"], row["artifact_schema_version"],
            row["artifact_content_hash"], row["artifact_created_at"], row["artifact_size_bytes"],
        )
        existing_checkpoint = (
            None
            if row["checkpoint_ns"] is None
            else (row["checkpoint_ns"], row["checkpoint_id"], row["checkpoint_digest"])
        )
        return (
            row["node_name"] == node_name
            and existing_artifact == artifact
            and existing_checkpoint == checkpoint
            and row["reason_code"] == reason_code
        )

    async def create_pending_command(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        command_kind: str,
        node_name: str,
        owner: UUID,
        fencing_token: int,
        encoded_command: DurableExecutionEncodedCommand | None = None,
        pause_token_digest: str | None = None,
        pause_context_type: str | None = None,
        pause_context_version: str | None = None,
        pause_context_bytes: bytes | None = None,
        pause_context_digest: str | None = None,
        pre_checkpoint: tuple[str, str, str] | None = None,
        status: str = "awaiting_input",
    ) -> None:
        if status not in _PENDING_STATES:
            raise DurableExecutionStateError() from None
        command = None if encoded_command is None else _fresh_encoded_command(encoded_command)
        pointer = _normalize_pointer(pre_checkpoint)
        if pause_context_bytes is not None and type(pause_context_bytes) is not bytes:
            raise DurableExecutionUsageError() from None
        if pause_context_bytes is not None and len(pause_context_bytes) > self._limits.max_payload_bytes:
            raise DurableExecutionConfigurationError() from None
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            await connection.execute(
                """
                INSERT INTO durable_execution.pending_commands
                (run_id, pause_generation, command_kind, node_name,
                 command_type_id, command_type_version, command_bytes,
                 command_digest, command_size_bytes, status,
                 pause_token_digest, pause_context_type, pause_context_version,
                 pause_context_bytes, pause_context_digest, pause_context_size_bytes,
                 pre_checkpoint_ns, pre_checkpoint_id, pre_checkpoint_digest,
                 accepted_at, dispatch_started_at, checkpointed_at, cleared_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                        CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                        CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                        CASE WHEN %s THEN clock_timestamp() ELSE NULL END)
                """,
                (
                    run_id,
                    pause_generation,
                    command_kind,
                    node_name,
                    None if command is None else command.type_id,
                    None if command is None else command.type_version,
                    None if status not in {"accepted", "dispatch_started"} or command is None else command.canonical_bytes,
                    None if command is None else command.digest,
                    None if command is None else command.size_bytes,
                    status,
                    pause_token_digest,
                    pause_context_type,
                    pause_context_version,
                    pause_context_bytes if status in {"awaiting_input", "accepted", "dispatch_started"} else None,
                    pause_context_digest,
                    None if pause_context_bytes is None else len(pause_context_bytes),
                    None if pointer is None else pointer[0],
                    None if pointer is None else pointer[1],
                    None if pointer is None else pointer[2],
                    command is not None and status != "awaiting_input",
                    status in {"dispatch_started", "checkpointed"},
                    status == "checkpointed",
                    status == "cleared",
                ),
            )

    async def transition_pending_command(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        expected_status: str,
        new_status: str,
        owner: UUID,
        fencing_token: int,
        encoded_command: DurableExecutionEncodedCommand | None = None,
    ) -> None:
        if expected_status not in _PENDING_STATES or new_status not in _PENDING_STATES:
            raise DurableExecutionStateError() from None
        if new_status not in _PENDING_EDGES[expected_status]:
            raise DurableExecutionStateError() from None
        command = None if encoded_command is None else _fresh_encoded_command(encoded_command)
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            row = await self._one(
                connection,
                """
                SELECT command_bytes, command_type_id, command_type_version,
                       command_digest, command_size_bytes, pause_context_bytes
                FROM durable_execution.pending_commands
                WHERE run_id = %s AND pause_generation = %s AND status = %s
                FOR UPDATE
                """,
                (run_id, pause_generation, expected_status),
            )
            if row is None:
                raise DurableExecutionStateError() from None
            command_bytes = (
                None
                if new_status not in {"accepted", "dispatch_started"}
                else row["command_bytes"] if command is None else command.canonical_bytes
            )
            command_values = (
                row["command_type_id"],
                row["command_type_version"],
                row["command_digest"],
                row["command_size_bytes"],
            )
            if command is not None:
                command_values = (command.type_id, command.type_version, command.digest, command.size_bytes)
            await connection.execute(
                """
                UPDATE durable_execution.pending_commands
                SET status = %s,
                    command_bytes = CASE WHEN %s IN ('accepted', 'dispatch_started') THEN %s::bytea ELSE NULL END,
                    command_type_id = %s, command_type_version = %s,
                    command_digest = %s, command_size_bytes = %s,
                    pause_context_bytes = CASE WHEN %s IN ('checkpointed', 'cleared') THEN NULL ELSE pause_context_bytes END,
                    accepted_at = CASE WHEN %s = 'accepted' THEN clock_timestamp() ELSE accepted_at END,
                    dispatch_started_at = CASE WHEN %s = 'dispatch_started' THEN clock_timestamp() ELSE dispatch_started_at END,
                    checkpointed_at = CASE WHEN %s = 'checkpointed' THEN clock_timestamp() WHEN %s = 'cleared' THEN NULL ELSE checkpointed_at END,
                    cleared_at = CASE WHEN %s = 'cleared' THEN clock_timestamp() ELSE cleared_at END
                WHERE run_id = %s AND pause_generation = %s AND status = %s
                """,
                (
                    new_status,
                    new_status,
                    command_bytes,
                    *command_values,
                    new_status,
                    new_status,
                    new_status,
                    new_status,
                    new_status,
                    new_status,
                    run_id,
                    pause_generation,
                    expected_status,
                ),
            )

    async def set_root_checkpoint_pointer(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
        checkpoint_id: str,
        checkpoint_digest: str,
        bootstrap_checkpointed: bool = True,
    ) -> None:
        pointer = _normalize_pointer(("", checkpoint_id, checkpoint_digest))
        assert pointer is not None
        async with self._transaction(run_id=run_id) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            await self._set_root_checkpoint_pointer_locked(
                connection,
                run_id=run_id,
                checkpoint_id=pointer[1],
                checkpoint_digest=pointer[2],
                bootstrap_checkpointed=bootstrap_checkpointed,
            )

    async def _set_root_checkpoint_pointer_locked(
        self,
        connection,
        *,
        run_id: UUID,
        checkpoint_id: str,
        checkpoint_digest: str,
        bootstrap_checkpointed: bool = True,
    ) -> None:
        pointer = _normalize_pointer(("", checkpoint_id, checkpoint_digest))
        assert pointer is not None
        result = await connection.execute(
            """
            UPDATE durable_execution.runs
            SET latest_root_checkpoint_ns = %s,
                latest_root_checkpoint_id = %s,
                latest_root_checkpoint_digest = %s,
                bootstrap_state = CASE WHEN %s THEN 'bootstrap_checkpointed' ELSE bootstrap_state END
            WHERE run_id = %s AND lifecycle = 'open'
            """,
            (pointer[0], pointer[1], pointer[2], bootstrap_checkpointed, run_id),
        )
        if getattr(result, "rowcount", 1) != 1:
            raise DurableExecutionStateError() from None

    async def close_run(
        self,
        *,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
        pointer: tuple[str, str, str],
    ) -> None:
        normalized = _normalize_pointer(pointer)
        if normalized is None:
            raise DurableExecutionStateError() from None
        async with self._transaction(run_id=run_id, capacity_lock=True) as connection:
            await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)
            row = await self._one(
                connection,
                "SELECT lifecycle FROM durable_execution.runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            )
            if row is None:
                raise DurableExecutionRunNotFoundError() from None
            if row["lifecycle"] != "open":
                return
            await connection.execute(
                """
                UPDATE durable_execution.runs
                SET lifecycle = 'closed', finalized_at = clock_timestamp(),
                    latest_root_checkpoint_ns = %s,
                    latest_root_checkpoint_id = %s,
                    latest_root_checkpoint_digest = %s
                WHERE run_id = %s AND lifecycle = 'open'
                """,
                (*normalized, run_id),
            )
            await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = NULL, expires_at = NULL, renewed_at = NULL
                WHERE run_id = %s AND lease_owner IS NOT NULL
                """,
                (run_id,),
            )
            result = await connection.execute(
                """
                UPDATE durable_execution.capacity_state
                SET active_run_count = active_run_count - 1
                WHERE singleton_id = 1 AND active_run_count > 0
                """,
            )
            if getattr(result, "rowcount", 1) != 1:
                raise DurableExecutionConfigurationError() from None

    async def repair_dispatch_checkpoint(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        expected_stored_pointer: tuple[str, str, str] | None,
        pointer: tuple[str, str, str],
        journal_repairs: Sequence[Mapping[str, object]],
    ) -> None:
        """无 lease 地原子修复已越过 dispatch node 的 checkpoint crash-gap。"""

        old_pointer = _normalize_pointer(expected_stored_pointer)
        normalized = _normalize_pointer(pointer)
        if (
            type(run_id) is not UUID
            or type(pause_generation) is not int
            or isinstance(pause_generation, bool)
            or pause_generation < 0
            or (expected_stored_pointer is not None and old_pointer is None)
            or normalized is None
            or normalized[0] != ""
            or not isinstance(journal_repairs, Sequence)
            or isinstance(journal_repairs, (str, bytes))
        ):
            raise DurableExecutionUsageError() from None
        async with self._transaction(run_id=run_id) as connection:
            run = await self._one(
                connection,
                """
                SELECT lifecycle, latest_root_checkpoint_ns,
                       latest_root_checkpoint_id, latest_root_checkpoint_digest
                FROM durable_execution.runs WHERE run_id = %s FOR UPDATE
                """,
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            if run["lifecycle"] != "open":
                raise DurableExecutionStateError() from None
            stored = (
                None
                if run["latest_root_checkpoint_id"] is None
                else (
                    str(run["latest_root_checkpoint_ns"]),
                    str(run["latest_root_checkpoint_id"]),
                    str(run["latest_root_checkpoint_digest"]),
                )
            )
            if stored != old_pointer:
                raise DurableExecutionRunBusyError() from None
            lease = await self._one(
                connection,
                """
                SELECT (lease_owner IS NOT NULL AND expires_at > clock_timestamp()) AS active
                FROM durable_execution.run_leases WHERE run_id = %s FOR UPDATE
                """,
                (run_id,),
            )
            if lease is None:
                raise DurableExecutionDependencyError() from None
            if lease["active"]:
                raise DurableExecutionRunBusyError() from None
            pending = await self._one(
                connection,
                """
                SELECT pause_generation, status
                FROM durable_execution.pending_commands
                WHERE run_id = %s
                ORDER BY pause_generation DESC LIMIT 1 FOR UPDATE
                """,
                (run_id,),
            )
            if (
                pending is None
                or int(pending["pause_generation"]) != pause_generation
                or pending["status"] != "dispatch_started"
            ):
                raise DurableExecutionRunBusyError() from None

            for repair in journal_repairs:
                if not isinstance(repair, Mapping):
                    raise DurableExecutionUsageError() from None
                phase = repair.get("operation_phase")
                logical_step = repair.get("operation_logical_step")
                node_name = repair.get("node_name")
                expected_state = repair.get("expected_state")
                transitions = repair.get("transitions")
                if (
                    type(phase) is not str
                    or type(logical_step) is not str
                    or type(node_name) is not str
                    or expected_state not in {
                        "execution_permitted", "artifact_confirmed", "budget_rejected", "checkpoint_confirmed",
                    }
                    or not isinstance(transitions, Sequence)
                    or isinstance(transitions, (str, bytes))
                    or not transitions
                ):
                    raise DurableExecutionUsageError() from None
                latest = await self._one(
                    connection,
                    """
                    SELECT node_name, journal_state,
                           artifact_id, artifact_type, artifact_schema_version,
                           artifact_content_hash, artifact_created_at, artifact_size_bytes,
                           checkpoint_ns, checkpoint_id, checkpoint_digest
                    FROM durable_execution.operation_journal
                    WHERE run_id = %s AND operation_phase = %s
                      AND operation_logical_step = %s
                    ORDER BY transition_ordinal DESC LIMIT 1 FOR UPDATE
                    """,
                    (run_id, phase, logical_step),
                )
                if (
                    latest is None
                    or latest["journal_state"] != expected_state
                    or latest["node_name"] != node_name
                ):
                    raise DurableExecutionRunBusyError() from None
                if expected_state == "checkpoint_confirmed" and (
                    latest["checkpoint_ns"],
                    latest["checkpoint_id"],
                    latest["checkpoint_digest"],
                ) != normalized:
                    raise DurableExecutionRunBusyError() from None
                artifact = None
                if expected_state in {"artifact_confirmed", "checkpoint_confirmed"}:
                    if latest["artifact_id"] is None:
                        if expected_state == "artifact_confirmed":
                            raise DurableExecutionStateError() from None
                    else:
                        artifact = {
                            "artifact_id": latest["artifact_id"],
                            "artifact_type": latest["artifact_type"],
                            "artifact_schema_version": latest["artifact_schema_version"],
                            "artifact_content_hash": latest["artifact_content_hash"],
                            "artifact_created_at": latest["artifact_created_at"],
                            "artifact_size_bytes": latest["artifact_size_bytes"],
                        }
                current_state = str(expected_state)
                for transition in transitions:
                    if not isinstance(transition, Mapping):
                        raise DurableExecutionUsageError() from None
                    next_state = transition.get("journal_state")
                    transition_artifact = transition.get("artifact", artifact)
                    if next_state not in {"artifact_confirmed", "checkpoint_confirmed", "completed"}:
                        raise DurableExecutionUsageError() from None
                    if current_state in {"execution_permitted", "budget_rejected"} and next_state == "artifact_confirmed":
                        pass
                    elif current_state in {"execution_permitted", "budget_rejected", "artifact_confirmed"} and next_state == "checkpoint_confirmed":
                        pass
                    elif current_state == "checkpoint_confirmed" and next_state == "completed":
                        pass
                    else:
                        raise DurableExecutionUsageError() from None
                    inserted = await self._append_journal_in_transaction(
                        connection,
                        run_id=run_id,
                        operation_phase=phase,
                        logical_step=logical_step,
                        node_name=node_name,
                        journal_state=str(next_state),
                        artifact=transition_artifact,
                        checkpoint=normalized if next_state in {"checkpoint_confirmed", "completed"} else None,
                    )
                    if inserted:
                        await connection.execute(
                            "UPDATE durable_execution.runs SET journal_row_count = journal_row_count + 1 WHERE run_id = %s",
                            (run_id,),
                        )
                    current_state = str(next_state)

            await self._set_root_checkpoint_pointer_locked(
                connection,
                run_id=run_id,
                checkpoint_id=normalized[1],
                checkpoint_digest=normalized[2],
            )
            checkpointed = await connection.execute(
                """
                UPDATE durable_execution.pending_commands
                SET status = 'checkpointed', checkpointed_at = clock_timestamp(),
                    command_bytes = NULL
                WHERE run_id = %s AND pause_generation = %s AND status = 'dispatch_started'
                """,
                (run_id, pause_generation),
            )
            if getattr(checkpointed, "rowcount", 1) != 1:
                raise DurableExecutionRunBusyError() from None
            cleared = await connection.execute(
                """
                UPDATE durable_execution.pending_commands
                SET status = 'cleared', checkpointed_at = NULL, command_bytes = NULL,
                    pause_context_bytes = NULL, cleared_at = clock_timestamp()
                WHERE run_id = %s AND pause_generation = %s AND status = 'checkpointed'
                """,
                (run_id, pause_generation),
            )
            if getattr(cleared, "rowcount", 1) != 1:
                raise DurableExecutionRunBusyError() from None

    async def repair_terminal_close(
        self,
        *,
        run_id: UUID,
        pointer: tuple[str, str, str],
        artifact_by_operation: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
        artifact_journal_nodes: Sequence[str] = (),
    ) -> None:
        """无执行 lease 的 terminal crash-gap 修复。"""

        normalized = _normalize_pointer(pointer)
        if type(run_id) is not UUID or normalized is None:
            raise DurableExecutionUsageError() from None
        if artifact_by_operation is not None and not isinstance(artifact_by_operation, Mapping):
            raise DurableExecutionUsageError() from None
        if (
            not isinstance(artifact_journal_nodes, Sequence)
            or isinstance(artifact_journal_nodes, (str, bytes))
            or any(type(node) is not str for node in artifact_journal_nodes)
        ):
            raise DurableExecutionUsageError() from None
        supplied_artifacts = {} if artifact_by_operation is None else dict(artifact_by_operation)
        async with self._transaction(run_id=run_id, capacity_lock=True) as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (migration_advisory_key(domain="run", payload=str(run_id).encode("ascii")),),
            )
            run = await self._one(
                connection,
                "SELECT lifecycle FROM durable_execution.runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            if run["lifecycle"] == "blocked":
                raise DurableExecutionRunBlockedError() from None
            if run["lifecycle"] == "closed":
                return
            lease = await self._one(
                connection,
                """
                SELECT lease_owner, expires_at
                       , (lease_owner IS NOT NULL AND expires_at > clock_timestamp()) AS active
                FROM durable_execution.run_leases
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            if lease is None:
                raise DurableExecutionDependencyError() from None
            if bool(lease["active"]):
                raise DurableExecutionRunBusyError() from None
            pending = await self._one(
                connection,
                """
                SELECT status
                FROM durable_execution.pending_commands
                WHERE run_id = %s
                ORDER BY pause_generation DESC
                LIMIT 1
                FOR UPDATE
                """,
                (run_id,),
            )
            if pending is not None and pending["status"] == "awaiting_input":
                raise DurableExecutionStateError() from None
            latest_operations = await self._all(
                connection,
                """
                SELECT b.operation_phase, b.operation_logical_step,
                       j.node_name, j.journal_state,
                       j.artifact_id, j.artifact_type,
                       j.artifact_schema_version, j.artifact_content_hash,
                       j.artifact_created_at, j.artifact_size_bytes,
                       j.checkpoint_ns, j.checkpoint_id, j.checkpoint_digest
                FROM durable_execution.budget_operations AS b
                LEFT JOIN LATERAL (
                    SELECT node_name, journal_state,
                           artifact_id, artifact_type,
                           artifact_schema_version, artifact_content_hash,
                           artifact_created_at, artifact_size_bytes,
                           checkpoint_ns, checkpoint_id, checkpoint_digest
                    FROM durable_execution.operation_journal
                    WHERE run_id = b.run_id
                      AND operation_phase = b.operation_phase
                      AND operation_logical_step = b.operation_logical_step
                    ORDER BY transition_ordinal DESC
                    LIMIT 1
                ) AS j ON TRUE
                WHERE b.run_id = %s
                ORDER BY b.operation_phase, b.operation_logical_step
                FOR UPDATE OF b
                """,
                (run_id,),
            )
            for operation in latest_operations:
                key = (str(operation["operation_phase"]), str(operation["operation_logical_step"]))
                latest = operation["journal_state"]
                if latest is None or operation["node_name"] is None:
                    raise DurableExecutionStateError() from None
                latest = str(latest)
                if latest in {"completed"}:
                    continue
                if latest == "indeterminate":
                    raise DurableExecutionStateError() from None
                artifact = None
                stored_artifact = (
                    operation["artifact_id"], operation["artifact_type"],
                    operation["artifact_schema_version"], operation["artifact_content_hash"],
                    operation["artifact_created_at"], operation["artifact_size_bytes"],
                )
                if latest == "artifact_confirmed":
                    if stored_artifact[0] is None:
                        raise DurableExecutionStateError() from None
                    _artifact_fields({
                        "artifact_id": stored_artifact[0],
                        "artifact_type": stored_artifact[1],
                        "artifact_schema_version": stored_artifact[2],
                        "artifact_content_hash": stored_artifact[3],
                        "artifact_created_at": stored_artifact[4],
                        "artifact_size_bytes": stored_artifact[5],
                    })
                    artifact = {
                        "artifact_id": stored_artifact[0],
                        "artifact_type": stored_artifact[1],
                        "artifact_schema_version": stored_artifact[2],
                        "artifact_content_hash": stored_artifact[3],
                        "artifact_created_at": stored_artifact[4],
                        "artifact_size_bytes": stored_artifact[5],
                    }
                elif latest == "execution_permitted":
                    if str(operation["node_name"]) in set(artifact_journal_nodes):
                        candidate = supplied_artifacts.get(key)
                        if candidate is None:
                            raise DurableExecutionStateError() from None
                        fields = _artifact_fields(candidate)
                        artifact = {
                            "artifact_id": fields[0],
                            "artifact_type": fields[1],
                            "artifact_schema_version": fields[2],
                            "artifact_content_hash": fields[3],
                            "artifact_created_at": fields[4],
                            "artifact_size_bytes": fields[5],
                        }
                        inserted = await self._append_journal_in_transaction(
                            connection,
                            run_id=run_id,
                            operation_phase=key[0],
                            logical_step=key[1],
                            node_name=str(operation["node_name"]),
                            journal_state="artifact_confirmed",
                            artifact=artifact,
                        )
                        if not inserted:
                            raise DurableExecutionStateError() from None
                        await connection.execute(
                            "UPDATE durable_execution.runs SET journal_row_count = journal_row_count + 1 WHERE run_id = %s",
                            (run_id,),
                        )
                    # A non-recoverable operation deliberately carries no
                    # Artifact journal; the terminal checkpoint is its only
                    # permitted result evidence.
                elif latest not in {"budget_rejected", "checkpoint_confirmed"}:
                    raise DurableExecutionStateError() from None
                if latest == "checkpoint_confirmed":
                    artifact = (
                        {
                            "artifact_id": stored_artifact[0],
                            "artifact_type": stored_artifact[1],
                            "artifact_schema_version": stored_artifact[2],
                            "artifact_content_hash": stored_artifact[3],
                            "artifact_created_at": stored_artifact[4],
                            "artifact_size_bytes": stored_artifact[5],
                        }
                        if stored_artifact[0] is not None else None
                    )
                inserted = await self._append_journal_in_transaction(
                    connection,
                    run_id=run_id,
                    operation_phase=key[0],
                    logical_step=key[1],
                    node_name=str(operation["node_name"]),
                    journal_state="checkpoint_confirmed",
                    artifact=artifact,
                    checkpoint=normalized,
                )
                if inserted:
                    await connection.execute(
                        "UPDATE durable_execution.runs SET journal_row_count = journal_row_count + 1 WHERE run_id = %s",
                        (run_id,),
                    )
                inserted = await self._append_journal_in_transaction(
                    connection,
                    run_id=run_id,
                    operation_phase=key[0],
                    logical_step=key[1],
                    node_name=str(operation["node_name"]),
                    journal_state="completed",
                    artifact=artifact,
                    checkpoint=normalized,
                )
                if inserted:
                    await connection.execute(
                        "UPDATE durable_execution.runs SET journal_row_count = journal_row_count + 1 WHERE run_id = %s",
                        (run_id,),
                    )
            await connection.execute(
                """
                UPDATE durable_execution.pending_commands
                SET status = 'cleared', command_bytes = NULL,
                    pause_context_bytes = NULL,
                    cleared_at = clock_timestamp()
                WHERE run_id = %s
                  AND status IN ('accepted', 'dispatch_started', 'checkpointed')
                """,
                (run_id,),
            )
            result = await connection.execute(
                """
                UPDATE durable_execution.runs
                SET lifecycle = 'closed', finalized_at = clock_timestamp(),
                    latest_root_checkpoint_ns = %s,
                    latest_root_checkpoint_id = %s,
                    latest_root_checkpoint_digest = %s
                WHERE run_id = %s AND lifecycle = 'open'
                """,
                (*normalized, run_id),
            )
            if getattr(result, "rowcount", 1) != 1:
                raise DurableExecutionStateError() from None
            await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = NULL, expires_at = NULL, renewed_at = NULL
                WHERE run_id = %s
                """,
                (run_id,),
            )
            result = await connection.execute(
                """
                UPDATE durable_execution.capacity_state
                SET active_run_count = active_run_count - 1
                WHERE singleton_id = 1 AND active_run_count > 0
                """,
            )
            if getattr(result, "rowcount", 1) != 1:
                raise DurableExecutionConfigurationError() from None

    async def _capture_block_intent(
        self,
        *,
        run_id: UUID,
        owner: UUID | None,
        fencing_token: int | None,
    ) -> _BlockIntent:
        async with self._transaction(run_id=run_id, capacity_lock=True) as connection:
            run = await self._one(
                connection,
                """
                SELECT lifecycle, journal_row_count, managed_operation_count
                FROM durable_execution.runs WHERE run_id = %s
                """,
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            if owner is not None:
                await self._assert_fence(
                    connection,
                    run_id=run_id,
                    owner=owner,
                    fencing_token=fencing_token,
                )
            candidates = await self._all(
                connection,
                """
                SELECT j.operation_phase, j.operation_logical_step
                FROM durable_execution.operation_journal AS j
                JOIN durable_execution.budget_operations AS b
                  ON b.run_id = j.run_id
                 AND b.operation_phase = j.operation_phase
                 AND b.operation_logical_step = j.operation_logical_step
                WHERE j.run_id = %s AND j.journal_state = 'execution_permitted'
                  AND NOT EXISTS (
                      SELECT 1 FROM durable_execution.operation_journal AS later
                      WHERE later.run_id = j.run_id
                        AND later.operation_phase = j.operation_phase
                        AND later.operation_logical_step = j.operation_logical_step
                        AND later.transition_ordinal > j.transition_ordinal
                  )
                ORDER BY j.operation_phase, j.operation_logical_step
                """,
                (run_id,),
            )
            pending = await self._all(
                connection,
                """
                SELECT pause_generation, status, command_digest,
                       command_size_bytes, pause_context_digest,
                       pause_context_size_bytes
                FROM durable_execution.pending_commands
                WHERE run_id = %s ORDER BY pause_generation
                """,
                (run_id,),
            )
            capacity = await self._one(
                connection,
                "SELECT active_run_count FROM durable_execution.capacity_state WHERE singleton_id = 1",
            )
            if capacity is None:
                raise DurableExecutionConfigurationError() from None
            return _BlockIntent(
                run_id=run_id,
                lifecycle=str(run["lifecycle"]),
                journal_row_count=int(run["journal_row_count"]),
                managed_operation_count=int(run["managed_operation_count"]),
                active_run_count=int(capacity["active_run_count"]),
                candidate_keys=tuple(
                    (str(row["operation_phase"]), str(row["operation_logical_step"]))
                    for row in candidates
                ),
                pending_rows=tuple(
                    (
                        row["pause_generation"],
                        row["status"],
                        row["command_digest"],
                        row["command_size_bytes"],
                        row["pause_context_digest"],
                        row["pause_context_size_bytes"],
                    )
                    for row in pending
                ),
            )

    async def block_run(
        self,
        *,
        run_id: UUID,
        facts: _BlockFacts | None = None,
        reason_code: str | None = None,
        owner: UUID | None = None,
        fencing_token: int | None = None,
        recovery_guard: tuple[tuple[str, str, str] | None, int, str] | None = None,
    ) -> None:
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        if reason_code is not None:
            # 兼容旧调用面的参数形状，但不再接受调用方直接指定生命周期原因。
            raise DurableExecutionUsageError() from None
        if facts is None:
            facts = _BlockFacts()
        if type(facts) is not _BlockFacts:
            raise DurableExecutionUsageError() from None
        if (owner is None) != (fencing_token is None):
            raise DurableExecutionUsageError() from None
        if owner is not None and (
            type(owner) is not UUID
            or type(fencing_token) is not int
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise DurableExecutionUsageError() from None
        normalized_guard = None
        if recovery_guard is not None:
            if type(recovery_guard) is not tuple or len(recovery_guard) != 3:
                raise DurableExecutionUsageError() from None
            expected_pointer = _normalize_pointer(recovery_guard[0])
            pause_generation = recovery_guard[1]
            pending_status = recovery_guard[2]
            if (
                (recovery_guard[0] is not None and expected_pointer is None)
                or type(pause_generation) is not int
                or isinstance(pause_generation, bool)
                or pause_generation < 0
                or type(pending_status) is not str
                or pending_status not in {"dispatch_started", "cleared"}
            ):
                raise DurableExecutionUsageError() from None
            normalized_guard = (expected_pointer, pause_generation, pending_status)
        intent = await self._capture_block_intent(
            run_id=run_id,
            owner=owner,
            fencing_token=fencing_token,
        )
        try:
            await self._block_run_once(
                run_id=run_id,
                facts=facts,
                owner=owner,
                fencing_token=fencing_token,
                recovery_guard=normalized_guard,
                _skip_fault=False,
            )
        except _CommitUnknown:
            await self._reconcile_block_commit(
                run_id=run_id,
                facts=facts,
                owner=owner,
                fencing_token=fencing_token,
                recovery_guard=normalized_guard,
                intent=intent,
            )

    async def _block_run_once(
        self,
        *,
        run_id: UUID,
        facts: _BlockFacts,
        owner: UUID | None,
        fencing_token: int | None,
        recovery_guard: tuple[tuple[str, str, str] | None, int, str] | None,
        _skip_fault: bool,
    ) -> None:
        async with self._transaction(
            run_id=run_id,
            capacity_lock=True,
            commit_exception_point=(
                "block_retry_commit_exception" if _skip_fault else "block_commit_exception"
            ),
            commit_unknown_point=(
                "block_retry_commit_unknown" if _skip_fault else "block_commit_unknown"
            ),
            commit_unknown=True,
        ) as connection:
            run = await self._one(
                connection,
                """
                SELECT lifecycle, latest_root_checkpoint_ns,
                       latest_root_checkpoint_id, latest_root_checkpoint_digest
                FROM durable_execution.runs WHERE run_id = %s FOR UPDATE
                """,
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            if run["lifecycle"] == "blocked":
                return
            if run["lifecycle"] == "closed":
                raise DurableExecutionStateError() from None
            if recovery_guard is not None:
                expected_pointer, pause_generation, pending_status = recovery_guard
                actual_pointer = (
                    None
                    if run["latest_root_checkpoint_id"] is None
                    else (
                        str(run["latest_root_checkpoint_ns"]),
                        str(run["latest_root_checkpoint_id"]),
                        str(run["latest_root_checkpoint_digest"]),
                    )
                )
                if actual_pointer != expected_pointer:
                    raise DurableExecutionRunBusyError() from None
                pending = await self._one(
                    connection,
                    """
                    SELECT pause_generation, status
                    FROM durable_execution.pending_commands
                    WHERE run_id = %s
                    ORDER BY pause_generation DESC LIMIT 1 FOR UPDATE
                    """,
                    (run_id,),
                )
                if (
                    pending is None
                    or int(pending["pause_generation"]) != pause_generation
                    or pending["status"] != pending_status
                ):
                    raise DurableExecutionRunBusyError() from None
                lease = await self._one(
                    connection,
                    """
                    SELECT (lease_owner IS NOT NULL AND expires_at > clock_timestamp()) AS active
                    FROM durable_execution.run_leases WHERE run_id = %s FOR UPDATE
                    """,
                    (run_id,),
                )
                if lease is None:
                    raise DurableExecutionDependencyError() from None
                if lease["active"]:
                    raise DurableExecutionRunBusyError() from None
            if owner is not None:
                await self._assert_fence(connection, run_id=run_id, owner=owner, fencing_token=fencing_token)

            # 只对 typed intention 明确指向、且同时存在 operation ledger 与
            # 最后 permitted journal 的精确 OperationId 封口。不能从 run 内
            # 的“恰好一个 permitted”反推调用方意图。
            candidates = await self._all(
                connection,
                """
                SELECT j.operation_phase, j.operation_logical_step, j.node_name,
                       b.amount, b.outcome
                FROM durable_execution.operation_journal AS j
                JOIN durable_execution.budget_operations AS b
                  ON b.run_id = j.run_id
                 AND b.operation_phase = j.operation_phase
                 AND b.operation_logical_step = j.operation_logical_step
                WHERE j.run_id = %s AND j.journal_state = 'execution_permitted'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM durable_execution.operation_journal AS later
                      WHERE later.run_id = j.run_id
                        AND later.operation_phase = j.operation_phase
                        AND later.operation_logical_step = j.operation_logical_step
                        AND later.transition_ordinal > j.transition_ordinal
                  )
                ORDER BY j.operation_phase, j.operation_logical_step
                FOR UPDATE
                """,
                (run_id,),
            )
            associated = ()
            if facts.operation_id is not None:
                if facts.operation_id.run_id != run_id:
                    raise DurableExecutionUsageError() from None
                associated = tuple(
                    candidate
                    for candidate in candidates
                    if candidate["operation_phase"] == facts.operation_id.phase.value
                    and candidate["operation_logical_step"] == facts.operation_id.logical_step
                )
                if (len(candidates) != 1 or len(associated) != 1) and (
                    facts.cause is not None
                    and facts.cause.reason is _BlockedReason.OPERATION_INDETERMINATE
                    or facts.artifact_missing_after_permission
                ):
                    raise DurableExecutionStateError() from None
            if len(candidates) == 1 and len(associated) == 1:
                lease = await self._one(
                    connection,
                    """
                    SELECT lease_owner, fencing_token,
                           (lease_owner IS NOT NULL AND expires_at > clock_timestamp()) AS active
                    FROM durable_execution.run_leases
                    WHERE run_id = %s
                    FOR UPDATE
                    """,
                    (run_id,),
                )
                if lease is None:
                    raise DurableExecutionDependencyError() from None
                indeterminate_reason = facts.indeterminate_reason(lease_active=bool(lease["active"]))
                candidate = associated[0]
                inserted = await self._append_journal_in_transaction(
                    connection,
                    run_id=run_id,
                    operation_phase=str(candidate["operation_phase"]),
                    logical_step=str(candidate["operation_logical_step"]),
                    node_name=str(candidate["node_name"]),
                    journal_state="indeterminate",
                    reason_code=indeterminate_reason,
                )
                if not inserted:
                    raise DurableExecutionStateError() from None
                count_result = await connection.execute(
                    """
                    UPDATE durable_execution.runs
                    SET journal_row_count = journal_row_count + 1
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
                if getattr(count_result, "rowcount", 1) != 1:
                    raise DurableExecutionStateError() from None
            else:
                indeterminate_reason = None
            blocked_reason = facts.blocked_reason_code(
                candidate_count=len(candidates),
                indeterminate_reason=indeterminate_reason,
            )
            await connection.execute(
                """
                UPDATE durable_execution.pending_commands
                SET status = 'cleared', command_bytes = NULL,
                    pause_context_bytes = NULL, cleared_at = clock_timestamp()
                WHERE run_id = %s AND status IN ('awaiting_input', 'accepted', 'dispatch_started')
                """,
                (run_id,),
            )
            lifecycle_result = await connection.execute(
                """
                UPDATE durable_execution.runs
                SET lifecycle = 'blocked', blocked_reason_code = %s,
                    finalized_at = clock_timestamp()
                WHERE run_id = %s AND lifecycle = 'open'
                """,
                (blocked_reason, run_id),
            )
            if getattr(lifecycle_result, "rowcount", 1) != 1:
                raise DurableExecutionStateError() from None
            await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = NULL, expires_at = NULL, renewed_at = NULL
                WHERE run_id = %s
                """,
                (run_id,),
            )
            result = await connection.execute(
                """
                UPDATE durable_execution.capacity_state
                SET active_run_count = active_run_count - 1
                WHERE singleton_id = 1 AND active_run_count > 0
                """,
                (),
            )
            if getattr(result, "rowcount", 1) != 1:
                raise DurableExecutionConfigurationError() from None
            if not _skip_fault:
                await self._fault("block_before_commit")

    async def _reconcile_block_commit(
        self,
        *,
        run_id: UUID,
        facts: _BlockFacts,
        owner: UUID | None,
        fencing_token: int | None,
        recovery_guard: tuple[tuple[str, str, str] | None, int, str] | None,
        intent: _BlockIntent,
    ) -> None:
        """处理阻断事务的 commit-unknown，最多一次重写、随后最终读取。"""

        for attempt in range(2):
            status = await self._read_block_commit(
                run_id=run_id,
                facts=facts,
                owner=owner,
                fencing_token=fencing_token,
            )
            if status:
                return
            if attempt == 0:
                current_intent = await self._capture_block_intent(
                    run_id=run_id,
                    owner=owner,
                    fencing_token=fencing_token,
                )
                if current_intent != intent:
                    raise DurableExecutionStateError() from None
                try:
                    await self._block_run_once(
                        run_id=run_id,
                        facts=facts,
                        owner=owner,
                        fencing_token=fencing_token,
                        recovery_guard=recovery_guard,
                        _skip_fault=True,
                    )
                except _CommitUnknown:
                    # 第二次写入即使再次处于未知提交，也只能进入最终读。
                    pass
                continue
        raise DurableExecutionStateError() from None

    async def _read_block_commit(
        self,
        *,
        run_id: UUID,
        facts: _BlockFacts,
        owner: UUID | None,
        fencing_token: int | None,
    ) -> bool:
        async with self._transaction(run_id=run_id, capacity_lock=True) as connection:
            run = await self._one(
                connection,
                """
                SELECT lifecycle, blocked_reason_code, journal_row_count,
                       managed_operation_count
                FROM durable_execution.runs
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            if run is None:
                raise DurableExecutionRunNotFoundError() from None
            if run["lifecycle"] != "blocked":
                return False
            journal_rows = await self._all(
                connection,
                """
                SELECT operation_phase, operation_logical_step, node_name,
                       journal_state, reason_code, transition_ordinal
                FROM durable_execution.operation_journal
                WHERE run_id = %s
                ORDER BY operation_phase, operation_logical_step, transition_ordinal
                """,
                (run_id,),
            )
            if int(run["journal_row_count"]) != len(journal_rows):
                raise DurableExecutionStateError() from None
            candidate_keys = {
                (str(row["operation_phase"]), str(row["operation_logical_step"]))
                for row in await self._all(
                    connection,
                    """
                    SELECT j.operation_phase, j.operation_logical_step
                    FROM durable_execution.operation_journal AS j
                    JOIN durable_execution.budget_operations AS b
                      ON b.run_id = j.run_id
                     AND b.operation_phase = j.operation_phase
                     AND b.operation_logical_step = j.operation_logical_step
                    WHERE j.run_id = %s AND j.journal_state = 'execution_permitted'
                    """,
                    (run_id,),
                )
            }
            candidates = tuple(
                row
                for row in journal_rows
                if row["journal_state"] == "execution_permitted"
                and (str(row["operation_phase"]), str(row["operation_logical_step"])) in candidate_keys
                and not any(
                    later["operation_phase"] == row["operation_phase"]
                    and later["operation_logical_step"] == row["operation_logical_step"]
                    and int(later["transition_ordinal"]) > int(row["transition_ordinal"])
                    and later["journal_state"] != "indeterminate"
                    for later in journal_rows
                )
            )
            indeterminate = tuple(row for row in journal_rows if row["journal_state"] == "indeterminate")
            associated = ()
            if facts.operation_id is not None:
                if facts.operation_id.run_id != run_id:
                    raise DurableExecutionUsageError() from None
                associated = tuple(
                    candidate
                    for candidate in candidates
                    if facts.operation_id.phase.value == candidate["operation_phase"]
                    and facts.operation_id.logical_step == candidate["operation_logical_step"]
                )
                if (len(candidates) != 1 or len(associated) != 1) and (
                    facts.cause is not None
                    and facts.cause.reason is _BlockedReason.OPERATION_INDETERMINATE
                    or facts.artifact_missing_after_permission
                ):
                    raise DurableExecutionStateError() from None
            expected_indeterminate = None
            if len(candidates) == 1 and len(associated) == 1:
                if len(indeterminate) != 1:
                    raise DurableExecutionStateError() from None
                expected_from_facts = facts.indeterminate_reason(lease_active=True)
                allowed_reasons = {expected_from_facts, "lease_lost_after_permission"}
                expected_indeterminate = str(indeterminate[0]["reason_code"])
                if expected_indeterminate not in allowed_reasons:
                    raise DurableExecutionStateError() from None
                candidate = candidates[0]
                if any(
                    row["operation_phase"] != candidate["operation_phase"]
                    or row["operation_logical_step"] != candidate["operation_logical_step"]
                    or row["node_name"] != candidate["node_name"]
                    or row["reason_code"] != expected_indeterminate
                    or int(row["transition_ordinal"]) != int(candidate["transition_ordinal"]) + 1
                    for row in indeterminate
                ):
                    raise DurableExecutionStateError() from None
            elif indeterminate:
                raise DurableExecutionStateError() from None
            expected_reason = facts.blocked_reason_code(
                candidate_count=len(candidates),
                indeterminate_reason=expected_indeterminate,
            )
            if run["blocked_reason_code"] != expected_reason:
                raise DurableExecutionStateError() from None
            pending = await self._one(
                connection,
                """
                SELECT count(*) AS open_count
                FROM durable_execution.pending_commands
                WHERE run_id = %s AND status IN ('awaiting_input', 'accepted', 'dispatch_started')
                """,
                (run_id,),
            )
            if pending is None or int(pending["open_count"]) != 0:
                raise DurableExecutionStateError() from None
            lease = await self._one(
                connection,
                """
                SELECT count(*) AS active_count
                FROM durable_execution.run_leases
                WHERE run_id = %s AND (lease_owner IS NOT NULL OR expires_at IS NOT NULL OR renewed_at IS NOT NULL)
                """,
                (run_id,),
            )
            if lease is None or int(lease["active_count"]) != 0:
                raise DurableExecutionStateError() from None
            operation_count = await self._one(
                connection,
                """
                SELECT count(*) AS value
                FROM durable_execution.budget_operations
                WHERE run_id = %s
                """,
                (run_id,),
            )
            if operation_count is None or int(run["managed_operation_count"]) != int(operation_count["value"]):
                raise DurableExecutionStateError() from None
            capacity = await self._one(
                connection,
                """
                SELECT c.active_run_count,
                       (SELECT count(*) FROM durable_execution.runs WHERE lifecycle = 'open') AS open_count
                FROM durable_execution.capacity_state AS c
                WHERE c.singleton_id = 1
                """,
            )
            if capacity is None or int(capacity["active_run_count"]) != int(capacity["open_count"]):
                raise DurableExecutionStateError() from None
            return True


__all__ = [
    "SCHEMA_NAME",
    "EXPECTED_REVISION",
    "EXPECTED_BUSINESS_TABLES",
    "EXPECTED_SAVER_TABLES",
    "EXPECTED_TABLES",
    "EXPECTED_SAVER_MIGRATION_VERSIONS",
    "EXPECTED_EXPLICIT_INDEXES",
    "migration_advisory_key",
    "check_durable_execution_schema",
]
