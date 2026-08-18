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
import app.requirement_subgraph.checkpoint as checkpoint_module
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


@pytest.mark.parametrize(
    "value",
    [object(), float("nan"), float("inf")],
    ids=["unserializable", "nan", "infinity"],
)
def test_serializer_private_json_and_tree_guards_fail_closed(value):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._canonical_json(value)
    assert caught.value.args == ()

    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._validate_json_value(value)
    assert caught.value.args == ()


def test_json_value_validator_rejects_non_string_mapping_key():
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._validate_json_value({1: "non-string key"})
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "data",
    [bytearray(b"{}"), b"\xef\xbb\xbf{}", b"not-json", b"\xff"],
    ids=["bytearray", "bom", "invalid-json", "invalid-utf8"],
)
def test_serializer_json_tree_parser_rejects_noncanonical_inputs(data):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._json_tree_from_bytes(data)
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "value",
    [
        checkpoint_module._INVALID_NUMBER,
        checkpoint_module._ParsedObject([("a", checkpoint_module._INVALID_NUMBER)]),
        checkpoint_module._ParsedObject([("a", 1), ("a", 2)]),
        [checkpoint_module._INVALID_NUMBER],
        {"a": 1},
    ],
    ids=["invalid-number", "nested-invalid-number", "duplicate", "list", "valid"],
)
def test_invalid_json_tree_detects_numbers_and_duplicate_keys(value):
    expected = value is checkpoint_module._INVALID_NUMBER or (
        isinstance(value, checkpoint_module._ParsedObject)
        and (bool(value.duplicates) or any(checkpoint_module._invalid_json_tree(item) for item in value.values()))
    ) or (type(value) is list and any(checkpoint_module._invalid_json_tree(item) for item in value))
    assert checkpoint_module._invalid_json_tree(value) is expected


def test_serializer_fresh_tagged_requires_model_and_maps_fresh_validation_error(monkeypatch):
    state = make_initial_state()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._fresh_tagged(TravelSessionState, object())
    assert caught.value.args == ()

    def fail_fresh(_model_type, _value):
        raise ValueError("invalid model")

    monkeypatch.setattr(checkpoint_module, "_fresh_model", fail_fresh)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._fresh_tagged(TravelSessionState, state)
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "value",
    [
        (object(),),
        (ArtifactRef,),
    ],
    ids=["invalid-artifact", "wrong-artifact-type"],
)
def test_artifact_tuple_json_rejects_non_exact_artifacts(value):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._artifact_tuple_json(value)  # type: ignore[arg-type]
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        Interrupt(value=None, id=1),  # type: ignore[arg-type]
        Overwrite(value=1),
        Send("node", {"ok": True}, timeout=1),
        Command(graph=1),  # type: ignore[arg-type]
        Command(goto=(1,)),  # type: ignore[arg-type]
        Command(goto=[1]),  # type: ignore[arg-type]
        Command(goto=1),  # type: ignore[arg-type]
        {"lc": 2, "type": "constructor"},
        {"bad": object()},
        (1, 2),
    ],
    ids=[
        "non-finite",
        "interrupt-id",
        "overwrite-type-default-is-valid",
        "send-timeout",
        "command-graph",
        "command-tuple-item",
        "command-list-item",
        "command-goto",
        "constructor-dict",
        "unsupported-dict-value",
        "unsupported-tuple",
    ],
)
def test_serializer_tree_validator_rejects_unsafe_control_shapes(value):
    if isinstance(value, Overwrite):
        object.__setattr__(value, "type", "not-overwrite")
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._validate_tree(value)
    assert caught.value.args == ()


def test_serializer_tree_validator_accepts_nested_documented_controls():
    value = [
        None,
        True,
        1,
        1.5,
        "text",
        uuid4(),
        datetime(2026, 8, 17, tzinfo=timezone.utc),
        date(2026, 8, 17),
        time(12, 30),
        timedelta(seconds=1),
        timezone.utc,
        ZoneInfo("Asia/Shanghai"),
        WorkflowPhase.REQUIREMENTS,
        DeliveryStatus.IN_PROGRESS,
        Interrupt(value={"ok": [1, "two"]}, id="a" * 32),
        Overwrite(value=[1, 2]),
        Send("node", {"ok": True}),
        Command(graph="graph", update={"ok": True}, resume="resume", goto=["next", Send("other", 1)]),
    ]
    checkpoint_module._validate_tree(value)


