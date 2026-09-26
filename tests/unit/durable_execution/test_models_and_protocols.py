from __future__ import annotations

import hashlib
import inspect
import json
from uuid import UUID, uuid4
from typing import get_type_hints

import pytest
from pydantic import TypeAdapter, ValidationError

from app.requirement_subgraph import (
    NoOpRequirementSubgraphSignalSink,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    create_requirement_subgraph_embedded_driver,
)
from app.requirement_subgraph.errors import RequirementSubgraphInputError
from app.durable_execution.models import (
    DEMO_DURABLE_EXECUTION_LIMITS,
    DurableExecutionCommandCodecDescriptor,
    DurableExecutionContract,
    DurableExecutionEncodedCommand,
    DurableExecutionLimits,
    DurableExecutionNodeRecoveryRule,
    DurableExecutionPauseEnvelope,
    DurableExecutionPaused,
    DurableExecutionTerminal,
    NodeRecoveryClass,
    contract_canonical_bytes,
)
from app.durable_execution.errors import DurableExecutionUsageError
from app.durable_execution.protocols import (
    DurableExecutionBudgetPlanProvider,
    DurableExecutionCommandCodec,
    DurableExecutionGraphCapabilities,
    DurableExecutionNodeCapabilities,
)
from app.requirements import NeedClarification, RequirementTurnInput
from app.workflow_contracts import (
    BudgetConsumeRequest,
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    InMemoryExecutionBudgetManager,
    OperationId,
    WorkflowPhase,
)
from tests.support.durable_execution_fakes import (
    FakeCommandCodec,
    FakeGraphCapabilities,
    FakeArtifactRepository,
    RUN_ID,
    TRACE_ID,
    fake_limits,
    make_contract,
)
from tests.requirement_subgraph_fakes import (
    ScriptedRequirementDomainService,
    make_initial_state,
)


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _repacked(
    encoded: DurableExecutionEncodedCommand,
    *,
    canonical_bytes: bytes | None = None,
    command_kind: str | None = None,
    type_id: str | None = None,
    type_version: str | None = None,
) -> DurableExecutionEncodedCommand:
    data = encoded.canonical_bytes if canonical_bytes is None else canonical_bytes
    return DurableExecutionEncodedCommand(
        command_kind=command_kind or encoded.command_kind,
        type_id=type_id or encoded.type_id,
        type_version=type_version or encoded.type_version,
        canonical_bytes=data,
        digest=_digest(data),
        size_bytes=len(data),
    )


def test_contract_canonical_bytes_are_stable_without_normalization() -> None:
    contract = make_contract()
    first = contract_canonical_bytes(contract)
    expected = json.dumps(
        contract.model_dump(
            mode="json",
            exclude={"contract_digest"},
            exclude_none=False,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert first == expected
    assert first == first.strip()
    assert not first.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in first
    assert "城市".encode("utf-8") in first

    reordered = {
        "contract_digest": contract.contract_digest,
        "node_rules": contract.node_rules,
        "budgeted_phases": contract.budgeted_phases,
        "command_codec": contract.command_codec,
        "contract_id": contract.contract_id,
        "schema_version": contract.schema_version,
    }
    assert contract_canonical_bytes(DurableExecutionContract.model_validate(reordered, strict=True)) == first


def test_contract_rejects_digest_order_phase_and_node_violations() -> None:
    contract = make_contract()
    with pytest.raises(ValidationError):
        DurableExecutionContract.model_validate(
            {**contract.model_dump(mode="python"), "contract_digest": _digest(b"wrong")},
            strict=True,
        )
    with pytest.raises(ValidationError):
        DurableExecutionContract(
            contract_id=contract.contract_id,
            command_codec=contract.command_codec,
            budgeted_phases=(WorkflowPhase.HOTEL_SELECTION,),
            node_rules=contract.node_rules,
            contract_digest=contract.contract_digest,
        )
    with pytest.raises(ValidationError):
        DurableExecutionContract(
            contract_id=contract.contract_id,
            command_codec=contract.command_codec,
            budgeted_phases=(WorkflowPhase.REQUIREMENTS, WorkflowPhase.REQUIREMENTS),
            node_rules=contract.node_rules,
            contract_digest=contract.contract_digest,
        )
    with pytest.raises(ValidationError):
        DurableExecutionNodeRecoveryRule(
            node_name="bad|namespace",
            phase=WorkflowPhase.REQUIREMENTS,
            recovery_class=NodeRecoveryClass.REPLAY_SAFE,
        )


def test_nested_public_dtos_are_fresh_exact_copies() -> None:
    source = make_contract()
    rebuilt = DurableExecutionContract(
        contract_id=source.contract_id,
        command_codec=source.command_codec,
        budgeted_phases=source.budgeted_phases,
        node_rules=source.node_rules,
        contract_digest=source.contract_digest,
    )
    assert rebuilt.command_codec is not source.command_codec
    assert rebuilt.node_rules is not source.node_rules
    assert all(left is not right for left, right in zip(rebuilt.node_rules, source.node_rules, strict=True))

    state = make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID)
    outcome = NeedClarification(
        requirement=state.requirement_state.requirement,
        metadata=state.requirement_state.metadata,
        issues=(),
        message="需要补充",
    )
    waiting = state.model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_outcome": outcome,
        }
    )
    envelope = DurableExecutionPauseEnvelope(pause_token="a" * 32)
    paused = DurableExecutionPaused(
        state=waiting,
        pause=envelope,
    )
    assert paused.state is not waiting
    assert paused.pause is not envelope


