# 04 工作流合同 Vibe Coding 精确执行契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次预算接线规划key更新为planning.model_invocation=17；阶段计数归M09Artifact。M04预算/Repository/事件/摘要和state codec不新增诊断正文或SQL字段。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档状态：v1.2（已批准的需求状态连续性 maintenance 契约）
> 当前实现与验证状态：见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 权威语义：[`04_workflow_state_and_artifacts.md`](04_workflow_state_and_artifacts.md)
> 上位规范：[`00_agent_master_design.md`](00_agent_master_design.md)
> 路线图：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 共享规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)

## 1. 契约强度与执行模式

本文面向只能严格按步骤实施的编码 Agent。“必须”“精确”“固定”“禁止”“失败即停止”没有自由裁量权。权威设计规范回答语义；本文只规定实施顺序、文件白名单、测试和 Gate。两份文档不一致时立即停止，不得挑选其一。

禁止行为：

- 不得合并、跳过或重排 Task。
- 不得更换文件名、包名、公共符号、字段、签名、Enum、异常或常量。
- 不得用另一套近似实现替换 L0/L1 公共合同。
- 不得删除、弱化或绕过断言，不得 skip/xfail。
- 不得创建兼容层、双 API、反射适配或自动注册。
- 状态连续性 maintenance 必须按 `RSC-T00..RSC-T06` 执行；阶段 A 开始时允许 transition 尚不存在，阶段 B 才要求模块 01 包根三个公共类型齐全。
- 不得加入 Graph、checkpoint、数据库、MCP、模型、供应商 SDK、CLI 或 Web。
- 不得报告未实际运行的测试、Docker 或 live 结果。
- 不得创建下一阶段 Prompt、commit、push、PR、部署或合并，除非用户另行明确授权。

| 模式 | 进入条件 | 允许动作 |
|---|---|---|
| `full_rebuild` | `app/workflow_contracts` 不存在，用户明确要求按冻结文档实施 | 严格执行 WF-T00..WF-T09 |
| `maintenance` | 用户给出局部修复白名单；当前实现状态以状态页和只读核验为准 | 普通维护只执行受影响 WF Task；状态连续性修订只执行 RSC 路由 |
| `requalification_only` | 用户只要求重新验证纯领域合同 | 不改生产代码，只运行 WF-T09 指定 Gate |

首次实现只能使用 `full_rebuild`。模式不明确时停止并请求用户选择；不得自行推断为 full rebuild。

执行路由固定为：full rebuild 执行 WF-T00..WF-T09；普通 maintenance 先执行只读 WF-T00，再执行用户白名单对应 Task、其下游 Task、WF-T08、WF-T09；需求状态连续性 maintenance 执行 `RSC-T00..RSC-T06`，不重复执行无关 Artifact/Budget/Event Task；requalification_only 执行只读 WF-T00 后直接执行 WF-T09。maintenance 不得借机运行 full rebuild 或修改用户白名单外文件。

## 2. L0/L1 真源与开始条件

### 2.1 L0 不可变上位合同

按顺序完整读取：

```text
README.md
pyproject.toml
docs/00_agent_master_design.md
docs/IMPLEMENTATION_ROADMAP.md
docs/SHARED_IMPLEMENTATION_RULES.md
docs/01_requirement_and_dialogue.md
docs/04_workflow_state_and_artifacts.md
docs/04_workflow_state_and_artifacts_vibe_coding.md
app/requirements/__init__.py
app/requirements/models.py
```

### 2.2 开始条件

- 从项目工作目录执行所有命令。
- 当前必须位于用户授权的非 `main` 功能或修复分支。
- 工作区必须 clean，或 dirty 文件全部位于当前任务明确白名单且归属已确认。四份已批准设计文档允许作为 pre-existing dirty/untracked 只读基线：`docs/01_requirement_and_dialogue.md`、`docs/01_requirement_and_dialogue_vibe_coding.md`、`docs/04_workflow_state_and_artifacts.md`、`docs/04_workflow_state_and_artifacts_vibe_coding.md`；实施期间不得修改、stage 或删除它们。
- `app.workflow_contracts` 和两处模块 04 测试目录在 full rebuild 开始前不得已有未知实现；maintenance/requalification_only 则要求它们已经存在并与权威规范一致。
- full rebuild、普通 maintenance 与状态连续性阶段 B 开始时，`app.requirements` 包根必须公开 `RequirementDomainState`、`RequirementDomainOutcome` 和 `RequirementDomainTransition`。状态连续性阶段 A 开始时允许 `RequirementDomainTransition` 尚不存在，前提是 `RSC-T00` 证明这是预期旧基线。
- Python 必须满足 `>=3.12,<3.13`，Pydantic 必须是项目锁定版本。
- 不允许新增或更新依赖。

任何条件不满足都停止。不得 reset、checkout 丢弃、clean、stash 或覆盖用户改动。

## 3. 固定文件树与白名单

full rebuild 只允许创建或修改以下路径：

```text
app/workflow_contracts/__init__.py
app/workflow_contracts/errors.py
app/workflow_contracts/identifiers.py
app/workflow_contracts/providers.py
app/workflow_contracts/state.py
app/workflow_contracts/summary.py
app/workflow_contracts/artifacts/__init__.py
app/workflow_contracts/artifacts/models.py
app/workflow_contracts/artifacts/registry.py
app/workflow_contracts/artifacts/canonical_json.py
app/workflow_contracts/artifacts/repository.py
app/workflow_contracts/artifacts/in_memory.py
app/workflow_contracts/budgets/__init__.py
app/workflow_contracts/budgets/models.py
app/workflow_contracts/budgets/manager.py
app/workflow_contracts/budgets/in_memory.py
app/workflow_contracts/events/__init__.py
app/workflow_contracts/events/models.py
app/workflow_contracts/events/registry.py
app/workflow_contracts/events/sink.py
tests/unit/workflow_contracts/fakes.py
tests/unit/workflow_contracts/test_identifiers.py
tests/unit/workflow_contracts/test_providers.py
tests/unit/workflow_contracts/test_state.py
tests/unit/workflow_contracts/test_summary.py
tests/unit/workflow_contracts/test_artifact_models.py
tests/unit/workflow_contracts/test_artifact_registry.py
tests/unit/workflow_contracts/test_canonical_json.py
tests/unit/workflow_contracts/test_in_memory_artifact_repository.py
tests/unit/workflow_contracts/test_budget_models.py
tests/unit/workflow_contracts/test_in_memory_budget_manager.py
tests/unit/workflow_contracts/test_event_models.py
tests/unit/workflow_contracts/test_event_registry.py
tests/unit/workflow_contracts/test_event_sinks.py
tests/unit/workflow_contracts/test_security.py
tests/contract/workflow_contracts/test_public_api.py
tests/contract/workflow_contracts/test_artifact_repository_contract.py
tests/contract/workflow_contracts/test_execution_budget_manager_contract.py
tests/contract/workflow_contracts/test_safe_workflow_event_sink_contract.py
tests/contract/workflow_contracts/test_import_boundaries.py
tests/contract/workflow_contracts/test_requirement_state_continuity.py
```

模块 04 full rebuild 白名单禁止修改 README、pyproject、Dockerfile、现有 docs、app/requirements、app/llm_runtime、app/requirement_extraction、现有测试和白名单外路径。需求状态连续性 maintenance 必须作为相邻两阶段任务：先按模块 01 契约及其白名单修改 `app/requirements/models.py`、`app/requirements/service.py`、`app/requirements/__init__.py`、`tests/unit/requirements/test_models.py`、`tests/unit/requirements/test_requirement_service.py`，并同步修改直接调用旧返回 API 的 `tests/contract/requirement_extraction/test_requirement_extractor_contract.py`（断言 `transition.outcome` 并验证连续 state）；再修改本节模块 04 白名单中的 `state.py`、`test_state.py`、`test_security.py`、public/import contract tests 与 `test_requirement_state_continuity.py`；其他路径仍禁止。发现必须扩大范围时停止并报告文件、冲突条款、最小改动和不修改后果。

上述两阶段的精确路由不是普通 WF-T06 前置的别名，而是第 6.1 节 `RSC-T00..RSC-T06`。阶段 A 由 `RSC-T01 + RSC-T02` 组成，允许开始时模块 01 尚无 transition；阶段 B 由 `RSC-T03 + RSC-T04` 组成，只能在阶段 A PASS 后开始。代码阶段完成后必须执行 Docker `RSC-T05` 和独立 `MODULE-CLOSEOUT`；实施模型不得 stage 或 commit。

上述白名单约束受控源码和测试文件。各 Python 命令块必须先把 `PYTHONPYCACHEPREFIX` 指向已忽略的 `.pytest_cache/pycache-module04`；pytest、coverage 和 compileall 生成的临时文件只能位于已忽略的 `.pytest_cache/`，不得 stage，也不得在源码或测试树留下 `__pycache__`。

### 3.1 文件职责

| 文件 | 唯一职责 |
|---|---|
| `errors.py` | 全部公共异常，不含映射业务 |
| `identifiers.py` | Phase、受约束名称、OperationId、FieldRequirement |
| `providers.py` | 三个 Provider Protocol 与三个系统实现 |
| `state.py` | state、terminal DTO/Registry、state JSON Codec |
| `summary.py` | SafeCounterValue 与 RunSummary DTO |
| `artifacts/models.py` | Artifact 公共 DTO/outcome/limits |
| `artifacts/registry.py` | 注册、严格 prepare/decode、私有 PreparedArtifact |
| `artifacts/canonical_json.py` | 私有规范化与 SHA-256 helper |
| `artifacts/repository.py` | ArtifactRepository Protocol |
| `artifacts/in_memory.py` | 进程内参考实现 |
| `budgets/models.py` | 预算 key/plan/request/outcome/snapshot/limits |
| `budgets/manager.py` | ExecutionBudgetManager Protocol |
| `budgets/in_memory.py` | 进程内原子 consume 实现 |
| `events/models.py` | 安全事件、counter、emission outcome |
| `events/registry.py` | 事件注册、字段组合校验与投递失败码 Registry |
| `events/sink.py` | Sink Protocol 与 NoOp 实现 |
| `__init__.py` | 仅显式 re-export 与 `__all__` |

禁止创建 `utils.py`、service locator、singleton、Graph 文件、数据库 Adapter 或业务 Artifact DTO。

表中顶层 `app/workflow_contracts/__init__.py` 承担 re-export；三个子包 `__init__.py` 只写 docstring，不建立第二公共入口。

## 4. 固定公共合同清单

### 4.1 Enum 完整成员

