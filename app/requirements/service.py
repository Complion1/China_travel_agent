from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID, uuid4

from app.requirements.dialogue import render_clarification, render_confirmation_summary
from app.requirements.evidence import verify_evidence
from app.requirements.extraction_contract import (
    EvidenceVerified,
    ExtractionInput,
    ExtractionAttemptSucceeded,
    ExtractionRepairFeedback,
    ExtractionRepairRequired,
    RequirementExtractor,
    StrictRequirementPatchDTO,
)
from app.requirements.manual_template import ManualTemplateParseError, parse_manual_template
from app.requirements.merge import (
    MergeResult,
    RequirementMerger,
    iter_patch_leaf_paths,
    update_metadata_for_patch,
)
from app.requirements.models import (
    Clock,
    ConfirmedTravelRequirement,
    EvidenceReceipt,
    EvidenceSink,
    FieldMetadataRegistry,
    FieldStatus,
    HubType,
    ManualTemplateSubmission,
    NeedClarification,
    NeedConfirmation,
    NormalizationError,
    NormalizationRecord,
    RequirementConfirmation,
    RequirementDomainOutcome,
    RequirementDomainState,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementFieldPath,
    RequirementIntent,
    RequirementInvalidated,
    RequirementIssue,
    RequirementPatch,
    RequirementTurnInput,
    TravelBudget,
    TravelConstraints,
    TravelParty,
    TravelPreferences,
    TravelRequirement,
    TravelRooms,
    TravelTimeWindow,
    TravelTimeWindowArrival,
    TravelTimeWindowDeparture,
    VerifiedFieldEvidence,
)
from app.requirements.normalization import (
    infer_hub_type,
    normalize_budget,
    normalize_relative_date,
)


def _digest_model(model: object) -> str:
    payload = model.model_dump_json(exclude_none=False)  # type: ignore[attr-defined]
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique_issues(issues: list[RequirementIssue]) -> tuple[RequirementIssue, ...]:
    unique: dict[tuple[str, RequirementFieldPath], RequirementIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.field_path)] = issue
    return tuple(sorted(unique.values(), key=lambda item: (item.field_path.value, item.code)))