def test_nested_dto_subclasses_are_rejected_instead_of_reused() -> None:
    source = make_contract()

    class DescriptorChild(DurableExecutionCommandCodecDescriptor):
        pass

    child = DescriptorChild.model_validate(source.command_codec.model_dump(mode="python"), strict=True)
    with pytest.raises(ValidationError):
        DurableExecutionContract(
            contract_id=source.contract_id,
            command_codec=child,
            budgeted_phases=source.budgeted_phases,
            node_rules=source.node_rules,
            contract_digest=source.contract_digest,
        )


def test_dto_contracts_are_strict_frozen_and_extra_forbidden() -> None:
    descriptor = DurableExecutionCommandCodecDescriptor(
        codec_id="test.codec",
        codec_version="1.0",
        codec_digest="sha256:" + "0" * 64,
    )
    assert descriptor.schema_version == "1.0"
    with pytest.raises(ValidationError):
        DurableExecutionCommandCodecDescriptor(
            codec_id="test.codec",
            codec_version="1.0",
            codec_digest="sha256:" + "0" * 64,
            extra="no",
        )
    with pytest.raises(ValidationError):
        DurableExecutionNodeRecoveryRule(
            node_name="requirements\n",
            phase=WorkflowPhase.REQUIREMENTS,
            recovery_class=NodeRecoveryClass.REPLAY_SAFE,
        )
    with pytest.raises(ValidationError):
        descriptor.codec_id = "other"


def test_public_dto_field_order_and_annotations_are_frozen() -> None:
    expected = {
        DurableExecutionCommandCodecDescriptor: ("schema_version", "codec_id", "codec_version", "codec_digest"),
        DurableExecutionNodeRecoveryRule: ("schema_version", "node_name", "phase", "recovery_class"),
        DurableExecutionContract: ("schema_version", "contract_id", "command_codec", "budgeted_phases", "node_rules", "contract_digest"),
        DurableExecutionEncodedCommand: ("schema_version", "command_kind", "type_id", "type_version", "canonical_bytes", "digest", "size_bytes"),
        DurableExecutionPauseEnvelope: ("schema_version", "pause_token"),
        DurableExecutionPaused: ("schema_version", "outcome_type", "state", "pause"),
        DurableExecutionTerminal: ("schema_version", "outcome_type", "state"),
    }
    for model_type, fields in expected.items():
        assert tuple(model_type.model_fields) == fields
        assert model_type.model_config["strict"] is True
        assert model_type.model_config["frozen"] is True
        assert model_type.model_config["extra"] == "forbid"
        assert set(get_type_hints(model_type)) >= set(fields)