```python
class WorkflowPhase(str, Enum):
    REQUIREMENTS = "requirements"
    HOTEL_SELECTION = "hotel_selection"
    CANDIDATE_RESEARCH = "candidate_research"
    ITINERARY_PLANNING = "itinerary_planning"
    VALIDATION = "validation"
    BUDGET_EVALUATION = "budget_evaluation"
    OUTPUT_FORMATTING = "output_formatting"

class DeliveryStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    AWAITING_USER = "awaiting_user"
    DEMO_READY = "demo_ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TerminalCategory(str, Enum):
    INPUT = "input"
    CONFIGURATION = "configuration"
    PROVIDER = "provider"
    TOOL = "tool"
    VALIDATION = "validation"
    BUDGET = "budget"
    INTERNAL = "internal"

class FieldRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"

class ExecutionBudgetScope(str, Enum):
    RUN = "run"
    PHASE = "phase"
```

不得增加 created、completed、success、error 或 future phase。

### 4.2 受约束类型

精确复制权威规范 6.3 的名称、长度和正则：`TerminalCode`、`UserActionCode`、`LogicalStep`、`ArtifactType`、`ArtifactSchemaVersion`、`ContentHash`、`ExecutionBudgetName`、`WorkflowEventType`、`SafeCounterName`、`EventDeliveryFailureCode`。禁止改成封闭 Enum。

### 4.3 DTO 字段总表

除 `ArtifactSchemaRegistration` 外，表中 DTO 使用 `ConfigDict(strict=True, frozen=True, extra="forbid")`。`ArtifactSchemaRegistration` 是不参与 JSON 的 frozen/slots dataclass descriptor。表中字段全部 required；只有写出的 discriminator 提供固定默认值。

| DTO | 精确字段 |
|---|---|
| `OperationId` | `run_id: UUID`; `phase: WorkflowPhase`; `logical_step: LogicalStep` |
| `TerminalOutcome` | `schema_version: Literal["1.0"]`; `terminal_code: TerminalCode`; `category: TerminalCategory`; `recoverable: bool`; `user_action_code: UserActionCode | None`; `trace_id: UUID` |
| `TravelSessionState` | `schema_version: Literal["2.0"]`; `run_id: UUID`; `trace_id: UUID`; `phase: WorkflowPhase`; `delivery_status: DeliveryStatus`; `terminal_code: TerminalCode | None`; `terminal_outcome: TerminalOutcome | None`; `requirement_state: RequirementDomainState`; `requirement_outcome: RequirementDomainOutcome | None`; `artifact_refs: tuple[ArtifactRef, ...]` |
| `TerminalCodeRegistration` | `terminal_code: TerminalCode`; `delivery_status: DeliveryStatus`; `outcome_requirement: FieldRequirement`; `category: TerminalCategory | None`; `recoverable: bool | None`; `user_action_codes: tuple[UserActionCode, ...]` |
| `ArtifactRef` | `artifact_id: UUID`; `artifact_type: ArtifactType`; `schema_version: ArtifactSchemaVersion`; `content_hash: ContentHash`; `operation_id: OperationId`; `created_at: datetime`; `size_bytes: int` |
| `ArtifactWriteRequest` | `operation_id: OperationId`; `artifact_type: ArtifactType`; `schema_version: ArtifactSchemaVersion`; `content: dict[str, JsonValue]` |
| `ArtifactReadResult[T]` | `ref: ArtifactRef`; `content: T` |
| `ArtifactFound` | `outcome_type: Literal["found"]="found"`; `ref: ArtifactRef` |
| `ArtifactAbsent` | `outcome_type: Literal["absent"]="absent"`; `operation_id: OperationId` |
| `ArtifactSchemaRegistration[T]` | `artifact_type: ArtifactType`; `schema_version: ArtifactSchemaVersion`; `model: type[T]` |
| `ArtifactRepositoryLimits` | `max_artifact_bytes: int`; `max_artifacts: int`; `max_total_content_bytes: int` |
| `ExecutionBudgetKey` | `scope: ExecutionBudgetScope`; `name: ExecutionBudgetName`; `phase: WorkflowPhase | None` |
| `ExecutionBudgetLimit` | `key: ExecutionBudgetKey`; `limit: int` |
| `ExecutionBudgetPlan` | `schema_version: Literal["1.0"]`; `run_id: UUID`; `limits: tuple[ExecutionBudgetLimit, ...]` |
| `ExecutionBudgetSnapshot` | `schema_version: Literal["1.0"]`; `run_id: UUID`; `key: ExecutionBudgetKey`; `limit: int`; `consumed: int`; `remaining: int` |
| `BudgetConsumeRequest` | `operation_id: OperationId`; `key: ExecutionBudgetKey`; `amount: int` |
| `BudgetConsumed` | `outcome_type: Literal["consumed"]="consumed"`; `disposition: Literal["applied", "replayed"]`; `operation_id: OperationId`; `key: ExecutionBudgetKey`; `amount: int`; `snapshot: ExecutionBudgetSnapshot` |
| `BudgetExceeded` | `outcome_type: Literal["exceeded"]="exceeded"`; `disposition: Literal["rejected", "replayed"]`; `operation_id: OperationId`; `key: ExecutionBudgetKey`; `amount: int`; `snapshot: ExecutionBudgetSnapshot` |
| `ExecutionBudgetManagerLimits` | `max_operation_records: int` |
| `SafeCounterDelta` | `name: SafeCounterName`; `delta: int` |
| `SafeWorkflowEvent` | `schema_version: Literal["1.0"]`; `event_id: UUID`; `sequence_number: int`; `occurred_at: datetime`; `run_id: UUID`; `trace_id: UUID`; `operation_id: OperationId | None`; `phase: WorkflowPhase`; `event_type: WorkflowEventType`; `counter_deltas: tuple[SafeCounterDelta, ...]`; `duration_ms: int | None`; `terminal_code: TerminalCode | None` |
| `SafeWorkflowEventRegistration` | `event_type: WorkflowEventType`; `counter_names: tuple[SafeCounterName, ...]`; `operation_id: FieldRequirement`; `duration_ms: FieldRequirement`; `terminal_code: FieldRequirement` |
| `EventEmitted` | `outcome_type: Literal["emitted"]="emitted"`; `disposition: Literal["applied", "replayed"]`; `event_id: UUID` |
| `EventDeliveryFailed` | `outcome_type: Literal["delivery_failed"]="delivery_failed"`; `event_id: UUID`; `failure_code: EventDeliveryFailureCode` |
| `SafeCounterValue` | `name: SafeCounterName`; `value: int` |
| `RunSummary` | `schema_version: Literal["1.0"]`; `run_id: UUID`; `trace_id: UUID`; `terminal_code: TerminalCode`; `duration_ms: int`; `cache_hits: int`; `event_delivery_failures: int`; `counters: tuple[SafeCounterValue, ...]`; `budget_snapshots: tuple[ExecutionBudgetSnapshot, ...]` |

完整类型、范围、nullable 和跨字段不变量只从权威规范 5–16 读取；不得在实现中另建第二套规则。TravelSessionState 字段顺序就是表中顺序，十字段全部 required；`requirement_state` non-null/no-default，初始显式为 `RequirementDomainState()`。

模块 01 的 `RequirementInvalidated` 分支精确字段顺序为 `outcome_type`、`changed_fields`、`before_digest`、`after_digest`、`reason`、`summary`。`summary` 由模块 01 service 通过确定性 renderer 对 modified state requirement 生成；模块 04 只复用 public outcome 并 strict round-trip，不导入 renderer。`NeedConfirmation` 和 `RequirementInvalidated` 的 awaiting-confirmation state 都可在模块 05 展示各自 summary 后交给模块 01 `confirm(state, action)`；04/05 不复制确认完整性规则。

Limits 固定规则：Artifact 三个字段和 Budget `max_operation_records` 均是 `int` 1..`2**53-1`，且 `max_artifact_bytes <= max_total_content_bytes`；Recording test fake 的 `max_events` 是 `int` 0..`2**53-1`。ArtifactRef/Event 的 UTC metadata 固定以 `utcoffset() == timedelta(0)` 判定；Artifact 正文中的非 UTC aware datetime 转 UTC，naive 值拒绝。

### 4.4 Protocol 完整签名

```python
class UtcClock(Protocol):
    def now(self) -> datetime: ...

class MonotonicClock(Protocol):
    def now_ns(self) -> int: ...

class UUIDFactory(Protocol):
    def new(self) -> UUID: ...

class ArtifactRepository(Protocol):
    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef: ...
    async def lookup(self, *, operation_id: OperationId) -> ArtifactLookupOutcome: ...
    async def get(
        self,
        ref: ArtifactRef,
        *,
        expected_model: type[TArtifact],
    ) -> ArtifactReadResult[TArtifact]: ...

class ExecutionBudgetManager(Protocol):
    async def consume(self, request: BudgetConsumeRequest) -> BudgetConsumeOutcome: ...
    async def get_snapshot(self, *, key: ExecutionBudgetKey) -> ExecutionBudgetSnapshot: ...
    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]: ...

class SafeWorkflowEventSink(Protocol):
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome: ...

class InMemoryArtifactRepository:
    def __init__(
        self,
        *,
        registry: ArtifactSchemaRegistry,
        utc_clock: UtcClock,
        uuid_factory: UUIDFactory,
        limits: ArtifactRepositoryLimits,
    ) -> None: ...

class InMemoryExecutionBudgetManager:
    def __init__(
        self,
        *,
        plan: ExecutionBudgetPlan,
        limits: ExecutionBudgetManagerLimits,
    ) -> None: ...

class NoOpSafeWorkflowEventSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome: ...
```

禁止同步副本、exists、list、delete、clear、reset、reserve、release、close、aclose 或 context manager。

### 4.5 Registry 与 Codec 签名

```python
class TerminalCodeRegistry:
    def __init__(self, registrations: tuple[TerminalCodeRegistration, ...]) -> None: ...
    def resolve(self, terminal_code: TerminalCode) -> TerminalCodeRegistration: ...
    def validate_state(self, state: TravelSessionState) -> None: ...

class ArtifactSchemaRegistry:
    def __init__(self, registrations: tuple[ArtifactSchemaRegistration, ...]) -> None: ...
    def resolve(
        self,
        *,
        artifact_type: ArtifactType,
        schema_version: ArtifactSchemaVersion,
    ) -> ArtifactSchemaRegistration: ...
    def prepare(self, request: ArtifactWriteRequest) -> PreparedArtifact: ...
    def decode(
        self,
        *,
        ref: ArtifactRef,
        canonical_bytes: bytes,
        expected_model: type[TArtifact],
    ) -> TArtifact: ...

class SafeWorkflowEventRegistry:
    def __init__(self, registrations: tuple[SafeWorkflowEventRegistration, ...]) -> None: ...
    def resolve(self, event_type: WorkflowEventType) -> SafeWorkflowEventRegistration: ...
    def validate(self, event: SafeWorkflowEvent) -> None: ...

class EventDeliveryFailureCodeRegistry:
    def __init__(self, codes: tuple[EventDeliveryFailureCode, ...]) -> None: ...
    def validate(self, code: EventDeliveryFailureCode) -> None: ...

def serialize_travel_session_state(state: TravelSessionState) -> bytes: ...
def parse_travel_session_state_json(data: bytes) -> TravelSessionState: ...
```

