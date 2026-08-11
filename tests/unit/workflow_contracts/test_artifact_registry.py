"""Contract tests for strict Artifact Schema Registry behavior."""

from datetime import date, datetime, timezone
from dataclasses import fields
from enum import Enum, IntEnum
from hashlib import sha256
from typing import Annotated, Literal, Mapping
from typing import get_type_hints
from uuid import UUID

import pytest
from pydantic import (
    BaseModel,
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainValidator,
    RootModel,
    WrapValidator,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
    model_serializer,
)
from pydantic.functional_serializers import PlainSerializer, SerializeAsAny, WrapSerializer

import app.workflow_contracts.artifacts as artifacts_package
from app.workflow_contracts.artifacts.models import ArtifactRef, ArtifactWriteRequest
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
    PreparedArtifact,
)
from app.workflow_contracts.errors import (
    ArtifactExpectedModelMismatchError,
    ArtifactIntegrityError,
    ArtifactRegistryConfigurationError,
    ArtifactRequestValidationError,
    UnsupportedArtifactTypeError,
    UnsupportedArtifactVersionError,
)
from app.workflow_contracts.identifiers import ContentHash, OperationId, WorkflowPhase
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
ARTIFACT_ID = UUID("87654321-4321-8765-4321-876543218765")
OPERATION_ID = OperationId(
    run_id=RUN_ID,
    phase=WorkflowPhase.REQUIREMENTS,
    logical_step="prepare_artifact",
)
CONTENT = {
    "label": "sample",
    "count": 2,
    "ratio": 1.5,
    "occurred_on": "2026-08-11",
    "observed_at": "2026-08-11T09:30:00Z",
    "note": None,
}


def _registry(model: type[BaseModel] = StrictSampleArtifact) -> ArtifactSchemaRegistry:
    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=model,
            ),
        )
    )


def _request(content: dict[str, object] | None = None) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=OPERATION_ID,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content=CONTENT if content is None else content,
    )


def test_prepare_is_strict_deterministic_and_decode_is_integrity_checked() -> None:
    registry = _registry()
    first = registry.prepare(_request())
    second = registry.prepare(_request())
    assert first.canonical_bytes == second.canonical_bytes
    assert first.content_hash == second.content_hash
    assert first.size_bytes == len(first.canonical_bytes)

    ref = ArtifactRef(
        artifact_id=ARTIFACT_ID,
        artifact_type=first.artifact_type,
        schema_version=first.schema_version,
        content_hash=first.content_hash,
        operation_id=OPERATION_ID,
        created_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        size_bytes=first.size_bytes,
    )
    decoded = registry.decode(
        ref=ref,
        canonical_bytes=first.canonical_bytes,
        expected_model=StrictSampleArtifact,
    )
    assert decoded == StrictSampleArtifact(
        label="sample",
        count=2,
        ratio=1.5,
        occurred_on=date(2026, 8, 11),
        observed_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        note=None,
    )

    with pytest.raises(ArtifactExpectedModelMismatchError):
        registry.decode(
            ref=ref,
            canonical_bytes=first.canonical_bytes,
            expected_model=OtherArtifact,
        )
    with pytest.raises(ArtifactIntegrityError):
        registry.decode(
            ref=ref,
            canonical_bytes=first.canonical_bytes + b" ",
            expected_model=StrictSampleArtifact,
        )
    with pytest.raises(ArtifactIntegrityError):
        registry.decode(
            ref=ref,
            canonical_bytes=bytearray(first.canonical_bytes),
            expected_model=StrictSampleArtifact,
        )


def test_prepared_artifact_is_private_and_has_exact_typed_frozen_slots() -> None:
    assert [field.name for field in fields(PreparedArtifact)] == [
        "operation_id",
        "artifact_type",
        "schema_version",
        "canonical_bytes",
        "content_hash",
        "size_bytes",
    ]
    hints = get_type_hints(PreparedArtifact, include_extras=True)
    assert hints["operation_id"] is OperationId
    assert hints["content_hash"] == ContentHash
    assert PreparedArtifact.__dataclass_params__.frozen is True
    assert hasattr(PreparedArtifact, "__slots__")
    assert not hasattr(artifacts_package, "PreparedArtifact")
    assert "PreparedArtifact" not in getattr(artifacts_package, "__all__", ())


class OtherArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    value: int


def test_empty_registry_and_unknown_type_version_are_explicit() -> None:
    empty = ArtifactSchemaRegistry(())
    with pytest.raises(UnsupportedArtifactTypeError):
        empty.prepare(_request())
    registry = _registry()
    with pytest.raises(UnsupportedArtifactTypeError):
        registry.resolve(artifact_type="other.artifact", schema_version="1.0")
    with pytest.raises(UnsupportedArtifactVersionError):
        registry.resolve(artifact_type="test.sample_artifact", schema_version="2.0")


def test_registry_rejects_invalid_registration_shapes_and_unhashable_resolution_input() -> None:
    registration = ArtifactSchemaRegistration(
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        model=StrictSampleArtifact,
    )
    invalid_registrations = (
        [registration],
        (object(),),
        (ArtifactSchemaRegistration(artifact_type=1, schema_version="1.0", model=StrictSampleArtifact),),
        (ArtifactSchemaRegistration(artifact_type="test.sample_artifact", schema_version=1, model=StrictSampleArtifact),),
        (ArtifactSchemaRegistration(artifact_type="not a valid type", schema_version="1.0", model=StrictSampleArtifact),),
        (ArtifactSchemaRegistration(artifact_type="test.sample_artifact", schema_version="not-a-version", model=StrictSampleArtifact),),
        (ArtifactSchemaRegistration(artifact_type="test.sample_artifact", schema_version="1.0", model=object),),
    )
    for registrations in invalid_registrations:
        with pytest.raises(ArtifactRegistryConfigurationError):
            ArtifactSchemaRegistry(registrations)  # type: ignore[arg-type]

    registry = _registry()
    with pytest.raises(UnsupportedArtifactTypeError):
        registry.resolve(artifact_type=[], schema_version="1.0")  # type: ignore[arg-type]