def test_encoded_command_digest_size_and_one_mib_hard_boundary() -> None:
    payload = b"x" * 1_048_576
    encoded = DurableExecutionEncodedCommand(
        command_kind="initial",
        type_id="test.command",
        type_version="1.0",
        canonical_bytes=payload,
        digest=_digest(payload),
        size_bytes=len(payload),
    )
    assert encoded.size_bytes == len(encoded.canonical_bytes)
    with pytest.raises(ValidationError):
        DurableExecutionEncodedCommand(
            command_kind="initial",
            type_id="test.command",
            type_version="1.0",
            canonical_bytes=payload + b"x",
            digest=_digest(payload + b"x"),
            size_bytes=len(payload) + 1,
        )
    with pytest.raises(ValidationError):
        DurableExecutionEncodedCommand(
            command_kind="initial",
            type_id="test.command",
            type_version="1.0",
            canonical_bytes=b"payload",
            digest=_digest(b"other"),
            size_bytes=7,
        )


def test_codec_registry_round_trip_is_byte_exact_and_binds_node_generation() -> None:
    codec = FakeCommandCodec()
    initial = codec.encode_initial(run_id=RUN_ID, trace_id=TRACE_ID, command="初始")
    assert codec.decode_initial(run_id=RUN_ID, trace_id=TRACE_ID, encoded=initial) == "初始"
    assert codec.encode_initial(run_id=RUN_ID, trace_id=TRACE_ID, command="初始").canonical_bytes == initial.canonical_bytes

    payload = json.loads(initial.canonical_bytes.decode("utf-8"))
    reordered = json.dumps(
        dict(reversed(tuple(payload.items()))),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    spaced = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    for variant in (reordered, spaced):
        with pytest.raises(ValueError):
            codec.decode_initial(
                run_id=RUN_ID,
                trace_id=TRACE_ID,
                encoded=_repacked(initial, canonical_bytes=variant),
            )
    for variant in (
        _repacked(initial, type_id="other.command"),
        _repacked(initial, type_version="2.0"),
        _repacked(initial, command_kind="resume"),
    ):
        with pytest.raises(ValueError):
            codec.decode_initial(run_id=RUN_ID, trace_id=TRACE_ID, encoded=variant)

    resumed = codec.encode_resume(
        run_id=RUN_ID,
        pause_generation=1,
        node_name="requirements",
        command="继续",
    )
    assert codec.decode_resume(
        run_id=RUN_ID,
        pause_generation=1,
        node_name="requirements",
        encoded=resumed,
    ) == "继续"
    with pytest.raises((TypeError, ValueError)):
        codec.encode_resume(run_id=RUN_ID, pause_generation=0, node_name="requirements", command="继续")
    with pytest.raises(ValueError):
        codec.decode_initial(run_id=RUN_ID, trace_id=UUID("33333333-3333-4333-8333-333333333333"), encoded=initial)
    with pytest.raises(ValueError):
        codec.decode_resume(
            run_id=RUN_ID,
            pause_generation=2,
            node_name="requirements",
            encoded=resumed,
        )
    with pytest.raises(ValueError):
        codec.decode_resume(
            run_id=RUN_ID,
            pause_generation=1,
            node_name="requirements/unknown",
            encoded=resumed,
        )
    with pytest.raises((TypeError, ValueError)):
        codec.encode_resume(
            run_id=RUN_ID,
            pause_generation=1,
            node_name="requirements/unknown",
            command="继续",
        )

    first_turn = codec.requirement_turn(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        decoded_command="初始",
    )
    second_turn = codec.requirement_turn(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        decoded_command="初始",
    )
    assert type(first_turn) is RequirementTurnInput
    assert first_turn is not second_turn
    first_resume = codec.requirement_resume(
        run_id=RUN_ID,
        pause_generation=1,
        node_name="requirements",
        decoded_command="继续",
    )
    second_resume = codec.requirement_resume(
        run_id=RUN_ID,
        pause_generation=1,
        node_name="requirements",
        decoded_command="继续",
    )
    assert first_resume is not second_resume
    assert first_resume.turn is not second_resume.turn
    assert first_turn.current is not second_turn.current
    assert first_resume.turn.current is not second_resume.turn.current


@pytest.mark.asyncio
async def test_budget_plan_provider_and_capabilities_are_executable_and_run_bound() -> None:
    from tests.support.durable_execution_fakes import FakeBudgetPlanProvider

    provider = FakeBudgetPlanProvider()
    start_plan = provider.plan_for_start(run_id=RUN_ID)
    assert start_plan.run_id == RUN_ID
    assert tuple(limit.key.phase for limit in start_plan.limits) == (WorkflowPhase.REQUIREMENTS,)
    assert start_plan.limits
    assert provider.plan_for_start(run_id=RUN_ID) is not start_plan

    state = make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID)
    with pytest.raises(DurableExecutionUsageError):
        provider.plan_for_phase(run_id=RUN_ID, phase=WorkflowPhase.REQUIREMENTS, state=state)
    with pytest.raises(DurableExecutionUsageError):
        provider.plan_for_phase(run_id=RUN_ID, phase=WorkflowPhase.CANDIDATE_RESEARCH, state=state)
    with pytest.raises(DurableExecutionUsageError):
        provider.plan_for_phase(run_id=uuid4(), phase=WorkflowPhase.HOTEL_SELECTION, state=state)
    phase_plan = provider.plan_for_phase(
        run_id=RUN_ID,
        phase=WorkflowPhase.HOTEL_SELECTION,
        state=state,
    )
    assert phase_plan.limits[0].key.phase is WorkflowPhase.HOTEL_SELECTION
    assert provider.start_calls == [RUN_ID, RUN_ID]
    assert provider.phase_calls == [(RUN_ID, WorkflowPhase.HOTEL_SELECTION, RUN_ID)]

    def manager_factory(run_id: UUID) -> InMemoryExecutionBudgetManager:
        plan = ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name="requirements.clarification_round",
                        phase=WorkflowPhase.REQUIREMENTS,
                    ),
                    limit=6,
                ),
            ),
        )
        return InMemoryExecutionBudgetManager(
            plan=plan,
            limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
        )

    capabilities = FakeGraphCapabilities(
        artifact_repository=FakeArtifactRepository(),
        manager_factory=manager_factory,
        limits=RequirementSubgraphCoordinatorLimits(
            max_runs=fake_limits().max_stored_runs
        ),
        durable_limits=fake_limits(),
    )
    node = capabilities.for_node(node_name="requirements")
    with pytest.raises(DurableExecutionUsageError):
        node.current_command()
    with pytest.raises(DurableExecutionUsageError):
        _ = node.artifact_repository
    for invalid_node in ("__start__", "unknown"):
        with pytest.raises(DurableExecutionUsageError):
            capabilities.for_node(node_name=invalid_node)
    manager_for_run = capabilities.execution_budget_manager_factory(
        node_name="requirements"
    )(RUN_ID)
    assert capabilities.execution_budget_manager_factory(node_name="requirements")(RUN_ID) is manager_for_run
    other_run = uuid4()
    manager_for_other_run = capabilities.execution_budget_manager_factory(
        node_name="requirements"
    )(other_run)
    assert manager_for_other_run is not manager_for_run
    manager_for_other_node = capabilities.execution_budget_manager_factory(
        node_name="requirements/城市"
    )(RUN_ID)
    assert manager_for_other_node is not manager_for_run
    budget_key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="requirements.clarification_round",
        phase=WorkflowPhase.REQUIREMENTS,
    )
    request = BudgetConsumeRequest(
        operation_id=OperationId(
            run_id=RUN_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="clarification_round",
        ),
        key=budget_key,
        amount=1,
    )

    async def assert_manager_rejects(
        manager: object,
        rejected_request: BudgetConsumeRequest,
        rejected_key: ExecutionBudgetKey,
    ) -> None:
        with pytest.raises(DurableExecutionUsageError) as consume_error:
            await manager.consume(rejected_request)
        with pytest.raises(DurableExecutionUsageError) as snapshot_error:
            await manager.get_snapshot(key=rejected_key)
        with pytest.raises(DurableExecutionUsageError) as all_snapshots_error:
            await manager.get_all_snapshots()
        for error in (
            consume_error.value,
            snapshot_error.value,
            all_snapshots_error.value,
        ):
            assert error.args == ()
            assert error.__cause__ is None

    await assert_manager_rejects(manager_for_run, request, budget_key)
    with capabilities.task_context(
        node_name="requirements",
        run_id=RUN_ID,
        command="继续",
    ) as active_node:
        assert active_node.current_command() == "继续"
        assert active_node.execution_budget_manager is manager_for_run
        assert active_node.artifact_repository is not None
        assert (await manager_for_run.consume(request)).snapshot.consumed == 1
        assert (await manager_for_run.get_snapshot(key=budget_key)).consumed == 1
        assert await manager_for_run.get_all_snapshots()
        await active_node.ensure_phase_budget(state=state)
        with pytest.raises(DurableExecutionUsageError):
            await active_node.ensure_phase_budget(
                state=state.model_copy(update={"run_id": other_run})
            )
        other_request = request.model_copy(
            update={
                "operation_id": request.operation_id.model_copy(
                    update={"run_id": other_run}
                )
            }
        )
        await assert_manager_rejects(
            manager_for_other_run,
            other_request,
            budget_key,
        )

    await assert_manager_rejects(manager_for_run, request, budget_key)
    with capabilities.task_context(
        node_name="requirements/城市",
        run_id=RUN_ID,
        command="城市",
    ):
        await assert_manager_rejects(manager_for_run, request, budget_key)
        assert (await manager_for_other_node.consume(request)).snapshot.consumed == 1
        assert (await manager_for_other_node.get_snapshot(key=budget_key)).consumed == 1
        assert await manager_for_other_node.get_all_snapshots()

    with capabilities.task_context(
        node_name="requirements",
        run_id=RUN_ID,
        command="再次继续",
    ) as active_node:
        assert active_node.execution_budget_manager is manager_for_run
        assert (await manager_for_run.consume(request)).disposition == "replayed"
        assert (await manager_for_run.get_snapshot(key=budget_key)).consumed == 1
        assert await manager_for_run.get_all_snapshots()

    await assert_manager_rejects(manager_for_run, request, budget_key)
    with pytest.raises(DurableExecutionUsageError):
        node.current_command()
    with pytest.raises(DurableExecutionUsageError):
        _ = node.execution_budget_manager
    with pytest.raises(DurableExecutionUsageError):
        _ = node.artifact_repository
    with pytest.raises(DurableExecutionUsageError):
        await node.ensure_phase_budget(state=state)