`ArtifactSchemaRegistration` 和私有 `PreparedArtifact` 使用 `@dataclass(frozen=True, slots=True)`；其他注册 DTO 按 Pydantic 共同规则实现。

### 4.6 Outcome union

必须使用 `Annotated[Union, Field(discriminator="outcome_type")]`：

```text
ArtifactLookupOutcome = ArtifactFound | ArtifactAbsent
BudgetConsumeOutcome = BudgetConsumed | BudgetExceeded
EventEmissionOutcome = EventEmitted | EventDeliveryFailed
```

Budget consumed disposition 只允许 applied/replayed；exceeded 只允许 rejected/replayed；event emitted 只允许 applied/replayed。

### 4.7 固定注册目录与限制

```text
BASE_TERMINAL_CODE_REGISTRATIONS:
  workflow_completed -> demo_ready, outcome forbidden
  cancelled_by_user -> cancelled, outcome required, input, nonrecoverable

STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS:
  workflow.started -> workflow.started; operation forbidden; duration forbidden; terminal forbidden
  workflow.resumed -> workflow.resumed; operation forbidden; duration forbidden; terminal forbidden
  workflow.replayed -> workflow.replayed; operation required; duration forbidden; terminal forbidden
  workflow.terminal -> workflow.terminal; operation forbidden; duration required; terminal required
  artifact.created -> artifact.created; operation required; duration forbidden; terminal forbidden
  artifact.reused -> artifact.cache_hit; operation required; duration forbidden; terminal forbidden
  budget.consumed -> budget.units_consumed; operation required; duration forbidden; terminal forbidden
  budget.exceeded -> budget.exceeded; operation required; duration forbidden; terminal forbidden

Event delivery failure codes:
  sink_unavailable
  sink_timeout
  sink_rejected
  sink_capacity_exceeded

STANDARD_EVENT_DELIVERY_FAILURE_CODES:
  ("sink_unavailable", "sink_timeout", "sink_rejected", "sink_capacity_exceeded")

DEMO_ARTIFACT_REPOSITORY_LIMITS:
  max_artifact_bytes = 1_048_576
  max_artifacts = 2_048
  max_total_content_bytes = 67_108_864

DEMO_EXECUTION_BUDGET_MANAGER_LIMITS:
  max_operation_records = 4_096
```

不得添加 generic Artifact type 或业务预算 key 常量。

### 4.8 公共异常完整清单

`errors.py` 必须实现权威规范 17 的继承树，名称固定为：

```text
WorkflowContractsError
WorkflowStateContractError
UnsupportedWorkflowStateVersionError
WorkflowStateValidationError
TerminalContractError
TerminalRegistryConfigurationError
UnsupportedTerminalCodeError
TerminalStateMismatchError
ArtifactRepositoryError
ArtifactRegistryConfigurationError
ArtifactRequestValidationError
UnsupportedArtifactTypeError
UnsupportedArtifactVersionError
ArtifactExpectedModelMismatchError
ArtifactNotFoundError
ArtifactConflictError
ArtifactIntegrityError
ArtifactTooLargeError
ArtifactCapacityExceededError
ArtifactRepositoryDependencyError
ArtifactRepositoryUsageError
ExecutionBudgetError
ExecutionBudgetPlanValidationError
ExecutionBudgetRequestValidationError
BudgetKeyNotConfiguredError
BudgetOperationScopeMismatchError
BudgetConsumeConflictError
ExecutionBudgetCapacityExceededError
ExecutionBudgetManagerUsageError
WorkflowReplaySafetyError
OperationResultIndeterminateError
SafeWorkflowEventError
SafeWorkflowEventRegistryConfigurationError
SafeWorkflowEventValidationError
UnsupportedWorkflowEventTypeError
EventIdentityConflictError
EventSequenceConflictError
EventSinkContractError
```

没有通用 error-code Enum。Terminal code、event delivery failure code 与异常类各自承担独立职责。禁止用 `ValueError`、`KeyError`、`None` 或 bool 穿过公共边界。

所有公共异常是零参数 marker class，`str(error)` 固定为类名，包装底层错误一律 `raise TypedError() from None`。直接 BaseModel 构造可以抛 Pydantic `ValidationError`。State、Terminal/Event registration 与 event、Repository DTO、Budget DTO、Sink outcome 按权威规范 17 执行 Python-tree strict preflight 后再 strict JSON round-trip，不能用 `validate_python(existing_instance)` 代替；ArtifactSchemaRegistration 的 class/type graph 与 Failure-code 字符串不是 JSON DTO，分别按 Registry descriptor/type-graph 和 constrained-string 规则直接复验。四个 Registry 的错误映射仍遵守权威规范 17。

### 4.9 包根 `__all__`

`app/workflow_contracts/__init__.py` 的 `__all__` 必须精确包含：

```text
WorkflowPhase, DeliveryStatus, TerminalCategory, FieldRequirement,
TerminalCode, UserActionCode, LogicalStep, OperationId,
TerminalOutcome, TravelSessionState,
TerminalCodeRegistration, TerminalCodeRegistry,
BASE_TERMINAL_CODE_REGISTRATIONS,
serialize_travel_session_state, parse_travel_session_state_json,
UtcClock, MonotonicClock, UUIDFactory,
SystemUtcClock, SystemMonotonicClock, RandomUUIDFactory,
ArtifactType, ArtifactSchemaVersion, ContentHash, JsonValue,
ArtifactRef, ArtifactWriteRequest, ArtifactReadResult,
ArtifactFound, ArtifactAbsent, ArtifactLookupOutcome,
ArtifactSchemaRegistration, ArtifactSchemaRegistry, ArtifactRepository,
ArtifactRepositoryLimits, DEMO_ARTIFACT_REPOSITORY_LIMITS,
InMemoryArtifactRepository,
ExecutionBudgetScope, ExecutionBudgetName,
ExecutionBudgetKey, ExecutionBudgetLimit, ExecutionBudgetPlan,
ExecutionBudgetSnapshot, BudgetConsumeRequest,
BudgetConsumed, BudgetExceeded, BudgetConsumeOutcome,
ExecutionBudgetManager, ExecutionBudgetManagerLimits,
DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
InMemoryExecutionBudgetManager,
WorkflowEventType, SafeCounterName, EventDeliveryFailureCode,
SafeCounterDelta, SafeWorkflowEvent,
SafeWorkflowEventRegistration, SafeWorkflowEventRegistry,
STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS,
EventDeliveryFailureCodeRegistry, STANDARD_EVENT_DELIVERY_FAILURE_CODES,
EventEmitted, EventDeliveryFailed, EventEmissionOutcome,
SafeWorkflowEventSink, NoOpSafeWorkflowEventSink,
SafeCounterValue, RunSummary,
WorkflowContractsError, WorkflowStateContractError,
UnsupportedWorkflowStateVersionError, WorkflowStateValidationError,
TerminalContractError, TerminalRegistryConfigurationError,
UnsupportedTerminalCodeError, TerminalStateMismatchError,
ArtifactRepositoryError, ArtifactRegistryConfigurationError,
ArtifactRequestValidationError, UnsupportedArtifactTypeError,
UnsupportedArtifactVersionError, ArtifactExpectedModelMismatchError,
ArtifactNotFoundError, ArtifactConflictError, ArtifactIntegrityError,
ArtifactTooLargeError, ArtifactCapacityExceededError,
ArtifactRepositoryDependencyError, ArtifactRepositoryUsageError,
ExecutionBudgetError, ExecutionBudgetPlanValidationError,
ExecutionBudgetRequestValidationError, BudgetKeyNotConfiguredError,
BudgetOperationScopeMismatchError, BudgetConsumeConflictError,
ExecutionBudgetCapacityExceededError, ExecutionBudgetManagerUsageError,
WorkflowReplaySafetyError, OperationResultIndeterminateError,
SafeWorkflowEventError, SafeWorkflowEventRegistryConfigurationError,
SafeWorkflowEventValidationError, UnsupportedWorkflowEventTypeError,
EventIdentityConflictError, EventSequenceConflictError,
EventSinkContractError
```

不得导出：

```text
PreparedArtifact
canonical JSON 私有 helper
内部 record 与 lock
RecordingSafeWorkflowEventSink
StrictSampleArtifact
TypeAdapter
```

禁止 wildcard import。合同测试必须比较实际集合与权威清单，不能只抽查三个名称。

## 5. 测试 Fake 固定行为

位置固定为 `tests/unit/workflow_contracts/fakes.py`。

```python
class FixedUtcClock:
    def __init__(self, fixed: datetime) -> None: ...
    def now(self) -> datetime: ...

class StepMonotonicClock:
    def __init__(self, values_ns: tuple[int, ...]) -> None: ...
    def now_ns(self) -> int: ...

class SequenceUUIDFactory:
    def __init__(self, values: tuple[UUID, ...]) -> None: ...
    def new(self) -> UUID: ...

class RecordingSafeWorkflowEventSink:
    def __init__(
        self,
        *,
        registry: SafeWorkflowEventRegistry,
        max_events: int,
    ) -> None: ...
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome: ...
    @property
    def events(self) -> tuple[SafeWorkflowEvent, ...]: ...
```

- Fixed clock 构造时拒绝 naive 或非 UTC 值。
- Step clock 和 UUID Factory 按 tuple 顺序返回；耗尽时抛 `AssertionError`，不回绕。
- Recording Sink 先调用 Registry，按 event ID 与 run/sequence 去重，事件读取按 run ID 字符串和 sequence 排序；NoOp 不保存去重状态且每次返回 applied。
- 相同 ID 同内容返回 replayed；冲突使用公共异常。
- `max_events` 范围是 0..安全整数上限；先处理精确 replay，再对新事件检查容量。达到测试容量后返回 `EventDeliveryFailed(..., failure_code="sink_capacity_exceeded")`，不静默删除。

