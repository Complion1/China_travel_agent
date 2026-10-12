import pytest
from datetime import datetime, timezone
from uuid import uuid4

from app.candidate_research import CandidateResearchConfigurationError
from app.candidate_research import prompt_loader as loader
from app.candidate_research.prompting import build_action_prompt, build_observation_prompt, build_prompt_envelope, canonical_dynamic_json
from app.candidate_research import ResearchRole
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


def test_frozen_prompt_resources_load_and_hash():
    action = loader.load_action_prompt()
    observation = loader.load_observation_prompt()
    assert action.endswith("\n") and not action.endswith("\n\n")
    assert observation.endswith("\n") and not observation.endswith("\n\n")
    assert loader.ACTION_PROMPT_CONTENT_DIGEST == "sha256:" + loader.ACTION_PROMPT_SHA256
    assert loader.OBSERVATION_PROMPT_CONTENT_DIGEST == "sha256:" + loader.OBSERVATION_PROMPT_SHA256
    assert "interests_locked" in action
    assert "Do not repeat an effective Search or Details request" in action
    assert "exactly one property named output" in action
    assert "do not return outcome_type at the top level" in action
    assert "include interest and meal_intent and copy them exactly" in action
    assert "Query wording never substitutes for these fields" in action
    assert "evidence_handles of that exact candidate" in observation
    assert "belong to every defaulted candidate" in observation
    assert "required_judgments" in observation
    assert "accepted_search is controller-validated" in observation
    assert "Do not assume a key was assessed merely" in observation
    assert "default unresolved" in observation
    assert "Omitted non-required keys retain earlier conclusions" in observation


@pytest.mark.parametrize("resource_name,expected", [
    (loader.ACTION_PROMPT_RESOURCE, loader.ACTION_PROMPT_SHA256),
    (loader.OBSERVATION_PROMPT_RESOURCE, loader.OBSERVATION_PROMPT_SHA256),
])
def test_prompt_tamper_fails_closed(monkeypatch, resource_name, expected):
    class Resource:
        def read_bytes(self):
            return b"changed\n"

    class Package:
        def joinpath(self, name):
            return Resource()

    monkeypatch.setattr(loader, "files", lambda _: Package())
    with pytest.raises(CandidateResearchConfigurationError):
        loader._read_prompt(resource_name, expected)


def test_prompt_encoding_and_terminal_newline_are_configuration_errors(monkeypatch):
    class Resource:
        def __init__(self, value):
            self.value = value

        def read_bytes(self):
            return self.value

    class Package:
        def __init__(self, value):
            self.value = value

        def joinpath(self, name):
            return Resource(self.value)

    for value in (b"\xff\n", b"x\n\n", "\ufeffx\n".encode("utf-8"), b"x\r\n"):
        monkeypatch.setattr(loader, "files", lambda _, value=value: Package(value))
        with pytest.raises(CandidateResearchConfigurationError):
            loader._read_prompt(loader.ACTION_PROMPT_RESOURCE, loader.ACTION_PROMPT_SHA256)


def test_prompt_resource_failures_do_not_expose_cause_or_text(monkeypatch):
    def fail(_):
        raise OSError("secret path")

    monkeypatch.setattr(loader, "files", fail)
    with pytest.raises(CandidateResearchConfigurationError) as caught:
        loader._read_prompt(loader.ACTION_PROMPT_RESOURCE, loader.ACTION_PROMPT_SHA256)
    error = caught.value
    assert str(error) == "" and error.args == () and error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__ is True


def test_prompting_uses_shared_canonical_typed_tree_and_rejects_nonfinite():
    run_id = uuid4()
    ref = ArtifactRef(
        artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="observation"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )
    envelope = build_action_prompt({"role": ResearchRole.ATTRACTION, "ref": ref, "nullable": None})
    assert '"nullable":null' in envelope.user_text
    assert '"role":"attraction"' in envelope.user_text
    with pytest.raises(ValueError):
        build_action_prompt({"bad": float("inf")})


@pytest.mark.parametrize("kind", ("action", "observation", "judgment"))
def test_prompt_envelope_dispatches_action_observation_and_judgment(kind):
    envelope = build_prompt_envelope(kind=kind, dynamic_payload={"z": 1, "a": None})
    assert envelope.user_text == '{"a":null,"z":1}'
    assert envelope.system_text.endswith("\n")


@pytest.mark.parametrize("kind", ("unknown", "", None))
def test_prompt_envelope_rejects_unknown_kind(kind):
    with pytest.raises(ValueError):
        build_prompt_envelope(kind=kind, dynamic_payload={})


def test_canonical_dynamic_json_handles_typed_values_and_rejects_nonfinite():
    assert canonical_dynamic_json({"b": (2, 1), "a": True}) == '{"a":true,"b":[2,1]}'
    with pytest.raises(ValueError):
        canonical_dynamic_json({"bad": float("nan")})


def test_prompt_loader_accepts_read_text_fallback_and_rejects_invalid_hash(monkeypatch):
    class Resource:
        def read_bytes(self):
            raise OSError("bytes unavailable")

        def read_text(self, encoding="utf-8"):
            return "x\n"

    class Package:
        def joinpath(self, name):
            return Resource()

    monkeypatch.setattr(loader, "files", lambda _: Package())
    expected = __import__("hashlib").sha256(b"x\n").hexdigest()
    assert loader._read_prompt("x.txt", expected) == "x\n"
    for bad in ("sha256:" + "g" * 64, "bad", "sha256:" + "a" * 63):
        with pytest.raises(CandidateResearchConfigurationError):
            loader._read_prompt("x.txt", bad)
