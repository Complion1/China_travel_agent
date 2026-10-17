from uuid import uuid4
import pytest
from app.frontend_experience.projection import clean, project_artifact, project_requirement
from app.hotel_selection import HOTEL_ARTIFACT_REGISTRATIONS, HotelPlaceSearchArtifact, HotelSearchSuccessEvidence, HotelSearchNoResultEvidence
from app.workflow_contracts import ArtifactWriteRequest, ArtifactRef, OperationId, WorkflowPhase
from tests.unit.hotel_selection.test_artifacts import place, UTC


def request_and_ref(artifact, artifact_type="hotel.place_search", phase=WorkflowPhase.HOTEL_SELECTION):
    request=ArtifactWriteRequest(operation_id=OperationId(run_id=uuid4(),phase=phase,logical_step="public_projection"),
        artifact_type=artifact_type,schema_version=artifact.schema_version,content=artifact.model_dump(mode="json"))
    ref=ArtifactRef(artifact_id=uuid4(),artifact_type=artifact_type,schema_version=artifact.schema_version,
        content_hash="sha256:"+"1"*64,operation_id=request.operation_id,created_at=UTC,size_bytes=1)
    return request,ref


@pytest.mark.parametrize("success",[True,False])
def test_hotel_real_model_projection(success):
    artifact=HotelPlaceSearchArtifact(schema_version="1.0",search_ordinal=1,request_hash="sha256:"+"1"*64,
        source="baidu_maps_mcp",verified=True,retrieved_at=UTC,evidence=HotelSearchSuccessEvidence(schema_version="1.0",
        outcome_type="success",places=(place(),)) if success else HotelSearchNoResultEvidence(schema_version="1.0",outcome_type="no_result"))
    request,ref=request_and_ref(artifact)
    result=project_artifact(request=request,ref=ref,registrations=HOTEL_ARTIFACT_REGISTRATIONS,secrets=())
    assert result.kind=="hotel_search" and result.status=="observed"
    public=result.model_dump_json()
    assert "request_hash" not in public and "artifact_id" not in public and "coordinate_system" not in public
    assert ("虚构地址" in public)==success
    with pytest.raises(ValueError): project_artifact(request=request,ref=ref.model_copy(update={"operation_id":OperationId(run_id=uuid4(),phase=WorkflowPhase.HOTEL_SELECTION,logical_step="wrong")}),registrations=HOTEL_ARTIFACT_REGISTRATIONS,secrets=())
    unknown=request.model_copy(update={"artifact_type":"demo.internal"})
    assert project_artifact(request=unknown,ref=ref,registrations=(),secrets=()) is None


def test_redaction_precedes_truncation_and_removes_controls():
    value=clean("private-key alice@example.com 13812345678 C:\\Users\\private\\key.json sk-123456789abcdef \x00\u202e",secrets=("private-key",))
    for hidden in ("private-key","alice@example.com","13812345678","C:\\Users","sk-123456789abcdef","\x00","\u202e"):
        assert hidden not in value
    assert clean("x"*700,secrets=())=="x"*512
    for path in ("/etc/private.conf", "/root/.env", "/opt/service/config.json", r"\\server\private\key.json",
            "/.env", "/secrets.json", "/私有/密钥.json"):
        assert path not in clean(path,secrets=())
        assert clean("文件：" + path + "，请隐藏。",secrets=()) == "文件：[已隐藏]，请隐藏。"
        assert clean("path:" + path, secrets=()) == "path:[已隐藏]"
    readable = "路线 A/B；地铁 1/2 号线；https://example.com/path"
    assert clean(readable,secrets=()) == readable


def test_rejection_only_exposes_known_reason_and_field_labels():
    from app.frontend_experience.projection import _rejection
    known = dict(_rejection("route_end_time_mismatch", "draft.days[3].route_legs[2].end_at"))
    assert known == {"校验反馈":"路线结束时间与已查询的交通时长不一致", "涉及字段":"每日交通结束时间"}
    unknown = list(_rejection("private-model-reason", "/srv/private/key.json"))
    assert unknown == [("校验反馈", "校验未接受，请等待后续处理")]


def test_registered_transport_and_candidate_states_have_readable_labels():
    from app.frontend_experience.projection import _word
    from app.itinerary_planning import TransportSemantic
    from app.candidate_research import CandidateState
    assert {_word(value) for value in TransportSemantic} == {"步行", "骑行", "公共交通", "网约车", "私家车"}
    assert _word(CandidateState.WITHHELD) == "暂缓准入"


@pytest.mark.parametrize("status", ["success", "no_result", "not_issued"])
def test_real_matrix_artifact_reports_only_saved_route_counts(status):
    from datetime import date
    from app.baidu_map import MapCapability, MatrixTravelMode
    from app.itinerary_planning import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
    from app.itinerary_planning.artifacts import (ItineraryPlanningRouteMatrixObservationArtifact,
        MapProvenanceSnapshot, CandidateLocationRefSnapshot, RouteMatrixCellSuccessSnapshot)
    operation = OperationId(run_id=uuid4(), phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="matrix")
    action = ArtifactRef(artifact_id=uuid4(), artifact_type="itinerary_planning.model_action", schema_version="6.0",
        content_hash="sha256:"+"1"*64, operation_id=operation, created_at=UTC, size_bytes=1)
    provenance = None if status=="not_issued" else MapProvenanceSnapshot(schema_version="1.0", source="baidu_maps_mcp",
        capability=MapCapability.ROUTE_MATRIX, retrieved_at_utc=UTC, verified=True)
    value = ItineraryPlanningRouteMatrixObservationArtifact(schema_version="1.0", request_digest="sha256:"+"2"*64,
        model_action_ref=action, action_ordinal=0, date=date(2026,11,1), mode=MatrixTravelMode.WALKING,
        request_fingerprint="sha256:"+"3"*64,
        ordered_locations=(CandidateLocationRefSnapshot(schema_version="1.0", ref_type="candidate",
            admission_ordinal=1, uid="private-hotel-uid"),),
        cells=(RouteMatrixCellSuccessSnapshot(schema_version="1.0", status="success", origin_index=0,
            destination_index=0, distance_m=0, duration_s=0),) if status=="success" else (),
        observation_status=status, provenance=provenance, map_failure=None)
    request, ref = request_and_ref(value, "itinerary_planning.route_matrix_observation", WorkflowPhase.ITINERARY_PLANNING)
    result = project_artifact(request=request, ref=ref, registrations=ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS, secrets=())
    assert result.kind=="route_matrix" and result.status=="observed"
    facts = dict((item.label,item.value) for item in result.facts)
    assert facts["返回路段"] == ("1" if status=="success" else "0")
    assert facts["核验成功路段"] == facts["返回路段"]
    assert "private-hotel-uid" not in result.model_dump_json() and "request_digest" not in result.model_dump_json()