`StrictSampleArtifact` 是同文件私有测试 DTO：

```python
class StrictSampleArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    label: str
    count: int
    ratio: float
    occurred_on: date
    observed_at: datetime
    note: str | None
```

测试注册键固定为 `("test.sample_artifact", "1.0")`。生产包不得导入该类型。

### 5.1 可复用合同测试 factory

不创建 `conftest.py`。三个合同测试文件各自定义并公开测试 helper：

```python
ArtifactRepositoryFactory = Callable[
    [ArtifactSchemaRegistry, UtcClock, UUIDFactory, ArtifactRepositoryLimits],
    ArtifactRepository,
]
ExecutionBudgetManagerFactory = Callable[
    [ExecutionBudgetPlan, ExecutionBudgetManagerLimits],
    ExecutionBudgetManager,
]
SafeWorkflowEventSinkFactory = Callable[
    [SafeWorkflowEventRegistry, EventDeliveryFailureCodeRegistry],
    SafeWorkflowEventSink,
]

async def assert_artifact_repository_contract(
    factory: ArtifactRepositoryFactory,
) -> None: ...

async def assert_execution_budget_manager_contract(
    factory: ExecutionBudgetManagerFactory,
) -> None: ...

async def assert_safe_workflow_event_sink_contract(
    factory: SafeWorkflowEventSinkFactory,
) -> None: ...
```

Artifact assertion helper 自行构造 strict sample Registry、固定 Clock/UUID 和每个场景所需的小容量 limits，再传给 factory；跨 Adapter helper 验证全部 Protocol 可观察语义，包括容量、validation/capacity/conflict 失败后无可观察部分记录，但不强求 Adapter 使用注入依赖生成其内部 ID。UUID collision 与 Clock/UUID dependency failure 属于 InMemory-only 单元测试，因为持久 Adapter 可使用数据库身份和时间源。其他当前合同文件用 lambda 构造 InMemory/NoOp 实现。未来 Adapter 只传入新 factory 并复用同一 assertion helper；helper 只调用 Protocol，不读取内部索引。

Sink 通用 assertion helper 只验证：调用方前置 Event Registry、outcome 可 strict 解析、event ID 一致、failure code 已注册、delivery failure 不改变业务结果。NoOp factory 可以忽略两个 Registry 构造参数。Recording fake 的 replay/identity/sequence/capacity 由 `test_event_sinks.py` 的 fake-only 测试验证，不放进通用 helper。

## 6. WF-T00——只读依赖、Git 与模式核验

### 目标

证明当前环境满足 full rebuild，且未触碰其他模块。

### 允许动作

只读命令与文件读取。禁止创建文件或分支；分支由调用本契约的用户任务预先授权。

### 执行

```powershell
Resolve-Path .
git branch --show-current
if ($LASTEXITCODE -ne 0) { throw "git branch check failed" }
git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw "git HEAD check failed" }
git status --short
if ($LASTEXITCODE -ne 0) { throw "git status check failed" }
$agentFiles = @(rg --files --hidden --no-ignore -g AGENTS.md -g AGENT.md -g '!**/.git/**' -g '!process_simulation-platform-dev/**')
$agentScan = $LASTEXITCODE
if ($agentScan -gt 1) { throw "AGENTS discovery failed" }
foreach ($agentFile in $agentFiles) {
  "===== $agentFile ====="
  Get-Content -LiteralPath $agentFile -Raw -Encoding utf8 -ErrorAction Stop
}
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -c "import sys, pydantic; assert sys.version_info[:2] == (3, 12); assert pydantic.__version__ == '2.13.4'; print(sys.version.split()[0], pydantic.__version__)"
if ($LASTEXITCODE -ne 0) { throw "Python/Pydantic version check failed" }
Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
```

全部 AGENTS/AGENT 命中文件必须逐个完整读取并按目录作用域应用；任一指令冲突或文件无法完整读取都 STOP。命令无命中时 `rg` 退出码 1 合法。

完整读取 2.1 列出的 L0 文件；再执行：

```powershell
rg -n "TravelSessionState|ArtifactRef|ArtifactRepository|OperationId|ExecutionBudget|SafeWorkflowEvent|RunSummary" app tests
$implementationScan = $LASTEXITCODE
if ($implementationScan -gt 1) { throw "implementation symbol scan failed" }
rg -n "TravelSessionState|ArtifactRef|ArtifactRepository|OperationId|ExecutionBudget|SafeWorkflowEvent|RunSummary" docs
if ($LASTEXITCODE -gt 1) { throw "documentation symbol scan failed" }
```

`app tests` 的命中用于判断是否存在未知实现；`docs` 命中必然包含当前两份规范，只用于人工核对上位合同，不触发“发现同名实现”。full rebuild 期望 implementation scan 退出码为 1；maintenance/requalification_only 期望有命中并逐项确认属于已验收模块 04。

### PASS

- 分支是用户授权的非 main 功能或修复分支。
- 工作区归属明确。
- 模块 01 包根符号存在。
- full rebuild 时模块 04 未实现；maintenance/requalification_only 时模块 04 已实现且用户任务模式明确。
- 没有必须修改上位模块的冲突。

### STOP

任一开始条件不满足；发现上位合同冲突；发现白名单外 dirty 文件归属不明。模式专属停止条件：full rebuild 发现任意模块 04 实现即 STOP；maintenance/requalification_only 仅在模块 04 实现缺失、来源未知、与当前 HEAD 验收事实无法对应或不符合权威规范时 STOP，不能因为预期中的已验收同名实现而停止。

## 6.1 需求状态连续性 maintenance：`RSC-T00..RSC-T06`

本节是模块 01/03/04 连续性修订的唯一可执行路由。不得要求阶段 A 开始时 transition 已存在；不得重新实施或修改无关 Artifact、Budget、Event；每个未运行 Task 必须报告 `NOT_RUN` 与阻断前置，不能写 PASS 或复用历史结果。

### `RSC-T00`：Git、引用、旧基线和白名单核验

- 前置：用户明确授权现有非 `main` 分支和本连续性 maintenance。
- 精确文件白名单：无；只读 Git、权威文档、状态页、Dockerfile、模块 01/03/04 代码与指定测试。
- 禁止：修改文件、分支操作、stage/commit、Docker/live、其他项目。
- 命令：`git branch --show-current`、`git rev-parse HEAD`、`git status --short`、`git diff --cached --name-only`；再用 `rg` 和完整读取核验引用、符号、签名与文件存在性。
- 允许的旧基线：`RequirementDomainTransition` 尚不存在；模块 01 三个入口仍返回裸 outcome；模块 04 仍为 v1 九字段。必须证明它们是状态页/Git 可解释的预期旧实现，而非来源未知文件。
- PASS：分支、HEAD、dirty/staged、白名单和旧基线证据齐全，所有引用存在。
- STOP：未知 dirty、白名单不足、已批准合同冲突、需要模块 02/03 生产变更或需要修改模块 04 Artifact/Budget/Event。
- NOT_RUN：本项是首个必需 Task，不得标记 `NOT_RUN`；无法执行只读核验时写 `RSC-T00 FAIL` 并停止。

### `RSC-T01`：模块 01 transition DTO、service 与测试

- 前置：`RSC-T00 PASS`。
- 精确文件白名单：`app/requirements/models.py`、`app/requirements/service.py`、`app/requirements/__init__.py`、`tests/unit/requirements/test_models.py`、`tests/unit/requirements/test_requirement_service.py`。
- 禁止：其他生产/测试文件、设计/Vibe、README、状态页、Dockerfile、依赖与配置。
- 实施：public transition 和包根导出；三个入口只返回 transition；invalidation 六字段含 renderer-exact summary；state-based confirm；8,000 service preflight 与 8,001 fresh failed transition；深层解除引用和异常原样冒泡。
- 命令：与 `RSC-T02` 同组完成后执行 `python -m pytest -q -p no:cacheprovider tests/unit/requirements/test_models.py tests/unit/requirements/test_requirement_service.py`。宿主机结果只作快速反馈。
- PASS：三入口 transition、summary、accept 和两个超限分支的定向行为测试通过，零 skip/xfail。
- STOP：需要 outcome 参数验证 confirm、修改 renderer/merge/extraction contract、模块 03 生产代码或白名单外文件；8,001 仍被公共 DTO 拒绝。
- NOT_RUN：仅当 `RSC-T00` 未通过时报告 `NOT_RUN（blocked by RSC-T00）`。

### `RSC-T02`：模块 03 直接消费合同测试同步

- 前置：`RSC-T01` 实现完成且 transition 可从 `app.requirements` 导入。
- 精确文件白名单：`tests/contract/requirement_extraction/test_requirement_extractor_contract.py`。
- 禁止：`app/requirement_extraction/**`、Prompt、模块 02、其他测试和文档。
- 实施：旧 service outcome 断言改为 `transition.outcome`，同时验证 `transition.state` 连续消费；adapter Session/Operation/repair 语义不变。
- 命令：`python -m pytest -q -p no:cacheprovider tests/contract/requirement_extraction/test_requirement_extractor_contract.py`。
- PASS：直接消费合同与 `RSC-T01` 定向测试均通过；阶段 A 结束时包根三类型公开、模块 01 三入口返回 transition。
- STOP：必须改变 adapter/Prompt/runtime 生产语义、live case 或两次调用预算。
- NOT_RUN：仅当 `RSC-T01` 未完成或未通过时报告 `NOT_RUN（blocked by RSC-T01）`。

### `RSC-T03`：模块 04 state v2、Codec 与测试

- 前置：`RSC-T01`、`RSC-T02 PASS`；`RequirementDomainState`、`RequirementDomainOutcome`、`RequirementDomainTransition` 均从模块 01 包根公开。
- 精确文件白名单：`app/workflow_contracts/state.py`、`tests/unit/workflow_contracts/test_state.py`、`tests/unit/workflow_contracts/test_security.py`。
- 禁止：模块 04 Artifact/Budget/Event/summary/providers/identifiers/errors、模块 01/03、设计文档和白名单外测试。
- 实施：v2 十字段、authoritative state+routing outcome、public transition 复验、v1/unknown 拒绝且无迁移、Codec 错误优先级、fresh copy、repr 安全，以及 invalidation summary strict round-trip。
- 命令：与 `RSC-T04` 同组完成后执行 `python -m pytest -q -p no:cacheprovider tests/unit/workflow_contracts/test_state.py tests/unit/workflow_contracts/test_security.py tests/contract/workflow_contracts/test_requirement_state_continuity.py tests/contract/workflow_contracts/test_public_api.py tests/contract/workflow_contracts/test_import_boundaries.py`。
- PASS：字段顺序/required/non-null、五分支、连续性、v1 拒绝、错误顺序和 mutation/security canary 全通过。
- STOP：需要复制模块 01 五分支、导入 renderer/私有文件、增加 v1 迁移或修改无关模块 04 子系统。
- NOT_RUN：仅当阶段 A（`RSC-T01 + RSC-T02`）未通过时报告 `NOT_RUN（blocked by RSC-T01/RSC-T02）`。

