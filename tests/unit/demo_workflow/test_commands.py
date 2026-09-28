from uuid import uuid4

import pytest

from app.demo_workflow import (
    DemoStartCommand, DemoRequirementCommand, DemoHotelCommand,
    DemoCommandCodec, DemoWorkflowUsageError, DemoWorkflowConfigurationError,
)
from app.requirements import RequirementConfirmation
from app.requirement_subgraph import RequirementConfirmationResume
from app.hotel_selection import HotelSelectCommand
from app.workflow_contracts import WorkflowPhase
from tests.requirement_subgraph_fakes import make_initial_state, make_turn
from tests.contract.candidate_research.test_artifact_registration import _ref


def test_codec_start_and_resume_are_fresh_and_canonical():
    state = make_initial_state()
    codec = DemoCommandCodec(max_payload_bytes=1048576)
    command = DemoStartCommand(schema_version="1.0", kind="start", turn=make_turn(state))
    ids = dict(run_id=state.run_id, trace_id=state.trace_id)
    encoded = codec.encode_initial(**ids, command=command)
    assert codec.decode_initial(**ids, encoded=encoded) == command
    assert codec.initial_state(**ids, decoded_command=command) == state
    turn = codec.requirement_turn(**ids, decoded_command=command)
    assert turn == command.turn and turn is not command.turn
    turn.current.requirement.destination = "changed"
    assert command.turn.current.requirement.destination is None
    assert codec.descriptor == DemoCommandCodec(max_payload_bytes=1).descriptor

    requirement = DemoRequirementCommand(schema_version="1.0", kind="requirement", map_requested=True,
        resume=RequirementConfirmationResume(action=RequirementConfirmation(decision="accept", revision_text=None)))
    hotel = DemoHotelCommand(schema_version="1.0", kind="hotel",
        candidate_set_ref=_ref(state.run_id, "hotel.candidates", WorkflowPhase.HOTEL_SELECTION),
        action=HotelSelectCommand(selected_uid="private hotel uid"))
    for node, value in (("requirements", requirement), ("__root__", hotel)):
        resume_ids = dict(run_id=state.run_id, pause_generation=1, node_name=node)
        data = codec.encode_resume(**resume_ids, command=value)
        assert codec.decode_resume(**resume_ids, encoded=data) == value
        assert "private hotel uid" not in repr(value)
        if node == "requirements":
            assert codec.requirement_resume(**resume_ids, decoded_command=value) == value.resume


@pytest.mark.parametrize("limit", [True, 0, -1, 1048577, "32"])
def test_codec_limits_are_explicit(limit):
    with pytest.raises(DemoWorkflowConfigurationError):
        DemoCommandCodec(max_payload_bytes=limit)


@pytest.mark.parametrize("node,generation", [("hotel_wait", 1), ("requirements", 0), ("requirements", True)])
def test_resume_scope_rejected(node, generation):
    with pytest.raises(DemoWorkflowUsageError):
        DemoCommandCodec(max_payload_bytes=1024).encode_resume(
            run_id=uuid4(), pause_generation=generation, node_name=node, command=object())


def test_corrupt_command_envelope_and_payload_limit_are_rejected():
    state = make_initial_state()
    command = DemoStartCommand(schema_version="1.0", kind="start", turn=make_turn(state))
    ids = dict(run_id=state.run_id, trace_id=state.trace_id)
    codec = DemoCommandCodec(max_payload_bytes=1048576)
    encoded = codec.encode_initial(**ids, command=command)
    for change in ({"type_version": "2.0"}, {"command_kind": "resume"}, {"digest": "sha256:" + "0" * 64},
                   {"canonical_bytes": b'{"schema_version":"0.0"}'}, {"canonical_bytes": encoded.canonical_bytes + b" "}):
        with pytest.raises(DemoWorkflowUsageError):
            codec.decode_initial(**ids, encoded=encoded.model_copy(update=change))
    with pytest.raises(DemoWorkflowUsageError):
        DemoCommandCodec(max_payload_bytes=1).encode_initial(**ids, command=command)
