from __future__ import annotations

import json
from decimal import Decimal
from ipaddress import IPv4Address

import pytest
from langgraph.types import Interrupt, PregelTask, StateSnapshot, TimeoutPolicy

from app.requirement_subgraph import RequirementCheckpointSerializer
from app.requirement_subgraph.errors import RequirementSubgraphStateError
from tests.requirement_subgraph_fakes import make_initial_state


def test_delegate_allowlists_are_exact_and_pickle_fallback_is_disabled():
    serializer = RequirementCheckpointSerializer()
    assert serializer._delegate.pickle_fallback is False
    assert serializer._delegate._allowed_json_modules == {
        ("app", "workflow_contracts", "identifiers", "WorkflowPhase"),
        ("app", "workflow_contracts", "state", "DeliveryStatus"),
    }
    assert serializer._delegate._allowed_msgpack_modules == {
        ("app.workflow_contracts.identifiers", "WorkflowPhase"),
        ("app.workflow_contracts.state", "DeliveryStatus"),
    }


def test_checkpoint_bytes_contain_only_state_and_safe_interrupt_tree():
    serializer = RequirementCheckpointSerializer()
    state = make_initial_state()
    token = "a" * 32
    nonce = "b" * 32
    values = (
        serializer.dumps_typed(state),
        serializer.dumps_typed(
            (
                Interrupt(
                    value={
                        "schema_version": "1.0",
                        "interrupt_type": "clarification",
                        "message": "安全展示",
                        "sentinel": "requirement_resume_v1",
                    },
                    id="c" * 32,
                ),
            )
        ),
    )
    blob = b"".join(payload for _, payload in values)
    assert b"user_message" not in blob
    assert b"revision_text" not in blob
    assert token.encode() not in blob
    assert nonce.encode() not in blob
    assert b"requirement_resume_v1" in blob
    assert b"\xef\xbb\xbf" not in blob


@pytest.mark.parametrize(
    "value",
    [
        {"decimal": Decimal("1.1")},
        {"ip": IPv4Address("127.0.0.1")},
        {"set": {1}},
        {"pregel": PregelTask(id="a", name="n", path=("p",))},
        {"snapshot": StateSnapshot(values={}, next=(), config={}, metadata={}, created_at=None, parent_config=None, tasks=(), interrupts=())},
        {"timeout": TimeoutPolicy(run_timeout=1)},
    ],
)
def test_named_langgraph_and_safe_set_types_are_not_widened(value):
    with pytest.raises(RequirementSubgraphStateError):
        RequirementCheckpointSerializer().dumps_typed(value)


@pytest.mark.parametrize(
    ("identifier", "enum_value"),
    [
        (
            ["app", "workflow_contracts", "identifiers", "WorkflowPhase"],
            "requirements",
        ),
        (
            ["app", "workflow_contracts", "state", "DeliveryStatus"],
            "in_progress",
        ),
    ],
)
def test_json_allowlist_constructor_positive_and_semantic_round_trip(
    identifier,
    enum_value,
):
    serializer = RequirementCheckpointSerializer()
    payload = json.dumps(
        {
            "lc": 2,
            "type": "constructor",
            "id": identifier,
            "args": [enum_value],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    from app.workflow_contracts import WorkflowPhase
    from app.workflow_contracts import DeliveryStatus

    expected = (
        WorkflowPhase.REQUIREMENTS
        if enum_value == "requirements"
        else DeliveryStatus.IN_PROGRESS
    )
    restored = serializer.loads_typed(("json", payload))
    assert type(restored) is type(expected)
    assert restored is expected
    tag, redumped = serializer._delegate.dumps_typed(restored)
    assert tag == "msgpack"
    assert serializer._delegate.loads_typed((tag, redumped)) is expected


@pytest.mark.parametrize(
    "payload",
    [
        b' {"a":1}',
        b"\xef\xbb\xbf{}",
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        json.dumps(
            {
                "lc": 2,
                "type": "constructor",
                "id": ["app", "not_allowed", "WorkflowPhase"],
                "args": ["requirements"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "lc": 2,
                "type": "constructor",
                "id": ["app", "workflow_contracts", "identifiers", "WorkflowPhase"],
                "args": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "lc": 2,
                "type": "constructor",
                "id": ["app", "workflow_contracts", "identifiers", "WorkflowPhase"],
                "kwargs": {"value": "requirements"},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "lc": 2,
                "type": "constructor",
                "id": ["app", "workflow_contracts", "identifiers", "WorkflowPhase"],
                "args": ["not-a-phase"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ],
)
def test_json_constructor_and_noncanonical_payloads_fail_closed(payload):
    with pytest.raises(RequirementSubgraphStateError):
        RequirementCheckpointSerializer().loads_typed(("json", payload))


def test_owned_tag_bytes_must_be_canonical_and_round_trip_fresh():
    serializer = RequirementCheckpointSerializer()
    state = make_initial_state()
    tag, payload = serializer.dumps_typed(state)
    noncanonical = b" {" + payload[1:]
    with pytest.raises(RequirementSubgraphStateError):
        serializer.loads_typed((tag, noncanonical))
    restored = serializer.loads_typed((tag, payload))
    assert type(restored) is type(state)
    assert restored is not state
