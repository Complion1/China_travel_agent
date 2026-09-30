"""Small, redacted error-location events; no payload storage or replay."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from pathlib import Path
import traceback

from pydantic import BaseModel, ValidationError

LOGGER = logging.getLogger("app.error_location")
_RUN = ContextVar("error_location_run", default=None)


@contextmanager
def error_context(run_id):
    token = _RUN.set(str(run_id))
    try:
        yield
    finally:
        _RUN.reset(token)


def locations(error: BaseException) -> list[dict]:
    """Only source locations; never exception messages or local variables."""
    result = []
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 3:
        seen.add(id(error))
        for frame in traceback.extract_tb(error.__traceback__)[-3:]:
            result.append(dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name))
        error = error.__cause__ or error.__context__
    return result


def validation_details(error: ValidationError, schema: type[BaseModel]) -> list[dict]:
    fields = set()
    def visit(node):
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema.model_json_schema())
    details = []
    for item in error.errors(include_url=False, include_input=False)[:20]:
        context = item.get("ctx", {})
        detail = dict(type=item["type"], path=[p if type(p) is int or p in fields else "unknown_field" for p in item["loc"]])
        # Numeric bounds/counts are safe; values, patterns and messages are not.
        detail.update({k: v for k, v in context.items() if k in {"actual_length", "min_length", "max_length", "ge", "gt", "le", "lt"} and type(v) in {int, float}})
        if isinstance(context.get("error"), BaseException):
            detail["location"] = locations(context["error"])
        details.append(detail)
    return details


def record_error(event: str, **safe_fields) -> None:
    # Call sites supply only static codes, validated field paths and counters.
    # A broken log destination must never replace the original failure.
    try:
        LOGGER.warning(json.dumps(dict(event=event, run_id=_RUN.get(), **safe_fields), ensure_ascii=False))
    except Exception:
        pass


def record_invalid_json(arguments, *, finish_reason=None, ordinal=0):
    fields = dict(ordinal=ordinal, finish_reason=finish_reason if finish_reason in {"tool_calls", "stop", "length", "content_filter"} else "unknown")
    if isinstance(arguments, str):
        fields["argument_bytes"] = len(arguments.encode("utf-8"))
        try:
            json.loads(arguments)
        except json.JSONDecodeError as error:
            fields.update(position=error.pos, line=error.lineno, column=error.colno,
                          reason={"Extra data": "extra_data", "Expecting value": "expecting_value",
                                  "Expecting ',' delimiter": "missing_delimiter"}.get(error.msg, "invalid_json"))
    record_error("tool_json_invalid", **fields)
