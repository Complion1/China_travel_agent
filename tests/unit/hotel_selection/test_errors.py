"""Module 07 异常安全合同。"""

import pytest

from app.hotel_selection import (
    HotelCandidateIdentityConflictError,
    HotelInputError,
    HotelModuleError,
    HotelReplayError,
)


@pytest.mark.parametrize(
    ("error_type", "code"),
    (
        (HotelInputError, "invalid_input"),
        (HotelReplayError, "artifact_replay_mismatch"),
        (HotelCandidateIdentityConflictError, "candidate_identity_conflict"),
    ),
)
def test_errors_have_fixed_code_args_and_safe_repr(error_type: type[HotelModuleError], code: str) -> None:
    error = error_type()
    assert isinstance(error, HotelModuleError)
    assert error.code == code
    assert error.args == (code,)
    assert str(error) == code
    assert repr(error) == f"{error_type.__name__}(code={code})"
    for secret in ("uid-secret", "杭州", "sha256:", "30.2", "底层异常"):
        assert secret not in repr(error)
    with pytest.raises(AttributeError):
        error.code = "changed"
    with pytest.raises(AttributeError):
        error.args = ("changed",)
    with pytest.raises(AttributeError):
        error.extra = "forbidden"
    with pytest.raises(TypeError):
        error_type("unexpected")


def test_error_special_exception_attributes_are_controlled() -> None:
    error = HotelInputError()
    error.__traceback__ = None
    with pytest.raises(AttributeError):
        del error.extra
