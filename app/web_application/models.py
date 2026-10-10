"""Strict, immutable HTTP projections. Upstream state is never an HTTP DTO."""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from app.durable_execution import DurableRunView
from app.requirements import RequirementFieldPath
from app.workflow_contracts import DeliveryStatus, RunSummary, TerminalCode, WorkflowPhase

MAX_SAFE = 2**53 - 1
Nonnegative = Annotated[int, Field(ge=0, le=MAX_SAFE)]
Positive = Annotated[int, Field(ge=1, le=MAX_SAFE)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=8000)]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
UrlToken = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
ErrorCode = Literal["invalid_request", "body_too_large", "origin_rejected", "csrf_rejected",
    "not_found", "busy", "quota_exceeded", "capacity_unavailable", "dependency_unavailable",
    "request_conflict", "stale_pause", "pause_unavailable", "admission_unconfirmed",
    "recovery_unavailable", "result_not_ready", "summary_not_saved", "process_history_unavailable",
    "service_closing"]
SummaryStatus = Literal["not_finalized", "pending_save", "saved", "not_saved", "process_history_unavailable"]
Action = Literal["clarify", "confirm", "hotel", "cancel", "recover"]

def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("UTC required")
    return value

UtcDateTime = Annotated[datetime, AfterValidator(utc)]

class WebModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", revalidate_instances="always")
    schema_version: Literal["1.0"]

    @field_validator("request_id", "run_id", mode="before", check_fields=False)
    @classmethod
    def canonical_uuid(cls, value):
        if type(value) is str and value != str(UUID(value)):
            raise ValueError("canonical UUID required")
        return value

    @field_validator("visitor_ttl_seconds", mode="before", check_fields=False)
    @classmethod
    def exact_literal_integer(cls, value):
        if type(value) is not int:
            raise ValueError("integer required")
        return value

    @field_validator("realtime", mode="before", check_fields=False)
    @classmethod
    def exact_literal_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("boolean required")
        return value

def fresh(value, expected):
    if type(value) is not expected:
        raise ValueError("invalid object")
    # Preserve Python types before JSON can turn a forged bool-in-int union into 1.
    value = expected.model_validate(value.model_dump(mode="python"), strict=True)
    return expected.model_validate_json(value.model_dump_json(), strict=True)

class WebStartRequest(WebModel):
    request_id: UUID
    user_message: Text

class WebClarifyRequest(WebStartRequest):
    pause_generation: Nonnegative

class WebConfirmRequest(WebModel):
    request_id: UUID
    pause_generation: Nonnegative
    decision: Literal["accept", "revise"]
    revision_text: Text | None
    map_requested: bool
    @model_validator(mode="after")
    def validate_choice(self):
        if (self.decision == "accept" and self.revision_text is not None or
            self.decision == "revise" and (self.revision_text is None or self.map_requested)):
            raise ValueError("invalid confirmation")
        return self

class WebHotelRequest(WebModel):
    request_id: UUID
    pause_generation: Nonnegative
    decision: Literal["select", "decline"]
    ordinal: Positive | None
    @model_validator(mode="after")
    def validate_choice(self):
        if (self.decision == "select") != (self.ordinal is not None):
            raise ValueError("invalid choice")
        return self

class WebControlRequest(WebModel):
    request_id: UUID

class WebVisitorContext(WebModel):
    visitor_hash: Fingerprint
    expires_at: UtcDateTime

class WebSessionView(WebModel):
    csrf_token: UrlToken
    expires_at: UtcDateTime
    visitor_ttl_seconds: Literal[2592000]

class WebAdmissionView(WebModel):
    can_start: bool
    global_remaining: Nonnegative
    visitor_remaining: Nonnegative
    ip_remaining: Nonnegative
    reason: Literal["busy", "quota_exceeded", "capacity_unavailable", "service_closing"] | None
    retry_after_seconds: Positive | None
    @model_validator(mode="after")
    def validate_admission(self):
        if self.can_start:
            if self.reason is not None or self.retry_after_seconds is not None or min(
                    self.global_remaining, self.visitor_remaining, self.ip_remaining) < 1:
                raise ValueError("invalid admission")
        elif self.reason is None or (self.reason == "quota_exceeded") != (self.retry_after_seconds is not None):
            raise ValueError("invalid refusal")
        return self