def test_registry_rejects_forbidden_validator_and_type_graph_shapes() -> None:
    class BeforeDecorated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @field_validator("value", mode="before")
        @classmethod
        def keep_before(cls, value: int) -> int:
            return value

    class PlainDecorated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @field_validator("value", mode="plain")
        @classmethod
        def keep_plain(cls, value: object) -> object:
            return value

    class WrapDecorated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @field_validator("value", mode="wrap")
        @classmethod
        def keep_wrap(cls, value: object, handler: object) -> object:
            return handler(value)  # type: ignore[operator]

    class BytesLiteral(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Literal[b"bytes"]

    class BytesEnum(Enum):
        VALUE = b"bytes"

    class InvalidEnum(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: BytesEnum

    class EmptyTuple(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: tuple[()]

    class InvalidTuple(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: tuple

    class InvalidDictKey(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: dict[int, str]

    invalid_models = (
        BeforeDecorated,
        PlainDecorated,
        WrapDecorated,
        BytesLiteral,
        InvalidEnum,
        EmptyTuple,
        InvalidTuple,
        InvalidDictKey,
    )
    for model in invalid_models:
        with pytest.raises(ArtifactRegistryConfigurationError):
            _registry(model)


@pytest.mark.parametrize(
    ("injection_name", "injected"),
    [
        ("datetime", datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)),
        ("uuid", UUID("12345678-1234-5678-1234-567812345678")),
        ("bytes", b"bytes"),
        ("tuple", ("tuple",)),
        ("set", {"set"}),
        ("object", object()),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_nested_content_mutation_preflight_rejects_non_json_values(
    injection_name: str,
    injected: object,
) -> None:
    request = _request()
    request.content["injected"] = injected  # type: ignore[assignment]
    prepared = None
    with pytest.raises(ArtifactRequestValidationError):
        prepared = _registry().prepare(request)
    assert prepared is None


@pytest.mark.parametrize(
    ("injection_name", "injected"),
    [
        ("datetime", datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)),
        ("uuid", UUID("12345678-1234-5678-1234-567812345678")),
        ("bytes", b"bytes"),
        ("tuple", ("tuple",)),
        ("set", {"set"}),
        ("object", object()),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_model_construct_preflight_rejects_non_json_values_before_json_dump(
    injection_name: str,
    injected: object,
) -> None:
    request = ArtifactWriteRequest.model_construct(
        operation_id=OPERATION_ID,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"injected": injected},
    )
    prepared = None
    with pytest.raises(ArtifactRequestValidationError):
        prepared = _registry().prepare(request)
    assert prepared is None


def test_registry_rejects_duplicate_keys_and_non_strict_model_graphs() -> None:
    registration = ArtifactSchemaRegistration(
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        model=StrictSampleArtifact,
    )
    with pytest.raises(ArtifactRegistryConfigurationError):
        ArtifactSchemaRegistry((registration, registration))

    class DefaultField(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int = 0

    class AliasField(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int = Field(alias="renamed")

    class BytesField(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: bytes

    class SetField(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: set[int]

    class MappingField(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Mapping[str, int]

    class BadLeaf(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: bytes

    class BadNested(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        child: BadLeaf

    for model in (DefaultField, AliasField, BytesField, SetField, MappingField, BadNested, RootInt):
        with pytest.raises(ArtifactRegistryConfigurationError):
            _registry(model)


class RootInt(RootModel[int]):
    pass


def test_registry_rejects_computed_serializer_and_forbidden_metadata() -> None:
    class Computed(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @computed_field
        @property
        def doubled(self) -> int:
            return self.value * 2

    class Serialized(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @field_serializer("value")
        def serialize_value(self, value: int) -> str:
            return str(value)

    class ModelSerialized(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @model_serializer(mode="plain")
        def serialize_model(self) -> dict[str, int]:
            return {"value": self.value}

    class BeforeValidated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, BeforeValidator(lambda value: value)]

    class PlainValidated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, PlainValidator(lambda value: value)]

    class WrappedValidated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[
            int,
            WrapValidator(lambda value, handler: handler(value)),
        ]

    unknown_callable = lambda value: value

    class UnknownCallableMetadata(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, unknown_callable]

    class AdditionalModelConfig(BaseModel):
        model_config = ConfigDict(
            strict=True,
            frozen=True,
            extra="forbid",
            validate_assignment=True,
        )
        value: int

    class ValidationAlias(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int = Field(validation_alias="incoming")

    class SerializationAlias(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int = Field(serialization_alias="outgoing")

    for model in (
        Computed,
        Serialized,
        ModelSerialized,
        BeforeValidated,
        PlainValidated,
        WrappedValidated,
        UnknownCallableMetadata,
        AdditionalModelConfig,
        ValidationAlias,
        SerializationAlias,
    ):
        with pytest.raises(ArtifactRegistryConfigurationError):
            _registry(model)

    class PlainSerialized(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, PlainSerializer(lambda value: str(value))]

    class WrappedSerialized(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, WrapSerializer(lambda value, handler: handler(value))]

    class SerializeAnyModel(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, SerializeAsAny()]

    for model in (PlainSerialized, WrappedSerialized, SerializeAnyModel):
        with pytest.raises(ArtifactRegistryConfigurationError):
            _registry(model)


def test_registry_supports_annotated_literal_nullable_nested_and_recursive_models() -> None:
    class Nested(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        label: Annotated[str, Field(min_length=1)]
        mode: Literal["a", 1]
        optional: str | None

    class Recursive(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int
        child: "Recursive | None"

    Recursive.model_rebuild()

    class Composite(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        nested: Nested
        nodes: list[Recursive]
        mapping: dict[str, int]

    class AfterAssert(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int, AfterValidator(lambda value: value)]

    registry = _registry(Composite)
    request = ArtifactWriteRequest(
        operation_id=OPERATION_ID,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "nested": {"label": "nested", "mode": "a", "optional": None},
            "nodes": [{"value": 1, "child": None}],
            "mapping": {"key": 2},
        },
    )
    assert registry.prepare(request).size_bytes > 0
    assert _registry(AfterAssert).prepare(
        ArtifactWriteRequest(
            operation_id=OPERATION_ID,
            artifact_type="test.sample_artifact",
            schema_version="1.0",
            content={"value": 1},
        )
    ).size_bytes > 0


def test_registry_supports_fixed_and_variadic_tuples_and_union_paths_deterministically() -> None:
    class TupleArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        fixed: tuple[int, str]
        variadic: tuple[int, ...]
        nullable: int | None
        alternative: int | str
        nested: list[dict[str, tuple[int, ...]]]
        annotated_values: list[Annotated[int, AfterValidator(lambda value: value)]]
        identifier: UUID

    registry = _registry(TupleArtifact)
    request = _request(
        {
            "fixed": [1, "one"],
            "variadic": [2, 3],
            "nullable": None,
            "alternative": "text",
            "nested": [{"values": [4, 5]}],
            "annotated_values": [6],
            "identifier": str(RUN_ID),
        }
    )
    first = registry.prepare(request)
    second = registry.prepare(request)
    assert first.canonical_bytes == second.canonical_bytes
    assert first.content_hash == second.content_hash
    assert first.size_bytes == len(first.canonical_bytes)


def test_after_validator_must_preserve_values_and_path_kinds() -> None:
    class Transforming(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @field_validator("value", mode="after")
        @classmethod
        def transform(cls, value: int) -> int:
            return value + 1

    registry = _registry(Transforming)
    request = ArtifactWriteRequest(
        operation_id=OPERATION_ID,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"value": 1},
    )
    with pytest.raises(ArtifactRequestValidationError):
        registry.prepare(request)


def test_after_validator_conversions_are_rejected_for_union_enum_and_uuid_paths() -> None:
    class OptionalTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[int | None, AfterValidator(lambda _: None)]

    class Color(Enum):
        RED = "red"

    class EnumTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[Color, AfterValidator(lambda _: "red")]

    class UUIDTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[UUID, AfterValidator(lambda _: "not-a-uuid")]

    cases = (
        (OptionalTransform, {"value": 1}),
        (EnumTransform, {"value": "red"}),
        (UUIDTransform, {"value": str(RUN_ID)}),
    )
    for model, content in cases:
        with pytest.raises(ArtifactRequestValidationError):
            _registry(model).prepare(_request(content))


def test_float_json_int_and_numeric_enum_kinds_are_exact() -> None:
    class FloatField(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: float

    with pytest.raises(ArtifactRequestValidationError):
        _registry(FloatField).prepare(_request({"value": 1}))

    class RegularIntEnum(Enum):
        VALUE = 1

    class IntEnumValue(IntEnum):
        VALUE = 1

    class RegularFloatEnum(Enum):
        VALUE = 1.0

    class RegularBoolEnum(Enum):
        VALUE = True

    class RegularIntArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: RegularIntEnum

    class IntEnumArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: IntEnumValue

    class RegularFloatArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: RegularFloatEnum

    class RegularBoolArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: RegularBoolEnum

    cases = (
        (RegularIntArtifact, RegularIntEnum, 1),
        (IntEnumArtifact, IntEnumValue, 1),
        (RegularFloatArtifact, RegularFloatEnum, 1.0),
        (RegularBoolArtifact, RegularBoolEnum, True),
    )
    for model, enum_type, raw_value in cases:
        registry = _registry(model)
        prepared = registry.prepare(_request({"value": raw_value}))
        decoded = registry.decode(
            ref=ArtifactRef(
                artifact_id=ARTIFACT_ID,
                artifact_type=prepared.artifact_type,
                schema_version=prepared.schema_version,
                content_hash=prepared.content_hash,
                operation_id=OPERATION_ID,
                created_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
                size_bytes=prepared.size_bytes,
            ),
            canonical_bytes=prepared.canonical_bytes,
            expected_model=model,
        )
        assert type(decoded.value) is enum_type
        assert type(decoded.value.value) is type(raw_value)
        assert decoded.value.value == raw_value

    for model, wrong_value in (
        (RegularIntArtifact, True),
        (IntEnumArtifact, True),
        (RegularFloatArtifact, 1),
        (RegularBoolArtifact, 1),
    ):
        with pytest.raises(ArtifactRequestValidationError):
            _registry(model).prepare(_request({"value": wrong_value}))


def test_registry_preserves_uuid_date_and_timezone_datetime_observations() -> None:
    class TemporalArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        identifier: UUID
        occurred_on: date
        observed_at: datetime

    prepared = _registry(TemporalArtifact).prepare(
        _request(
            {
                "identifier": str(RUN_ID),
                "occurred_on": "2026-08-11",
                "observed_at": "2026-08-11T17:30:00+08:00",
            }
        )
    )
    assert prepared.canonical_bytes == (
        b'{"identifier":"12345678-1234-5678-1234-567812345678",'
        b'"observed_at":"2026-08-11T09:30:00Z","occurred_on":"2026-08-11"}'
    )


def test_model_after_validator_must_be_identity_or_pure_assertion() -> None:
    class IdentityValidated(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @model_validator(mode="after")
        def verify(self) -> "IdentityValidated":
            if self.value < 0:
                raise ValueError("value must be non-negative")
            return self

    assert _registry(IdentityValidated).prepare(_request({"value": 1})).size_bytes > 0

    class TransformingModel(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @model_validator(mode="after")
        def transform(self) -> "TransformingModel":
            return self.model_copy(update={"value": self.value + 1})

    with pytest.raises(ArtifactRequestValidationError):
        _registry(TransformingModel).prepare(_request({"value": 1}))


def test_literal_kind_and_after_validator_regressions() -> None:
    class LiteralBool(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Literal[True]

    class LiteralInt(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Literal[1]

    class LiteralFloat(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Literal[1.0]

    for model, value in ((LiteralBool, True), (LiteralInt, 1), (LiteralFloat, 1.0)):
        assert _registry(model).prepare(
            ArtifactWriteRequest(
                operation_id=OPERATION_ID,
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                content={"value": value},
            )
        ).size_bytes > 0

    for model, value in ((LiteralBool, 1), (LiteralInt, True), (LiteralFloat, 1)):
        with pytest.raises(ArtifactRequestValidationError):
            _registry(model).prepare(
                ArtifactWriteRequest(
                    operation_id=OPERATION_ID,
                    artifact_type="test.sample_artifact",
                    schema_version="1.0",
                    content={"value": value},
                )
            )

    class LiteralTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: Annotated[Literal[1], AfterValidator(lambda _: 1.0)]

    with pytest.raises(ArtifactRequestValidationError):
        _registry(LiteralTransform).prepare(
            ArtifactWriteRequest(
                operation_id=OPERATION_ID,
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                content={"value": 1},
            )
        )


def test_nested_list_and_dict_transforming_after_validators_are_rejected() -> None:
    class NestedTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

        @field_validator("value", mode="after")
        @classmethod
        def bump(cls, value: int) -> int:
            return value + 1

    class ListTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        values: list[int]

        @field_validator("values", mode="after")
        @classmethod
        def append_value(cls, value: list[int]) -> list[int]:
            return [*value, 2]

    class DictTransform(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        values: dict[str, int]

        @field_validator("values", mode="after")
        @classmethod
        def add_value(cls, value: dict[str, int]) -> dict[str, int]:
            return {**value, "added": 2}

    cases = (
        (NestedTransform, {"value": 1}),
        (ListTransform, {"values": [1]}),
        (DictTransform, {"values": {"one": 1}}),
    )
    for model, content in cases:
        with pytest.raises(ArtifactRequestValidationError):
            _registry(model).prepare(
                ArtifactWriteRequest(
                    operation_id=OPERATION_ID,
                    artifact_type="test.sample_artifact",
                    schema_version="1.0",
                    content=content,
                )
            )


def test_decode_rejects_hash_matched_noncanonical_and_invalid_json_bytes() -> None:
    class ValueArtifact(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: int

    registry = _registry(ValueArtifact)
    for canonical_bytes in (b' {"value":1}', b"not-json"):
        ref = ArtifactRef(
            artifact_id=ARTIFACT_ID,
            artifact_type="test.sample_artifact",
            schema_version="1.0",
            content_hash="sha256:" + sha256(canonical_bytes).hexdigest(),
            operation_id=OPERATION_ID,
            created_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
            size_bytes=len(canonical_bytes),
        )
        with pytest.raises(ArtifactIntegrityError):
            registry.decode(
                ref=ref,
                canonical_bytes=canonical_bytes,
                expected_model=ValueArtifact,
            )