@pytest.mark.parametrize(
    "value",
    [
        object(),
        [Interrupt(value=None, id="bad")],
        (),
        [1],
        [ArtifactRef],
    ],
    ids=["not-tuple", "interrupt-list", "empty", "non-interrupt", "non-artifact"],
)
def test_serializer_exact_tuple_guards_distinguish_invalid_shapes(value):
    assert checkpoint_module._require_exact_interrupt_tuple(value) is None
    if value != ():
        assert checkpoint_module._require_exact_artifact_tuple(value) is None


def test_serializer_exact_interrupt_tuple_rejects_bad_identifier():
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._require_exact_interrupt_tuple(
            (Interrupt(value=None, id="bad"),)
        )
    assert caught.value.args == ()


def test_serializer_exact_interrupt_tuple_validates_id_and_json_value():
    valid = (Interrupt(value={"message": [1, True]}, id="a" * 32),)
    assert checkpoint_module._require_exact_interrupt_tuple(valid) == valid
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._require_exact_interrupt_tuple(
            (Interrupt(value=float("nan"), id="a" * 32),)
        )
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._require_exact_interrupt_tuple(
            (Interrupt(value=None, id="bad"),)
        )
    assert caught.value.args == ()


@pytest.mark.parametrize(
    "value",
    [float("nan"), [float("inf")], {1: "bad"}, object()],
    ids=["non-finite", "nested", "non-string-key", "unsupported"],
)
def test_json_value_validator_rejects_nested_unsafe_values(value):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._validate_json_value(value)
    assert caught.value.args == ()


def test_plain_json_tree_recursively_removes_parser_wrapper():
    tree = checkpoint_module._ParsedObject(
        [("outer", checkpoint_module._ParsedObject([("items", [1, 2])]))]
    )
    assert checkpoint_module._plain_json_tree(tree) == {"outer": {"items": [1, 2]}}


@pytest.mark.parametrize(
    "tree",
    [
        checkpoint_module._ParsedObject([]),
        checkpoint_module._ParsedObject([("lc", 1), ("type", "constructor"), ("id", []), ("args", ["x"])]),
        checkpoint_module._ParsedObject([("lc", 2), ("type", "wrong"), ("id", []), ("args", ["x"])]),
        checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["app"]), ("args", ["x"])]),
        checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["app", "x", "y", 1]), ("args", ["x"])]),
        checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["app", "x", "y", "z"]), ("args", [])]),
        checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["app", "x", "y", "z"]), ("args", [1])]),
        checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["app", "x", "y", "z"]), ("args", ["x"])]),
    ],
    ids=["wrong-fields", "wrong-lc", "wrong-type", "id-length", "id-item", "args-length", "args-item", "unknown-id"],
)
def test_json_constructor_validator_rejects_each_structural_shape(tree):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._validate_json_constructor(tree)
    assert caught.value.args == ()


def test_json_tree_validator_recurses_through_objects_and_lists():
    checkpoint_module._validate_json_tree(
        checkpoint_module._ParsedObject(
            [("nested", [checkpoint_module._ParsedObject([("value", 1)])])]
        )
    )
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._validate_json_tree(
            checkpoint_module._ParsedObject(
                [("lc", 2), ("type", "constructor"), ("id", ["bad"]), ("args", ["x"])]
            )
        )
    assert caught.value.args == ()


@pytest.mark.parametrize(
    ("tree", "restored"),
    [
        (checkpoint_module._ParsedObject([("a", 1)]), {"b": 1}),
        (checkpoint_module._ParsedObject([("a", 1)]), [1]),
        ([1, 2], [1]),
        ([1], [2]),
        (1, True),
        (checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["bad"]), ("args", ["x"])]), object()),
        (checkpoint_module._ParsedObject([("lc", 2), ("type", "constructor"), ("id", ["app", "workflow_contracts", "identifiers", "WorkflowPhase"]), ("args", ["bad"])]), WorkflowPhase.REQUIREMENTS),
    ],
    ids=["dict-keys", "dict-type", "list-length", "list-value", "scalar-type", "unknown-constructor", "constructor-value"],
)
def test_json_tree_matches_rejects_shape_or_semantic_mismatch(tree, restored):
    assert checkpoint_module._json_tree_matches(tree, restored) is False