class WebCommandReceipt(WebModel):
    request_id: UUID
    run_id: UUID | None
    status: Literal["confirming", "dispatched", "rejected", "uncertain"]
    reason: ErrorCode | None
    @model_validator(mode="after")
    def validate_receipt(self):
        if (self.status in {"rejected", "uncertain"}) != (self.reason is not None):
            raise ValueError("invalid receipt")
        return self

class WebFact(WebModel):
    label: str = Field(min_length=1, max_length=80)
    value: Text

class WebHotelChoice(WebModel):
    ordinal: Positive
    name: str = Field(min_length=1, max_length=512)
    address: str | None = Field(max_length=1024)
    facts: tuple[WebFact, ...]

class WebClarificationOption(WebModel):
    value: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=80)

class WebClarificationQuestion(WebModel):
    field: RequirementFieldPath
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["single", "multiple", "date", "datetime", "number", "ages", "text"]
    options: tuple[WebClarificationOption, ...] = Field(default=(), max_length=6)
    minimum: Annotated[int, Field(ge=0, le=100)] | None = None
    maximum: Annotated[int, Field(ge=0, le=100)] | None = None
    item_count: Annotated[int, Field(ge=1, le=100)] | None = None

    @model_validator(mode="after")
    def validate_question(self):
        if self.field.value.startswith("budget."):
            raise ValueError("budget collection deferred")
        if self.kind in {"single", "multiple"}:
            if len(self.options) < 2 or len({option.value for option in self.options}) != len(self.options):
                raise ValueError("invalid options")
        elif self.options:
            raise ValueError("unexpected options")
        if self.kind in {"number", "ages"}:
            if self.minimum is None or self.maximum is None or self.minimum > self.maximum:
                raise ValueError("invalid bounds")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("unexpected bounds")
        if (self.kind == "ages") != (self.item_count is not None):
            raise ValueError("invalid age count")
        return self

class WebInteractionCard(WebModel):
    kind: Literal["clarification", "confirmation", "hotel"]
    title: str = Field(min_length=1, max_length=128)
    paragraphs: tuple[Text, ...]
    facts: tuple[WebFact, ...]
    choices: tuple[WebHotelChoice, ...]
    questions: tuple[WebClarificationQuestion, ...] = Field(default=(), max_length=40)
    @model_validator(mode="after")
    def validate_card(self):
        if self.kind != "hotel" and self.choices:
            raise ValueError("invalid choices")
        if self.kind != "clarification" and self.questions or len({question.field for question in self.questions}) != len(self.questions):
            raise ValueError("invalid questions")
        return self

class WebInputIssue(WebModel):
    field: RequirementFieldPath
    code: Literal["hub_name_missing", "hub_name_not_specific", "hub_type_missing", "hub_type_mismatch", "hub_time_missing"]
    message: Text

    @model_validator(mode="after")
    def validate_hub_issue(self):
        parts = self.field.value.split(".")
        if len(parts) != 3 or parts[0] != "time_window" or parts[1] not in {"arrival", "departure"}:
            raise ValueError("hub field required")
        allowed = {"hub_name_missing": {"hub_name"}, "hub_name_not_specific": {"hub_name"},
            "hub_type_missing": {"hub_type"}, "hub_type_mismatch": {"hub_name", "hub_type"},
            "hub_time_missing": {"local_datetime"}}
        label = "到达" if parts[1] == "arrival" else "离开"
        messages = {
            "hub_name_missing": f"请填写{label}目的城市的具体机场或火车站名称",
            "hub_name_not_specific": f"请填写{label}目的城市的具体机场或火车站全名，不能只填写城市名",
            "hub_type_missing": f"请选择{label}枢纽类型：机场或火车站/高铁站",
            "hub_type_mismatch": f"{label}枢纽名称与类型不一致，请核对具体名称及类型",
            "hub_time_missing": f"请填写{label}目的城市的日期与时间",
        }
        if parts[2] not in allowed[self.code] or self.message != messages[self.code]:
            raise ValueError("fixed safe hub issue required")
        return self

