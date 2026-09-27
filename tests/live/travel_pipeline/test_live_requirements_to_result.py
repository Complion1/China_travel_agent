"""One authorized live business chain; see docs/12_live_pipeline_acceptance.md.

CLI modes are explicit so pytest collection cannot consume real API calls.
Only fixed codes, public enum values, counts and hashes reach stdout.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import date
from enum import Enum
import hashlib
import json
import logging
import os
import re
import sys
import traceback
from typing import Literal
from uuid import UUID
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from langchain_core.callbacks import BaseCallbackHandler
from langchain_deepseek import ChatDeepSeek

from app.artifact_persistence import create_artifact_persistence_runtime, load_artifact_persistence_settings
from app.baidu_map import create_baidu_map_runtime, load_baidu_map_settings
from app.candidate_research import (
    CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS, CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY,
    CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, CandidateResearchCompleted,
    CandidateResearchIncomplete, CandidateResearchInitialRequest, CandidateResearchOperationPlan,
    CandidateResearchService, ResearchIterationOperationIds, CandidateResearchArtifact,
)
from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS, HOTEL_PLACE_CALL_BUDGET_KEY, HotelCandidateOperationIds,
    HotelCandidateSelectionService, HotelCandidatesReady, HotelSelectCommand, HotelSelected,
    HotelSelectionRequest, HotelSelectionArtifact,
)
from app.itinerary_planning import (
    ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS, ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_ITERATION_BUDGET_KEY, ItineraryPlanningCompleted,
    ItineraryPlanningInitialRequest, ItineraryPlanningModelActionArtifact,
    ItineraryPlanningOperationPlan, ItineraryPlanningService, ItineraryPlanningArtifact,
    PlanningIterationOperationIds, MapPlanningFailure,
)
from app.llm_runtime import DeepSeekRuntime, LLMErrorCode, LLMRuntimeError, ModelRole
from app.llm_runtime.provider import LangChainDeepSeekAdapter
from app.llm_runtime.protocols import ProviderFailure, ProviderResponse
from app.llm_runtime.settings import load_settings
from app.requirement_extraction import DeepSeekRequirementExtractor, RequirementExtractionRuntimeError
from app.requirement_subgraph import (
    create_demo_requirement_subgraph_runtime, RequirementConfirmationResume,
    RequirementResumeEnvelope, RequirementSubgraphConfirmed, RequirementSubgraphPaused,
)
from app.requirements import (
    EvidenceReceipt, HubType, InterestTag, NeedConfirmation, RequirementConfirmation,
    RequirementDomainService, RequirementDomainState, RequirementIntent, RequirementTurnInput,
    TravelPace, StrictRequirementPatchDTO,
)
from app.requirements.merge import RequirementMerger
from app.result_formatting import (
    RESULT_FORMATTING_ARTIFACT_REGISTRATIONS, ResultDayArtifact, ResultFormattingLoaded,
    ResultFormattingPrepared, ResultFormattingRequest, ResultFormattingService,
    ResultIndexArtifact, ResultIndexReady, ResultReadRequest, build_result_artifact_write_request,
)
from app.workflow_contracts import (
    ArtifactAbsent, ArtifactFound, ArtifactSchemaRegistration, ArtifactSchemaRegistry,
    ArtifactWriteRequest, DeliveryStatus, ExecutionBudgetLimit, ExecutionBudgetManagerLimits,
    ExecutionBudgetPlan, InMemoryExecutionBudgetManager, OperationId, SystemUtcClock,
    TravelSessionState, WorkflowPhase, parse_travel_session_state_json,
    serialize_travel_session_state,
)
from tests.live.travel_pipeline.checkpoints import STAGES, StageManifest, source_fingerprint

RUN_ID = UUID("12000000-0000-4000-8000-00000000d001")
TRACE_ID = UUID("12000000-0000-4000-8000-00000000d002")
TURN_ID = UUID("12000000-0000-4000-8000-00000000d003")
RAW_INPUT = """我和一位朋友计划去杭州旅行，共 2 位成年人，没有儿童和老人。

2026 年 10 月 20 日上午 09:00 到达杭州东站，10 月 22 日晚上 19:00 从杭州东站离开，均为北京时间、确定时刻。游览日期为 10 月 20 日至 22 日，共 3 天。

酒店计划 10 月 20 日入住、22 日退房，共 2 晚，需要 1 间房，偏好双床房，希望住在西湖附近。尚未预订酒店。

我们喜欢自然风光和人文景点，行程节奏选择“标准”。没有行动限制和饮食禁忌，不限定交通方式，也没有指定的必去或避开地点，没有额外作息要求。

