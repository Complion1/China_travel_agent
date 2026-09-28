"""Canonical codec for M11 durable pending inputs."""
import hashlib
import json
from uuid import UUID

from app.durable_execution import DurableExecutionCommandCodecDescriptor, DurableExecutionEncodedCommand
from app.workflow_contracts import DeliveryStatus, TravelSessionState, WorkflowPhase

from .errors import DemoWorkflowConfigurationError, DemoWorkflowUsageError
from .models import DemoStartCommand, DemoRequirementCommand, DemoHotelCommand, fresh


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


class DemoCommandCodec:
    def __init__(self, *, max_payload_bytes: int):
        if type(max_payload_bytes) is not int or not 1 <= max_payload_bytes <= 1048576:
            raise DemoWorkflowConfigurationError()
        self._max = max_payload_bytes
        self._descriptor = DurableExecutionCommandCodecDescriptor(
            codec_id="demo_workflow.command", codec_version="1.0",
            codec_digest=digest(canonical({
                "codec_id": "demo_workflow.command", "codec_version": "1.0",
                "type_ids": ["demo_workflow.start", "demo_workflow.requirement", "demo_workflow.hotel"],
                "state_schema": "2.0", "native_pause": "__root__:hotel",
                "requirement_pause": "requirements", "command_schema_revision": "m13_commands_1",
            })),
        )

    @property
    def descriptor(self):
        return fresh(self._descriptor, DurableExecutionCommandCodecDescriptor)

    def _encode(self, command, model, kind):
        try:
            command = fresh(command, model)
            payload = canonical(command.model_dump(mode="json"))
            if len(payload) > self._max:
                raise ValueError("payload limit")
            return DurableExecutionEncodedCommand(
                command_kind=kind, type_id="demo_workflow." + command.kind,
                type_version="1.0", canonical_bytes=payload, digest=digest(payload), size_bytes=len(payload),
            )
        except Exception:
            raise DemoWorkflowUsageError() from None

    def _decode(self, encoded, model, kind):
        try:
            encoded = fresh(encoded, DurableExecutionEncodedCommand)
            command = model.model_validate_json(encoded.canonical_bytes, strict=True)
            if self._encode(command, model, kind) != encoded:
                raise ValueError("noncanonical command")
            return command
        except Exception:
            raise DemoWorkflowUsageError() from None

    @staticmethod
    def _ids(*values):
        if any(type(value) is not UUID for value in values):
            raise DemoWorkflowUsageError()

    def _resume_model(self, run_id, pause_generation, node_name):
        self._ids(run_id)
        if type(pause_generation) is not int or pause_generation < 1:
            raise DemoWorkflowUsageError()
        if node_name == "requirements":
            return DemoRequirementCommand
        if node_name == "__root__":
            return DemoHotelCommand
        raise DemoWorkflowUsageError()

    def encode_initial(self, *, run_id, trace_id, command):
        self._ids(run_id, trace_id)
        return self._encode(command, DemoStartCommand, "initial")

    def decode_initial(self, *, run_id, trace_id, encoded):
        self._ids(run_id, trace_id)
        return self._decode(encoded, DemoStartCommand, "initial")

    def encode_resume(self, *, run_id, pause_generation, node_name, command):
        model = self._resume_model(run_id, pause_generation, node_name)
        if type(command) is DemoHotelCommand and command.candidate_set_ref.operation_id.run_id != run_id:
            raise DemoWorkflowUsageError()
        return self._encode(command, model, "resume")

    def decode_resume(self, *, run_id, pause_generation, node_name, encoded):
        model = self._resume_model(run_id, pause_generation, node_name)
        command = self._decode(encoded, model, "resume")
        self.encode_resume(run_id=run_id, pause_generation=pause_generation, node_name=node_name, command=command)
        return command

    def initial_state(self, *, run_id, trace_id, decoded_command):
        encoded = self.encode_initial(run_id=run_id, trace_id=trace_id, command=decoded_command)
        command = self.decode_initial(run_id=run_id, trace_id=trace_id, encoded=encoded)
        return TravelSessionState(
            schema_version="2.0", run_id=run_id, trace_id=trace_id,
            phase=WorkflowPhase.REQUIREMENTS, delivery_status=DeliveryStatus.IN_PROGRESS,
            terminal_code=None, terminal_outcome=None, requirement_state=command.turn.current,
            requirement_outcome=None, artifact_refs=(),
        )

    def requirement_turn(self, *, run_id, trace_id, decoded_command):
        encoded = self.encode_initial(run_id=run_id, trace_id=trace_id, command=decoded_command)
        return self.decode_initial(run_id=run_id, trace_id=trace_id, encoded=encoded).turn

    def requirement_resume(self, *, run_id, pause_generation, node_name, decoded_command):
        if node_name != "requirements":
            raise DemoWorkflowUsageError()
        encoded = self.encode_resume(run_id=run_id, pause_generation=pause_generation, node_name=node_name, command=decoded_command)
        return self.decode_resume(run_id=run_id, pause_generation=pause_generation, node_name=node_name, encoded=encoded).resume
