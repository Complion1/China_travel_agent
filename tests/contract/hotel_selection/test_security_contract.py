"""Module 07 安全字段、纯函数和禁止测试逃逸合同。"""

import json
import inspect
from uuid import UUID

import pytest

import app.hotel_selection as hotel_selection
from app.hotel_selection import (
    HotelCandidatesReady,
    HotelCandidateSelectionPort,
    HotelCandidateSelectionService,
    HotelDiscoveryArtifact,
    HotelInputError,
)
from app.hotel_selection import artifacts, query_policy
from tests.support.hotel_selection_fakes import (
    confirmed_requirement,
    make_service,
    map_success,
    operation_ids,
    place,
)


def test_artifacts_and_errors_do_not_store_queries_or_raw_provider_fields() -> None:
    artifact_source = inspect.getsource(artifacts)
    assert "query:" not in artifact_source
    assert "destination:" not in artifact_source
    assert "payload:" not in artifact_source
    assert "secret" not in artifact_source.lower()
    error = HotelInputError()
    assert "query" not in repr(error)
    assert "destination" not in repr(error)


def test_query_policy_is_deterministic_and_has_no_io_or_environment_access() -> None:
    source = inspect.getsource(query_policy)
    assert "open(" not in source
    assert "os.environ" not in source
    assert "socket" not in source
    assert "http" not in source.lower()
    assert "sleep(" not in source


@pytest.mark.asyncio
async def test_artifact_and_public_outcome_content_exclude_request_and_provider_secrets() -> None:
    raw_query = "RAW_QUERY_SENTINEL"
    raw_destination = "RAW_DESTINATION_SENTINEL"
    raw_summary = "FULL_CONFIRMATION_SUMMARY_SENTINEL"
    raw_payload = "RAW_PROVIDER_PAYLOAD_SENTINEL"
    confirmed = confirmed_requirement(hotel_preference=raw_query)
    confirmed = confirmed.model_copy(
        update={
            "confirmation_summary_digest": "sha256:summary-digest",
            "requirement": confirmed.requirement.model_copy(
                update={"destination": raw_destination}
            ),
        }
    )
    service, _, repository, _, _ = make_service(
        (
            map_success(
                place("safe-a", index=0),
                place("safe-b", index=1),
                place("safe-c", index=2),
            ),
        ),
        run_id=UUID("00000000-0000-0000-0000-000000000031"),
    )
    outcome = await service.discover_candidates(
        confirmed, operation_ids(UUID("00000000-0000-0000-0000-000000000031"))
    )
    assert isinstance(outcome, HotelCandidatesReady)
    candidate_set = await repository.get(outcome.candidate_set_ref, expected_model=HotelDiscoveryArtifact)
    serialized = json.dumps(
        {
            "outcome": outcome.model_dump(mode="json"),
            "artifact": candidate_set.content.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    assert raw_query not in serialized
    assert raw_destination not in serialized
    assert raw_summary not in serialized
    assert raw_payload not in serialized
    assert '"query"' not in serialized
    assert '"destination"' not in serialized
    assert '"payload"' not in serialized
    assert '"secret"' not in serialized
    assert "sha256:summary-digest" in serialized


def test_domain_errors_keep_low_level_details_out_of_public_text() -> None:
    error = HotelInputError()
    forbidden = (
        "RAW_QUERY_SENTINEL",
        "RAW_DESTINATION_SENTINEL",
        "RAW_PROVIDER_PAYLOAD_SENTINEL",
        "SecretValue",
        "RuntimeError('backend detail')",
    )
    assert error.args == ("invalid_input",)
    assert all(value not in str(error) and value not in repr(error) for value in forbidden)


def test_final_public_service_and_port_are_exported_without_agent_symbols() -> None:
    assert hotel_selection.HotelCandidateSelectionPort is HotelCandidateSelectionPort
    assert hotel_selection.HotelCandidateSelectionService is HotelCandidateSelectionService
    assert not hasattr(hotel_selection, "HotelSelectionAgent")