请查询真实的酒店、景点、餐厅和交通路线，安排三天行程及适用的附近备选，最后输出中文攻略。没有可靠依据的价格和营业信息请明确标注，不要猜测。"""
COUNTS: Counter = Counter()
STAGE = "preflight"


class HarnessFailure(Exception):
    """Only locally defined fixed codes may be placed in this exception."""


def require(condition, code):
    if not condition:
        raise HarnessFailure(code)


def emit(event, **values):
    print("LIVE_EVENT " + json.dumps({"event": event, **values}, sort_keys=True, ensure_ascii=True), flush=True)


def stage(name):
    global STAGE
    STAGE = name
    emit("stage", stage=name, calls=dict(COUNTS))


class LiveContext(BaseModel):
    """Test-only persisted M04 envelope; never a production checkpoint claim."""
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"]
    run_id: UUID
    state_json: str = Field(repr=False)


def registry():
    return ArtifactSchemaRegistry((
        *HOTEL_ARTIFACT_REGISTRATIONS, *CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
        *ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS, *RESULT_FORMATTING_ARTIFACT_REGISTRATIONS,
        ArtifactSchemaRegistration(artifact_type="live_test.context", schema_version="1.0", model=LiveContext),
    ))


def op(phase, name):
    return OperationId(run_id=RUN_ID, phase=phase, logical_step=name)


def plans():
    hotel = HotelCandidateOperationIds(
        search_operation_ids=tuple(op(WorkflowPhase.HOTEL_SELECTION, f"hotel_search_{i}") for i in range(3)),
        candidate_set_operation_id=op(WorkflowPhase.HOTEL_SELECTION, "hotel_candidates"),
    )
    research = CandidateResearchOperationPlan(
        run_id=RUN_ID,
        iteration_slots=tuple(ResearchIterationOperationIds(
            invocation_slot_index=i,
            action_operation_id=op(WorkflowPhase.CANDIDATE_RESEARCH, f"research_action_{i}"),
            judgment_operation_id=op(WorkflowPhase.CANDIDATE_RESEARCH, f"research_judgment_{i}"),
            candidate_set_operation_id=op(WorkflowPhase.CANDIDATE_RESEARCH, f"research_state_{i}"),
            place_observation_operation_ids=tuple(op(WorkflowPhase.CANDIDATE_RESEARCH, f"research_place_{i}_{j}") for j in range(8)),
        ) for i in range(6)),
        terminal_successor_operation_id=op(WorkflowPhase.CANDIDATE_RESEARCH, "research_terminal"),
    )
    planning = ItineraryPlanningOperationPlan(
        run_id=RUN_ID,
        iteration_slots=tuple(PlanningIterationOperationIds(
            invocation_slot_index=i,
            action_operation_id=op(WorkflowPhase.ITINERARY_PLANNING, f"plan_action_{i}"),
            planning_state_operation_id=op(WorkflowPhase.ITINERARY_PLANNING, f"plan_state_{i}"),
        ) for i in range(6)),
        hub_geocode_operation_ids=tuple(op(WorkflowPhase.ITINERARY_PLANNING, f"plan_hub_{i}") for i in range(2)),
        matrix_operation_ids=tuple(op(WorkflowPhase.ITINERARY_PLANNING, f"plan_matrix_{i}") for i in range(18)),
        direction_operation_ids=tuple(op(WorkflowPhase.ITINERARY_PLANNING, f"plan_direction_{i}") for i in range(26)),
        terminal_action_operation_id=op(WorkflowPhase.ITINERARY_PLANNING, "plan_terminal_action"),
        terminal_successor_operation_id=op(WorkflowPhase.ITINERARY_PLANNING, "plan_terminal"),
    )
    return hotel, research, planning


def budget():
    limits = ((HOTEL_PLACE_CALL_BUDGET_KEY, 3), (CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, 6),
              (CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, 48),
              (ITINERARY_PLANNING_ITERATION_BUDGET_KEY, 6), (ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY, 26))
    return InMemoryExecutionBudgetManager(
        plan=ExecutionBudgetPlan(schema_version="1.0", run_id=RUN_ID,
                                 limits=tuple(ExecutionBudgetLimit(key=k, limit=v) for k, v in limits)),
        limits=ExecutionBudgetManagerLimits(max_operation_records=4096),
    )


class Clock:
    def today(self):
        return date(2026, 9, 26)


class EvidenceSink:
    def accept(self, *, turn_id, evidence):
        return tuple(EvidenceReceipt(field_path=e.field_path,
            evidence_ref=f"live:{turn_id}:{e.field_path.value}",
            evidence_digest=hashlib.sha256(e.text.encode("utf-8")).hexdigest()) for e in evidence)


class RealSleeper:
    def sleep(self, seconds):
        import time
        time.sleep(seconds)

    async def async_sleep(self, seconds):
        await asyncio.sleep(seconds)


class ExtractionEvents:
    def accept(self, event):
        emit("extraction_event", attempt=event.attempt_kind, outcome=event.outcome_kind,
             error_code=event.runtime_error.code.value if event.runtime_error is not None else None)


class SafeSdkDiagnostics(BaseCallbackHandler):
    """Observe the real SDK through its public callback API, never its payloads."""
    def on_llm_end(self, response, **kwargs):
        output = response.llm_output if isinstance(response.llm_output, dict) else {}
        usage = output.get("token_usage")
        safe_usage = {}
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens",
                        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                value = usage.get(key)
                safe_usage[key] = value if type(value) is int and value >= 0 else type(value).__name__
            for key in ("completion_tokens_details", "prompt_tokens_details"):
                details = usage.get(key)
                safe_usage[key + "_type"] = type(details).__name__
                if isinstance(details, dict):
                    for detail in ("reasoning_tokens", "cached_tokens"):
                        value = details.get(detail)
                        if type(value) is int and value >= 0:
                            safe_usage[key + "." + detail] = value
        generations = []
        for group in response.generations:
            for generation in group:
                message = getattr(generation, "message", None)
                info = generation.generation_info or {}
                finish = info.get("finish_reason")
                generations.append({
                    "finish_reason": finish if finish in {"stop", "length", "tool_calls", "content_filter", "insufficient_system_resource"} else "unclassified",
                    "content_type": type(getattr(message, "content", None)).__name__,
                    "usage_metadata_type": type(getattr(message, "usage_metadata", None)).__name__,
                    "metadata_type": type(getattr(message, "response_metadata", None)).__name__,
                    "invalid_tool_call_count": len(getattr(message, "invalid_tool_calls", ()) or ()),
                })
        emit("sdk_response_shape", stage=STAGE, usage_type=type(usage).__name__,
             usage=safe_usage, model_recognized=output.get("model_name") in {"deepseek-flash", "deepseek-v4-flash"},
             generations=generations)

    def on_llm_error(self, error, **kwargs):
        emit("sdk_error", stage=STAGE, error_type=type(error).__name__,
             location=[{"file": os.path.basename(frame.filename), "line": frame.lineno}
                       for frame in traceback.extract_tb(error.__traceback__)[-3:]])


def observed_sdk_factory(**kwargs):
    # Keep the actual ChatDeepSeek instance and all production request options.
    # The callback only emits allowlisted shape/count data and cannot repair output.
    return ChatDeepSeek(**kwargs, callbacks=[SafeSdkDiagnostics()])


def safe_error_code(error):
    if isinstance(error, HarnessFailure):
        return str(error)
    if isinstance(error, RequirementExtractionRuntimeError) and type(error.runtime_error) is LLMRuntimeError:
        return error.runtime_error.code.value
    return "UNEXPECTED_EXCEPTION"


class ObservedProvider:
    """Counts public provider attempts without recording prompts or responses."""
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        method = getattr(self.delegate, name)
        tracked = {"invoke_json", "invoke_tools", "invoke_tool_continuation",
                   "ainvoke_json", "ainvoke_tools", "ainvoke_tool_continuation"}
        if name not in tracked:
            return method
        if name.startswith("a"):
            async def call(*args, **kwargs):
                COUNTS["llm_" + name[1:]] += 1
                emit("provider_attempt", stage=STAGE, capability=name[1:], ordinal=COUNTS["llm_" + name[1:]])
                response = await method(*args, **kwargs)
                provider_diagnostics(response, structured=name == "ainvoke_json")
                return response
        else:
            def call(*args, **kwargs):
                COUNTS["llm_" + name] += 1
                return method(*args, **kwargs)
        return call


def provider_diagnostics(response, *, structured):
    if isinstance(response, ProviderFailure):
        safe_codes = {code.value for code in LLMErrorCode}
        emit("provider_failure", stage=STAGE,
             code=response.code if response.code in safe_codes else "unclassified",
             retryable=response.retryable, status=response.provider_status_code)
        return
    if not isinstance(response, ProviderResponse):
        return
    usage = {key: value for key, value in response.usage.items()
             if key in {"prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"}
             and type(value) is int and value >= 0}
    emit("provider_response", stage=STAGE, finish_reason=response.finish_reason,
         usage=usage, content_bytes=len((response.content or "").encode("utf-8")), tool_calls=len(response.tool_calls))
    if structured and STAGE == "requirements" and response.content is not None:
        try:
            StrictRequirementPatchDTO.model_validate_json(response.content, strict=True)
        except ValidationError as error:
            # Error messages/input/ctx and unknown key names can contain user data.
            # Keep only Pydantic error types and known top-level schema fields.
            fields = StrictRequirementPatchDTO.model_fields
            errors = error.errors(include_url=False, include_context=False, include_input=False)
            emit("extraction_schema_errors", errors=[{"type": item["type"],
                "field": item["loc"][0] if item["loc"] and item["loc"][0] in fields else "unclassified"}
                for item in errors[:40]], count=len(errors))


class ObservedMap:
    def __init__(self, delegate):
        self.delegate = delegate

    async def _call(self, name, request):
        COUNTS["map_" + name] += 1
        return await getattr(self.delegate, name)(request)

    async def geocode(self, request):
        return await self._call("geocode", request)

    async def reverse_geocode(self, request):
        return await self._call("reverse_geocode", request)

    async def search_places(self, request):
        return await self._call("search_places", request)

    async def get_place_details(self, request):
        return await self._call("get_place_details", request)

    async def calculate_route_matrix(self, request):
        return await self._call("calculate_route_matrix", request)

    async def plan_directions(self, request):
        return await self._call("plan_directions", request)

    async def get_weather(self, request):
        return await self._call("get_weather", request)


def scenario_matches(r):
    """Validate extraction against approval; never replace extracted fields."""
    t, p = r.time_window, r.preferences
    require(r.destination in {"杭州", "杭州市"}, "REQUIREMENT_DESTINATION_MISMATCH")
    for hub, moment in ((t.arrival, "2026-10-20 09:00"), (t.departure, "2026-10-22 19:00")):
        require(hub.hub_name == "杭州东站" and hub.hub_type is HubType.RAILWAY_STATION
                and hub.local_datetime == moment and not hub.is_approximate, "REQUIREMENT_HUB_MISMATCH")
    require((t.sightseeing_start_date, t.sightseeing_end_date, t.sightseeing_days,
             t.hotel_check_in_date, t.hotel_check_out_date, t.hotel_nights) ==
            ("2026-10-20", "2026-10-22", 3, "2026-10-20", "2026-10-22", 2), "REQUIREMENT_DATES_MISMATCH")
    require(r.budget.total_cny is None and not r.budget.is_approximate, "DEFERRED_BUDGET_UNEXPECTED_VALUE")
    require((r.party.adults_count, r.party.children_count, r.party.seniors_count, r.party.total_people) == (2, 0, 0, 2)
            and not r.party.children_ages, "REQUIREMENT_PARTY_MISMATCH")
    require(r.rooms.room_count == 1 and "双床" in (r.rooms.room_type_requirements or ""), "REQUIREMENT_ROOMS_MISMATCH")
    require(set(p.interests or ()) == {InterestTag.NATURE, InterestTag.CULTURE}
            and p.pace is TravelPace.NORMAL and not p.agent_decides_interests and not p.agent_decides_pace,
            "REQUIREMENT_INTERESTS_MISMATCH")
    require("西湖" in (p.hotel_preferences or "") and "附近" in p.hotel_preferences, "REQUIREMENT_HOTEL_MISMATCH")
    require(not p.must_visit_places and not p.avoid_places, "REQUIREMENT_PLACES_MISMATCH")
    none_values = {None, "无", "没有", "不限", "无要求", "无特殊要求", "无行动限制", "没有行动限制",
                   "无饮食禁忌", "没有饮食禁忌", "无饮食限制", "不限定交通方式", "不限交通方式",
                   "无额外作息要求", "没有额外作息要求", "无交通偏好", "无特殊偏好", "无偏好",
                   "没有行动限制和饮食禁忌"}
    require(all(v in none_values for v in (r.constraints.mobility_constraints,
            r.constraints.dietary_restrictions, p.transport_preferences, p.daily_time_preferences)),
            "REQUIREMENT_CONSTRAINTS_MISMATCH")


def requirement_missing_diagnostics(requirement):
    # Read-only public deterministic rules; never inspect the clarification text.
    emit("requirement_missing_fields", fields=RequirementMerger().detect_missing_fields(requirement),
         needs_interests=requirement.preferences.interests is None and not requirement.preferences.agent_decides_interests,
         needs_pace=requirement.preferences.pace is None and not requirement.preferences.agent_decides_pace)


class ObservedExtractionSession:
    def __init__(self, inner):
        self.inner = inner

    async def extract(self):
        return await self.inner.extract()

    async def repair(self, feedback):
        emit("requirement_repair_feedback", code=feedback.code,
             fields=[path.value for path in feedback.field_paths])
        return await self.inner.repair(feedback)


class ObservedExtractor:
    def __init__(self, inner):
        self.inner = inner

    def start_session(self, request):
        return ObservedExtractionSession(self.inner.start_session(request))


@contextmanager
def research_exception_diagnostics():
    """Observe swallowed input errors without changing service calls or values."""
    previous = sys.gettrace()
    observed = set()

    def trace(frame, event, arg):
        if not frame.f_code.co_filename.replace("\\", "/").startswith("/workspace/app/candidate_research/"):
            return None
        if event == "exception" and issubclass(arg[0], (ValueError, TypeError, AttributeError)):
            marker = (os.path.basename(frame.f_code.co_filename), frame.f_lineno, arg[0].__name__)
            if marker not in observed and len(observed) < 12:
                observed.add(marker)
                emit("research_exception_location", file=marker[0], line=marker[1], error_type=marker[2])
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def backup_slot_diagnostics(error):
    day_no = getattr(error, "day_no", None)
    slots = getattr(error, "slots", None)
    expected, actual = getattr(slots, "expected", None), getattr(slots, "actual", None)
    if type(day_no) is not int or not 1 <= day_no <= 64:
        return
    roles = {"attraction", "lunch", "dinner"}
    if any(type(items) is not tuple or len(items) > 3
           or any(type(item) is not str or item not in roles for item in items)
           for items in (expected, actual)):
        return
    emit("planning_backup_slots", day_no=day_no, expected=list(expected), actual=list(actual))


@contextmanager
def planning_exception_diagnostics():
    """Report bounded code locations, never exception text or local values."""
    previous = sys.gettrace()
    observed = set()
    allowed = {"_Indeterminate", "_ArtifactWriteIndeterminate", "_BusinessRejected",
               "_BackupSlotsMismatch", "_BackupSlotsRejected",
               "ValueError", "TypeError", "AttributeError", "KeyError"}

    def trace(frame, event, arg):
        if not frame.f_code.co_filename.replace("\\", "/").startswith("/workspace/app/itinerary_planning/"):
            return None
        if event == "exception" and arg[0].__name__ in allowed:
            marker = (os.path.basename(frame.f_code.co_filename), frame.f_lineno, arg[0].__name__)
            if marker not in observed and len(observed) < 32:
                observed.add(marker)
                emit("planning_exception_location", file=marker[0], line=marker[1], error_type=marker[2])
                if arg[0].__name__ == "_BackupSlotsRejected":
                    backup_slot_diagnostics(arg[1])
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def new_state(original=None, *, phase=WorkflowPhase.REQUIREMENTS, refs=()):
    return TravelSessionState(schema_version="2.0", run_id=RUN_ID, trace_id=TRACE_ID,
        phase=phase, delivery_status=DeliveryStatus.IN_PROGRESS, terminal_code=None, terminal_outcome=None,
        requirement_state=original.requirement_state if original else RequirementDomainState(),
        requirement_outcome=original.requirement_outcome if original else None, artifact_refs=refs)


def requirement_graph(runtime):
    return create_demo_requirement_subgraph_runtime(domain_service=RequirementDomainService(
        extractor=ObservedExtractor(DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=ExtractionEvents())),
        evidence_sink=EvidenceSink(), clock=Clock()))


def context_write(state):
    context = LiveContext(schema_version="1.0", run_id=RUN_ID,
                          state_json=serialize_travel_session_state(state).decode("utf-8"))
    return ArtifactWriteRequest(operation_id=op(WorkflowPhase.REQUIREMENTS, "test_context"),
        artifact_type="live_test.context", schema_version="1.0", content=context.model_dump(mode="json"))


def outcome_summary(outcome):
    result = {"type": type(outcome).__name__}
    if hasattr(outcome, "stop_reasons"):
        result["stop_reasons"] = [s.value for s in outcome.stop_reasons]
    if hasattr(outcome, "gaps"):
        result["gap_types"] = dict(Counter(type(g).__name__ for g in outcome.gaps))
    failure = getattr(outcome, "failure", None)
    if failure is not None:
        result["failure_type"] = type(failure).__name__
        code = getattr(failure, "code", None)
        if isinstance(code, Enum):
            result["failure_code"] = code.value
        for name in ("error_code", "code"):
            nested = getattr(getattr(failure, "error", failure), name, None)
            if isinstance(nested, Enum):
                result["error_code"] = nested.value
        error = getattr(failure, "error", None)
        if isinstance(error, LLMRuntimeError) and error.code is LLMErrorCode.INPUT_TOO_LARGE:
            result["actual_bytes"] = error.actual_bytes
            result["max_bytes"] = error.max_bytes
        if isinstance(failure, MapPlanningFailure):
            result["capability"] = failure.capability
            result["action_ordinal"] = failure.action_ordinal
            result["retryable"] = failure.retryable
    return result


async def planning_diagnostics(repository, plan):
    dispositions = Counter()
    tool_counts = Counter()
    for operation_id in (*[s.action_operation_id for s in plan.iteration_slots], plan.terminal_action_operation_id):
        found = await repository.lookup(operation_id=operation_id)
        if isinstance(found, ArtifactFound):
            artifact = (await repository.get(found.ref, expected_model=ItineraryPlanningModelActionArtifact)).content
            dispositions[artifact.disposition] += 1
            tool_counts.update(call.tool.value for call in artifact.call_summaries)
    emit("planning_saved_actions", dispositions=dict(dispositions), tools=dict(tool_counts))
    for index, operation_id in enumerate((*[s.planning_state_operation_id for s in plan.iteration_slots], plan.terminal_successor_operation_id)):
        found = await repository.lookup(operation_id=operation_id)
        if isinstance(found, ArtifactFound):
            artifact = (await repository.get(found.ref, expected_model=ItineraryPlanningArtifact)).content
            emit("planning_saved_state", slot=index, record_type=artifact.record.record_type,
                 stop_reasons=[reason.value for reason in getattr(artifact.record, "stop_reasons", ())])


def safe_ref(ref):
    return {"artifact_id": str(ref.artifact_id), "content_hash": ref.content_hash, "size_bytes": ref.size_bytes}


def manifest(loaded):
    def check(artifact, ref):
        prepared = registry().prepare(build_result_artifact_write_request(operation_id=ref.operation_id, artifact=artifact))
        require(prepared.content_hash == ref.content_hash and prepared.size_bytes == ref.size_bytes
                and "sha256:" + hashlib.sha256(prepared.canonical_bytes).hexdigest() == ref.content_hash,
                "CANONICAL_CONTENT_MISMATCH")
    require(len(loaded.days) == 3 and tuple(e.day_no for e in loaded.index.days) == (1, 2, 3), "THREE_DAY_INDEX_MISMATCH")
    check(loaded.index, loaded.index_ref)
    for entry, day in zip(loaded.index.days, loaded.days, strict=True):
        check(day, entry.ref)
        require(day.data.day_no == entry.day_no and day.data.date == entry.date
                and day.source_itinerary_ref == loaded.index.source_itinerary_ref, "DAY_SOURCE_MISMATCH")
    return {"run_id": str(RUN_ID), "day_count": len(loaded.days), "index": safe_ref(loaded.index_ref),
            "days": [safe_ref(e.ref) for e in loaded.index.days],
            "source_itinerary": safe_ref(loaded.index.source_itinerary_ref),
            "markdown_sha256": "sha256:" + hashlib.sha256(loaded.markdown.encode("utf-8")).hexdigest()}


async def saved(repository, phase, name, model):
    found = await repository.lookup(operation_id=op(phase, name))
    require(isinstance(found, ArtifactFound), "CHECKPOINT_ARTIFACT_MISSING")
    read = await repository.get(found.ref, expected_model=model)
    require(read.ref == found.ref, "CHECKPOINT_ARTIFACT_MISMATCH")
    return read


async def saved_context(repository):
    read = await saved(repository, WorkflowPhase.REQUIREMENTS, "test_context", LiveContext)
    state = parse_travel_session_state_json(read.content.state_json.encode("utf-8"))
    require(read.content.run_id == state.run_id == RUN_ID and state.trace_id == TRACE_ID,
            "CHECKPOINT_CONTEXT_MISMATCH")
    require(type(state.requirement_outcome).__name__ == "ConfirmedTravelRequirement", "CHECKPOINT_UNCONFIRMED")
    scenario_matches(state.requirement_outcome.requirement)
    return read.ref, state


async def checkpoint_manifest(repository, completed_stage):
    require(completed_stage in STAGES, "CHECKPOINT_STAGE_INVALID")
    context_ref, state = await saved_context(repository)
    refs = [context_ref]
    if STAGES.index(completed_stage) >= 1:
        selected = await saved(repository, WorkflowPhase.HOTEL_SELECTION, "hotel_selection", HotelSelectionArtifact)
        refs.append(selected.ref)
    if STAGES.index(completed_stage) >= 2:
        candidate = await saved(repository, WorkflowPhase.CANDIDATE_RESEARCH, "research_terminal", CandidateResearchArtifact)
        require(candidate.content.record.record_type in {"completed", "incomplete"}, "CHECKPOINT_RESEARCH_NOT_READY")
        require(candidate.content.record.state.hotel_selection_ref == refs[-1], "CHECKPOINT_HOTEL_LINK_MISMATCH")
        confirmed_digest = state.requirement_outcome.confirmation_summary_digest
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", confirmed_digest):
            confirmed_digest = "sha256:" + hashlib.sha256(json.dumps(
                state.requirement_outcome.model_dump(mode="json", exclude_none=False),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        require(candidate.content.record.state.confirmed_requirement_digest == confirmed_digest,
                "CHECKPOINT_REQUIREMENT_LINK_MISMATCH")
        refs.append(candidate.ref)
    if STAGES.index(completed_stage) >= 3:
        planned = await saved(repository, WorkflowPhase.ITINERARY_PLANNING, "plan_terminal", ItineraryPlanningArtifact)
        require(planned.content.record.record_type == "completed", "CHECKPOINT_PLANNING_NOT_COMPLETED")
        require(planned.content.directories.hotel_selection_refs == (refs[1],)
                and planned.content.directories.candidate_set_refs[-1] == refs[2], "CHECKPOINT_PLANNING_LINK_MISMATCH")
        refs.append(planned.ref)
    return StageManifest(stage=completed_stage, input_sha256=hashlib.sha256(RAW_INPUT.encode()).hexdigest(),
                         source_fingerprint=source_fingerprint(completed_stage), run_id=RUN_ID,
                         trace_id=TRACE_ID, artifacts=tuple(refs))


async def validate_checkpoint(repository):
    expected = StageManifest.model_validate_json(os.environ["CHECKPOINT_STAGE_MANIFEST"], strict=True)
    actual = await checkpoint_manifest(repository, expected.stage)
    require(actual == expected, "CHECKPOINT_MANIFEST_MISMATCH")
    require(not COUNTS, "CHECKPOINT_UNEXPECTED_EXTERNAL_CALLS")
    emit("checkpoint_reused", stage=actual.stage, source_fingerprint=actual.source_fingerprint,
         input_sha256=actual.input_sha256, refs=[safe_ref(ref) for ref in actual.artifacts])
    return actual.model_dump(mode="json")


async def checkpoint_probe(repository, *, restore=False):
    """Offline pg_dump/restore test; metadata is marked unusable for live reuse."""
    if not restore:
        await repository.put(context_write(new_state()))
    read = await saved(repository, WorkflowPhase.REQUIREMENTS, "test_context", LiveContext)
    expected_content = context_write(new_state()).content
    require(read.content.model_dump(mode="json") == expected_content, "CHECKPOINT_PROBE_CONTENT_CHANGED")
    result = StageManifest(stage="requirements", input_sha256=hashlib.sha256(RAW_INPUT.encode()).hexdigest(),
                           source_fingerprint=source_fingerprint("requirements"), run_id=RUN_ID,
                           trace_id=TRACE_ID, artifacts=(read.ref,))
    if restore:
        require(result == StageManifest.model_validate_json(os.environ["CHECKPOINT_STAGE_MANIFEST"], strict=True),
                "CHECKPOINT_PROBE_REF_CHANGED")
    require(not COUNTS, "CHECKPOINT_PROBE_UNEXPECTED_API")
    return result.model_dump(mode="json")


async def format_result(repository):
    _, state = await saved_context(repository)
    source = await saved(repository, WorkflowPhase.ITINERARY_PLANNING, "plan_terminal", ItineraryPlanningArtifact)
    require(source.content.record.record_type == "completed", "CHECKPOINT_PLANNING_NOT_COMPLETED")
    require(isinstance(await repository.lookup(operation_id=op(WorkflowPhase.OUTPUT_FORMATTING, "result_index")), ArtifactAbsent),
            "STAGE_ALREADY_EXISTS")
    stage("formatting")
    request = ResultFormattingRequest(schema_version="1.0", state=new_state(state,
        phase=WorkflowPhase.ITINERARY_PLANNING, refs=(source.ref,)), itinerary_ref=source.ref)
    formatter = ResultFormattingService(artifact_repository=repository)
    prepared = await formatter.prepare(request)
    require(isinstance(prepared, ResultFormattingPrepared) and len(prepared.days) == 3, "FORMAT_PREPARE_FAILED")
    day_refs = tuple([await repository.put(build_result_artifact_write_request(
        operation_id=op(WorkflowPhase.OUTPUT_FORMATTING, f"result_day_{day.data.day_no:02d}"), artifact=day)) for day in prepared.days])
    ready = await formatter.build_index(request, day_refs=day_refs)
    require(isinstance(ready, ResultIndexReady), "INDEX_BUILD_FAILED")
    index_ref = await repository.put(build_result_artifact_write_request(
        operation_id=op(WorkflowPhase.OUTPUT_FORMATTING, "result_index"), artifact=ready.index))
    loaded = await formatter.load(ResultReadRequest(schema_version="1.0", state=new_state(state,
        phase=WorkflowPhase.OUTPUT_FORMATTING, refs=(source.ref, index_ref)), index_ref=index_ref))
    require(isinstance(loaded, ResultFormattingLoaded), "RESULT_LOAD_FAILED")
    require(not COUNTS, "FORMATTING_UNEXPECTED_EXTERNAL_CALLS")
    return manifest(loaded)


async def live_stage(repository, name):
    require(name in (*STAGES, "formatting"), "STAGE_INVALID")
    if name == "formatting":
        return await format_result(repository)
    target = {"requirements": (WorkflowPhase.REQUIREMENTS, "test_context"),
              "hotel": (WorkflowPhase.HOTEL_SELECTION, "hotel_selection"),
              "research": (WorkflowPhase.CANDIDATE_RESEARCH, "research_terminal"),
              "planning": (WorkflowPhase.ITINERARY_PLANNING, "plan_terminal")}[name]
    require(isinstance(await repository.lookup(operation_id=op(*target)), ArtifactAbsent), "STAGE_ALREADY_EXISTS")
    hotel_plan, research_plan, planning_plan = plans()
    manager = budget()
    runtime = DeepSeekRuntime(provider=ObservedProvider(LangChainDeepSeekAdapter(load_settings(
        environ={"DEEPSEEK_API_KEY": os.environ["DEEPSEEK_API_KEY"]}))), sleeper=RealSleeper())
    try:
        stage(name)
        if name == "requirements":
            graph = requirement_graph(runtime)
            paused = await graph.start(state=new_state(), turn=RequirementTurnInput(
                turn_id=TURN_ID, user_message=RAW_INPUT, session_date=Clock().today(),
                timezone="Asia/Shanghai", intent=RequirementIntent.INITIAL, current=RequirementDomainState()))
            emit("requirement_outcome", type=type(paused).__name__,
                 outcome_type=type(paused.state.requirement_outcome).__name__,
                 issue_fields=[i.field_path.value for i in getattr(paused.state.requirement_outcome, "issues", ())],
                 issue_codes=[i.code if re.fullmatch(r"[a-z_]{1,64}", i.code) else "unclassified"
                              for i in getattr(paused.state.requirement_outcome, "issues", ())],
                 domain_code=getattr(getattr(paused.state.requirement_outcome, "code", None), "value", None))
            requirement_missing_diagnostics(paused.state.requirement_state.requirement)
            require(isinstance(paused, RequirementSubgraphPaused) and isinstance(paused.state.requirement_outcome, NeedConfirmation),
                    "REQUIREMENT_NOT_READY_FOR_CONFIRMATION")
            scenario_matches(paused.state.requirement_outcome.requirement)
            confirmed_result = await graph.resume(run_id=RUN_ID, envelope=RequirementResumeEnvelope(
                pause_token=paused.pause.pause_token, resume=RequirementConfirmationResume(
                    action=RequirementConfirmation(decision="accept", revision_text=None))))
            require(isinstance(confirmed_result, RequirementSubgraphConfirmed), "REQUIREMENT_CONFIRMATION_FAILED")
            state = confirmed_result.state
            scenario_matches(state.requirement_outcome.requirement)
            context_ref = await repository.put(context_write(state))
            emit("requirements_pass", context=safe_ref(context_ref), input_sha256=hashlib.sha256(RAW_INPUT.encode()).hexdigest())
        else:
            _, state = await saved_context(repository)
            confirmed = state.requirement_outcome
            if name != "hotel":
                selected = await saved(repository, WorkflowPhase.HOTEL_SELECTION, "hotel_selection", HotelSelectionArtifact)
            if name == "planning":
                previous = await checkpoint_manifest(repository, "research")
                candidate_ref = previous.artifacts[-1]
            map_settings = load_baidu_map_settings(environ={"BAIDU_MAPS_API_KEY": os.environ["BAIDU_MAPS_API_KEY"]})
            COUNTS["map_runtime_start"] += 1
            async with create_baidu_map_runtime(settings=map_settings, clock=SystemUtcClock()) as maps:
                adapter = ObservedMap(maps.adapter)
                if name == "hotel":
                    hotels = HotelCandidateSelectionService(adapter, repository, manager, SystemUtcClock())
                    candidates = await hotels.discover_candidates(confirmed, hotel_plan)
                    emit("hotel_outcome", **outcome_summary(candidates))
                    require(isinstance(candidates, HotelCandidatesReady) and bool(candidates.candidates), "HOTEL_CANDIDATES_UNAVAILABLE")
                    selection = await hotels.record_selection(HotelSelectionRequest(
                        selection_operation_id=op(WorkflowPhase.HOTEL_SELECTION, "hotel_selection"),
                        candidate_set_ref=candidates.candidate_set_ref,
                        command=HotelSelectCommand(selected_uid=candidates.candidates[0].uid)), active_candidate_set_ref=candidates.candidate_set_ref)
                    require(isinstance(selection, HotelSelected), "HOTEL_SELECTION_FAILED")
                    emit("hotel_pass", candidate_count=len(candidates.candidates), selection=safe_ref(selection.selection_ref))
                elif name == "research":
                    service = CandidateResearchService(runtime=runtime, map_adapter=adapter, artifact_repository=repository, budget_manager=manager)
                    try:
                        with research_exception_diagnostics():
                            researched = await service.research_initial(CandidateResearchInitialRequest(
                                confirmed=confirmed, hotel_selection_ref=selected.ref, operation_plan=research_plan))
                    finally:
                        await service.aclose()
                    emit("research_outcome", **outcome_summary(researched))
                    require(isinstance(researched, (CandidateResearchCompleted, CandidateResearchIncomplete)), "RESEARCH_FAILED")
                    emit("research_counts", eligible=researched.counts.unique_eligible_count,
                         attractions=researched.counts.attraction_count, restaurants=researched.counts.restaurant_count,
                         details=researched.coverage.details_success_count, candidate_set=safe_ref(researched.candidate_set_ref))
                else:
                    service = ItineraryPlanningService(runtime=runtime, map_adapter=adapter, artifact_repository=repository, budget_manager=manager)
                    try:
                        with planning_exception_diagnostics():
                            planned = await service.plan_initial(ItineraryPlanningInitialRequest(confirmed=confirmed,
                                hotel_selection_ref=selected.ref, candidate_set_ref=candidate_ref, operation_plan=planning_plan))
                    finally:
                        await service.aclose()
                    emit("planning_outcome", **outcome_summary(planned))
                    await planning_diagnostics(repository, planning_plan)
                    require(isinstance(planned, ItineraryPlanningCompleted), "ITINERARY_NOT_COMPLETED")
                    require(COUNTS["map_calculate_route_matrix"] > 0 and COUNTS["map_plan_directions"] > 0, "REAL_ROUTES_NOT_EXERCISED")
        return (await checkpoint_manifest(repository, name)).model_dump(mode="json")
    finally:
        await runtime.aclose()

async def verify(repository):
    stage("restart_verify")
    context_found = await repository.lookup(operation_id=op(WorkflowPhase.REQUIREMENTS, "test_context"))
    index_found = await repository.lookup(operation_id=op(WorkflowPhase.OUTPUT_FORMATTING, "result_index"))
    require(isinstance(context_found, ArtifactFound) and isinstance(index_found, ArtifactFound), "RESTART_CONTEXT_OR_INDEX_MISSING")
    context = (await repository.get(context_found.ref, expected_model=LiveContext)).content
    state = parse_travel_session_state_json(context.state_json.encode("utf-8"))
    require(context.run_id == state.run_id == RUN_ID and state.trace_id == TRACE_ID, "RESTART_CONTEXT_MISMATCH")
    scenario_matches(state.requirement_outcome.requirement)
    index = (await repository.get(index_found.ref, expected_model=ResultIndexArtifact)).content
    for entry in index.days:
        found = await repository.lookup(operation_id=op(WorkflowPhase.OUTPUT_FORMATTING, f"result_day_{entry.day_no:02d}"))
        require(isinstance(found, ArtifactFound) and found.ref == entry.ref, "RESTART_DAY_LOOKUP_MISMATCH")
        await repository.get(entry.ref, expected_model=ResultDayArtifact)
    loaded = await ResultFormattingService(artifact_repository=repository).load(ResultReadRequest(
        schema_version="1.0", state=new_state(state, phase=WorkflowPhase.OUTPUT_FORMATTING,
        refs=(index.source_itinerary_ref, index_found.ref)), index_ref=index_found.ref))
    require(isinstance(loaded, ResultFormattingLoaded), "RESTART_LOAD_FAILED")
    require(not COUNTS, "RESTART_UNEXPECTED_EXTERNAL_CALLS")
    return manifest(loaded)


def settings():
    prefix = os.environ.get("LIVE_TEST_PREFIX", "")
    require(re.fullmatch(r"china-travel-agent-m12live-[a-z0-9][a-z0-9-]{2,30}", prefix) is not None
            and os.environ.get("POSTGRES_HOST") == "postgres" and os.environ.get("POSTGRES_PORT") == "5432"
            and os.environ.get("POSTGRES_DB") == prefix.replace("-", "_") + "_main"
            and os.environ.get("POSTGRES_USER") == "m12_live_test"
            and len(os.environ.get("POSTGRES_PASSWORD", "")) >= 32, "ISOLATED_DATABASE_REQUIRED")
    return load_artifact_persistence_settings(environ={k: os.environ[k] for k in (
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")})


def selfcheck():
    plans()
    budget()
    registry().prepare(context_write(new_state()))
    # Construct the exact production dependency graph before supplier access.
    runtime = DeepSeekRuntime(provider=object(), sleeper=RealSleeper())
    requirement_graph(runtime)
    runtime.close()
    require(parse_travel_session_state_json(serialize_travel_session_state(new_state())) == new_state(), "STATE_CODEC_FAILED")
    # Public structural protocol construction must work before any live call.
    from app.baidu_map import BaiduMapAdapter
    class UncalledMap:
        async def geocode(self, request): pass
        reverse_geocode = search_places = get_place_details = calculate_route_matrix = plan_directions = get_weather = geocode
    require(isinstance(ObservedMap(UncalledMap()), BaiduMapAdapter), "MAP_PROTOCOL_MISMATCH")
    for code in LLMErrorCode:
        error = RequirementExtractionRuntimeError(LLMRuntimeError(code=code, role=ModelRole.REQUIREMENT_EXTRACTION,
            retryable=False, possible_duplicate=False))
        require(safe_error_code(error) == code.value, "SAFE_ERROR_ENUM_LOST")
    require(safe_error_code(Exception("untrusted_payload")) == "UNEXPECTED_EXCEPTION", "UNTRUSTED_ERROR_EXPOSED")
    require(not COUNTS, "SELFCHECK_EXTERNAL_CALLS")
    emit("selfcheck_pass", input_sha256=hashlib.sha256(RAW_INPUT.encode("utf-8")).hexdigest())


async def run(mode):
    persistence = await create_artifact_persistence_runtime(settings=settings(), registry=registry())
    try:
        if mode in (*STAGES, "formatting"):
            with patch("app.llm_runtime.provider.ChatDeepSeek", side_effect=observed_sdk_factory):
                return await asyncio.wait_for(live_stage(persistence.repository, mode), timeout=1800)
        if mode == "checkpoint_validate":
            return await asyncio.wait_for(validate_checkpoint(persistence.repository), timeout=120)
        if mode in {"checkpoint_probe", "checkpoint_probe_verify"}:
            return await checkpoint_probe(persistence.repository, restore=mode == "checkpoint_probe_verify")
        return await asyncio.wait_for(verify(persistence.repository), timeout=1800)
    finally:
        await persistence.close()


class SafeQualificationReport:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def pytest_collection_modifyitems(self, items):
        case_ids = [re.search(r"test_fixed_live_cases\[(\d{3})\]$", item.nodeid) for item in items]
        require(len(items) == 16 and {match[1] for match in case_ids if match} ==
                {f"{number:03d}" for number in range(1, 17)}, "QUALIFICATION_CASE_SET_MISMATCH")

    def pytest_runtest_logreport(self, report):
        match = re.search(r"test_fixed_live_cases\[(\d{3})\]$", report.nodeid)
        require(match is not None, "QUALIFICATION_CASE_ID_INVALID")
        if report.when == "call" and report.passed:
            self.passed += 1
        if report.failed:
            self.failed += 1
        if report.when == "call" or report.failed:
            # No traceback, assertion values, message, or captured output is emitted.
            field = re.search(r"field mismatch: ([a-z_]+)", str(report.longrepr)) if report.failed else None
            emit("qualification_case", case=match[1], phase=report.when, passed=report.passed,
                 field=field[1] if field and field[1] in StrictRequirementPatchDTO.model_fields else None)


def qualify():
    import pytest
    from tests.live.requirement_extraction import test_deepseek_requirement_extraction_live as cases
    from app.requirement_extraction import PROMPT_VERSION, EXTRACT_PROMPT_SHA256, REPAIR_PROMPT_SHA256

    report = SafeQualificationReport()
    stage("extraction_qualification")
    emit("qualification_identity", version=PROMPT_VERSION, extract_sha256=EXTRACT_PROMPT_SHA256,
         repair_sha256=REPAIR_PROMPT_SHA256, case_count=16)
    with patch.object(cases, "LangChainDeepSeekAdapter", side_effect=lambda settings: ObservedProvider(LangChainDeepSeekAdapter(settings))), \
            patch("app.llm_runtime.provider.ChatDeepSeek", side_effect=observed_sdk_factory):
        code = pytest.main(["-s", "-p", "no:terminal", "-p", "no:cacheprovider",
            "tests/live/requirement_extraction/test_deepseek_requirement_extraction_live.py"], plugins=[report])
    emit("qualification_summary", passed=report.passed, failed=report.failed, exit_code=int(code), calls=dict(COUNTS))
    require(code == 0 and report.passed == 16 and report.failed == 0, "QUALIFICATION_FAILED")


def main():
    logging.disable(logging.CRITICAL)
    mode = sys.argv[1] if len(sys.argv) == 2 else "invalid"
    try:
        require(mode in {"selfcheck", "verify", "qualify", "checkpoint_validate", "checkpoint_probe",
                         "checkpoint_probe_verify", *STAGES, "formatting"}, "INVALID_MODE")
        if mode == "selfcheck":
            selfcheck()
        elif mode == "qualify":
            qualify()
        else:
            emit("manifest", mode=mode, manifest=asyncio.run(run(mode)))
        emit("finished", mode=mode, status="PASS", calls=dict(COUNTS))
        return 0
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        emit("finished", mode=mode, status="FAIL", stage=STAGE,
             code=safe_error_code(error),
             exception_type=type(error).__name__, calls=dict(COUNTS),
             location=[{"file": os.path.basename(f.filename), "line": f.lineno} for f in frames[-3:]])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