class RequirementDomainService:
    def __init__(
        self,
        *,
        extractor: RequirementExtractor,
        evidence_sink: EvidenceSink,
        clock: Clock,
    ) -> None:
        self._extractor = extractor
        self._evidence_sink = evidence_sink
        self._clock = clock
        self._merger = RequirementMerger()

    async def process_turn(self, input: RequirementTurnInput) -> RequirementDomainOutcome:
        extraction_input = ExtractionInput(
            turn_id=input.turn_id,
            user_message=input.user_message,
            session_date=input.session_date,
            timezone=input.timezone,
            current_requirement=input.current.requirement.model_copy(deep=True),
            intent=input.intent,
        )
        extracted = await self._extract_with_one_repair(extraction_input)
        if isinstance(extracted, RequirementExtractionFailed):
            return extracted
        dto, verified = extracted

        receipts = self._evidence_sink.accept(
            turn_id=input.turn_id,
            evidence=verified,
        )
        if not self._receipts_match(verified, receipts):
            return RequirementExtractionFailed(code=RequirementErrorCode.EVIDENCE_MISSING)

        try:
            patch, normalizations, pending = self._dto_to_patch(
                dto,
                verified,
                input.session_date,
            )
        except NormalizationError as exc:
            issue = RequirementIssue(
                code=RequirementErrorCode.NORMALIZATION_ERROR.value,
                field_path=self._normalization_error_path(exc),
                message="输入无法确定性规范化，请提供明确日期、时间或金额",
            )
            state = input.current.model_copy(deep=True)
            state.issues = (issue,)
            return self._need_clarification(state, ())

        state = input.current.model_copy(deep=True)
        before = state.requirement.model_copy(deep=True)
        result = self._merger.merge(state.requirement, patch)
        state.requirement = result.requirement
        validation_issues = self._merger.detect_conflicts(state.requirement)
        state.issues = _unique_issues([*result.issues, *validation_issues])
        update_metadata_for_patch(
            state.metadata,
            patch,
            input.turn_id,
            receipts=receipts,
            normalizations=normalizations,
            issues=state.issues,
            field_paths=iter_patch_leaf_paths(patch),
            derived_fields=result.derived_fields,
            pending_confirmation_fields=pending,
        )
        return self._decide_outcome(state, before, result)

    async def confirm(
        self,
        state: RequirementDomainState,
        action: RequirementConfirmation,
    ) -> RequirementDomainOutcome:
        if action.decision == "revise":
            return await self.process_turn(
                RequirementTurnInput(
                    schema_version="1.0",
                    turn_id=uuid4(),
                    user_message=action.revision_text or "",
                    session_date=self._clock.today(),
                    timezone="Asia/Shanghai",
                    intent=RequirementIntent.CONFIRMATION_REVISION,
                    current=state.model_copy(deep=True),
                )
            )

        working = state.model_copy(deep=True)
        missing = self._merger.detect_missing_fields(working.requirement)
        unresolved = [*working.issues, *self._merger.detect_conflicts(working.requirement)]
        if working.confirmation_status != "awaiting_confirmation" or missing or unresolved:
            issue = RequirementIssue(
                code=RequirementErrorCode.CONFIRMATION_NOT_READY.value,
                field_path=RequirementFieldPath.DESTINATION,
                message="需求尚未达到确认条件",
            )
            working.confirmation_status = "collecting"
            working.issues = _unique_issues([*unresolved, issue])
            return self._need_clarification(working, missing)

        working.confirmation_status = "confirmed"
        for metadata in working.metadata.metadata.values():
            if not metadata.issues:
                metadata.status = FieldStatus.CONFIRMED
        summary = render_confirmation_summary(working.requirement)
        return ConfirmedTravelRequirement(
            schema_version="1.0",
            requirement=working.requirement,
            metadata_without_raw_evidence=working.metadata,
            confirmation_summary_digest=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        )

    def process_manual_template(
        self,
        state: RequirementDomainState,
        submission: ManualTemplateSubmission,
    ) -> RequirementDomainOutcome:
        try:
            patch = parse_manual_template(submission)
        except ManualTemplateParseError as exc:
            issues = tuple(
                RequirementIssue(
                    code=RequirementErrorCode.MANUAL_TEMPLATE_ERROR.value,
                    field_path=RequirementFieldPath.FREE_TEXT_NOTES,
                    message=f"{field}: {message}",
                )
                for field, message in sorted(exc.field_errors.items())
            )
            working = state.model_copy(deep=True)
            working.issues = issues
            return self._need_clarification(working, ())

        working = state.model_copy(deep=True)
        before = working.requirement.model_copy(deep=True)
        result = self._merger.merge(working.requirement, patch)
        working.requirement = result.requirement
        working.issues = _unique_issues(
            [*result.issues, *self._merger.detect_conflicts(working.requirement)]
        )
        update_metadata_for_patch(
            working.metadata,
            patch,
            UUID(int=0),
            issues=working.issues,
            field_paths=iter_patch_leaf_paths(patch),
            derived_fields=result.derived_fields,
        )
        return self._decide_outcome(working, before, result)

    async def _extract_with_one_repair(
        self,
        request: ExtractionInput,
    ) -> (
        tuple[StrictRequirementPatchDTO, tuple[VerifiedFieldEvidence, ...]]
        | RequirementExtractionFailed
    ):
        session = self._extractor.start_session(request)
        first = await session.extract()
        if isinstance(first, ExtractionRepairRequired):
            feedback = first.feedback
        else:
            checked = self._validate_attempt(first, request)
            if isinstance(checked, EvidenceVerified):
                return first.value, checked.evidence
            feedback = checked.feedback

        repaired = await session.repair(feedback)
        if isinstance(repaired, ExtractionRepairRequired):
            return RequirementExtractionFailed(
                code=self._feedback_error_code(repaired.feedback)
            )
        checked = self._validate_attempt(repaired, request)
        if isinstance(checked, ExtractionRepairRequired):
            return RequirementExtractionFailed(
                code=self._feedback_error_code(checked.feedback)
            )
        return repaired.value, checked.evidence

    @staticmethod
    def _validate_attempt(
        attempt: ExtractionAttemptSucceeded,
        request: ExtractionInput,
    ) -> EvidenceVerified | ExtractionRepairRequired:
        expected_correction = request.intent in {
            RequirementIntent.CORRECTION,
            RequirementIntent.CONFIRMATION_REVISION,
        }
        if attempt.value.is_correction is not expected_correction:
            return ExtractionRepairRequired(
                feedback=ExtractionRepairFeedback(
                    code="intent_mismatch",
                    field_paths=(),
                )
            )
        return verify_evidence(attempt.value, request.user_message)

    @staticmethod
    def _feedback_error_code(
        feedback: ExtractionRepairFeedback,
    ) -> RequirementErrorCode:
        mapping = {
            "structured_output_invalid": RequirementErrorCode.STRICT_SCHEMA_ERROR,
            "intent_mismatch": RequirementErrorCode.INTENT_MISMATCH,
            "evidence_missing": RequirementErrorCode.EVIDENCE_MISSING,
            "evidence_duplicate": RequirementErrorCode.EVIDENCE_DUPLICATE,
            "evidence_not_substring": RequirementErrorCode.EVIDENCE_NOT_SUBSTRING,
            "orphan_evidence": RequirementErrorCode.ORPHAN_EVIDENCE,
        }
        return mapping[feedback.code]

    @staticmethod
    def _normalization_error_path(exc: NormalizationError) -> RequirementFieldPath:
        prefix = str(exc).partition(":")[0]
        try:
            return RequirementFieldPath(prefix)
        except ValueError:
            return RequirementFieldPath.FREE_TEXT_NOTES

    @staticmethod
    def _receipts_match(
        verified: tuple[VerifiedFieldEvidence, ...],
        receipts: tuple[EvidenceReceipt, ...],
    ) -> bool:
        verified_paths = [item.field_path for item in verified]
        receipt_paths = [item.field_path for item in receipts]
        return len(set(receipt_paths)) == len(receipt_paths) and sorted(
            path.value for path in verified_paths
        ) == sorted(path.value for path in receipt_paths)

    def _dto_to_patch(
        self,
        dto: StrictRequirementPatchDTO,
        verified: tuple[VerifiedFieldEvidence, ...],
        session_date,
    ) -> tuple[RequirementPatch, dict[str, NormalizationRecord], tuple[str, ...]]:
        normalizations: dict[str, NormalizationRecord] = {}
        pending: set[str] = set()
        evidence_by_path = {item.field_path: item.text for item in verified}

        def normalize_date(value: str | None, path: RequirementFieldPath) -> str | None:
            if value is None:
                return None
            try:
                normalized, approximate = normalize_relative_date(value, session_date)
            except NormalizationError as exc:
                raise NormalizationError(f"{path.value}: {exc}") from exc
            normalizations[path.value] = NormalizationRecord(
                raw_value=value,
                normalized_value=normalized,
                rule_name="relative_date" if approximate else "iso_date",
            )
            if approximate:
                pending.add(path.value)
            return normalized

        def normalize_datetime(value: str | None, path: RequirementFieldPath) -> str | None:
            if value is None:
                return None
            try:
                datetime.strptime(value, "%Y-%m-%d %H:%M")
                return value
            except ValueError:
                try:
                    date_text, time_text = value.rsplit(" ", 1)
                    normalized_date, approximate = normalize_relative_date(date_text, session_date)
                    normalized = f"{normalized_date} {time_text}"
                    datetime.strptime(normalized, "%Y-%m-%d %H:%M")
                except (ValueError, NormalizationError) as exc:
                    raise NormalizationError(f"{path.value}: invalid_datetime") from exc
                normalizations[path.value] = NormalizationRecord(
                    raw_value=value,
                    normalized_value=normalized,
                    rule_name="relative_datetime",
                )
                if approximate:
                    pending.add(path.value)
                return normalized

        destination = dto.destination
        start = normalize_date(dto.sightseeing_start_date, RequirementFieldPath.SIGHTSEEING_START_DATE)
        end = normalize_date(dto.sightseeing_end_date, RequirementFieldPath.SIGHTSEEING_END_DATE)
        arrival_datetime = normalize_datetime(dto.arrival_datetime, RequirementFieldPath.ARRIVAL_LOCAL_DATETIME)
        departure_datetime = normalize_datetime(dto.departure_datetime, RequirementFieldPath.DEPARTURE_LOCAL_DATETIME)

        arrival_type = dto.arrival_hub_type
        if arrival_type is None and dto.arrival_hub_name:
            inferred = infer_hub_type(dto.arrival_hub_name)
            arrival_type = HubType(inferred) if inferred else None
        departure_type = dto.departure_hub_type
        if departure_type is None and dto.departure_hub_name:
            inferred = infer_hub_type(dto.departure_hub_name)
            departure_type = HubType(inferred) if inferred else None

        budget_value = dto.budget_total_cny
        budget_approximate = dto.budget_is_approximate
        raw_budget = evidence_by_path.get(RequirementFieldPath.BUDGET_TOTAL_CNY)
        if budget_value is not None and raw_budget:
            try:
                parsed_budget, parsed_approximate = normalize_budget(raw_budget)
            except NormalizationError as exc:
                raise NormalizationError(
                    f"{RequirementFieldPath.BUDGET_TOTAL_CNY.value}: {exc}"
                ) from exc
            if parsed_budget != budget_value:
                raise NormalizationError(
                    f"{RequirementFieldPath.BUDGET_TOTAL_CNY.value}: "
                    "evidence_value_mismatch"
                )
            budget_approximate = bool(budget_approximate or parsed_approximate)
            normalizations[RequirementFieldPath.BUDGET_TOTAL_CNY.value] = NormalizationRecord(
                raw_value=raw_budget,
                normalized_value=str(budget_value),
                rule_name="cny_amount",
            )

        patch_data: dict[str, object] = {"is_correction": dto.is_correction}
        if destination is not None:
            patch_data["destination"] = destination

        arrival_data: dict[str, object] = {}
        if dto.arrival_hub_name is not None:
            arrival_data["hub_name"] = dto.arrival_hub_name
        if arrival_type is not None:
            arrival_data["hub_type"] = arrival_type
        if arrival_datetime is not None:
            arrival_data["local_datetime"] = arrival_datetime
        if dto.arrival_is_approximate is not None:
            arrival_data["is_approximate"] = dto.arrival_is_approximate
        departure_data: dict[str, object] = {}
        if dto.departure_hub_name is not None:
            departure_data["hub_name"] = dto.departure_hub_name
        if departure_type is not None:
            departure_data["hub_type"] = departure_type
        if departure_datetime is not None:
            departure_data["local_datetime"] = departure_datetime
        if dto.departure_is_approximate is not None:
            departure_data["is_approximate"] = dto.departure_is_approximate
        time_data: dict[str, object] = {}
        if arrival_data:
            time_data["arrival"] = TravelTimeWindowArrival(**arrival_data)
        if departure_data:
            time_data["departure"] = TravelTimeWindowDeparture(**departure_data)
        for name, value in (
            ("sightseeing_start_date", start),
            ("sightseeing_end_date", end),
            ("sightseeing_days", dto.sightseeing_days),
        ):
            if value is not None:
                time_data[name] = value
        if time_data:
            patch_data["time_window"] = TravelTimeWindow(**time_data)

        budget_data: dict[str, object] = {}
        if budget_value is not None:
            budget_data["total_cny"] = budget_value
        if budget_approximate is not None:
            budget_data["is_approximate"] = budget_approximate
        if budget_data:
            patch_data["budget"] = TravelBudget(**budget_data)

        party_data = {
            name: value
            for name, value in (
                ("adults_count", dto.adults_count),
                ("children_count", dto.children_count),
                ("children_ages", dto.children_ages),
                ("seniors_count", dto.seniors_count),
            )
            if value is not None
        }
        if party_data:
            patch_data["party"] = TravelParty(**party_data)

        room_type_requirements = dto.room_type_requirements
        if room_type_requirements == "无特殊要求":
            room_type_requirements = "NO_PREFERENCE"
            normalizations[RequirementFieldPath.ROOM_TYPE_REQUIREMENTS.value] = (
                NormalizationRecord(
                    raw_value="无特殊要求",
                    normalized_value="NO_PREFERENCE",
                    rule_name="room_type_no_preference",
                )
            )
        room_data = {
            name: value
            for name, value in (
                ("room_count", dto.room_count),
                ("room_type_requirements", room_type_requirements),
            )
            if value is not None
        }
        if room_data:
            patch_data["rooms"] = TravelRooms(**room_data)

        constraint_data = {
            name: value
            for name, value in (
                ("mobility_constraints", dto.mobility_constraints),
                ("dietary_restrictions", dto.dietary_restrictions),
            )
            if value is not None
        }
        if constraint_data:
            patch_data["constraints"] = TravelConstraints(**constraint_data)

        preference_data = {
            name: value
            for name, value in (
                ("interests", dto.interests),
                ("pace", dto.pace),
                ("agent_decides_interests", dto.agent_decides_interests),
                ("agent_decides_pace", dto.agent_decides_pace),
                ("transport_preferences", dto.transport_preferences),
                ("hotel_preferences", dto.hotel_preferences),
                ("must_visit_places", dto.must_visit_places),
                ("avoid_places", dto.avoid_places),
                ("daily_time_preferences", dto.daily_time_preferences),
                ("free_text_notes", dto.free_text_notes),
            )
            if value is not None
        }
        if preference_data:
            patch_data["preferences"] = TravelPreferences(**preference_data)

        return RequirementPatch(**patch_data), normalizations, tuple(sorted(pending))

    def _decide_outcome(
        self,
        state: RequirementDomainState,
        before: TravelRequirement,
        result: MergeResult,
    ) -> RequirementDomainOutcome:
        missing = self._merger.detect_missing_fields(state.requirement)
        needs_interests = (
            state.requirement.preferences.interests is None
            and not state.requirement.preferences.agent_decides_interests
        )
        needs_pace = (
            state.requirement.preferences.pace is None
            and not state.requirement.preferences.agent_decides_pace
        )
        if missing or state.issues or needs_interests or needs_pace:
            state.confirmation_status = "collecting"
            return self._need_clarification(
                state,
                missing,
                needs_interests=needs_interests,
                needs_pace=needs_pace,
            )

        if result.changed_fields and state.confirmation_status == "confirmed":
            return RequirementInvalidated(
                changed_fields=tuple(RequirementFieldPath(path) for path in result.changed_fields),
                before_digest=_digest_model(before),
                after_digest=_digest_model(state.requirement),
                reason="confirmed_requirement_changed",
            )

        state.confirmation_status = "awaiting_confirmation"
        return NeedConfirmation(
            requirement=state.requirement,
            metadata=state.metadata,
            summary=render_confirmation_summary(state.requirement),
        )

    @staticmethod
    def _need_clarification(
        state: RequirementDomainState,
        missing: tuple[str, ...] | list[str],
        *,
        needs_interests: bool = False,
        needs_pace: bool = False,
    ) -> NeedClarification:
        return NeedClarification(
            requirement=state.requirement,
            metadata=state.metadata,
            issues=state.issues,
            message=render_clarification(
                list(missing),
                list(state.issues),
                needs_interests,
                needs_pace,
            ),
        )