class WebRunView(WebModel):
    run: DurableRunView
    card: WebInteractionCard | None
    actions: tuple[Action, ...]
    result_available: bool
    summary_status: SummaryStatus
    input_issues: tuple[WebInputIssue, ...] = Field(default=(), max_length=6)
    progress_phase: WorkflowPhase | None = None
    @model_validator(mode="after")
    def copy_run(self):
        object.__setattr__(self, "run", DurableRunView.model_validate_json(self.run.model_dump_json(), strict=True))
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("duplicate action")
        if self.progress_phase is not None and not (self.run.lifecycle == "open"
                and self.run.activity == "executing" and not self.run.cancel_requested
                and self.run.phase is None):
            raise ValueError("active execution observation required")
        if len({issue.field for issue in self.input_issues}) != len(self.input_issues):
            raise ValueError("duplicate input issue")
        if self.input_issues and not (self.run.lifecycle == "closed" and self.run.activity == "terminal"
                and self.run.delivery_status is DeliveryStatus.FAILED
                and self.run.terminal_code in {"requirement_failed", "workflow_contract_failed"}):
            raise ValueError("failed input task required")
        return self

class WebHistoryPage(WebModel):
    items: tuple[WebRunView, ...] = Field(max_length=20)
    next_cursor: str | None = Field(max_length=512)
    @model_validator(mode="after")
    def validate_history(self):
        if any(item.card is not None or item.input_issues for item in self.items):
            raise ValueError("history has no cards")
        return self

class WebResultDay(WebModel):
    ordinal: Positive
    markdown: str

class WebResultView(WebModel):
    result_schema_version: Literal["2.0"]
    markdown: str
    days: tuple[WebResultDay, ...]
    delivery_status: DeliveryStatus
    terminal_code: TerminalCode

class WebSummaryView(WebModel):
    status: SummaryStatus
    summary: RunSummary | None
    reason: ErrorCode | None
    @model_validator(mode="after")
    def validate_summary(self):
        if self.status == "saved":
            if self.summary is None or self.reason is not None:
                raise ValueError("saved summary required")
            object.__setattr__(self, "summary", RunSummary.model_validate_json(self.summary.model_dump_json(), strict=True))
        else:
            expected = {"not_saved": "summary_not_saved", "process_history_unavailable": "process_history_unavailable"}.get(self.status)
            if self.summary is not None or self.reason != expected:
                raise ValueError("invalid summary")
        return self

class WebSampleView(WebModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=128)
    generated_date: date
    source_kind: Literal["synthetic", "validated_result"]
    realtime: Literal[False]
    markdown: str
    @model_validator(mode="after")
    def validate_size(self):
        if len(self.markdown.encode("utf-8")) > 1048576:
            raise ValueError("sample too large")
        return self

class WebSamplesView(WebModel):
    items: tuple[WebSampleView, ...] = Field(min_length=1, max_length=5)
    @model_validator(mode="after")
    def unique_slugs(self):
        if len({item.slug for item in self.items}) != len(self.items):
            raise ValueError("duplicate sample")
        return self

MESSAGES = {
    "invalid_request": "请求格式不正确", "body_too_large": "请求内容过大",
    "origin_rejected": "请从配置的网页地址操作", "csrf_rejected": "页面凭证已失效，请刷新",
    "not_found": "任务不存在或不属于当前浏览器", "busy": "正在执行其他任务，请稍后操作",
    "quota_exceeded": "体验额度暂时用完，请查看完整样例", "capacity_unavailable": "存储容量暂不可用",
    "dependency_unavailable": "服务暂不可用，请稍后查询", "request_conflict": "请求标识已用于其他操作",
    "stale_pause": "当前等待步骤已变化，请刷新", "pause_unavailable": "无法继续，请创建新任务或查看已完成结果",
    "admission_unconfirmed": "接纳仍待核对，请保留原请求标识并查询",
    "recovery_unavailable": "当前任务不能安全恢复", "result_not_ready": "尚无完整攻略",
    "summary_not_saved": "攻略可正常查看，统计未保存", "process_history_unavailable": "本进程缺少完整运行历史",
    "service_closing": "服务正在关闭"
}

class WebApiError(WebModel):
    code: ErrorCode
    message: str
    request_id: UUID | None
    retry_after_seconds: Positive | None
    @model_validator(mode="after")
    def fixed_message(self):
        if self.message != MESSAGES[self.code]:
            raise ValueError("fixed message required")
        return self