### `RSC-T04`：模块 01/04 连续性和 public/import boundary

- 前置：`RSC-T03` 实现完成。
- 精确文件白名单：`app/workflow_contracts/__init__.py`、`tests/contract/workflow_contracts/test_requirement_state_continuity.py`、`tests/contract/workflow_contracts/test_public_api.py`、`tests/contract/workflow_contracts/test_import_boundaries.py`。
- 禁止：除 `RSC-T03` 同组文件外的其他生产/测试文件；禁止 Artifact/Budget/Event 变更和模块 05 文件。
- 实施：包根与 `__all__`；只从模块 01 包根导入 State/Outcome/Transition；验证 current state → module 01 transition → v2 JSON → next input current；04 不验证 renderer 语义、不复制 state-based confirm。
- 命令：执行 `RSC-T03` 同组命令；再以 `/tmp` 或忽略目录为 `PYTHONPYCACHEPREFIX` 执行 `python -m compileall -q app/requirements app/workflow_contracts`。
- PASS：阶段 B 全部定向测试、compileall、public/import boundary 通过，源码树无临时文件。
- STOP：需要扩大 public API、修改模块 05、从模块 01 私有文件导入或复制确认规则。
- NOT_RUN：仅当 `RSC-T03` 未完成或未通过时报告 `NOT_RUN（blocked by RSC-T03）`。

### `RSC-T05`：coverage、Docker 与模块 01～04 完整回归

- 前置：`RSC-T00..RSC-T04 PASS`；冻结设计/Vibe 在代码实施中只读；受控变更只在上述白名单。
- 精确文件白名单：无受控文件写入；仅本任务 Docker 镜像、构建缓存和 `--rm` 容器内 `/tmp`。
- 禁止：宿主机 Python 作为验收证据、live/Secret、网络化测试容器、复用旧容器、修改容器源码、其他项目 Docker 资源。
- 命令：完整执行第 15 节 Gate E；从当前源码重建 runtime/test 镜像，用新 `--rm --network none --read-only --tmpfs /tmp` 容器核验版本、模块 01/03/04 定向、模块 01/04 各自 line/branch/combined、compileall、模块 01～04 全 unit/contract 回归和残留容器。
- PASS：全部命令零退出；两模块三项 coverage 分别 `>=95%`；测试收集非零且 skip/xfail 为 0；无验收容器残留。
- STOP：旧镜像、非安全容器参数、coverage 分母为 0/阈值不足、测试为 0、skip/xfail、命令非零或 Docker 资源归属不明。
- NOT_RUN：仅当 `RSC-T00..RSC-T04` 未全部通过时报告 `NOT_RUN`，并列出第一个未通过的前置 Task。

### `RSC-T06`：MODULE-CLOSEOUT

- 前置：`RSC-T05 PASS`，模块 05 继续暂停。
- 精确文件白名单：`README.md`、`docs/IMPLEMENTATION_STATUS.md`、`Dockerfile`；四份模块 01/04 权威设计/Vibe 只读。
- 禁止：其他文件、stage、commit、push、PR、deploy、模块 05 文档/代码/测试。
- 实施：README 同步已完成模块、生产包、实际测试数量、Docker 说明；状态页同步日期、待审 HEAD、模块 01/04 状态、实际命令/测试/三项 coverage；检查 Dockerfile 的 COPY、默认 runtime 自检和构建内容。无需修改 Dockerfile 时必须引用当前内容给出具体理由，禁止只写 `N/A`。
- 命令：文档同步后重新构建 runtime/test 镜像；用安全新容器运行 runtime 默认 CMD 和模块 01～04 完整 unit/contract 回归；最后检查无验收容器残留。
- PASS：closeout 真值与命令一致；重建、默认 CMD、回归、残留检查通过；设计/Vibe 未改；staged 为空。
- STOP：结果无法证明、Dockerfile 影响未检查、需要改变设计、准备开始模块 05，或实施模型准备 stage/commit。
- NOT_RUN：仅当 `RSC-T05` 未通过时报告 `NOT_RUN（blocked by RSC-T05）`；不得把未收尾的实现报告为完成。

`RSC-T06` 完成后固定停止，等待独立审查。只有独立审查通过且用户另行明确授权，审查者才可以提交。

## 7. WF-T01——错误、标识与 Provider 基础

### 前置

WF-T00 PASS。

### 允许文件

```text
app/workflow_contracts/__init__.py
app/workflow_contracts/errors.py
app/workflow_contracts/identifiers.py
app/workflow_contracts/providers.py
tests/unit/workflow_contracts/fakes.py
tests/unit/workflow_contracts/test_identifiers.py
tests/unit/workflow_contracts/test_providers.py
```

### 实现顺序

1. 创建顶层 `__init__.py`，当前只含 docstring；完整 re-export 只能在 WF-T08 写入。
2. 按 4.8 创建异常继承树；每个异常是零参数 marker class，`str(error)` 固定为类名，公开边界包装一律 `raise ... from None`。
3. 按 4.1/4.2 创建 Enum、受约束类型、OperationId 与 FieldRequirement。
4. `OperationId.safe_display()` 返回 `{run_uuid}:{phase_value}:{logical_step}`；禁止覆盖 `__str__`。
5. 实现三个 Protocol 和三个系统 Provider；禁止默认 singleton。
6. 添加 Fixed/Step/Sequence fakes 和本 Task 单元测试。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q `
  tests/unit/workflow_contracts/test_identifiers.py `
  tests/unit/workflow_contracts/test_providers.py
if ($LASTEXITCODE -ne 0) { throw "WF-T01 pytest failed" }
```

### PASS

全部测试通过；Enum/约束/安全显示与 Provider fake 行为精确；异常零参数且不含动态输入。

### STOP

基础符号需要依赖 Artifact、Budget、Event 或模块 02+；任何异常需要携带正文；Provider 需要读取环境或创建 singleton。

## 8. WF-T02——Artifact 模型、Registry 与规范 JSON

### 前置

WF-T01 PASS。

### 允许文件

```text
app/workflow_contracts/artifacts/__init__.py
app/workflow_contracts/artifacts/models.py
app/workflow_contracts/artifacts/registry.py
app/workflow_contracts/artifacts/canonical_json.py
tests/unit/workflow_contracts/fakes.py
tests/unit/workflow_contracts/test_artifact_models.py
tests/unit/workflow_contracts/test_artifact_registry.py
tests/unit/workflow_contracts/test_canonical_json.py
```

### 实现顺序

1. 使用 Python 3.12 recursive type alias 创建 `JsonValue`。
2. 创建 Ref、write/read DTO、lookup outcome 与 limits。
3. ArtifactRef validator 以 `utcoffset() == timedelta(0)` 判定 UTC，拒绝 naive/non-UTC created_at 和非正 size；limits 使用 1..安全整数上限并要求 per-artifact 不大于 total。
4. 实现 surrogate、safe int、finite float、negative zero、date/datetime/Enum 规范化。
5. 使用唯一固定 `json.dumps` 参数生成 bytes、hash 与 size。
6. 创建 frozen slots registration 和 private PreparedArtifact。
7. Registry 构造阶段递归检查注册模型可达的完整 Pydantic 类型图：key 唯一、每个模型非 RootModel、`dict(model.model_config)` 精确等于 `{"strict": True, "frozen": True, "extra": "forbid"}`、所有字段 required，并拒绝 alias、computed field、field/model serializer；循环类型图按类型去重。字段只允许 JSON scalar、date、带时区 datetime、UUID、JSON-scalar Enum、合规模型及其 nullable/union/list/tuple/`dict[str, ...]` 组合，其他类型在构造时失败。`Annotated[T, ...]` 递归检查 T，保留原生 constraint/discriminator，拒绝 Before/Plain/Wrap validator、functional serializer 和未知 callable metadata；AfterValidator 与类 validator 只能断言。`Literal` 只接受 JSON scalar 值。
8. `prepare` 先保留完整 request 的 `mode="python"` tree，对该 tree strict `validate_python`，再对 fresh request 做 strict JSON round-trip；任一步失败都包装为 `ArtifactRequestValidationError`。随后才对 content 执行注册模型的 strict `validate_json`。按 annotation 逐路径比较 Python preflight 前保留且已证明符合 JsonValue 的原始 content tree 与 typed DTO：object keys、array 长度、普通 leaf kind/value 必须相同；Literal 同 kind/value；Enum 先取 typed `.value` 再与原始 leaf 比较 kind/value；date 精确 ISO；UUID 解析值相同；datetime 表示同一瞬间；所有嵌套递归。任何 validator 导致的路径、类型或值变化都拒绝。通过后执行 `model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)`，最后按 Enum → UUID → datetime → date → None → bool → int → float → str → list/tuple → dict 的固定优先级做 typed 规范化。
9. `decode` 固定执行 strict Ref copy → type/version resolve → expected model identity → canonical bytes type → hash/size → strict decode → recanonical byte comparison；按权威规范 17 映射异常。
10. 同一输入重复 prepare 必须产生相同 canonical bytes/hash；测试 DTO validator 不读取时间、随机、环境或全局状态。
11. 添加规范向量和 Registry 测试。

### 固定规范向量

至少断言：key 顺序、中文 UTF-8、NFC/NFD 不合并、isolated surrogate 拒绝、None 保留、bool/int 区分、安全整数上下界、越界整数、NaN/Infinity、`-0.0 -> 0.0`、int 输入不能进入 float 字段、1/1.0 区分、list/tuple 顺序与 array 输出、tuple 内 datetime、str/int Enum 分派、普通数值 Enum 与 IntEnum、UUID、datetime 优先于 date、UTC Z、非 UTC aware content datetime 转 UTC、naive datetime 拒绝、Annotated/Literal、嵌套合规模型、自递归类型图终止、任意深度嵌套违规模型拒绝、transforming validator 拒绝、无 BOM/空格/换行、alias/computed/serializer/default field/附加行为 config 注册失败。还必须用 `model_construct()` 和构造后 content mutation 分别注入 datetime、UUID、bytes、tuple、set、任意 object，断言在 Python preflight 以 `ArtifactRequestValidationError` 失败且没有记录。1.0 不另设公开嵌套深度；Python/Pydantic 递归错误统一包装为 `ArtifactRequestValidationError`。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q `
  tests/unit/workflow_contracts/test_artifact_models.py `
  tests/unit/workflow_contracts/test_artifact_registry.py `
  tests/unit/workflow_contracts/test_canonical_json.py
