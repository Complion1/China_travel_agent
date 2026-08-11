"""Strict schema registration and artifact preparation."""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from types import UnionType
from typing import Annotated, Any, Generic, Literal, TypeVar, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from pydantic.functional_serializers import (
    PlainSerializer,
    SerializeAsAny,
    WrapSerializer,
)

from app.workflow_contracts.artifacts.canonical_json import (
    _canonical_json_bytes,
    _content_hash,
)
from app.workflow_contracts.artifacts.models import (
    ArtifactRef,
    ArtifactWriteRequest,
    JsonValue,
)
from app.workflow_contracts.errors import (
    ArtifactExpectedModelMismatchError,
    ArtifactIntegrityError,
    ArtifactRegistryConfigurationError,
    ArtifactRequestValidationError,
    UnsupportedArtifactTypeError,
    UnsupportedArtifactVersionError,
)
from app.workflow_contracts.identifiers import (
    ArtifactSchemaVersion,
    ArtifactType,
    ContentHash,
    OperationId,
)


TArtifact = TypeVar("TArtifact", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ArtifactSchemaRegistration(Generic[TArtifact]):
    artifact_type: ArtifactType
    schema_version: ArtifactSchemaVersion
    model: type[TArtifact]


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    operation_id: OperationId
    artifact_type: ArtifactType
    schema_version: ArtifactSchemaVersion
    canonical_bytes: bytes
    content_hash: ContentHash
    size_bytes: int


def _configuration_failure() -> None:
    raise ArtifactRegistryConfigurationError() from None


def _validate_metadata(metadata: tuple[object, ...]) -> None:
    for item in metadata:
        if isinstance(item, (PlainSerializer, WrapSerializer, SerializeAsAny)):
            _configuration_failure()
        name = type(item).__name__
        if name in {"BeforeValidator", "PlainValidator", "WrapValidator"}:
            _configuration_failure()
        if name == "AfterValidator":
            continue
        if callable(item):
            _configuration_failure()


def _validate_model_decorators(model: type[BaseModel]) -> None:
    decorators = getattr(model, "__pydantic_decorators__", None)
    if decorators is None:
        return
    for collection_name in ("field_validators", "model_validators"):
        collection = getattr(decorators, collection_name, {})
        for decorator in collection.values():
            mode = getattr(getattr(decorator, "info", None), "mode", None)
            if mode in {"before", "plain", "wrap"}:
                _configuration_failure()
    if getattr(decorators, "field_serializers", {}) or getattr(
        decorators, "model_serializers", {}
    ):
        _configuration_failure()


def _is_json_scalar_value(value: object) -> bool:
    if value is None or type(value) is bool or type(value) is str:
        return True
    if type(value) is int:
        return -(2**53 - 1) <= value <= 2**53 - 1
    if type(value) is float:
        return math.isfinite(value)
    return False


def _unwrap_annotated(annotation: object) -> object:
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        _validate_metadata(tuple(metadata))
        return _unwrap_annotated(base)
    return annotation


def _check_type_graph(annotation: object, visited: set[type[BaseModel]]) -> None:
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)

    if annotation is type(None):
        return
    if annotation in {bool, int, float, str}:
        return
    if annotation in {date, datetime, UUID}:
        return

    if origin is Literal:
        if not all(_is_json_scalar_value(value) for value in get_args(annotation)):
            _configuration_failure()
        return

    if origin in {Union, UnionType}:
        for branch in get_args(annotation):
            _check_type_graph(branch, visited)
        return

    if origin is list:
        args = get_args(annotation)
        if len(args) != 1:
            _configuration_failure()
        _check_type_graph(args[0], visited)
        return

    if origin is tuple:
        args = get_args(annotation)
        if not args:
            _configuration_failure()
        if len(args) == 2 and args[1] is Ellipsis:
            _check_type_graph(args[0], visited)
        else:
            for item in args:
                _check_type_graph(item, visited)
        return

    if origin is dict:
        args = get_args(annotation)
        if len(args) != 2 or _unwrap_annotated(args[0]) is not str:
            _configuration_failure()
        _check_type_graph(args[1], visited)
        return

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if not all(_is_json_scalar_value(member.value) for member in annotation):
            _configuration_failure()
        return

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if getattr(annotation, "__pydantic_root_model__", False):
            _configuration_failure()
        if annotation in visited:
            return
        visited.add(annotation)
        if dict(annotation.model_config) != {
            "strict": True,
            "frozen": True,
            "extra": "forbid",
        }:
            _configuration_failure()
        _validate_model_decorators(annotation)
        if getattr(annotation, "__pydantic_computed_fields__", {}):
            _configuration_failure()
        for field in annotation.model_fields.values():
            if not field.is_required():
                _configuration_failure()
            if (
                field.alias is not None
                or field.validation_alias is not None
                or field.serialization_alias is not None
            ):
                _configuration_failure()
            _validate_metadata(tuple(field.metadata))
            _check_type_graph(field.annotation, visited)
        return

    _configuration_failure()


