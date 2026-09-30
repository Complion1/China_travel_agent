import pytest
from pydantic import ValidationError

from app.demo_workflow import DemoStartCommand, DemoControlArtifact, DEMO_ARTIFACT_REGISTRATIONS, DEMO_TERMINAL_REGISTRATIONS
from app.workflow_contracts import ArtifactSchemaRegistry, TerminalCodeRegistry, ArtifactWriteRequest, OperationId, WorkflowPhase
from tests.requirement_subgraph_fakes import make_initial_state, make_turn


def control(**changes):
    values = dict(schema_version="2.0", sequence=0, previous_ref=None, hotel_invalid_count=0,
        map_requested=False, supplement_count=0, handled_gap_fingerprints=(), hotel_candidate_ref=None,
        hotel_selection_ref=None, candidate_set_ref=None, planning_ref=None, last_outcome="hotel_ready",
        terminal_code=None, supplemental_plan_call=None)
    return DemoControlArtifact(**(values | changes))


def test_control_uses_real_registry_and_required_fields():
    registry = ArtifactSchemaRegistry(DEMO_ARTIFACT_REGISTRATIONS)
    TerminalCodeRegistry(DEMO_TERMINAL_REGISTRATIONS)
    artifact = control()
    request = ArtifactWriteRequest(operation_id=OperationId(run_id=make_initial_state().run_id,
        phase=WorkflowPhase.HOTEL_SELECTION, logical_step="control_00"), artifact_type="demo.control",
        schema_version="2.0", content=artifact.model_dump(mode="json"))
    assert registry.prepare(request).canonical_bytes
    assert all(field.is_required() for field in DemoControlArtifact.model_fields.values())


@pytest.mark.parametrize("changes", [dict(sequence=True), dict(sequence=1), dict(hotel_invalid_count=4),
    dict(supplement_count=1), dict(map_requested="true"), dict(last_outcome="failed"),
    dict(last_outcome="hotel_declined"), dict(terminal_code="workflow_completed"), dict(extra=1)])
def test_receipt_rejects_invalid_routing(changes):
    with pytest.raises(ValidationError):
        control(**changes)


def test_start_requires_initial_unmodified_domain_state():
    state = make_initial_state()
    turn = make_turn(state)
    command = DemoStartCommand(schema_version="1.0", kind="start", turn=turn)
    assert command.turn is not turn
    turn.current.requirement.destination = "已修改"
    with pytest.raises(ValidationError):
        DemoStartCommand(schema_version="1.0", kind="start", turn=turn)
    with pytest.raises(ValidationError):
        DemoStartCommand(kind="start", turn=command.turn)