@pytest.mark.asyncio
async def test_real_module05_embedded_driver_rejects_before_domain_side_effect() -> None:
    def manager_factory(run_id: UUID) -> InMemoryExecutionBudgetManager:
        plan = ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name="requirements.clarification_round",
                        phase=WorkflowPhase.REQUIREMENTS,
                    ),
                    limit=6,
                ),
            ),
        )
        return InMemoryExecutionBudgetManager(
            plan=plan,
            limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
        )

    service = ScriptedRequirementDomainService()
    driver = create_requirement_subgraph_embedded_driver(
        domain_service=service,
        execution_budget_manager_factory=manager_factory,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    capabilities = FakeGraphCapabilities(
        artifact_repository=FakeArtifactRepository(),
        manager_factory=manager_factory,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    capabilities.register_requirement_subgraph(
        node_name="requirements",
        driver=driver,
    )
    assert capabilities.registered_drivers["requirements"] is driver
    with pytest.raises(DurableExecutionUsageError):
        capabilities.register_requirement_subgraph(
            node_name="requirements",
            driver=driver,
        )
    with pytest.raises(DurableExecutionUsageError):
        capabilities.register_requirement_subgraph(
            node_name="requirements",
            driver=object(),
        )
    raw_coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=NoOpRequirementSubgraphSignalSink(),
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    with pytest.raises(DurableExecutionUsageError):
        FakeGraphCapabilities(
            artifact_repository=FakeArtifactRepository(),
            manager_factory=manager_factory,
            limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
        ).register_requirement_subgraph(
            node_name="requirements",
            driver=raw_coordinator,
        )
    mismatched_driver = create_requirement_subgraph_embedded_driver(
        domain_service=service,
        execution_budget_manager_factory=manager_factory,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=3),
    )
    with pytest.raises(DurableExecutionUsageError):
        FakeGraphCapabilities(
            artifact_repository=FakeArtifactRepository(),
            manager_factory=manager_factory,
            limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
        ).register_requirement_subgraph(
            node_name="requirements",
            driver=mismatched_driver,
        )
    state = make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID)
    with pytest.raises(RequirementSubgraphInputError):
        async with driver.start_operation(state=state, turn=object()):
            pass
    assert service.process_calls == []