if ($LASTEXITCODE -ne 0) { throw "WF-T02 pytest failed" }
```

### PASS

所有向量 byte-for-byte 一致；hash 带 `sha256:`；Registry 不接受宽松数字/字符串转换；正文不出现在 repr 或异常。

### STOP

必须使用第三方 canonical JSON；必须允许 arbitrary object；hash 只能基于原始 dict；底层验证消息泄露输入。

## 9. WF-T03——ArtifactRepository Protocol

### 前置

WF-T02 PASS。

### 允许文件

```text
app/workflow_contracts/artifacts/repository.py
tests/contract/workflow_contracts/test_artifact_repository_contract.py
```

### 实现顺序

1. 精确复制 4.4 Protocol 签名。
2. 合同测试实现 5.1 的 `ArtifactRepositoryFactory` 与 assertion helper，不使用未命名 fixture。WF-T08 前，模块 04 自有测试从精确内部模块导入；WF-T08 再验证完整包根 API。
3. 测试必须只使用 Protocol 公共方法，不读取内部索引。
4. 当前文件先建立所有 Adapter 必须满足的 reusable assertions；WF-T04 注入内存实现。
5. 同一文件必须定义可收集的同步测试 `test_artifact_repository_protocol_surface`，用 `inspect.signature` 精确断言 Protocol 仅有 put/lookup/get 及 4.4 签名；不得只有 type alias/helper 而收集 0 项。

### 测试

WF-T03 只执行 import/collection，完整行为在 T04：

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q --collect-only tests/contract/workflow_contracts/test_artifact_repository_contract.py
if ($LASTEXITCODE -ne 0) { throw "WF-T03 collection failed" }
```

### PASS

恰有至少一个测试被收集且 `test_artifact_repository_protocol_surface` 存在；签名只有 put/lookup/get；没有同步或管理方法。

### STOP

Protocol 需要具体锁或存储类型；合同测试依赖内部实现；出现 list/delete/clear。

## 10. WF-T04——InMemoryArtifactRepository

### 前置

WF-T03 PASS。

### 允许文件

```text
app/workflow_contracts/artifacts/in_memory.py
tests/unit/workflow_contracts/test_in_memory_artifact_repository.py
tests/contract/workflow_contracts/test_artifact_repository_contract.py
```

### 实现顺序

1. 构造函数要求 Registry、UtcClock、UUIDFactory、limits 全部显式传入；limits 先按权威规范 17 两段式 envelope 复验并复制，失败抛 `ArtifactRepositoryUsageError`。
2. 创建两个私有 dict、immutable record 与一个私有 asyncio.Lock。
3. 每个 async 方法先绑定/核验 loop owner。put 锁外由 Registry strict 复验完整 request 并 prepare；锁内按 operation 精确比较 type/version/hash/canonical bytes，精确 replay 先于容量检查返回。
4. 新写入检查容量后生成 ID；ID 已存在时不重试、不覆盖，`raise ArtifactRepositoryDependencyError() from None`；随后读取 UTC 时间并原子写两个索引。
5. lookup 先按两段式 envelope 复验并复制 OperationId；get 先只复验并复制 Ref，envelope 失败抛 `ArtifactRequestValidationError`。两者锁内只取快照；get 的后续顺序固定为 artifact ID 命中 → 传入 Ref 与存储 Ref 七字段一致 → type/version resolve → expected model 精确 identity → hash/size → strict decode/recanonical，不得提前核验 expected model。
6. decode 后重新 typed 规范化并 byte-for-byte 比较 canonical bytes；返回 fresh Pydantic model，不返回 bytes。
7. 同事件循环 owner 在首次 async 操作时固定；错误使用抛 UsageError。
8. 用 barrier 构造同 key 同内容和同 key 冲突并发，不使用 sleep。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q `
  tests/unit/workflow_contracts/test_in_memory_artifact_repository.py `
  tests/contract/workflow_contracts/test_artifact_repository_contract.py
if ($LASTEXITCODE -ne 0) { throw "WF-T04 pytest failed" }
```

### PASS

权威幂等矩阵全部通过；七字段重放完全一致；get-after-put；容量满时已有 key 仍可重放；失败无部分索引。

### STOP

实现需要线程锁、文件、数据库、TTL、delete、clear；并发测试只能用 sleep；冲突采用覆盖。

## 11. WF-T05——执行预算合同与参考实现

### 前置

WF-T04 PASS。

### 允许文件

```text
app/workflow_contracts/budgets/__init__.py
app/workflow_contracts/budgets/models.py
app/workflow_contracts/budgets/manager.py
app/workflow_contracts/budgets/in_memory.py
tests/unit/workflow_contracts/test_budget_models.py
tests/unit/workflow_contracts/test_in_memory_budget_manager.py
tests/contract/workflow_contracts/test_execution_budget_manager_contract.py
```

### 实现顺序

1. 创建 scope、key、limit、plan、snapshot、request、outcome、limits。
2. validator 固定 run/phase scope、无重复 key、安全整数与 snapshot 算术。
3. 精确复制 Manager Protocol。
4. 构造时按权威规范 17 两段式 envelope 复验并复制 plan/limits；失败包装为 `ExecutionBudgetPlanValidationError`，不得保留调用方对象引用；随后建立 plan key 状态和一个 asyncio.Lock，不读取环境，limits 范围是 1..安全整数上限。
5. 每个 async 方法先绑定/核验 running loop，跨 loop 先抛 UsageError。consume 随后按两段式 envelope 复验并复制 request，失败包装为 `ExecutionBudgetRequestValidationError`，再依次验证 configured key、run、phase、历史 operation、容量、额度。get_snapshot 在 loop 后同样复验 key，失败包装为 `ExecutionBudgetRequestValidationError`，再查 configured key。
6. 首次足额写 applied，首次超限写 rejected；两者都记录原始 snapshot。
7. replay 返回原始 snapshot，不读取当前 snapshot 替换它。
8. 达到 4096 后仅允许已有记录 replay。
9. get_all snapshot 使用固定排序键。
10. 并发测试用 barrier 验证线性化和不超限。
11. 合同测试实现 5.1 的 Budget factory/assertion helper，当前用 lambda 构造内存 Manager。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q `
  tests/unit/workflow_contracts/test_budget_models.py `
  tests/unit/workflow_contracts/test_in_memory_budget_manager.py `
  tests/contract/workflow_contracts/test_execution_budget_manager_contract.py
if ($LASTEXITCODE -ne 0) { throw "WF-T05 pytest failed" }
```

### PASS

零 limit、足额、超限、applied/rejected replay、key/amount conflict、run/phase mismatch、容量与并发全部通过；没有退款 API。

### STOP

出现 TravelBudget 复用；reserve/commit/release/reset；失败调用退款；业务预算 key 硬编码进生产包。

## 12. WF-T06——State、Terminal Registry 与 RunSummary

### 前置

WF-T02 与 WF-T05 PASS；ArtifactRef 和 ExecutionBudgetSnapshot 已经存在。

### 允许文件

```text
app/workflow_contracts/state.py
app/workflow_contracts/summary.py
tests/unit/workflow_contracts/fakes.py
tests/unit/workflow_contracts/test_state.py
tests/unit/workflow_contracts/test_security.py
tests/unit/workflow_contracts/test_summary.py
tests/contract/workflow_contracts/test_requirement_state_continuity.py
```

### 实现顺序

1. 将 TravelSessionState 升级为 v2 十字段，并保留 TerminalCategory、TerminalOutcome、DeliveryStatus 既有独立版本。
2. state validator 固定状态正交、ArtifactRef same-run/唯一/排序；对非空 requirement outcome 必须调用模块 01 public `RequirementDomainTransition` 复验 state/outcome 一致性，不得复制五分支状态机。初始 outcome=None 只允许搭配显式默认 `RequirementDomainState()`。
3. 接收模块 01 transition 后分别深层重建 requirement_state 与 requirement_outcome；两者与 transition、调用输入和彼此都不得共享可变嵌套引用。用 identity 断言和双向 mutation canary 固定该行为。
4. 创建或保留 TerminalCodeRegistration、Registry 与两个基础注册项；Registry 构造先 strict 复验每个 registration，任一格式/语义/重复错误包装为 `TerminalRegistryConfigurationError`。
5. `validate_state` 先通过权威规范 17 两段式 envelope 取得 fresh state，任何结构/版本/state 不变量错误包装为 `TerminalStateMismatchError`；随后 nonterminal 合法 state 直接成功，terminal state 才 resolve code。recoverable 注册允许 `user_action_code=None`，非空 action 必须在允许 tuple 中。
6. Codec parser 使用两阶段 duplicate tracking；错误顺序精确为 bytes/UTF-8/JSON/top object → 顶层重复 version → 精确 v2 version → v2 duplicate/illegal number → strict fields → transition consistency → terminal/Artifact invariants。v1 和 unknown version 抛零参数 `UnsupportedWorkflowStateVersionError`；已识别 v2 后任意失败抛零参数 `WorkflowStateValidationError`。serializer 的任意 forged/version/mismatch/validation 失败均包装为零参数 `WorkflowStateValidationError`。
7. 不实现 v1 migration、双版本 parser、deprecated alias 或 checkpoint 转换；模块 05 启动前丢弃旧进程内 checkpoint，并以显式默认 `RequirementDomainState()` 创建新 session。
8. 创建或保留 SafeCounterValue 与 RunSummary；counter 与 budget key 唯一并按固定键排序。
9. `cache_hits` 必须等于 `artifact.cache_hit` counter；counter 缺失时 cache_hits 必须为 0。
10. 不创建聚合器、状态机、sequence allocator 或 terminal mapper。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q `
  tests/unit/workflow_contracts/test_state.py `
  tests/unit/workflow_contracts/test_security.py `
  tests/unit/workflow_contracts/test_summary.py `
  tests/contract/workflow_contracts/test_requirement_state_continuity.py
if ($LASTEXITCODE -ne 0) { throw "WF-T06 pytest failed" }
```

### PASS

state v2 JSON 恢复模块 01 state/outcome 分支；当前 state → transition → TravelSessionState JSON → 下一轮 `RequirementTurnInput.current` 全字段等值；v1/unknown version、错误优先级、五分支一致性、invalidation summary strict round-trip、深层解除引用、Terminal Registry 矩阵、RunSummary same-run/排序/cache-hit 一致性全部通过；repr 不显示需求内容。