def test_json_tree_matches_accepts_allowlisted_enum_constructors():
    phase_tree = checkpoint_module._ParsedObject(
        [
            ("lc", 2),
            ("type", "constructor"),
            ("id", ["app", "workflow_contracts", "identifiers", "WorkflowPhase"]),
            ("args", ["requirements"]),
        ]
    )
    status_tree = checkpoint_module._ParsedObject(
        [
            ("lc", 2),
            ("type", "constructor"),
            ("id", ["app", "workflow_contracts", "state", "DeliveryStatus"]),
            ("args", ["in_progress"]),
        ]
    )
    assert checkpoint_module._json_tree_matches(phase_tree, WorkflowPhase.REQUIREMENTS)
    assert checkpoint_module._json_tree_matches(status_tree, DeliveryStatus.IN_PROGRESS)


class _RoundTripDelegate:
    def __init__(self, dump_result=("json", b"null"), load_result=None, error=None):
        self.dump_result = dump_result
        self.load_result = load_result
        self.error = error

    def dumps_typed(self, _value):
        if self.error is not None:
            raise self.error
        return self.dump_result

    def loads_typed(self, _data):
        if self.error is not None:
            raise self.error
        return self.load_result


@pytest.mark.parametrize(
    "delegate",
    [
        _RoundTripDelegate(dump_result=("unsafe", b"x"), load_result=None),
        _RoundTripDelegate(dump_result=("json", b"null"), load_result=1),
        _RoundTripDelegate(dump_result=("json", b"null"), load_result=None, error=ValueError("delegate")),
    ],
    ids=["unknown-tag", "wrong-type", "delegate-error"],
)
def test_delegate_round_trip_rejects_untrusted_delegate_behavior(delegate):
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._delegate_round_trip(delegate, None)  # type: ignore[arg-type]
    assert caught.value.args == ()


def test_delegate_round_trip_rejects_unsafe_restored_value():
    delegate = _RoundTripDelegate(dump_result=("json", b"null"), load_result=object())
    with pytest.raises(RequirementSubgraphStateError) as caught:
        checkpoint_module._delegate_round_trip(delegate, object())  # type: ignore[arg-type]
    assert caught.value.args == ()


def test_interrupt_payload_and_loader_enforce_exact_canonical_tuple():
    valid = (Interrupt(value={"message": "safe"}, id="a" * 32),)
    encoded = checkpoint_module._interrupt_payload(valid)
    assert checkpoint_module._load_interrupt_tuple(encoded) == valid
    for invalid in (
        (Interrupt(value=None, id="bad"),),
        (Interrupt(value=float("nan"), id="a" * 32),),
    ):
        with pytest.raises(RequirementSubgraphStateError) as caught:
            checkpoint_module._interrupt_payload(invalid)
        assert caught.value.args == ()

    malformed = [
        b"null",
        b"[]",
        b"[1]",
        b'[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]',
        b'[{"id":1,"value":null}]',
        b'[{"id":"bad","value":null}]',
        b'[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","value":NaN}]',
        b'[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","value":null}] ',
    ]
    for payload in malformed:
        with pytest.raises(RequirementSubgraphStateError) as caught:
            checkpoint_module._load_interrupt_tuple(payload)
        assert caught.value.args == ()


def test_serializer_delegate_and_load_argument_contracts_are_zero_argument_errors(monkeypatch):
    serializer = RequirementCheckpointSerializer()
    for data in (
        None,
        ("json",),
        ("json", b"{}", b"extra"),
        (1, b"{}"),
        ("json", bytearray(b"{}")),
        ("requirement_subgraph.travel_session_state.2.0", bytearray(b"{}")),
    ):
        with pytest.raises(RequirementSubgraphStateError) as caught:
            serializer.loads_typed(data)  # type: ignore[arg-type]
        assert caught.value.args == ()

    class ExplodingDelegate:
        def dumps_typed(self, _value):
            raise ValueError("dump")

        def loads_typed(self, _data):
            raise ValueError("load")

    serializer._delegate = ExplodingDelegate()  # type: ignore[assignment]
    with pytest.raises(RequirementSubgraphStateError) as caught:
        serializer.dumps_typed(None)
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        serializer.loads_typed(("msgpack", b"x"))
    assert caught.value.args == ()


def test_serializer_json_delegate_round_trip_rejects_semantic_tree_mismatch(monkeypatch):
    serializer = RequirementCheckpointSerializer()
    original_loads = serializer._delegate.loads_typed

    def wrong_loads(data):
        if data[0] == "json":
            return (1, 2)
        return original_loads(data)

    monkeypatch.setattr(serializer._delegate, "loads_typed", wrong_loads)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        serializer.loads_typed(("json", b"[1,2]"))
    assert caught.value.args == ()