def test_limits_keep_hard_product_and_demo_values() -> None:
    assert DEMO_DURABLE_EXECUTION_LIMITS.max_journal_rows_per_run == 16_384
    with pytest.raises(ValidationError):
        DurableExecutionLimits(
            max_active_runs=4,
            max_stored_runs=4,
            max_managed_operations_per_run=1,
            max_journal_transitions_per_operation=3,
            max_journal_rows_per_run=3,
            max_payload_bytes=1024,
            max_lineage_checkpoints=256,
            recursion_limit=256,
            lease_duration_seconds=60,
            lease_renew_interval_seconds=15,
        )


def test_protocol_method_signatures_are_keyword_only_after_self() -> None:
    expected = {
        "encode_initial": ("self", "run_id", "trace_id", "command"),
        "encode_resume": ("self", "run_id", "pause_generation", "node_name", "command"),
        "decode_initial": ("self", "run_id", "trace_id", "encoded"),
        "decode_resume": ("self", "run_id", "pause_generation", "node_name", "encoded"),
        "initial_state": ("self", "run_id", "trace_id", "decoded_command"),
        "requirement_turn": ("self", "run_id", "trace_id", "decoded_command"),
        "requirement_resume": ("self", "run_id", "pause_generation", "node_name", "decoded_command"),
    }
    for name, parameters in expected.items():
        signature = inspect.signature(getattr(DurableExecutionCommandCodec, name))
        assert tuple(signature.parameters) == parameters
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in list(signature.parameters.values())[1:]
        )
    assert get_type_hints(DurableExecutionCommandCodec.requirement_turn)["return"] is object
    assert get_type_hints(DurableExecutionNodeCapabilities.current_command)["return"] is object
    assert get_type_hints(DurableExecutionGraphCapabilities.for_node)["return"].__name__ == "DurableExecutionNodeCapabilities"


def test_all_protocol_surfaces_have_exact_keyword_only_signatures() -> None:
    expected = {
        DurableExecutionBudgetPlanProvider: {
            "plan_for_start": ("self", "run_id"),
            "plan_for_phase": ("self", "run_id", "phase", "state"),
        },
        DurableExecutionNodeCapabilities: {
            "current_command": ("self",),
            "ensure_phase_budget": ("self", "state"),
        },
        DurableExecutionGraphCapabilities: {
            "for_node": ("self", "node_name"),
            "execution_budget_manager_factory": ("self", "node_name"),
            "register_requirement_subgraph": ("self", "node_name", "driver"),
        },
    }
    for protocol, methods in expected.items():
        for method_name, parameters in methods.items():
            signature = inspect.signature(getattr(protocol, method_name))
            assert tuple(signature.parameters) == parameters
            assert all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in list(signature.parameters.values())[1:]
            )


def test_pause_envelope_token_is_not_in_repr() -> None:
    envelope = DurableExecutionPauseEnvelope(pause_token="a" * 32)
    assert "a" * 32 not in repr(envelope)
    assert TypeAdapter(DurableExecutionPauseEnvelope).validate_python(
        envelope.model_dump(mode="python"), strict=True
    ) == envelope