### STOP

必须复制模块 01 DTO；Codec 需要 pickle；任何异常暴露完整 state；必须实现模块 05/13 状态机或聚合器。

## 13. WF-T07——安全事件、Registry 与 Sink

### 前置

WF-T06 PASS。

### 允许文件

```text
app/workflow_contracts/events/__init__.py
app/workflow_contracts/events/models.py
app/workflow_contracts/events/registry.py
app/workflow_contracts/events/sink.py
tests/unit/workflow_contracts/fakes.py
tests/unit/workflow_contracts/test_event_models.py
tests/unit/workflow_contracts/test_event_registry.py
tests/unit/workflow_contracts/test_event_sinks.py
tests/contract/workflow_contracts/test_safe_workflow_event_sink_contract.py
```

### 实现顺序

1. 创建 counter/event/emission DTO 与 validator。
2. 创建 event registration、event Registry、failure-code Registry、八种标准事件注册项与四个标准 failure codes。Event Registry 构造 strict 复验 registration 后检查重复/语义；Failure-code Registry 先 strict 校验 code 格式再检查重复；两者配置错误都抛 `SafeWorkflowEventRegistryConfigurationError`。
3. Event Registry 精确比较 counter name 集合及三个 FieldRequirement；标准 occurrence counter delta 固定为 1。
4. Event Registry validate 先按权威规范 17 两段式 envelope 复验 event，失败抛 `SafeWorkflowEventValidationError`，再做 type/字段/delta 校验。每次 emit 前由调用方先执行该 validate；Sink Protocol 只接收已验证事件。
5. 创建 Sink Protocol 与 NoOp；NoOp 不自行查 Registry、每次返回 applied、不保存事件。
6. Recording test fake 先做 identity/sequence replay，再检查容量；`max_events` 范围 0..安全整数上限。
7. 调用方先要求 Sink 返回值精确属于 `EventEmitted` 或 `EventDeliveryFailed`，再按权威规范 17 两段式 envelope 复验并核验 event ID；failure outcome 还要先校验 code 格式、再查 Failure-code Registry。dict、其他 BaseModel、子类或任一 shape/discriminator/字段/ID/code 错误都包装为 `EventSinkContractError`。
8. 合同测试实现 5.1 的通用 Sink factory/assertion helper；NoOp lambda 可忽略 Registry 构造参数，但每次 emit 前必须 validate。Recording 的 identity/sequence/replay/capacity 使用独立 fake-only 测试，不要求 NoOp replay。
9. 不创建 EventAccumulator、sequence allocator、重试队列或生产 Recording Sink。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q `
  tests/unit/workflow_contracts/test_event_models.py `
  tests/unit/workflow_contracts/test_event_registry.py `
  tests/unit/workflow_contracts/test_event_sinks.py `
  tests/contract/workflow_contracts/test_safe_workflow_event_sink_contract.py
if ($LASTEXITCODE -ne 0) { throw "WF-T07 pytest failed" }
```

### PASS

八种事件注册字段组合、四个 failure code、emit 前置校验与通用 Sink outcome 合同通过；Recording fake 的 identity/sequence/replay/capacity 独立测试通过；NoOp 不持久且每次 applied。

### STOP

事件出现 details/metadata/payload；生产 Sink 提供读取 API；投递失败改变业务状态；模块 04 开始实现模块 13 聚合。

## 14. WF-T08——包根导出、import boundary 与安全补全

### 前置

WF-T01..WF-T07 PASS。

### 允许文件

```text
app/workflow_contracts/__init__.py
tests/unit/workflow_contracts/test_security.py
tests/contract/workflow_contracts/test_public_api.py
tests/contract/workflow_contracts/test_import_boundaries.py
```

### 实现顺序

1. 按 4.9 精确 re-export 全部公共名称并写显式 `__all__`。
2. AST import boundary 测试递归扫描生产包；唯一允许 app 依赖是包根 `app.requirements`，精确导入 `RequirementDomainState`、`RequirementDomainOutcome`、`RequirementDomainTransition`，不得从模块 01 私有文件导入。
3. 测试 private names 不在包根，三个子包 `__init__.py` 只有 docstring，import 不创建实例、锁或 I/O。
4. 对照权威规范 22 核验前序 Task 已覆盖确定性参数化边界；发现缺口时回到拥有该测试文件的最早 Task。参数化 `id=` 使用 `WF-STATE-*`、`ART-REPO-*`、`EXEC-BUD-*`、`SAFE-EVT-*`、`RUN-SUM-*`。
5. `test_security.py` 使用 canary `MODULE04_CANARY_SECRET_7f91`，断言不出现在 requirement state/outcome repr、模块 04 typed exception、event、summary；直接 BaseModel `ValidationError` 不纳入此断言且不得记录。逐行为边界汇总 `model_construct()`/嵌套 mutation canary：错误 scalar、datetime、UUID、bytes、tuple、set 不得被 JSON dump 洗白，必须得到权威规范 17 指定的 typed error。
6. 属性测试只用固定表，不调用 Hypothesis；并发测试只用显式同步原语，不使用 sleep。

### 测试

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q tests/unit/workflow_contracts tests/contract/workflow_contracts
if ($LASTEXITCODE -ne 0) { throw "WF-T08 pytest failed" }
python -m compileall -q app/workflow_contracts
if ($LASTEXITCODE -ne 0) { throw "WF-T08 compileall failed" }
```

### PASS

测试全通过；完整 public set 一致；私有名称未导出；没有禁止依赖或循环 import；每个测试族有正向和失败关闭用例。

### STOP

必须从模块 01 私有文件导入；需要 wildcard export；import 创建 Repository、锁或读取环境；测试发现生产缺陷时把最早受影响 Task 标记为未完成，回到该 Task 白名单修复并重跑其后全部 Task。

## 15. WF-T09——覆盖率、完整回归与一致性 Gate

### 前置

- full rebuild：WF-T00..WF-T08 全部 PASS。
- maintenance：WF-T00、用户指定的受影响 Task、其全部下游 Task 与 WF-T08 PASS。
- requalification_only：只读 WF-T00 PASS，生产包无修改，然后直接执行本 Task。
- 工作区只含任务白名单，以及 2.2 明确允许只读保留的四份设计文档。

### 允许动作

不修改受控源文件。pytest、coverage、compileall 只可写入已忽略的 `.pytest_cache/`；T09 失败时回到最早受影响 Task。

### Gate A：模块 04 line/branch coverage

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
$env:COVERAGE_FILE = '.pytest_cache/.coverage-module04'
python -m pytest -q tests/unit/workflow_contracts tests/contract/workflow_contracts `
  --cov=app.workflow_contracts `
  --cov-branch `
  --cov-report=term-missing `
  --cov-report=json:.pytest_cache/coverage-module04.json `
  --cov-fail-under=95
if ($LASTEXITCODE -ne 0) { throw "Gate A pytest/coverage failed" }

$coverage = Get-Content -LiteralPath '.pytest_cache/coverage-module04.json' -Raw -ErrorAction Stop | ConvertFrom-Json
$linePercent = 100.0 * $coverage.totals.covered_lines / $coverage.totals.num_statements
$branchPercent = if ($coverage.totals.num_branches -eq 0) { 100.0 } else { 100.0 * $coverage.totals.covered_branches / $coverage.totals.num_branches }
$combinedDenominator = $coverage.totals.num_statements + $coverage.totals.num_branches
if ($combinedDenominator -eq 0) { throw "module04 coverage denominator is zero" }
$combinedPercent = 100.0 * ($coverage.totals.covered_lines + $coverage.totals.covered_branches) / $combinedDenominator
if ($linePercent -lt 95.0) { throw "module04 line coverage below 95%" }
if ($branchPercent -lt 95.0) { throw "module04 branch coverage below 95%" }
if ($combinedPercent -lt 95.0) { throw "module04 combined coverage below 95%" }
"line={0:N2}% branch={1:N2}% combined={2:N2}%" -f $linePercent, $branchPercent, $combinedPercent
```

PASS：测试全通过，line、branch、combined 均不低于 95%。不得使用当前 pyproject 的 `source=["app.llm_runtime"]` 结果冒充。

### Gate B：完整离线回归

在完整回归前必须单独运行受旧返回 API 影响的模块 03 合同测试，确保它按新 public transition 消费且没有改变 adapter 语义：

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q tests/contract/requirement_extraction/test_requirement_extractor_contract.py
if ($LASTEXITCODE -ne 0) { throw "Gate B module03 contract failed" }
```

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m pytest -q -ra tests/unit tests/contract
if ($LASTEXITCODE -ne 0) { throw "Gate B regression failed" }
rg -n "pytest\.mark\.skip|pytest\.mark\.xfail|pytest\.skip|skipif|xfail" tests/unit tests/contract
$scanExit = $LASTEXITCODE
if ($scanExit -eq 0) { throw "skip/xfail declaration found" }
if ($scanExit -gt 1) { throw "Gate B rg scan failed" }
```

PASS：模块 01–04 unit/contract 全通过，pytest 报告 0 skipped/xfail，静态扫描退出码 1 表示无命中。`rg` 退出码 0 表示发现禁止项，2 或更高表示扫描失败。不得运行 `tests/live`。

### Gate C：静态与工作区检查

```powershell
$env:PYTHONPYCACHEPREFIX = '.pytest_cache/pycache-module04'
python -m compileall -q app/workflow_contracts
if ($LASTEXITCODE -ne 0) { throw "Gate C compileall failed" }
git diff --check -- .
if ($LASTEXITCODE -ne 0) { throw "Gate C tracked diff whitespace failed" }
git status --short -- .
if ($LASTEXITCODE -ne 0) { throw "Gate C git status failed" }

$controlledFiles = @(
  Get-ChildItem -LiteralPath 'app/workflow_contracts' -Recurse -File -ErrorAction Stop
  Get-ChildItem -LiteralPath 'tests/unit/workflow_contracts' -Recurse -File -ErrorAction Stop
  Get-ChildItem -LiteralPath 'tests/contract/workflow_contracts' -Recurse -File -ErrorAction Stop
  Get-Item -LiteralPath 'docs/04_workflow_state_and_artifacts.md' -ErrorAction Stop
  Get-Item -LiteralPath 'docs/04_workflow_state_and_artifacts_vibe_coding.md' -ErrorAction Stop
)
$whitespaceHits = $controlledFiles | Select-String -Pattern '[ \t]+$'
if ($whitespaceHits) { $whitespaceHits; throw "Gate C trailing whitespace found" }
```

