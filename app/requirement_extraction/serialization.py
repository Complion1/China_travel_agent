from __future__ import annotations

import json

from app.requirements import ExtractionInput, ExtractionRepairFeedback


def serialize_extract_input(request: ExtractionInput) -> str:
    payload = {
        "current_requirement": request.current_requirement.model_dump(
            mode="json", exclude_none=False
        ),
        "intent": request.intent.value,
        "session_date": request.session_date.isoformat(),
        "timezone": request.timezone,
        "user_message": request.user_message,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def serialize_repair_input(
    request: ExtractionInput,
    feedback: ExtractionRepairFeedback,
) -> str:
    payload = json.loads(serialize_extract_input(request))
    payload["repair_feedback"] = {
        "code": feedback.code,
        "field_paths": [path.value for path in feedback.field_paths],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
