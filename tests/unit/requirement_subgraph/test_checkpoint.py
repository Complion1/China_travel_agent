from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Interrupt, Overwrite, Send

from app.requirement_subgraph import RequirementCheckpointSerializer
from app.requirement_subgraph.errors import RequirementSubgraphStateError
from app.requirement_subgraph.models import (
    RequirementClarificationInterrupt,
    _fresh_model,
)
from app.requirements import NeedClarification
from app.workflow_contracts import (
    ArtifactRef,
    DeliveryStatus,
    OperationId,
    TerminalOutcome,
    TravelSessionState,
    WorkflowPhase,
)
from app.workflow_contracts.state import TerminalCategory
from tests.requirement_subgraph_fakes import make_initial_state


def _artifact_ref(state: TravelSessionState) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid4(),
        artifact_type="test.item",
        schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(
            run_id=state.run_id,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="artifact_write",
        ),
        created_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        size_bytes=1,
    )


def _terminal_outcome(state: TravelSessionState) -> TerminalOutcome:
    return TerminalOutcome(
        schema_version="1.0",
        terminal_code="input_invalid",
        category=TerminalCategory.INPUT,
        recoverable=True,
        user_action_code="retry_input",
        trace_id=state.trace_id,
    )


def test_six_owned_tags_are_canonical_and_type_exact():
    state = make_initial_state()
    domain_state = state.requirement_state
    domain_outcome = NeedClarification(
        requirement=domain_state.requirement,
        metadata=domain_state.metadata,
        issues=(),
        message="请补充",
    )
    serializer = RequirementCheckpointSerializer()
    values = (
        state,
        domain_state,
        domain_outcome,
        _terminal_outcome(state),
        (_artifact_ref(state),),
        (Interrupt(value={"message": "safe"}, id="a" * 32),),
    )
    tags = [serializer.dumps_typed(value) for value in values]
    assert [tag for tag, _ in tags] == [
        "requirement_subgraph.travel_session_state.2.0",
        "requirement_subgraph.requirement_domain_state.1.0",
        "requirement_subgraph.requirement_domain_outcome.1.0",
        "requirement_subgraph.terminal_outcome.1.0",
        "requirement_subgraph.artifact_ref_tuple.1.0",
        "requirement_subgraph.interrupt_tuple.1.0",
    ]
    assert all(type(serializer.loads_typed(item)) is type(value) for item, value in zip(tags, values))
    assert type(serializer.loads_typed(tags[-1])) is tuple
    assert serializer.loads_typed(tags[-1]) == values[-1]


@pytest.mark.parametrize("value_index", range(6))
def test_owned_dumps_self_validates_through_wrapper_loads(monkeypatch, value_index):
    state = make_initial_state()
    values = (
        state,
        state.requirement_state,
        NeedClarification(
            requirement=state.requirement_state.requirement,
            metadata=state.requirement_state.metadata,
            issues=(),
            message="请补充",
        ),
        _terminal_outcome(state),
        (_artifact_ref(state),),
        (Interrupt(value={"message": "safe"}, id="a" * 32),),
    )
    serializer = RequirementCheckpointSerializer()
    calls = []
    original_loads = RequirementCheckpointSerializer.loads_typed

    def traced_loads(self, data):
        calls.append(data)
        return original_loads(self, data)

    monkeypatch.setattr(RequirementCheckpointSerializer, "loads_typed", traced_loads)
    tag, payload = serializer.dumps_typed(values[value_index])

    assert calls == [(tag, payload)]


def test_owned_dumps_preserves_zero_argument_load_failure(monkeypatch):
    state = make_initial_state()
    serializer = RequirementCheckpointSerializer()

    def fail_loads(_self, _data):
        raise RequirementSubgraphStateError()

    monkeypatch.setattr(RequirementCheckpointSerializer, "loads_typed", fail_loads)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        serializer.dumps_typed(state)
    assert caught.value.args == ()


