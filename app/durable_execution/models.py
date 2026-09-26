"""Module 11 的严格执行合同、限制和公开结果 DTO。"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.requirement_subgraph import RequirementSubgraphCoordinatorLimits
from app.workflow_contracts import (
    ArtifactRef,
    ArtifactSchemaVersion,
    ArtifactRepository,
    ContentHash,
    ExecutionBudgetPlan,
    TravelSessionState,
    WorkflowPhase,
)

from app.durable_execution.errors import DurableExecutionConfigurationError


_SAFE_INTEGER_MAX = 2**53 - 1
_MAX_PAYLOAD_BYTES = 1_048_576
_PHASE_ORDER = {phase: index for index, phase in enumerate(WorkflowPhase)}
_QUALIFIED_NODE_MAX = 128
_NODE_NAME_SEPARATORS = frozenset("/|:")


def _canonical_json(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        raise ValueError("value is not canonical JSON") from None
    if encoded.startswith(b"\xef\xbb\xbf"):
        raise ValueError("invalid canonical JSON")
    return encoded


def _fresh_state(value: TravelSessionState) -> TravelSessionState:
    if type(value) is not TravelSessionState:
        raise ValueError("state must be exact TravelSessionState")
    try:
        payload = value.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        artifact_refs = _fresh_artifact_refs(payload.get("artifact_refs"))
        payload_without_refs = dict(payload)
        payload_without_refs["artifact_refs"] = []
        base_state = TravelSessionState.model_validate_json(
            _canonical_json(payload_without_refs),
            strict=True,
        )
        fresh_payload = base_state.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        fresh_payload["artifact_refs"] = artifact_refs
        return TravelSessionState.model_validate(fresh_payload, strict=True)
    except Exception:
        raise ValueError("invalid state") from None


def _fresh_artifact_refs(value: object) -> tuple[ArtifactRef, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("invalid artifact refs")
    try:
        refs = []
        for item in value:
            if type(item) is ArtifactRef:
                payload = item.model_dump(
                    mode="json",
                    exclude_none=False,
                    by_alias=False,
                    exclude_computed_fields=True,
                )
            elif isinstance(item, Mapping):
                payload = dict(item)
            else:
                raise ValueError("invalid artifact ref")
            refs.append(
                ArtifactRef.model_validate_json(
                    _canonical_json(payload),
                    strict=True,
                )
            )
        return tuple(refs)
    except Exception:
        raise ValueError("invalid artifact refs") from None


def _fresh_model(model_type: type[BaseModel], value: BaseModel) -> BaseModel:
    """通过 canonical JSON 生成精确类型的新实例，拒绝共享或子类对象。"""

    if type(value) is not model_type:
        raise ValueError("nested DTO must be exact type")
    try:
        payload = value.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        return model_type.model_validate_json(_canonical_json(payload), strict=True)
    except Exception:
        raise ValueError("invalid nested DTO") from None


def _validate_node_segment(segment: str) -> None:
    if type(segment) is not str or segment == "" or segment.strip() != segment:
        raise ValueError("invalid node name")
    if any(char in _NODE_NAME_SEPARATORS for char in segment):
        raise ValueError("invalid node separator")
    if any(unicodedata.category(char).startswith("C") for char in segment):
        raise ValueError("invalid node control character")


def _validate_qualified_node_name(value: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= _QUALIFIED_NODE_MAX:
        raise ValueError("invalid qualified node name")
    if value.strip() != value or any(char in "|:" for char in value):
        raise ValueError("invalid qualified node name")
    segments = value.split("/")
    if any(segment == "" for segment in segments):
        raise ValueError("invalid qualified node name")
    for segment in segments:
        _validate_node_segment(segment)
    return value


class NodeRecoveryClass(str, Enum):
    REPLAY_SAFE = "replay_safe"
    JOURNAL_GUARDED = "journal_guarded"
    NON_RECOVERABLE = "non_recoverable"


class DurableExecutionCommandCodecDescriptor(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    codec_id: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$"),
    ]
    codec_version: ArtifactSchemaVersion
    codec_digest: ContentHash


class DurableExecutionNodeRecoveryRule(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    node_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    phase: WorkflowPhase
    recovery_class: NodeRecoveryClass

    @model_validator(mode="after")
    def validate_node_name(self) -> "DurableExecutionNodeRecoveryRule":
        _validate_qualified_node_name(self.node_name)
        return self


class DurableExecutionContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    contract_id: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$"),
    ]
    command_codec: DurableExecutionCommandCodecDescriptor
    budgeted_phases: tuple[WorkflowPhase, ...]
    node_rules: tuple[DurableExecutionNodeRecoveryRule, ...]
    contract_digest: ContentHash

    @model_validator(mode="after")
    def validate_contract(self) -> "DurableExecutionContract":
        object.__setattr__(
            self,
            "command_codec",
            _fresh_model(
                DurableExecutionCommandCodecDescriptor,
                self.command_codec,
            ),
        )
        object.__setattr__(
            self,
            "node_rules",
            tuple(
                _fresh_model(DurableExecutionNodeRecoveryRule, rule)
                for rule in self.node_rules
            ),
        )
        phases = self.budgeted_phases
        if not phases or phases[0] is not WorkflowPhase.REQUIREMENTS:
            raise ValueError("requirements must be the first budgeted phase")
        if len(phases) != len(set(phases)):
            raise ValueError("budgeted phases must be unique")
        if tuple(sorted(phases, key=_PHASE_ORDER.__getitem__)) != phases:
            raise ValueError("budgeted phases must follow WorkflowPhase order")

        rules = self.node_rules
        names = tuple(rule.node_name for rule in rules)
        if not names or len(names) != len(set(names)):
            raise ValueError("node rules must be non-empty and unique")
        if names != tuple(sorted(names)):
            raise ValueError("node rules must be Unicode-code-point sorted")

        expected = _contract_digest(self)
        if self.contract_digest != expected:
            raise ValueError("contract digest mismatch")
        return self


class DurableExecutionEncodedCommand(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    command_kind: Literal["initial", "resume"]
    type_id: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$"),
    ]
    type_version: ArtifactSchemaVersion
    canonical_bytes: bytes = Field(
        repr=False,
        min_length=1,
        max_length=_MAX_PAYLOAD_BYTES,
    )
    digest: ContentHash
    size_bytes: int = Field(ge=1, le=_MAX_PAYLOAD_BYTES)

    @model_validator(mode="after")
    def validate_payload(self) -> "DurableExecutionEncodedCommand":
        if self.size_bytes != len(self.canonical_bytes):
            raise ValueError("encoded command size mismatch")
        digest = "sha256:" + hashlib.sha256(self.canonical_bytes).hexdigest()
        if self.digest != digest:
            raise ValueError("encoded command digest mismatch")
        return self


class DurableExecutionPauseEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    pause_token: Annotated[
        str,
        StringConstraints(pattern=r"^[0-9a-f]{32}$"),
    ] = Field(repr=False)


class DurableExecutionPaused(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["paused"] = "paused"
    state: TravelSessionState
    pause: DurableExecutionPauseEnvelope

    @model_validator(mode="after")
    def validate_paused(self) -> "DurableExecutionPaused":
        object.__setattr__(self, "state", _fresh_state(self.state))
        object.__setattr__(
            self,
            "pause",
            _fresh_model(DurableExecutionPauseEnvelope, self.pause),
        )
        if self.state.delivery_status.value != "awaiting_user":
            raise ValueError("paused result requires awaiting_user")
        return self


class DurableExecutionTerminal(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["terminal"] = "terminal"
    state: TravelSessionState

    @model_validator(mode="after")
    def validate_terminal(self) -> "DurableExecutionTerminal":
        object.__setattr__(self, "state", _fresh_state(self.state))
        if self.state.delivery_status.value in {"in_progress", "awaiting_user"}:
            raise ValueError("terminal result requires terminal delivery status")
        return self


DurableExecutionResult = Annotated[
    DurableExecutionPaused | DurableExecutionTerminal,
    Field(discriminator="outcome_type"),
]


class DurableExecutionLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    max_active_runs: int = Field(ge=1, le=4_096)
    max_stored_runs: int = Field(ge=1, le=16_384)
    max_managed_operations_per_run: int = Field(ge=1, le=4_096)
    max_journal_transitions_per_operation: int = Field(ge=1)
    max_journal_rows_per_run: int = Field(ge=1, le=16_384)
    max_payload_bytes: int = Field(ge=1, le=_MAX_PAYLOAD_BYTES)
    max_lineage_checkpoints: int = Field(ge=1, le=256)
    recursion_limit: int = Field(ge=1)
    lease_duration_seconds: int = Field(ge=1)
    lease_renew_interval_seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_limits(self) -> "DurableExecutionLimits":
        if self.max_stored_runs < self.max_active_runs:
            raise ValueError("stored capacity must cover active capacity")
        if self.max_journal_transitions_per_operation != 4:
            raise ValueError("journal transition limit is fixed")
        if self.max_journal_rows_per_run != (
            self.max_managed_operations_per_run
            * self.max_journal_transitions_per_operation
        ):
            raise ValueError("journal row limit must equal operation capacity product")
        if self.recursion_limit != 256:
            raise ValueError("recursion limit is fixed")
        if self.lease_duration_seconds != 60:
            raise ValueError("lease duration is fixed")
        if self.lease_renew_interval_seconds != 15:
            raise ValueError("lease renewal interval is fixed")
        if self.lease_renew_interval_seconds >= self.lease_duration_seconds:
            raise ValueError("lease renewal interval must be shorter than lease")
        return self


DEMO_DURABLE_EXECUTION_LIMITS = DurableExecutionLimits(
    max_active_runs=4_096,
    max_stored_runs=16_384,
    max_managed_operations_per_run=4_096,
    max_journal_transitions_per_operation=4,
    max_journal_rows_per_run=16_384,
    max_payload_bytes=_MAX_PAYLOAD_BYTES,
    max_lineage_checkpoints=256,
    recursion_limit=256,
    lease_duration_seconds=60,
    lease_renew_interval_seconds=15,
)


def _contract_digest(contract: DurableExecutionContract) -> ContentHash:
    payload = contract.model_dump(
        mode="json",
        exclude={"contract_digest"},
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    return ContentHash(
        "sha256:" + hashlib.sha256(_canonical_json(payload)).hexdigest()
    )


def contract_canonical_bytes(contract: DurableExecutionContract) -> bytes:
    """Return the cross-module contract bytes without exposing a generic helper."""

    if type(contract) is not DurableExecutionContract:
        raise DurableExecutionConfigurationError() from None
    try:
        fresh = DurableExecutionContract.model_validate(
            contract.model_dump(mode="python", exclude_none=False),
            strict=True,
        )
        payload = fresh.model_dump(
            mode="json",
            exclude={"contract_digest"},
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        return _canonical_json(payload)
    except Exception:
        raise DurableExecutionConfigurationError() from None


__all__ = [
    "NodeRecoveryClass",
    "DurableExecutionNodeRecoveryRule",
    "DurableExecutionCommandCodecDescriptor",
    "DurableExecutionContract",
    "DurableExecutionEncodedCommand",
    "DurableExecutionPauseEnvelope",
    "DurableExecutionPaused",
    "DurableExecutionTerminal",
    "DurableExecutionResult",
    "DurableExecutionLimits",
    "DEMO_DURABLE_EXECUTION_LIMITS",
]
