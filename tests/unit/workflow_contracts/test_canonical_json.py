"""Fixed vectors for Artifact canonical JSON and hashing."""

from datetime import date, datetime, timezone, timedelta
from enum import Enum, IntEnum
from uuid import UUID

import pytest

from app.workflow_contracts.artifacts.canonical_json import (
    _canonical_json_bytes,
    _content_hash,
)
from app.workflow_contracts.errors import ArtifactRequestValidationError


class TextEnum(str, Enum):
    VALUE = "enum-value"


class NumberEnum(IntEnum):
    VALUE = 1


def test_object_order_unicode_and_exact_encoding() -> None:
    assert _canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert _canonical_json_bytes({"中文": "旅行"}) == ' {"中文":"旅行"}'.strip().encode()
    composed = "é"
    decomposed = "e\u0301"
    assert _canonical_json_bytes({"value": composed}) != _canonical_json_bytes(
        {"value": decomposed}
    )
    assert b" " not in _canonical_json_bytes({"a": 1})
    encoded = _canonical_json_bytes({"a": 1})
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert not encoded.endswith(b"\n")


@pytest.mark.parametrize("value", ["\ud800", "\udfff"])
def test_isolated_surrogates_are_rejected(value: str) -> None:
    with pytest.raises(ArtifactRequestValidationError):
        _canonical_json_bytes({"value": value})


def test_numbers_keep_json_kind_and_boundaries() -> None:
    assert _canonical_json_bytes({"value": 1}) == b'{"value":1}'
    assert _canonical_json_bytes({"value": 1.0}) == b'{"value":1.0}'
    assert _canonical_json_bytes({"value": -0.0}) == b'{"value":0.0}'
    assert _canonical_json_bytes({"value": -(2**53 - 1)})
    assert _canonical_json_bytes({"value": 2**53 - 1})
    with pytest.raises(ArtifactRequestValidationError):
        _canonical_json_bytes({"value": 2**53})
    with pytest.raises(ArtifactRequestValidationError):
        _canonical_json_bytes({"value": float("nan")})
    with pytest.raises(ArtifactRequestValidationError):
        _canonical_json_bytes({"value": float("inf")})
    assert _canonical_json_bytes({"value": True}) == b'{"value":true}'


def test_scalar_and_collection_type_dispatch() -> None:
    value = {
        "none": None,
        "bool": False,
        "list": [2, "x"],
        "tuple": ("first", 2),
        "enum": TextEnum.VALUE,
        "number_enum": NumberEnum.VALUE,
    }
    encoded = _canonical_json_bytes(value)
    assert encoded == (
        b'{"bool":false,"enum":"enum-value","list":[2,"x"],'
        b'"none":null,"number_enum":1,"tuple":["first",2]}'
    )


def test_uuid_date_and_datetime_vectors() -> None:
    encoded = _canonical_json_bytes(
        {
            "uuid": UUID("12345678-1234-5678-1234-567812345678"),
            "date": date(2026, 8, 11),
            "datetime": datetime(2026, 8, 11, 17, 30, tzinfo=timezone(timedelta(hours=8))),
        }
    )
    assert encoded == (
        b'{"date":"2026-08-11","datetime":"2026-08-11T09:30:00Z",'
        b'"uuid":"12345678-1234-5678-1234-567812345678"}'
    )
    with pytest.raises(ArtifactRequestValidationError):
        _canonical_json_bytes({"datetime": datetime(2026, 8, 11, 9, 30)})
    tuple_bytes = _canonical_json_bytes(
        {"items": (datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),)}
    )
    assert tuple_bytes == b'{"items":["2026-08-11T09:30:00Z"]}'


def test_hash_format_and_size_are_based_on_canonical_bytes() -> None:
    encoded = _canonical_json_bytes({"value": "hash-me"})
    digest = _content_hash(encoded)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    assert len(encoded) == len(b'{"value":"hash-me"}')