@pytest.mark.parametrize("failure", ["type", "equality"])
def test_owned_dumps_rejects_wrapper_type_or_equality_failure(monkeypatch, failure):
    state = make_initial_state()
    serializer = RequirementCheckpointSerializer()
    returned = (
        None
        if failure == "type"
        else state.model_copy(update={"trace_id": uuid4()})
    )

    def wrong_loads(_self, _data):
        return returned

    monkeypatch.setattr(RequirementCheckpointSerializer, "loads_typed", wrong_loads)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        serializer.dumps_typed(state)
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        [Interrupt(value=None, id="a" * 32)],
        (Interrupt(value=None, id="not-hex"),),
        (Interrupt(value=(1, 2), id="a" * 32),),
        {"model": RequirementClarificationInterrupt(message="x")},
        {"decimal": Decimal("1.0")},
        {"path": Path("/tmp/x")},
        {"set": {1, 2}},
    ],
)
def test_unsafe_or_lossy_values_fail_before_delegate(value):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        RequirementCheckpointSerializer().dumps_typed(value)
    assert caught.value.args == ()
    assert str(caught.value) == "RequirementSubgraphStateError"


@pytest.mark.parametrize(
    "data",
    [
        ("requirement_subgraph.unknown.1.0", b"{}"),
        ("requirement_subgraph.travel_session_state.9.0", b"{}"),
        ("pickle", b"not-pickle"),
        ("yaml", b"{}"),
        ("json", b"\xef\xbb\xbf{}"),
        ("json", b'{"a":1,"a":2}'),
        ("json", b"{\"a\":NaN}"),
    ],
)
def test_unknown_tags_pickle_and_invalid_json_are_rejected(data):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        RequirementCheckpointSerializer().loads_typed(data)
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "data",
    [
        ("json", b' {"a":1}'),
    ],
)
def test_delegate_json_rejects_noncanonical_payload(data):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        RequirementCheckpointSerializer().loads_typed(data)
    assert caught.value.args == ()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (("json", b"null"), None),
        (("json", b'{"a":1}'), {"a": 1}),
        (("msgpack", bytes.fromhex("cc01")), 1),
    ],
)
def test_delegate_loads_uses_semantic_round_trip(data, expected):
    serializer = RequirementCheckpointSerializer()
    restored = serializer.loads_typed(data)
    assert type(restored) is type(expected)
    assert restored == expected
    tag, payload = serializer._delegate.dumps_typed(restored)
    second = serializer._delegate.loads_typed((tag, payload))
    assert type(second) is type(restored)
    assert second == restored


def test_delegate_outputs_round_trip_with_identical_tag_and_payload():
    serializer = RequirementCheckpointSerializer()
    for value in (None, b"bytes", bytearray(b"bytearray"), {"a": 1}):
        tag, payload = serializer.dumps_typed(value)
        assert serializer.loads_typed((tag, payload)) == value
        assert serializer._delegate.dumps_typed(value) == (tag, payload)


def test_delegate_closed_type_tree_accepts_only_the_documented_controls():
    serializer = RequirementCheckpointSerializer()
    values = (
        None,
        b"bytes",
        bytearray(b"bytearray"),
        [None, True, 1, 1.5, "text"],
        {"phase": WorkflowPhase.REQUIREMENTS, "status": DeliveryStatus.IN_PROGRESS},
        uuid4(),
        datetime(2026, 8, 17, tzinfo=timezone.utc),
        date(2026, 8, 17),
        time(12, 30),
        timedelta(seconds=1),
        timezone.utc,
        ZoneInfo("Asia/Shanghai"),
        Interrupt(value={"message": "safe"}, id="b" * 32),
        Command(update={"ok": True}, goto="next"),
        Send("next", {"ok": True}),
        Overwrite(value=[1, 2]),
    )
    for value in values:
        tag, payload = serializer.dumps_typed(value)
        assert tag in {"null", "bytes", "bytearray", "msgpack"}
        restored = serializer.loads_typed((tag, payload))
        assert type(restored) is type(value)
        assert restored == value


def test_default_saver_tuple_distortion_remains_a_negative_control():
    saver = InMemorySaver()
    tag, payload = saver.serde.dumps_typed((1, 2))
    assert tag == "msgpack"
    assert saver.serde.loads_typed((tag, payload)) == [1, 2]
    assert RequirementCheckpointSerializer().loads_typed(
        RequirementCheckpointSerializer().dumps_typed(())[0:2]
    ) == ()
