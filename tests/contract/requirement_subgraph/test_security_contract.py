from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from pydantic import ValidationError
import app.requirement_subgraph as package_module
import app.requirement_subgraph.coordinator as coordinator_module
import app.requirement_subgraph.graph as graph_module
import app.requirement_subgraph.checkpoint as checkpoint_module
import app.requirement_subgraph.runtime as runtime_module
import app.requirement_subgraph.errors as errors_module
import app.requirement_subgraph.models as models_module
import app.requirement_subgraph.signals as signals_module

from app.requirement_subgraph import (
    RequirementClarificationInterrupt,
    RequirementClarificationResume,
    RequirementPauseEnvelope,
    RequirementResumeEnvelope,
    StartAccepted,
)
from tests.requirement_subgraph_fakes import make_initial_state, make_turn


def test_repr_and_json_do_not_leak_pause_token_or_resume_payload():
    state = make_initial_state()
    token = "a" * 32
    turn = make_turn(state, user_message="user_secret_message")
    envelope = RequirementResumeEnvelope(
        pause_token=token,
        resume=RequirementClarificationResume(turn=turn),
    )
    assert token not in repr(envelope)
    assert "user_secret_message" not in repr(envelope)
    assert token in envelope.model_dump_json()
    assert "user_secret_message" in envelope.model_dump_json()


def test_signal_does_not_have_pause_secret_shape():
    signal = StartAccepted(run_id=uuid4(), trace_id=uuid4())
    text = signal.model_dump_json()
    assert "token" not in text.lower()
    assert "nonce" not in text.lower()
    assert "user_message" not in text


def test_no_unsafe_import_or_constructor_fallback_in_production_modules():
    for module in (
        package_module,
        models_module,
        signals_module,
        errors_module,
        coordinator_module,
        graph_module,
        checkpoint_module,
        runtime_module,
    ):
        source = inspect.getsource(module)
        assert "model_construct" not in source
        assert "__import__" not in source
        assert "importlib" not in source.lower()
        assert "pickle.loads" not in source.lower()
        assert "pickle.dumps" not in source.lower()


def test_pause_envelope_rejects_non_hex_token():
    with pytest.raises(ValidationError):
        RequirementPauseEnvelope(
            pause_token="not-a-token",
            interrupt=RequirementClarificationInterrupt(message="safe"),
        )