def _strict_json_round_trip(
    model: BaseModel,
    model_type: type[BaseModel],
) -> tuple[dict[str, object], BaseModel]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            python_tree = model.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            first = TypeAdapter(model_type).validate_python(python_tree, strict=True)
            json_tree = first.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            encoded = json.dumps(
                json_tree,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            second = TypeAdapter(model_type).validate_json(encoded, strict=True)
    except Exception:
        raise ArtifactRequestValidationError() from None
    return python_tree, second


def _mismatch() -> None:
    raise ArtifactRequestValidationError() from None


def _compare_tree(raw: object, typed: object, annotation: object) -> None:
    annotation = _unwrap_annotated(annotation)
    origin = get_origin(annotation)

    if origin in {Union, UnionType}:
        for branch in get_args(annotation):
            try:
                _compare_tree(raw, typed, branch)
                return
            except ArtifactRequestValidationError:
                continue
        _mismatch()

    if annotation is type(None):
        if raw is not None or typed is not None:
            _mismatch()
        return
    if origin is Literal:
        literal_values = get_args(annotation)
        if not any(
            type(raw) is type(value)
            and type(typed) is type(value)
            and raw == value
            and typed == value
            for value in literal_values
        ):
            _mismatch()
        return
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(typed) is not annotation:
            _mismatch()
        value = typed.value
        if type(raw) is not type(value) or raw != value:
            _mismatch()
        return
    if annotation is UUID:
        if type(raw) is not str or type(typed) is not UUID:
            _mismatch()
        try:
            if UUID(raw) != typed:
                _mismatch()
        except (ValueError, AttributeError, TypeError):
            _mismatch()
        return
    if annotation is datetime:
        if type(raw) is not str or type(typed) is not datetime:
            _mismatch()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None or typed.tzinfo is None:
                _mismatch()
            if parsed.astimezone(timezone.utc) != typed.astimezone(timezone.utc):
                _mismatch()
        except (ValueError, OverflowError, AttributeError):
            _mismatch()
        return
    if annotation is date:
        if type(raw) is not str or type(typed) is not date:
            _mismatch()
        if raw != typed.isoformat():
            _mismatch()
        return
    if annotation is bool or annotation is int or annotation is float or annotation is str:
        if type(raw) is not annotation or type(typed) is not annotation or raw != typed:
            _mismatch()
        return
    if origin is list:
        args = get_args(annotation)
        if type(raw) is not list or type(typed) is not list or len(raw) != len(typed):
            _mismatch()
        for raw_item, typed_item in zip(raw, typed, strict=True):
            _compare_tree(raw_item, typed_item, args[0])
        return
    if origin is tuple:
        args = get_args(annotation)
        if type(raw) is not list or type(typed) is not tuple or len(raw) != len(typed):
            _mismatch()
        item_annotations = [args[0]] * len(raw) if len(args) == 2 and args[1] is Ellipsis else list(args)
        if len(item_annotations) != len(raw):
            _mismatch()
        for raw_item, typed_item, item_annotation in zip(raw, typed, item_annotations, strict=True):
            _compare_tree(raw_item, typed_item, item_annotation)
        return
    if origin is dict:
        args = get_args(annotation)
        if type(raw) is not dict or type(typed) is not dict or set(raw) != set(typed):
            _mismatch()
        for key in raw:
            if type(key) is not str:
                _mismatch()
            _compare_tree(raw[key], typed[key], args[1])
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if type(raw) is not dict or type(typed) is not annotation:
            _mismatch()
        fields = annotation.model_fields
        if set(raw) != set(fields):
            _mismatch()
        for name, field in fields.items():
            _compare_tree(raw[name], getattr(typed, name), field.annotation)
        return
    _mismatch()


class ArtifactSchemaRegistry:
    def __init__(self, registrations: tuple[ArtifactSchemaRegistration, ...]) -> None:
        try:
            if type(registrations) is not tuple:
                _configuration_failure()
            prepared: list[ArtifactSchemaRegistration] = []
            keys: set[tuple[str, str]] = set()
            for registration in registrations:
                if not isinstance(registration, ArtifactSchemaRegistration):
                    _configuration_failure()
                if type(registration.artifact_type) is not str:
                    _configuration_failure()
                if type(registration.schema_version) is not str:
                    _configuration_failure()
                TypeAdapter(ArtifactType).validate_python(registration.artifact_type, strict=True)
                TypeAdapter(ArtifactSchemaVersion).validate_python(
                    registration.schema_version, strict=True
                )
                if not isinstance(registration.model, type) or not issubclass(
                    registration.model, BaseModel
                ):
                    _configuration_failure()
                _check_type_graph(registration.model, set())
                key = (registration.artifact_type, registration.schema_version)
                if key in keys:
                    _configuration_failure()
                keys.add(key)
                prepared.append(registration)
        except ArtifactRegistryConfigurationError:
            raise
        except Exception:
            raise ArtifactRegistryConfigurationError() from None
        self._registrations = {(
            item.artifact_type,
            item.schema_version,
        ): item for item in prepared}

    def resolve(
        self,
        *,
        artifact_type: ArtifactType,
        schema_version: ArtifactSchemaVersion,
    ) -> ArtifactSchemaRegistration:
        try:
            key = (artifact_type, schema_version)
            if key not in self._registrations:
                if any(item[0] == artifact_type for item in self._registrations):
                    raise UnsupportedArtifactVersionError() from None
                raise UnsupportedArtifactTypeError() from None
            return self._registrations[key]
        except (UnsupportedArtifactTypeError, UnsupportedArtifactVersionError):
            raise
        except Exception:
            raise UnsupportedArtifactTypeError() from None

    def prepare(self, request: ArtifactWriteRequest) -> PreparedArtifact:
        python_tree, fresh_request = _strict_json_round_trip(request, ArtifactWriteRequest)
        registration = self.resolve(
            artifact_type=fresh_request.artifact_type,
            schema_version=fresh_request.schema_version,
        )
        try:
            raw_content = python_tree["content"]
            raw_bytes = _canonical_json_bytes(raw_content)
            typed_content = TypeAdapter(registration.model).validate_json(raw_bytes, strict=True)
            _compare_tree(raw_content, typed_content, registration.model)
            typed_tree = typed_content.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            canonical_bytes = _canonical_json_bytes(typed_tree)
        except ArtifactRequestValidationError:
            raise
        except Exception:
            raise ArtifactRequestValidationError() from None
        return PreparedArtifact(
            operation_id=fresh_request.operation_id,
            artifact_type=fresh_request.artifact_type,
            schema_version=fresh_request.schema_version,
            canonical_bytes=canonical_bytes,
            content_hash=_content_hash(canonical_bytes),
            size_bytes=len(canonical_bytes),
        )

    def decode(
        self,
        *,
        ref: ArtifactRef,
        canonical_bytes: bytes,
        expected_model: type[TArtifact],
    ) -> TArtifact:
        _, fresh_ref = _strict_json_round_trip(ref, ArtifactRef)
        registration = self.resolve(
            artifact_type=fresh_ref.artifact_type,
            schema_version=fresh_ref.schema_version,
        )
        if registration.model is not expected_model:
            raise ArtifactExpectedModelMismatchError() from None
        if type(canonical_bytes) is not bytes:
            raise ArtifactIntegrityError() from None
        if (
            _content_hash(canonical_bytes) != fresh_ref.content_hash
            or len(canonical_bytes) != fresh_ref.size_bytes
        ):
            raise ArtifactIntegrityError() from None
        try:
            typed = TypeAdapter(registration.model).validate_json(canonical_bytes, strict=True)
            typed_tree = typed.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            if _canonical_json_bytes(typed_tree) != canonical_bytes:
                raise ArtifactIntegrityError()
        except ArtifactIntegrityError:
            raise
        except Exception:
            raise ArtifactIntegrityError() from None
        return typed