再执行只读扫描：

```powershell
rg -n "langgraph|langchain|asyncpg|psycopg|FastAPI|MCP|DeepSeek|Baidu|百度|dotenv|os\.environ" app/workflow_contracts
$scanOne = $LASTEXITCODE
if ($scanOne -gt 1) { throw "Gate C dependency scan failed" }
rg -n "pickle|__repr__|details|metadata|raw_payload|provider_payload" app/workflow_contracts
$scanTwo = $LASTEXITCODE
if ($scanTwo -gt 1) { throw "Gate C serialization scan failed" }
rg -n "pytest\.mark\.skip|pytest\.mark\.xfail|skipif|xfail|time\.sleep|asyncio\.sleep" tests/unit/workflow_contracts tests/contract/workflow_contracts
$scanThree = $LASTEXITCODE
if ($scanThree -gt 1) { throw "Gate C test scan failed" }
```

三个扫描逐条执行；退出码 1 表示无命中，0 表示必须审查命中，2 或更高表示工具失败。禁止词只能位于明确拒绝逻辑的错误常量或测试断言；每个命中必须人工核验。PASS：无未解释命中，工作区只有 3 节白名单和四份只读设计文档。

### Gate D：文档一致性

逐项比较：文件树、`__all__`、Protocol 签名、DTO 字段、Enum、异常、固定注册、limits、测试命令。任一差异均失败。

### Gate E：状态连续性 maintenance 的正式 Docker 验收

本 Gate 只适用于 `RSC-T05/RSC-T06`，并且是必需 Gate；普通 WF full rebuild/maintenance/requalification 的 Docker 适用性由对应用户任务决定。RSC 的正式验收只能使用从当前源码重新构建的 runtime/test 镜像；宿主机 Python 只作快速诊断，不是验收证据。

执行参数、固定镜像/容器名和完整命令以模块 01 Vibe §15.5 为唯一命令真源，本文件不得维护第二份易漂移脚本；实施者必须逐条执行而不能只引用。固定门槛如下：

1. 从当前源码分别重建 runtime 与 test target。
2. 每个验收步骤创建新容器，使用 `--rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true`。
3. 容器内核验 Python 3.12 与 Pydantic 2.13.4。
4. 分别运行模块 01 定向测试、模块 03 直接 transition 消费合同、模块 04 定向连续性测试。
5. 模块 01 与模块 04 各自产生 coverage JSON，分别报告 line、branch、combined，三项均 `>=95%`。
6. combined 固定为 `(covered_lines + covered_branches) / (num_statements + num_branches)`；`num_branches==0` 仍使用同一 combined 公式，branch 单项记为 100%；只有 combined 分母为 0 才是配置错误并失败。
7. 容器内 compileall 模块 01–04，并执行模块 01～04 全部 unit/contract 回归；测试收集必须非零且 skip/xfail 为 0。
8. 结束时固定验收容器名全部不存在。不得停止、删除、复用或检查其他项目 Docker 资源。

不运行 DeepSeek live、MCP、百度地图或 PostgreSQL；不向容器传 Secret。任一镜像不是本次重建、容器参数缺失、测试/coverage 非零退出或有残留容器，`RSC-T05 FAIL`。

`RSC-T06` 同步 README、状态页和 Dockerfile 后必须再次重建两镜像，并重新运行 runtime 默认 CMD 与完整 unit/contract 回归；`RSC-T05` 的旧镜像结果不能替代 closeout 后证明。

## 16. 幂等、并发与安全断言清单

实现 Agent 必须逐项勾选，不能用单条 happy path 代替：

```text
[ ] 同 OperationId 同内容 Ref 七字段一致
[ ] 同 OperationId 不同 type/version/hash 冲突且不覆盖
[ ] 不同 OperationId 同 hash 仍产生不同 Ref
[ ] 并发同 key 同内容只产生一个逻辑 Artifact
[ ] 并发同 key 不同内容首写获胜
[ ] 验证/Clock/UUID/容量失败无部分索引
[ ] get 比较 Ref 七字段并每次重算 hash/size/schema/model
[ ] lookup absent 是 outcome，get absent 是 exception
[ ] Budget 首次 applied 只扣一次
[ ] Budget 首次 rejected 不扣且记录原 snapshot
[ ] applied/rejected replay 都返回原 snapshot
[ ] Budget key/amount 变化冲突
[ ] 并发 consume 不超过 limit
[ ] 容量满仍允许已有记录 replay
[ ] Event 相同 ID 同内容 replay 不双计
[ ] Event 相同 ID 不同内容 identity conflict
[ ] 同 run/sequence 不同 event sequence conflict
[ ] NoOp Sink 不保存且返回 applied
[ ] Sink delivery failure 不改变业务 outcome
[ ] State/Event/Artifact/Exception repr 不泄露 canary
[ ] 无 Provider/MCP/Prompt/用户原文/大型正文进入事件或摘要
```

## 17. 版本与迁移执行规则

- TravelSessionState 合同只允许 `schema_version="2.0"`；TerminalOutcome、Artifact/预算/事件/摘要独立 DTO 继续使用各自冻结的 1.0。
- 不创建 v1→v2 迁移器、deprecated alias、双版本模型或兼容解析分支；旧进程内 checkpoint 直接丢弃，新 session 显式使用默认 RequirementDomainState。
- state parser 必须先检查版本；v1 与未知版本都失败关闭。
- Artifact type/version 由显式 Registry 解析。
- 增加注册项不修改信封 schema；改变已有字段或不变量必须停止并回到设计阶段。

## 18. 最终报告模板

实现或重新资格验证完成后必须逐项填写，未运行项写“未运行”并给出原因：

```text
任务：模块 04 工作流合同实施 / 维护 / 重新资格验证
执行模式：full_rebuild / maintenance / requalification_only
最终状态：COMPLETE / INCOMPLETE

Git：
- 开始分支：
- 结束分支：
- 开始 HEAD：
- 结束 HEAD：
- 工作区开始状态：
- 工作区结束状态：

修改文件：
-

白名单外文件：
- 无 / 精确列出并说明

Task：
- WF-T00：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T01：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T02：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T03：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T04：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T05：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T06：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T07：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T08：PASS / FAIL / NOT_RUN（模式不适用）
- WF-T09：PASS / FAIL / NOT_RUN（模式不适用）
- RSC-T00：PASS / FAIL / NOT_RUN（精确原因）
- RSC-T01：PASS / FAIL / NOT_RUN（精确原因）
- RSC-T02：PASS / FAIL / NOT_RUN（精确原因）
- RSC-T03：PASS / FAIL / NOT_RUN（精确原因）
- RSC-T04：PASS / FAIL / NOT_RUN（精确原因）
- RSC-T05：PASS / FAIL / NOT_RUN（精确原因）
- RSC-T06 / MODULE-CLOSEOUT：PASS / FAIL / NOT_RUN（精确原因）

测试：
- 模块 04 unit/contract：命令与结果
- 模块 03 直接 transition 消费合同：命令、退出码与结果
- 模块 01 line/branch/combined coverage：三个精确百分比
- 模块 04 line/branch/combined coverage：三个精确百分比
- 完整离线回归：命令与结果
- skip/xfail：

静态验证：
- compileall：
- git diff --check：
- import boundary：
- sensitive canary：
- 文档一致性：

Docker/live：
- runtime/test image rebuild：命令、镜像与退出码
- safe container parameters：PASS / FAIL
- runtime default CMD：命令与结果
- Python/Pydantic：版本与结果
- module 01/module 03 direct/module 04 targets：命令与结果
- module 01-04 compileall/full regression：命令与结果
- post-closeout rebuild/default CMD/full regression：命令与结果
- acceptance containers remaining：0 / 精确名称
- DeepSeek live：未运行
- MCP/百度地图 live：未运行
- PostgreSQL：未运行

外部操作：
- staged：无（implementation model required）/ 精确异常
- commit：未执行（implementation model required）
- merge main：未执行
- push：未执行
- PR：未创建
- deploy：未执行

其他项目：
- process_simulation-platform-dev：未触碰

剩余问题：
- 无 / 精确列出

MODULE-CLOSEOUT：
- README 当前模块/生产包/测试数量/Docker 说明：精确变更
- IMPLEMENTATION_STATUS 日期/待审 HEAD/状态/命令/测试/coverage：精确变更
- Dockerfile：精确变更或基于当前 COPY/CMD 的具体无需修改理由（禁止只写 N/A）
- 四份模块 01/04 权威设计与 Vibe 在代码/closeout 中保持只读：PASS / FAIL
- 模块 05 保持暂停：PASS / FAIL
```

## 19. 完成定义

完成条件按模式分开，禁止把未运行项写成 PASS：

- `full_rebuild`：WF-T00..WF-T09 全部 PASS，才可报告“模块 04 实现完成”。
- `maintenance`：WF-T00、用户白名单对应的最早受影响 Task、其全部下游 Task、WF-T08、WF-T09 均 PASS；其他 Task 填 `NOT_RUN（模式不适用）`，才可报告“本次维护任务完成”，不得据此重新宣称未运行 Task 通过。
- 需求状态连续性 `maintenance`：只按 `RSC-T00..RSC-T06` 判定；七个 Task、Docker、两模块 line/branch/combined、closeout 后重建/default CMD/全回归和无残留容器全部 PASS，实施模型 staged/commit 均为空，才可报告“本次连续性维护完成并等待独立审查”。
- `requalification_only`：WF-T00 与 WF-T09 PASS，WF-T01..WF-T08 填 `NOT_RUN（模式不适用）`，才可报告“本次重新资格验证完成”；生产包实现状态沿用开始时已核验事实，不改写为本轮实现结果。

任何模式执行完成后停止，等待用户下一步指示；不得自动生成模块 05 任务或下一阶段 Prompt。当前实现和验证状态只在 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md) 维护，不写回本冻结契约。

## 20. M13 摘要例外的消费规则

本节对应权威设计 §15.1（D-13-10），只改变 M13 何时能够生成完整摘要，不改变 §4.3 的 RunSummary 字段和 WF-T06/09 既有验证。M04 无需为该例外修改生产 DTO、序列化版本或默认 Registry。

M13 执行 13_demo_workflow_vibe_coding.md §6 的摘要测试：完整聚合、投递故障仍计数、终态同一性、缓存精确计数、跨进程缺失标记、不得以部分统计拼完整摘要。M04 回归继续执行 tests/unit/workflow_contracts/test_summary.py。如必须修改 M04 代码才能满足该包装合同，STOP 并报告最小设计差异，不能把可选摘要字段塞进 RunSummary。
