# 04 工作流状态、Artifact Repository、执行预算与安全事件规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次预算接线规划key更新为planning.model_invocation=17；阶段计数归M09Artifact。M04预算/Repository/事件/摘要和state codec不新增诊断正文或SQL字段。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档状态：v1.2（已批准的需求状态连续性合同）
> 当前实现与验证状态：见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 上位规范：[`00_agent_master_design.md`](00_agent_master_design.md)
> 依赖与工作包：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 前置合同：[`01_requirement_and_dialogue.md`](01_requirement_and_dialogue.md)
> 配套编码契约：[`04_workflow_state_and_artifacts_vibe_coding.md`](04_workflow_state_and_artifacts_vibe_coding.md)
> 规范属性：本文件是模块 04 公共合同、进程内参考实现语义与下游接入边界的唯一真源

```yaml
module_id: "04"
delivery_phase: "Demo"
blocks_demo: true
blocks_production_release: true
depends_on: ["01"]
provides:
  - TravelSessionState / WorkflowPhase / DeliveryStatus / TerminalOutcome
  - TerminalCodeRegistry / BASE_TERMINAL_CODE_REGISTRATIONS
  - ArtifactRef / ArtifactWriteRequest / ArtifactReadResult
  - ArtifactSchemaRegistry / ArtifactRepository / InMemoryArtifactRepository
  - OperationId 与 Artifact 幂等、完整性、容量合同
  - ExecutionBudgetPlan / ExecutionBudgetManager / InMemoryExecutionBudgetManager
  - SafeWorkflowEvent / SafeWorkflowEventRegistry / SafeWorkflowEventSink
  - NoOpSafeWorkflowEventSink / STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS
  - EventDeliveryFailureCodeRegistry / STANDARD_EVENT_DELIVERY_FAILURE_CODES
  - RunSummary / SafeCounterValue
consumes:
  - app.requirements.RequirementDomainState
  - app.requirements.RequirementDomainOutcome
  - app.requirements.RequirementDomainTransition
parallel_with: ["02", "03"]
not_in_scope:
  - 工作流状态转换机、LangGraph node、edge、interrupt、resume 与 checkpoint
  - 完整 Demo 主图、最终路由、事件聚合器与终态映射
  - 酒店、地图、候选研究、规划、校验、费用和攻略业务 DTO
  - DeepSeek、MCP、百度地图、供应商 SDK、网络与数据库
  - PostgreSQL Repository、长期 Memory、TTL、删除、加密与审计队列
  - Application Service、CLI、FastAPI、Web、GUI 与客户端
acceptance_gate:
  - 公共 DTO strict、frozen、extra=forbid 且 JSON round-trip 通过
  - Artifact 规范化、hash、幂等、并发、完整性与容量合同通过
  - 执行预算原子消费、重放、超限、并发与容量合同通过
  - 安全事件、Registry、Sink、Terminal Registry 与 RunSummary 合同通过
  - app.workflow_contracts line/branch/combined coverage 分别不低于 95%
  - 模块 01–03 完整离线回归无失败
test_class: pure-domain
unit_test_command: python -m pytest -q tests/unit/workflow_contracts
contract_test_command: python -m pytest -q tests/contract/workflow_contracts
allowed_test_dependencies:
  - Python 3.12 标准库
  - Pydantic 2.13.4
  - pytest / pytest-asyncio / pytest-cov
  - app.requirements 包根公共合同
forbidden_test_dependencies:
  - app.llm_runtime / app.requirement_extraction / app.graph / app.persistence / app.agent / app.tools / app.config
  - LangChain / LangGraph / FastAPI / asyncpg / psycopg / MCP / 供应商 SDK
  - 网络、数据库、Docker 与 live credential
required_fakes:
  - FixedUtcClock
  - StepMonotonicClock
  - SequenceUUIDFactory
  - RecordingSafeWorkflowEventSink
  - StrictSampleArtifact
stable_contracts:
  - TravelSessionState / TerminalOutcome / TerminalCodeRegistry
  - ArtifactRef / ArtifactSchemaRegistry / ArtifactRepository
  - OperationId / ArtifactLookupOutcome / ArtifactReadResult
  - ExecutionBudgetPlan / ExecutionBudgetManager / ExecutionBudgetSnapshot
  - SafeWorkflowEvent / SafeWorkflowEventRegistry / SafeWorkflowEventSink
  - EventDeliveryFailureCodeRegistry / STANDARD_EVENT_DELIVERY_FAILURE_CODES
  - RunSummary
durable_replacement:
  - InMemoryArtifactRepository 由 Module 10 的 PostgreSQL Artifact Adapter 替换
  - InMemoryExecutionBudgetManager 由 Module 11 的 PostgreSQL 执行账本替换
  - NoOpSafeWorkflowEventSink 由 20+21 可观察性与审计 Adapter 替换
```

## 1. 目标与设计结论

模块 04 为模块 05、06、07–14 提供纯领域工作流基础合同。它冻结以下结果：

1. Graph state 是小型、可序列化、版本化的控制平面信封。
2. 大型业务结果保存在 Artifact Repository，state 只保存 `ArtifactRef`。
3. `OperationId` 同时承担 Artifact 写入和执行预算消费的逻辑幂等键。
4. 外部副作用遵守“先查 Artifact、再消费预算、最后调用一次”的失败关闭顺序。
5. 执行资源预算与模块 01 的 `TravelBudget` 完全不同，不复用名称或语义。
6. 安全事件只携带白名单字段；事件投递故障不改变业务结果。
7. `RunSummary` 是模块 13 在终态生成的安全结算 DTO；模块 04 不聚合、不持久化。
8. Demo 参考实现只在当前 Python 进程和同一事件循环中有效。

## 2. 规范优先级与依赖方向

### 2.1 规范优先级

优先级固定为：用户最新明确决定 → `00_agent_master_design.md` → `IMPLEMENTATION_ROADMAP.md` → 模块 01 冻结公共合同 → 本规范 → 可执行合同测试 → 配套 Vibe Coding 契约 → `IMPLEMENTATION_STATUS.md` → 当前未验收代码。

发现上位规范冲突时必须停止。模块 04 不得修改模块 01–03、创建兼容层、复制 DTO 或通过私有导入消除冲突。

### 2.2 允许依赖

```text
Python 标准库 ─┐
Pydantic ──────┼──> app.workflow_contracts
app.requirements.RequirementDomainState/Outcome/Transition ─┘
```

`app.workflow_contracts` 不得导入模块 02、03 或 05–23，不得导入 LangGraph、数据库、网络客户端、MCP 或供应商 SDK。

## 3. 模块所有权与状态机边界

| 能力 | 所有者 | 模块 04 的关系 |
|---|---:|---|
| Requirement domain state/outcome/transition | 01 | 直接复用包根类型；04 不复制其状态机 |
| 工作流 DTO、Artifact、执行预算、安全事件合同 | 04 | 定义并隔离测试 |
| Demo 进程内 Artifact Repository | 04 | 提供参考实现 |
| Demo 进程内执行预算 Manager | 04 | 提供参考实现 |
| 需求子图、`InMemorySaver`、interrupt/resume | 05 | 消费模块 04 状态合同 |
| 百度地图 typed Adapter 与技术限流 | 06 | 只消费包根公开的 `UtcClock`；不消费 Artifact、OperationId 或执行预算合同 |
| 酒店、研究、规划、输出 | 07–09、12 | 定义并注册各自业务 Artifact DTO 与预算键 |
| PostgreSQL Artifact／evidence 持久化与迁移 | 10 | 实现现有 Repository 端口，不反向改变 Artifact DTO 语义 |
| PostgreSQL checkpoint、执行预算、operation journal 与恢复 | 11 | 实现持久执行端口，不反向改变 Graph 或业务状态语义 |
| 完整主图、状态转换、事件序号与聚合 | 13 | 消费模块 04 合同并拥有装配 |
| `demo_ready`、最终 terminal mapping、RunSummary 构造 | 13 | 模块 04 不执行 |
| Application Service、应用 API 和前端网页 | 14 | 消费模块 13 入口 |
| PostgreSQL Artifact／evidence | 10 | 替换进程内 Repository，不改变既有端口 |
| PostgreSQL checkpoint／执行预算／恢复 | 11 | 替换进程内 Saver 和预算 Manager；恢复扩展须单独设计 |
| 隐私生命周期、可观察性与审计 | 19–21 | 替换安全 Sink 并增加治理 |
| 长期语义或向量 Memory | Future | 当前不冻结 |

模块 04 不定义状态转换机。模块 05 只拥有需求子图局部转换；完整工作流状态机在模块 06–12 合同冻结后、模块 13 实现前另立文档。本任务不创建该文档。

## 4. 固定包结构

```text
app/workflow_contracts/
├── __init__.py
├── errors.py
├── identifiers.py
├── providers.py
├── state.py
├── summary.py
├── artifacts/
│   ├── __init__.py
│   ├── models.py
│   ├── registry.py
│   ├── canonical_json.py
│   ├── repository.py
│   └── in_memory.py
├── budgets/
│   ├── __init__.py
│   ├── models.py
│   ├── manager.py
│   └── in_memory.py
└── events/
    ├── __init__.py
    ├── models.py
    ├── registry.py
    └── sink.py
```

`state.py` 不导入具体 Repository、Manager 或 Sink。`summary.py` 只定义安全结算 DTO。三个子包按独立变化原因拆分；禁止增加万能 `utils.py`。

三个子包的 `__init__.py` 只含模块 docstring，不做二级 re-export；唯一稳定公共入口是顶层 `app.workflow_contracts`。

## 5. 公共模型共同规则

除 Enum、Protocol、受约束类型别名、异常和 `ArtifactSchemaRegistration` 外，模块 04 自有公共 DTO 统一使用：

```python
ConfigDict(strict=True, frozen=True, extra="forbid")
```

`ArtifactSchemaRegistration` 是公开但不参与 JSON 的 `@dataclass(frozen=True, slots=True)` descriptor；其 `type[BaseModel]` 字段由 Registry 构造阶段验证。

共同规则：

- 公开模型不得持有可变内部存储引用。
- Pydantic `frozen=True` 只禁止字段重新绑定，不宣称递归冻结第三方或模块 01 嵌套对象。模块 04 不修改嵌套输入；拆分 transition 时必须对 requirement state 与 outcome 各自产生深层 fresh snapshot，Repository 也不保留调用方 `content` dict。
- 声明为必填的字段没有默认值；调用方必须显式传入 `None` 或空 tuple。
- JSON 序列化使用字段名，不使用 alias，不输出 computed field，不省略 `None`。
- JSON round-trip 恢复 UUID、Enum、tuple、日期时间与 discriminated union 分支。
- 安全整数范围是 `-(2**53 - 1)` 至 `2**53 - 1`；计数与大小再叠加非负或正数限制。
- 含正文的字段设置 `repr=False`，但仍参与明确授权的序列化。
- 不允许 pickle、`repr()` 序列化或任意对象反序列化。

## 6. 标识、阶段与工作流状态

### 6.1 `WorkflowPhase`

位置：`identifiers.py`。基类：`str, Enum`。成员固定为：

```python
class WorkflowPhase(str, Enum):
    REQUIREMENTS = "requirements"
    HOTEL_SELECTION = "hotel_selection"
    CANDIDATE_RESEARCH = "candidate_research"
    ITINERARY_PLANNING = "itinerary_planning"
    VALIDATION = "validation"
    BUDGET_EVALUATION = "budget_evaluation"
    OUTPUT_FORMATTING = "output_formatting"
```

它只表示当前或最后执行的业务阶段，不包含 created、completed、failed、cancelled 或 demo_ready。新增成员必须升级状态 schema。

### 6.2 `DeliveryStatus`

位置：`state.py`。基类：`str, Enum`。

```python
class DeliveryStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    AWAITING_USER = "awaiting_user"
    DEMO_READY = "demo_ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

`phase`、`delivery_status`、`terminal_code` 三者正交。模块 13 是唯一可以写入 `demo_ready` 的所有者。

### 6.3 受约束标识

位置：`identifiers.py`。

| 名称 | 类型与约束 |
|---|---|
| `TerminalCode` | `Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]` |
| `UserActionCode` | 与 `TerminalCode` 相同的 lower_snake 约束 |
| `LogicalStep` | 与 `TerminalCode` 相同的 lower_snake 约束 |
| `ArtifactType` | 长度 3..65，pattern `r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$"` |
| `ArtifactSchemaVersion` | 长度 3..16，pattern `r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"` |
| `ContentHash` | pattern `r"^sha256:[0-9a-f]{64}$"` |
| `ExecutionBudgetName` | 与 `ArtifactType` 相同的两段式约束 |
| `WorkflowEventType` | 与 `ArtifactType` 相同的两段式约束 |
| `SafeCounterName` | 与 `ArtifactType` 相同的两段式约束 |
| `EventDeliveryFailureCode` | 与 `TerminalCode` 相同的 lower_snake 约束 |

这些字符串不是封闭 Enum。拥有语义的模块通过不可变 Registry 注册；运行中不允许自动扫描或热修改。

### 6.4 `OperationId`

位置：`identifiers.py`。基类：`BaseModel`。

| 字段 | 类型 | 默认值 | 规则 |
|---|---|---|---|
| `run_id` | `UUID` | 无 | 调用方注入 |
| `phase` | `WorkflowPhase` | 无 | 逻辑操作所属阶段 |
| `logical_step` | `LogicalStep` | 无 | 稳定步骤名，不含用户文本、地点、secret、hash、时间或调用次数 |

三字段整体是幂等键。安全显示格式固定为 `{run_uuid}:{phase_value}:{logical_step}`，只用于诊断；JSON 表现为对象。`OperationId` 必须可 hash。

```python
def safe_display(self) -> str: ...
```

`safe_display()` 是唯一承诺该格式的 API；不覆盖 `__str__`。

### 6.5 `TerminalCategory` 与 `TerminalOutcome`

位置：`state.py`。

```python
class TerminalCategory(str, Enum):
    INPUT = "input"
    CONFIGURATION = "configuration"
    PROVIDER = "provider"
    TOOL = "tool"
    VALIDATION = "validation"
    BUDGET = "budget"
    INTERNAL = "internal"
```

`TerminalOutcome` 字段：

| 字段 | 类型 | 默认值 | 规则 |
|---|---|---|---|
| `schema_version` | `Literal["1.0"]` | 无 | 必填 |
| `terminal_code` | `TerminalCode` | 无 | 与 state 顶层一致 |
| `category` | `TerminalCategory` | 无 | 安全分类 |
| `recoverable` | `bool` | 无 | 存在文档化安全恢复或重新执行路径 |
| `user_action_code` | `UserActionCode | None` | 无 | 非空时 `recoverable=True` |
| `trace_id` | `UUID` | 无 | 与 state 顶层一致 |

`recoverable=False` 时 `user_action_code` 必须为 `None`。Outcome 不包含消息、异常、堆栈、原始 payload 或正文。

### 6.6 `TravelSessionState`

位置：`state.py`。基类：`BaseModel`。

```python
class TravelSessionState(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["2.0"]
    run_id: UUID
    trace_id: UUID
    phase: WorkflowPhase
    delivery_status: DeliveryStatus
    terminal_code: TerminalCode | None
    terminal_outcome: TerminalOutcome | None
    requirement_state: RequirementDomainState
    requirement_outcome: RequirementDomainOutcome | None
    artifact_refs: tuple[ArtifactRef, ...]
```

十个字段全部必填，定义与序列化顺序固定为 `schema_version`、`run_id`、`trace_id`、`phase`、`delivery_status`、`terminal_code`、`terminal_outcome`、`requirement_state`、`requirement_outcome`、`artifact_refs`。nullable 字段也必须显式传入 `None`，无 Artifact 时显式传入 `()`。`requirement_state` 不可为 `None`、没有默认值；初始快照必须显式传 `RequirementDomainState()`。`requirement_state` 和 `requirement_outcome` 均设置 `repr=False`，直接复用模块 01 包根类型，不复制其字段定义。

模块 01 transition 是 state/outcome 一致性的唯一所有者。模块 04 接收 transition 时必须调用模块 01 的公开 `RequirementDomainTransition` 验证，不得复制五分支状态机；随后分别重建 fresh `requirement_state` 和 fresh `requirement_outcome`，二者不得引用 transition、彼此或调用方可变嵌套对象。state 构造和 JSON Codec 也必须通过 strict round-trip 重新验证两字段。调用方不得对已放入 state 的嵌套对象做原地修改，状态演进只能创建新 snapshot。

`RequirementInvalidated` 的公共 outcome 精确携带 `outcome_type`、`changed_fields`、`before_digest`、`after_digest`、`reason`、`summary`，字段顺序由模块 01 冻结。模块 04 只保存并 strict round-trip 该分支，不验证 renderer 语义、不导入模块 01 私有 dialogue 代码。模块 05 对 `NeedConfirmation` 或 `RequirementInvalidated` 展示 outcome 自带的 `summary`，收到确认后只把 `requirement_state` 与 action 交给模块 01；不得自行拼摘要、伪造 outcome 或复制完整性校验。

需求连续性不变量：

- 只有初始快照允许 `requirement_outcome=None`，且此时 `requirement_state` 必须严格等于默认 `RequirementDomainState()`。
- 一旦 `requirement_outcome` 非空，必须用 public `RequirementDomainTransition(state=requirement_state, outcome=requirement_outcome)` 复验；不匹配直接 validation fail。
- 已产生 outcome 后不得在后续 snapshot 中清空它；Graph 通过上一快照的非空 outcome 判定是否已经开始需求对话。
- 下一轮输入固定使用 `RequirementTurnInput.current = travel_session_state.requirement_state`，不得从 `requirement_outcome` 反向构造或只保存 outcome。
- 模块 01 `confirm(state, action)` 只按 awaiting-confirmation state、完整硬字段、空 issues、无 missing/conflict 和纯 accept action 验证；不接收 Graph 当前 outcome。因此 `NeedConfirmation` 和 `RequirementInvalidated` 的 state 都可在用户已看到对应 summary 后确认。
- terminal snapshot 仍保留最后的 requirement state/outcome，不因 phase 或 delivery status 改变而清除。

快照不变量：

| delivery status | terminal_code | terminal_outcome |
|---|---|---|
| `in_progress` | `None` | `None` |
| `awaiting_user` | `None` | `None` |
| `demo_ready` | 非空 | `None` |
| `degraded` | 非空 | 必填 |
| `failed` | 非空 | 必填 |
| `cancelled` | 非空 | 必填 |

Outcome 非空时，其 `terminal_code` 与 `trace_id` 必须和 state 顶层一致。state 不保存预算账本、计数器、RunSummary、当前外部调用、Graph node、interrupt/checkpoint ID、时间戳、错误消息、Provider、MCP session、Runnable、大型候选、路线或攻略正文。

`artifact_refs` 必须满足：

- 每个 `ref.operation_id.run_id` 与 state `run_id` 一致。
- `artifact_id` 和 `operation_id` 均不得重复。
- 引用可来自当前阶段和已完成阶段。
- 排序键固定为 phase 声明顺序，再按 `logical_step` 的 Unicode code point 升序。
- 顺序错误、重复或跨 run 输入抛 `WorkflowStateValidationError`；不自动排序或去重。

### 6.7 状态 JSON Codec

位置：`state.py`。

```python
def serialize_travel_session_state(state: TravelSessionState) -> bytes: ...
def parse_travel_session_state_json(data: bytes) -> TravelSessionState: ...
```

Codec 只接受 UTF-8 JSON bytes。序列化先执行 §17 的 Python-tree strict preflight，取得第一个 fresh state；再对 fresh state 执行 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)`，用 `sort_keys=True`、紧凑分隔符、`ensure_ascii=False`、`allow_nan=False` 生成 candidate bytes；随后用下述 parser 完整解析 candidate，取得第二个 fresh state，再以相同参数生成最终 bytes。任一 dump、解析、version、transition mismatch 或复验失败都统一抛零参数 `WorkflowStateValidationError`，即使输入来自 `model_construct()`；serialize 不向调用方传播 `UnsupportedWorkflowStateVersionError`。state JSON 不参与 Artifact hash。

解析算法固定为：

1. 参数不是 bytes、UTF-8 非法、存在 BOM、JSON 语法错误、顶层不是 object 或尾随非空白内容时，抛 `WorkflowStateValidationError`。
2. JSON object-pairs hook 记录全部重复 key，但除顶层重复 `schema_version` 外暂不抛错；`parse_constant` 记录 NaN/Infinity 并放入私有 sentinel。
3. 顶层 `schema_version` 重复时抛 `WorkflowStateValidationError`，因为无法选择可信版本。
4. 唯一版本缺失、类型不是 string 或值非 `"2.0"` 时抛零参数 `UnsupportedWorkflowStateVersionError`；不得把原始版本值写入消息。`"1.0"` 同样拒绝，不提供迁移或双版本解析。
5. 只有已知 v2 才检查嵌套重复 key、invalid-number sentinel 和其他字段；失败抛零参数 `WorkflowStateValidationError`。
6. 将已检查的 JSON tree 用固定参数重新编码，再用 `TypeAdapter(TravelSessionState).validate_json(..., strict=True)` 恢复 UUID、Enum、tuple 及模块 01 state/outcome 分支。
7. strict 字段恢复后调用模块 01 public transition validator 校验 requirement state/outcome，再校验 terminal 和 Artifact 不变量；任一步失败均抛零参数 `WorkflowStateValidationError`。

标准 JSON 的前后空白允许；尾随非空白内容拒绝。公开解析边界包装底层 Unicode/JSON/Pydantic 错误并 `raise ... from None`。不得隐式迁移。

## 7. Terminal Registry

### 7.1 公共模型

`FieldRequirement` 位于 `identifiers.py`；Terminal 注册 DTO 与 Registry 位于 `state.py`。

```python
class FieldRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"

class TerminalCodeRegistration(BaseModel):
    terminal_code: TerminalCode
    delivery_status: DeliveryStatus
    outcome_requirement: FieldRequirement
    category: TerminalCategory | None
    recoverable: bool | None
    user_action_codes: tuple[UserActionCode, ...]
```

`delivery_status` 只能是四种终态。`outcome_requirement` 只能是 required 或 forbidden。outcome forbidden 时后三个语义字段为空；outcome required 时 category 和 recoverable 必填。不可恢复注册不允许 user action。动作 code 按 Unicode code point 排序且唯一。recoverable 注册即使声明了允许动作，运行结果仍可使用 `user_action_code=None`；非空值必须在允许 tuple 内。

`TerminalCodeRegistry` 是不可变普通类：

```python
class TerminalCodeRegistry:
    def __init__(self, registrations: tuple[TerminalCodeRegistration, ...]) -> None: ...
    def resolve(self, terminal_code: TerminalCode) -> TerminalCodeRegistration: ...
    def validate_state(self, state: TravelSessionState) -> None: ...
```

重复注册抛 `TerminalRegistryConfigurationError`；未知 code 抛 `UnsupportedTerminalCodeError`；状态语义不符抛 `TerminalStateMismatchError`。`resolve` 返回 Registry 持有的 frozen registration。`validate_state` 先按 §17 两段式 envelope 获得 fresh state；任何结构、版本或 state 不变量错误统一包装为 `TerminalStateMismatchError`。随后对合法 nonterminal state 直接成功，不查询 Registry；terminal state 的校验顺序是 code resolve → delivery status → outcome required/forbidden → category → recoverable → user action。

### 7.2 基础注册项

公开不可变 tuple `BASE_TERMINAL_CODE_REGISTRATIONS` 固定包含：

| terminal code | delivery status | outcome | category | recoverable | actions |
|---|---|---|---|---|---|
| `workflow_completed` | `demo_ready` | forbidden | `None` | `None` | `()` |
| `cancelled_by_user` | `cancelled` | required | `input` | `False` | `()` |

`workflow_completed` 只能由模块 13 在当前已启用的全部阻断事实、执行预算、完整性与可选能力 Gate 通过后写入。未启用的旅行费用能力只产生固定未核验标签，不作为预算合规 Gate。其他模块只声明自有注册项，模块 13 组合并显式映射。

## 8. Artifact 公共合同

### 8.1 `ArtifactRef`

位置：`artifacts/models.py`。基类：`BaseModel`。七个字段全部必填。

| 字段 | 类型 | 规则 |
|---|---|---|
| `artifact_id` | `UUID` | Repository 通过注入的 UUID Factory 生成 |
| `artifact_type` | `ArtifactType` | 两段式注册名 |
| `schema_version` | `ArtifactSchemaVersion` | Artifact 正文版本，不是 state 版本 |
| `content_hash` | `ContentHash` | `sha256:` 加 64 位小写十六进制 |
| `operation_id` | `OperationId` | 写入幂等键 |
| `created_at` | `datetime` | 带时区 UTC，由注入 Clock 生成 |
| `size_bytes` | `int` | 规范 UTF-8 bytes 长度，范围 1..安全整数上限 |

Ref 不包含正文、URI、路径、metadata、数据库键或供应商字段。业务调用方不得手工伪造 Ref；正式 Ref 只能由 Repository 返回或由可信持久 Adapter 反序列化并完成完整性校验。

### 8.2 写入与读取 DTO

位置：`artifacts/models.py`。

```python
JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

class ArtifactWriteRequest(BaseModel):
    operation_id: OperationId
    artifact_type: ArtifactType
    schema_version: ArtifactSchemaVersion
    content: dict[str, JsonValue]  # repr=False

class ArtifactReadResult(BaseModel, Generic[TArtifact]):
    ref: ArtifactRef
    content: TArtifact  # repr=False
```

写入顶层必须是 object。调用方不能提交 hash、ID、时间、大小、DTO 实例、bytes、文件、pickle、Provider 或 MCP 对象。读取返回新的 typed DTO，不返回 dict、raw bytes 或内部引用。

### 8.3 Artifact Schema Registry

位置：`artifacts/registry.py`。

```python
class ArtifactSchemaRegistration(Generic[TArtifact]):
    artifact_type: ArtifactType
    schema_version: ArtifactSchemaVersion
    model: type[TArtifact]

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
```

`PreparedArtifact` 是私有 frozen dataclass，字段为 operation_id、artifact_type、schema_version、canonical_bytes、content_hash、size_bytes。它不从包根导出。

Registry 构造时递归检查注册模型及其字段类型可达的全部嵌套 Pydantic 模型；循环类型图按已访问类型去重。每个可达模型都必须是非 RootModel 的 `BaseModel` 子类，且 `dict(model.model_config)` 必须精确等于 `{"strict": True, "frozen": True, "extra": "forbid"}`；所有字段必须 required，nullable 值由调用方显式传 `null`。整个可达模型图不得声明 alias、validation alias、serialization alias、computed field、field serializer 或 model serializer。允许的字段类型只包括 JSON scalar、date、带时区 datetime、UUID、值为 JSON scalar 的 Enum、符合本规则的嵌套模型，以及这些类型组成的 nullable/union、list、tuple 和 `dict[str, ...]`；其他任意类、bytes、set、mapping 抽象和 arbitrary type 在 Registry 构造时失败。

类型语法还固定如下：`Annotated[T, ...]` 在保留 Pydantic 原生约束与 discriminator 校验的同时递归检查底层 `T`；拒绝 `BeforeValidator`、`PlainValidator`、`WrapValidator`、全部 functional serializer 与任意未知 callable metadata。`AfterValidator` 和类上 field/model validator 只可做纯断言，不可改变值，`prepare()` 的逐路径等价检查负责执行该限制。`Literal[...]` 只允许由 `None`、bool、int、有限 float、str 或这些 JSON scalar 值组成；其他 Literal 值失败。nullable/union 的实际分支由 strict validation 结果确定，随后仍做逐路径等价检查。键是 `(artifact_type, schema_version)`；重复键失败。空 Registry 合法，但任何写入均抛 `UnsupportedArtifactTypeError`。未知 type 与已知 type 的未知 version 分别抛 `UnsupportedArtifactTypeError`、`UnsupportedArtifactVersionError`。

Artifact DTO validator 只能是纯、确定性字段校验，不得读取时间、随机数、环境、网络或全局可变状态。Registry 不宣称可通过反射证明纯度；每个 DTO owner 的注册合同测试必须对同一输入重复 prepare 并断言 canonical bytes/hash 一致。

`resolve` 返回 Registry 持有的 frozen descriptor；不复制、不暴露可变 Registry 存储。

`prepare()` 先对整个 `ArtifactWriteRequest` 执行 §17 的两段式 envelope 复验：保留 `model_dump(mode="python", ...)` 产生的原始 Python tree，对该 tree strict `validate_python`，再对 fresh DTO 做 strict JSON round-trip，取得第二个 fresh request；任一步失败统一抛 `ArtifactRequestValidationError`。这保证 `model_construct()` 或构造后嵌套 mutation 塞入 content 的 datetime、UUID、bytes、tuple、set 或其他非 JsonValue 不会先被 JSON dump 洗成合法字符串/array。随后把已证明是 JsonValue 的 content JSON 编码并调用注册模型的 `TypeAdapter.validate_json(..., strict=True)`。只允许 JSON 对 date、带时区 datetime、UUID 和精确 Enum value 的正式恢复；拒绝字符串、数字、布尔之间的宽松转换。

为防止 Pydantic 的 int→float 行为或自定义 validator 绕过 strict，Registry 必须对 Python strict 复验前保留、且随后已证明完全符合 JsonValue 的原始 content tree、字段 annotation 和 typed DTO 做完整逐路径等价检查：object key 集与 array 长度必须相同；普通 `None`/bool/int/float/str leaf 的 JSON kind 与值必须完全相同；Literal 必须同 kind、同 value；Enum 比较前先取 typed Enum `.value`，再要求它与原始 leaf 同 JSON kind、同 value，因此普通数值 Enum 与 IntEnum 都合法且不会被误判为 int/float 转换；date 原字符串必须是该 typed date 的精确 `YYYY-MM-DD`；UUID 原字符串解析后必须等于 typed UUID；datetime 原字符串解析后必须与 typed datetime 表示同一瞬间，naive 值失败；list/tuple 和嵌套 model 递归检查。任何路径增加、缺失、类型变化、值变化或 validator 转换均抛 `ArtifactRequestValidationError`。验证后的 DTO 使用 `model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)`，再由类型感知规范化器处理 UUID、Enum、date、datetime 和 JSON scalar，最后计算 hash。调用方原始 dict 不直接参与 hash。

`decode()` 自身顺序固定为：§17 两段式 envelope 复验并复制 Ref → resolve type/version → 要求注册模型与 `expected_model` 是同一类型而非父子类兼容 → 要求 canonical_bytes 是 bytes → 重算并核验 hash/size → strict 解析 → typed 重新规范化并 byte-for-byte 比较 → 返回新对象。Ref 复验失败抛 `ArtifactRequestValidationError`，expected model 不符抛 `ArtifactExpectedModelMismatchError`，bytes/hash/size/decode/recanonical 失败抛 `ArtifactIntegrityError`。Repository `get` 在调用 decode 前还必须先比较调用方 Ref 与存储 Ref 的全部七个字段；同 artifact ID 下任何字段不一致均为 `ArtifactIntegrityError`。

模块 04 不提供业务 Artifact DTO 或 generic JSON 兜底。模块 07–09、12 拥有各自的业务 DTO 和注册项；模块 10/11 只实现持久化或恢复基础设施，不取得业务 Artifact DTO 所有权；模块 13 组合 Registry。

## 9. Artifact 规范 JSON 与 hash

位置：`artifacts/canonical_json.py`。底层函数私有，唯一算法固定为：

```python
json.dumps(
    normalized_value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

类型感知规范化器的分派顺序固定为 Enum → UUID → datetime → date → `None` → bool → int → float → str → list/tuple → dict；命中 Enum 后对其 JSON-scalar `.value` 再按 scalar 规则处理。该顺序防止 str/int Enum、datetime/date 与 bool/int 的子类关系改变结果。规范化规则：

- 顶层是 object；key 必须是 string，按 Unicode code point 升序。
- Unicode 保持输入码点，不执行 NFC/NFD；拒绝孤立 surrogate。
- `None` 保留为 `null`；nullable 字段不省略；bool 与整数不同。
- 整数必须位于安全整数范围。
- float 必须有限；拒绝 NaN 与正负 Infinity；`-0.0` 规范成 `0.0`；整数 1 与浮点 1.0 的 JSON 类型不同。
- list/tuple 都输出 JSON array，保持原顺序，不排序、不去重。
- Enum 输出 `.value`。
- UUID 输出标准小写连字符字符串。
- date 输出 `YYYY-MM-DD`。
- datetime 必须带时区，转换到 UTC 并输出 RFC 3339 `Z`；naive datetime 拒绝。
- 不使用 alias、computed field、BOM、空格、缩进或尾随换行。

1.0 不增加独立 JSON 嵌套深度字段；Python/Pydantic 的递归保护触发时统一包装为 `ArtifactRequestValidationError`，同时仍执行 per-artifact bytes 上限。未来若引入稳定深度上限，必须进入版本升级设计。

`content_hash = "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()`；`size_bytes = len(canonical_bytes)`。schema version 不额外拼入 hash；正文的规范 JSON 已包含注册模型全部字段，而 Ref 独立携带 type/version。

## 10. Artifact Repository 端口

`ArtifactFound`、`ArtifactAbsent` 与 `ArtifactLookupOutcome` 位于 `artifacts/models.py`；`ArtifactRepository` Protocol 位于 `artifacts/repository.py`。

```python
class ArtifactFound(BaseModel):
    outcome_type: Literal["found"] = "found"
    ref: ArtifactRef

class ArtifactAbsent(BaseModel):
    outcome_type: Literal["absent"] = "absent"
    operation_id: OperationId

ArtifactLookupOutcome = Annotated[
    ArtifactFound | ArtifactAbsent,
    Field(discriminator="outcome_type"),
]

class ArtifactRepository(Protocol):
    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef: ...
    async def lookup(self, *, operation_id: OperationId) -> ArtifactLookupOutcome: ...
    async def get(
        self,
        ref: ArtifactRef,
        *,
        expected_model: type[TArtifact],
    ) -> ArtifactReadResult[TArtifact]: ...
```

端口只有异步 API。`lookup` 未命中返回 `ArtifactAbsent`；`get` 未命中抛 `ArtifactNotFoundError`。不提供 exists、list、delete、clear、batch、TTL、close、aclose 或上下文管理器。实例由调用方拥有。

### 10.1 幂等矩阵

| 场景 | 固定结果 |
|---|---|
| 同 OperationId、同 type/version/content、首次 put | 创建并返回 Ref |
| 同 OperationId、同 type/version/hash/canonical bytes、重复 put | 返回七字段完全一致的 Ref |
| 同 OperationId，type/version/hash/canonical bytes 任一不同 | `ArtifactConflictError`，不覆盖；同 hash 不同 bytes 仍 conflict |
| 不同 OperationId、相同内容 | 新 artifact ID 与新 Ref；hash 可一致 |
| lookup 未知 OperationId | `ArtifactAbsent` |
| get 未知 artifact ID | `ArtifactNotFoundError` |
| get 的 Ref 与存储 Ref 任一字段不同 | `ArtifactIntegrityError` |
| get 的 expected model 不一致 | `ArtifactExpectedModelMismatchError` |
| 内容 hash、size、type 或规范 bytes 不一致 | `ArtifactIntegrityError` |
| 并发同 key 同内容 | 一个逻辑 Artifact，所有成功方得到同一 Ref |
| 并发同 key 不同内容 | 首个持锁写入成功，其他调用 conflict |
| replay 命中既有 Ref | 读取既有 Artifact，不重复外部副作用 |

一个 OperationId 只对应一个逻辑 Artifact。禁止 last-write-wins 和跨 OperationId Ref 复用。

## 11. Demo 进程内 Artifact Repository

`ArtifactRepositoryLimits` 位于 `artifacts/models.py`；Demo limits 常量与参考实现位于 `artifacts/in_memory.py`。

```python
class ArtifactRepositoryLimits(BaseModel):
    max_artifact_bytes: int
    max_artifacts: int
    max_total_content_bytes: int

DEMO_ARTIFACT_REPOSITORY_LIMITS = ArtifactRepositoryLimits(
    max_artifact_bytes=1_048_576,
    max_artifacts=2_048,
    max_total_content_bytes=67_108_864,
)

class InMemoryArtifactRepository:
    def __init__(
        self,
        *,
        registry: ArtifactSchemaRegistry,
        utc_clock: UtcClock,
        uuid_factory: UUIDFactory,
        limits: ArtifactRepositoryLimits,
    ) -> None: ...
```

三个 limits 均为 1..安全整数上限，且 `max_artifact_bytes <= max_total_content_bytes`。Repository 构造函数必须通过 §17 两段式 envelope 复验并复制 limits；无效或 forged limits 统一抛 `ArtifactRepositoryUsageError`，不得保留调用方对象引用。

内部结构固定为 `operation_id -> ArtifactRef` 与 `artifact_id -> immutable record(ref, canonical_bytes)` 两个私有索引，加一个私有 `asyncio.Lock`。

`put` 在锁外做 schema 验证和规范化；锁内先查 operation 索引并同时比较 type、version、hash、canonical bytes。精确重放在容量检查前返回既有 Ref。新写入再检查 per-artifact/count/total 容量，随后生成 artifact ID；若 ID 已存在，不重试、不覆盖，抛 `ArtifactRepositoryDependencyError`。最后读取 UTC 时间并原子写入两个索引。验证、UUID、Clock 或容量失败不得留下部分记录。相同 key 冲突时，首个进入锁且成功写入的内容获胜。

首次 async 公共方法调用绑定当前 running event loop，后续跨 loop 调用先抛 `ArtifactRepositoryUsageError`。随后 `put` 由 Registry 按 §17 两段式 envelope 复验完整 request；`lookup` 同样复验并复制 OperationId；`get` 只在此阶段复验并复制 Ref。put/lookup/get 的 envelope 复验失败均抛 `ArtifactRequestValidationError`；get 的 expected_model identity 必须等 artifact ID 命中、Ref 七字段一致并 resolve type/version 后才检查。

`get` 与 `lookup` 在锁内复制 Ref 和 immutable bytes 快照，锁外完成 decode。decode 严格解析后必须再次 typed 规范化并与存储 canonical bytes 做 byte-for-byte 比较；不一致是 `ArtifactIntegrityError`。put 成功返回后，同一实例上的 get 必须立刻可见。该实现只保证同一 Repository 实例、同一事件循环内的 coroutine safety；跨线程、跨进程、跨事件循环、崩溃恢复均不保证。

容量规则：

- 单 Artifact 超过 1 MiB 抛 `ArtifactTooLargeError`。
- 新 Artifact 导致数量超过 2048 或总规范正文超过 64 MiB，抛 `ArtifactCapacityExceededError`。
- 已存在 operation 的精确幂等 put 在容量满时仍成功重放。
- 总量只计算 canonical content bytes，不计算 Python 对象开销。
- 不同 OperationId 即使 hash 一致，也按各自 `size_bytes` 分别计入总量；内部共享 bytes 不能改变可观察容量语义。
- 不压缩、不驱逐、不清理、不持久化、不提供 TTL/delete/clear。
- 测试通过丢弃实例并新建来清理。

进程退出、容器退出或实例丢失后数据消失。Docker 只改变 Python 进程所在位置，不把该对象变成数据库、volume 或镜像仓库。

## 12. 外部副作用安全顺序

模块 04 冻结调用合同；模块 05 与模块 07–13 中实际拥有业务外部调用的调用者按各自设计实现下列编排。模块 06 只提供单次 typed 地图调用，不查 Artifact、不接收 OperationId、不消费执行预算，也不拥有本节顺序：

1. `repository.lookup(operation_id)`。
2. found：`get` 并复用；禁止外部调用。
3. absent：`budget_manager.consume(request)`。
4. `BudgetExceeded`：禁止外部调用。
5. `BudgetConsumed(disposition="replayed")` 且 Artifact 仍 absent：抛 `OperationResultIndeterminateError`，禁止再次外部调用。
6. `BudgetConsumed(disposition="applied")`：调用方获得一次执行许可。
7. 外部结果返回后，先 `repository.put()`，再进入 interrupt 或其他业务 await。

该顺序提供 at-most-once 与失败关闭，不宣称跨 Provider 的事务性 exactly-once。进程在外部副作用完成后、Artifact 写入前崩溃时，结果可能丢失；重放仍不得再次调用。

## 13. 执行预算合同

### 13.1 与旅行费用预算的边界

所有公共名称使用 `ExecutionBudget*`。模块 01 的 `TravelBudget` 表示旅行费用约束；模块 04 的执行预算表示调用、迭代或逻辑操作额度。两者禁止互换。

### 13.2 DTO

位置：`budgets/models.py`。所有字段必填且无隐式默认，discriminator literal 除外。

```python
class ExecutionBudgetScope(str, Enum):
    RUN = "run"
    PHASE = "phase"

class ExecutionBudgetKey(BaseModel):
    scope: ExecutionBudgetScope
    name: ExecutionBudgetName
    phase: WorkflowPhase | None

class ExecutionBudgetLimit(BaseModel):
    key: ExecutionBudgetKey
    limit: int

class ExecutionBudgetPlan(BaseModel):
    schema_version: Literal["1.0"]
    run_id: UUID
    limits: tuple[ExecutionBudgetLimit, ...]

class ExecutionBudgetSnapshot(BaseModel):
    schema_version: Literal["1.0"]
    run_id: UUID
    key: ExecutionBudgetKey
    limit: int
    consumed: int
    remaining: int

class BudgetConsumeRequest(BaseModel):
    operation_id: OperationId
    key: ExecutionBudgetKey
    amount: int

class BudgetConsumed(BaseModel):
    outcome_type: Literal["consumed"] = "consumed"
    disposition: Literal["applied", "replayed"]
    operation_id: OperationId
    key: ExecutionBudgetKey
    amount: int
    snapshot: ExecutionBudgetSnapshot

class BudgetExceeded(BaseModel):
    outcome_type: Literal["exceeded"] = "exceeded"
    disposition: Literal["rejected", "replayed"]
    operation_id: OperationId
    key: ExecutionBudgetKey
    amount: int
    snapshot: ExecutionBudgetSnapshot

BudgetConsumeOutcome = Annotated[
    BudgetConsumed | BudgetExceeded,
    Field(discriminator="outcome_type"),
]
```

整数上限是安全整数上限。limit 允许 0；amount 范围是 1..安全整数上限。run scope 要求 phase 为 `None`；phase scope 要求 phase 非空。Plan 不允许重复 key。Snapshot 要求 `0 <= consumed <= limit` 且 `remaining = limit - consumed`。

### 13.3 端口与参考实现

位置：`budgets/manager.py`。

```python
class ExecutionBudgetManager(Protocol):
    async def consume(self, request: BudgetConsumeRequest) -> BudgetConsumeOutcome: ...
    async def get_snapshot(self, *, key: ExecutionBudgetKey) -> ExecutionBudgetSnapshot: ...
    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]: ...
```

不提供 reserve、commit、release、reset、clear、动态升限、close 或上下文管理器。

`ExecutionBudgetManagerLimits` 位于 `budgets/models.py`；Demo limits 常量与参考实现位于 `budgets/in_memory.py`。

```python
class ExecutionBudgetManagerLimits(BaseModel):
    max_operation_records: int

DEMO_EXECUTION_BUDGET_MANAGER_LIMITS = ExecutionBudgetManagerLimits(
    max_operation_records=4_096,
)

class InMemoryExecutionBudgetManager:
    def __init__(
        self,
        *,
        plan: ExecutionBudgetPlan,
        limits: ExecutionBudgetManagerLimits,
    ) -> None: ...
```

`max_operation_records` 范围是 1..安全整数上限。

一个 Manager 只服务一个 Plan/run，内部单个 `asyncio.Lock` 线性化所有消费与快照读取。构造函数必须按 §17 两段式 envelope 复验并复制 plan/limits，不保留调用方对象引用；无效 plan 或 limits 统一抛 `ExecutionBudgetPlanValidationError`。首次 async 公共方法调用绑定当前 running event loop；以后跨 loop 调用先抛 `ExecutionBudgetManagerUsageError`。

consume 的校验顺序固定为：loop owner → 按 §17 两段式 envelope 复验并复制 request → key 是否配置 → operation run 是否匹配 Plan → phase scope 是否匹配 operation phase → 锁内既有 OperationId replay/conflict → 新 operation record 容量 → limit 足额/超限。request 复验失败抛 `ExecutionBudgetRequestValidationError`，不保留调用方对象引用。多个条件同时错误时只报告最先命中的 typed error。`get_snapshot` 使用 loop owner → §17 两段式 envelope 复验并复制 key（失败抛 `ExecutionBudgetRequestValidationError`）→ key 是否配置；`get_all_snapshots` 使用 loop owner → 锁内快照。

首次 consume 在额度足够时原子增加 consumed 并返回 applied；额度不足时不改 consumed，记录 rejected 结果并返回 `BudgetExceeded`。成功、失败、超时或无结果均不退款。相同 OperationId/key/amount 重放返回原始 snapshot 与 replayed；改变 key 或 amount 抛 `BudgetConsumeConflictError`。

成功与 rejected 首次请求都占一条操作记录。达到 4096 条后，已有请求仍可 replay，新 OperationId 抛 `ExecutionBudgetCapacityExceededError`；不驱逐。快照排序键固定为 scope Enum 声明顺序（run 后 phase）、phase 的 `None` 后声明顺序、name 的 Unicode code point 顺序。

### 13.4 预算所有权接线表

下表是下游消费合同，不是模块 04 生产常量：

| key name | scope/phase | Demo limit | 消费所有者 |
|---|---|---:|---:|
| `requirements.clarification_round` | phase/requirements | 6 | 05 |
| 模块 03 extract+repair | 模块 03 Session 私有 | 2 | 03，不进入模块 04 账本 |
| `hotel.place_call` | phase/hotel_selection | 3 | 07 |
| `research.iteration` | phase/candidate_research | 6 | 08 |
| `research.place_call` | phase/candidate_research | 48 | 08 |
| `planning.model_invocation` | phase/itinerary_planning | 17 | 09；分类计数及调用前准入见本次增量设计 §4 |
| `planning.direction_call` | phase/itinerary_planning | `8 * days + 2` | 09；13 解析公式 |
| `workflow.full_phase_restart` | run/None | 0 | 13 |

地图并发上限 3 属于模块 06 semaphore，不是 consume 预算。模块 13 构造 Plan；模块 04 不硬编码表中业务值。

## 14. 安全事件合同

### 14.1 DTO 与注册

位置：`events/models.py`。

```python
class SafeCounterDelta(BaseModel):
    name: SafeCounterName
    delta: int

class SafeWorkflowEvent(BaseModel):
    schema_version: Literal["1.0"]
    event_id: UUID
    sequence_number: int
    occurred_at: datetime
    run_id: UUID
    trace_id: UUID
    operation_id: OperationId | None
    phase: WorkflowPhase
    event_type: WorkflowEventType
    counter_deltas: tuple[SafeCounterDelta, ...]
    duration_ms: int | None
    terminal_code: TerminalCode | None
```

`sequence_number` 范围是 1..安全整数上限；`occurred_at` 必须是 UTC；delta 范围是 1..安全整数上限；duration 范围是 0..安全整数上限。counter name 在单事件内唯一并排序。operation 非空时 run/phase 与事件一致。空 counter tuple 和 nullable 字段必须显式传入。

位置：`events/registry.py`。

```python
class SafeWorkflowEventRegistration(BaseModel):
    event_type: WorkflowEventType
    counter_names: tuple[SafeCounterName, ...]
    operation_id: FieldRequirement
    duration_ms: FieldRequirement
    terminal_code: FieldRequirement

class SafeWorkflowEventRegistry:
    def __init__(self, registrations: tuple[SafeWorkflowEventRegistration, ...]) -> None: ...
    def resolve(self, event_type: WorkflowEventType) -> SafeWorkflowEventRegistration: ...
    def validate(self, event: SafeWorkflowEvent) -> None: ...

class EventDeliveryFailureCodeRegistry:
    def __init__(self, codes: tuple[EventDeliveryFailureCode, ...]) -> None: ...
    def validate(self, code: EventDeliveryFailureCode) -> None: ...
```

counter name tuple 必须按 Unicode code point 排序且唯一，事件中的 counter 集合必须与注册项精确一致。注册项集合不可变；重复或自相矛盾注册失败。未知 event type 失败。`SafeWorkflowEventRegistry.validate()` 先按 §17 两段式 envelope 把 event 复验成 fresh DTO；结构、类型或事件自身不变量错误抛 `SafeWorkflowEventValidationError`，随后才 resolve event type。

Failure-code Registry 构造时先逐项 strict 校验 `EventDeliveryFailureCode` 格式，再检查重复；格式非法或重复均抛 `SafeWorkflowEventRegistryConfigurationError`，之后按 Unicode code point 排序并冻结。`validate()` 同样先 strict 校验格式，再查 membership；格式非法或未注册 code 均抛 `EventSinkContractError`。两个 Registry 均显式注入，不做自动扫描。

两个 `resolve/validate` 路径可以返回或读取 Registry 持有的 frozen registration/string；不得返回内部可变容器。

### 14.2 标准事件目录

公开不可变 tuple `STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS` 固定包含：

| event type | counter | operation | duration | terminal |
|---|---|---|---|---|
| `workflow.started` | `workflow.started` | forbidden | forbidden | forbidden |
| `workflow.resumed` | `workflow.resumed` | forbidden | forbidden | forbidden |
| `workflow.replayed` | `workflow.replayed` | required | forbidden | forbidden |
| `workflow.terminal` | `workflow.terminal` | forbidden | required | required |
| `artifact.created` | `artifact.created` | required | forbidden | forbidden |
| `artifact.reused` | `artifact.cache_hit` | required | forbidden | forbidden |
| `budget.consumed` | `budget.units_consumed` | required | forbidden | forbidden |
| `budget.exceeded` | `budget.exceeded` | required | forbidden | forbidden |

`budget.units_consumed` delta 只记录首次 applied amount；replay 不重复累计。`artifact.cache_hit` 只统计稳定 OperationId 的 Artifact 复用。`workflow.resumed` 不携带 interrupt/checkpoint ID。生命周期事件由模块 13 产生；模块 04 只冻结名称和字段合同。

除 `budget.units_consumed` 外，七种标准 occurrence counter 的 delta 固定为 1；Event Registry 对这七种类型执行该校验。`budget.units_consumed` 的 delta 由模块 13 从首次 applied amount 复制，模块 04 Event DTO 无业务 amount 字段，因此 Registry 只验证其为正整数。

### 14.3 Sink

`EventEmitted`、`EventDeliveryFailed` 与 `EventEmissionOutcome` 位于 `events/models.py`；Sink Protocol 与 NoOp 实现位于 `events/sink.py`。

```python
class EventEmitted(BaseModel):
    outcome_type: Literal["emitted"] = "emitted"
    disposition: Literal["applied", "replayed"]
    event_id: UUID

class EventDeliveryFailed(BaseModel):
    outcome_type: Literal["delivery_failed"] = "delivery_failed"
    event_id: UUID
    failure_code: EventDeliveryFailureCode

EventEmissionOutcome = Annotated[
    EventEmitted | EventDeliveryFailed,
    Field(discriminator="outcome_type"),
]

class SafeWorkflowEventSink(Protocol):
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome: ...

class NoOpSafeWorkflowEventSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome: ...
```

公开不可变 tuple `STANDARD_EVENT_DELIVERY_FAILURE_CODES` 固定为 `sink_unavailable`、`sink_timeout`、`sink_rejected`、`sink_capacity_exceeded`。新增 code 必须由所属 Adapter 显式加入注入的 Failure-code Registry 并提供合同测试。未知程序错误不能伪装成 `unknown_error`。

安全发送顺序固定为：调用方先执行 `SafeWorkflowEventRegistry.validate(event)` → `sink.emit(event)` → 要求返回值精确属于 `EventEmitted` 或 `EventDeliveryFailed`，并按 §17 两段式 envelope 复验成 fresh `EventEmissionOutcome` → 校验 outcome event ID 与输入一致 → delivery failure 时执行 `EventDeliveryFailureCodeRegistry.validate(code)`。dict、任意其他 BaseModel、子类或 outcome dump/shape/discriminator/字段/ID/code 任一步失败均抛 `EventSinkContractError`。Registry 校验是 Sink 之前的强制步骤；Sink Protocol 不重复承担 event type 注册校验。NoOp Sink 总是返回 emitted/applied。已知基础设施投递故障返回 `EventDeliveryFailed`，不改变业务结果；错误 event ID、未知 failure code、Sink 协议、身份或序号冲突抛异常并失败关闭。

模块 04 和模块 13 不自动重试 EventDeliveryFailed。模块 20 可以用完全相同的 event ID、sequence、time 和字段执行有界重试；成功重试返回 emitted/replayed，不重复本地 counter。

具备记录能力的 Sink 对同一 event ID、全部字段一致的重发返回 replayed 且不重复计数；同一 ID 内容不同抛 `EventIdentityConflictError`；同一 run/sequence 对应不同事件抛 `EventSequenceConflictError`。NoOp Sink 是明确例外：它不保留去重状态，每次都返回 applied；模块 13 的本地聚合负责防止摘要双计。逻辑顺序按 per-run sequence，不按时间或到达顺序。重试重发原 event；Graph replay 产生新的 `workflow.replayed` event ID 与 sequence。

`RecordingSafeWorkflowEventSink` 只位于 `tests/unit/workflow_contracts/fakes.py`，不进入生产包或包根导出。其 `max_events` 范围是 0..安全整数上限；emit 先判断精确 replay/identity/sequence，再对新事件检查容量，因此容量满时已有事件精确 replay 仍成功且不增加记录。测试 fake 的只读 `events` property 不违反“生产 Sink 无读取 API”边界。

## 15. RunSummary

位置：`summary.py`。

```python
class SafeCounterValue(BaseModel):
    name: SafeCounterName
    value: int

class RunSummary(BaseModel):
    schema_version: Literal["1.0"]
    run_id: UUID
    trace_id: UUID
    terminal_code: TerminalCode
    duration_ms: int
    cache_hits: int
    event_delivery_failures: int
    counters: tuple[SafeCounterValue, ...]
    budget_snapshots: tuple[ExecutionBudgetSnapshot, ...]
```

所有整数范围是 0..安全整数上限。counter name 唯一并按 Unicode code point 排序；budget snapshot 属于同一 run、budget key 唯一，并按 scope Enum 声明顺序、phase 的 None 后声明顺序、name Unicode 顺序排列。`cache_hits` 只来自稳定 OperationId Artifact 复用，且必须等于 counters 中 `artifact.cache_hit` 的 value；该 counter 缺失时 `cache_hits` 必须为 0。

模块 13 在具备完整本次运行诊断证据时，于终态只生成一次 RunSummary。run、trace、terminal code 与最终 state 一致；duration 使用注入的 monotonic clock。模块 03 的 adapter-specific 安全调用事件由模块 13 转成已注册 counter 后汇入，模块 04 不导入或重定义 `ExtractionInvocationEvent`。摘要不复制 delivery status、phase、TerminalOutcome、事件列表、ArtifactRef、异常、用户内容、Prompt、模型或 Provider 字段。

事件聚合、per-run sequence 和 16,384 条运行级安全事件容量属于模块 13 下游实现约束。模块 04 不提供 EventAccumulator 或 `SafeEventAccumulatorLimits`。Sink 投递失败时，模块 13 的本地安全聚合仍保留计数并增加 `event_delivery_failures`；NoOp Sink 不导致摘要计数归零。

### 15.1 首版完整摘要不可用例外（D-13-10）

跨进程恢复后，单进程事件聚合或 monotonic 起点不完整时，M13 返回 summary=None 与有限原因 process_history_unavailable，不得用零值、局部统计或墙钟推算冒充整次 RunSummary。该诊断缺失不改变已通过业务门槛的交付状态。当前进程内重复终态读取可复用同一完整摘要；M13本身重启后不重建它。M14计划同库保存真正曾生成的最终完整摘要，重启只读回并复验已存值，而非重建观测历史；保存/失败例外/诊断释放见 [M14 §9](14_web_application.md)。无保存值时仍不能合成完整摘要，本DTO与state codec不变。

完整 RunSummary 的必填字段、严格校验、排序、缓存计数与终态一致性全部不变；不向该 DTO 填 None，也不为此改 TravelSessionState v2。M13 包装 DTO、聚合和测试见 13_demo_workflow.md §12。预算/journal 的持久化与恢复安全不能以本例外豁免。只有已知观测投递故障可不影响业务，协议违规仍按 §14 失败关闭。

## 16. Clock 与 UUID Provider

位置：`providers.py`。

```python
class UtcClock(Protocol):
    def now(self) -> datetime: ...

class MonotonicClock(Protocol):
    def now_ns(self) -> int: ...

class UUIDFactory(Protocol):
    def new(self) -> UUID: ...

class SystemUtcClock:
    def now(self) -> datetime: ...

class SystemMonotonicClock:
    def now_ns(self) -> int: ...

class RandomUUIDFactory:
    def new(self) -> UUID: ...
```

系统实现分别使用 `datetime.now(timezone.utc)`、`time.monotonic_ns()`、`uuid.uuid4()`。接口同步且无 I/O。UUID Factory 只生成 artifact/event 基础设施 ID；run ID、trace ID、OperationId 均由调用方传入。模块 13 负责 monotonic start/end 与回拨错误；模块 04 不定义其聚合器。

## 17. 错误与 outcome 矩阵

位置：`errors.py`。所有类直接或间接继承 `WorkflowContractsError`。每个类都是零参数 marker exception，不增加实例字段；`str(error)` 固定为类名。公开边界包装底层错误时一律 `raise PublicTypedError() from None`。异常不携带正文、原始 DTO、ID、hash、供应商 payload、Prompt 或底层验证输入；诊断关联使用调用方已有 run/trace ID，不塞进异常。

```text
WorkflowContractsError
├── WorkflowStateContractError
│   ├── UnsupportedWorkflowStateVersionError
│   └── WorkflowStateValidationError
├── TerminalContractError
│   ├── TerminalRegistryConfigurationError
│   ├── UnsupportedTerminalCodeError
│   └── TerminalStateMismatchError
├── ArtifactRepositoryError
│   ├── ArtifactRegistryConfigurationError
│   ├── ArtifactRequestValidationError
│   ├── UnsupportedArtifactTypeError
│   ├── UnsupportedArtifactVersionError
│   ├── ArtifactExpectedModelMismatchError
│   ├── ArtifactNotFoundError
│   ├── ArtifactConflictError
│   ├── ArtifactIntegrityError
│   ├── ArtifactTooLargeError
│   ├── ArtifactCapacityExceededError
│   ├── ArtifactRepositoryDependencyError
│   └── ArtifactRepositoryUsageError
├── ExecutionBudgetError
│   ├── ExecutionBudgetPlanValidationError
│   ├── ExecutionBudgetRequestValidationError
│   ├── BudgetKeyNotConfiguredError
│   ├── BudgetOperationScopeMismatchError
│   ├── BudgetConsumeConflictError
│   ├── ExecutionBudgetCapacityExceededError
│   └── ExecutionBudgetManagerUsageError
├── WorkflowReplaySafetyError
│   └── OperationResultIndeterminateError
└── SafeWorkflowEventError
    ├── SafeWorkflowEventRegistryConfigurationError
    ├── SafeWorkflowEventValidationError
    ├── UnsupportedWorkflowEventTypeError
    ├── EventIdentityConflictError
    ├── EventSequenceConflictError
    └── EventSinkContractError
```

直接调用 Pydantic DTO 构造器属于 Python 编程边界，可以产生 Pydantic `ValidationError`。行为边界收到 Pydantic DTO 时，不得用 `validate_python(existing_instance)` 充当复验，固定两段式 envelope 算法如下：

1. 执行 `model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)` 得到普通 Python tree；把该 tree 而非 existing instance 交给目标 TypeAdapter 的 `validate_python(..., strict=True)`，获得第一个 fresh DTO。该 preflight 必须先于任何 `mode="json"` dump，防止 datetime、UUID、bytes、tuple 或任意对象被序列化洗白。
2. 对第一个 fresh DTO 执行 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)`，用 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` 生成 bytes，再交给同一 TypeAdapter 的 `validate_json(..., strict=True)`，获得第二个 fresh DTO；行为逻辑只使用第二个 fresh DTO。

任何一步的 dump/encode/validate 失败均按下表包装。Registry descriptor 中的 class 等非 JSON 配置字段不执行该算法，按各 Registry 构造规则直接 TypeAdapter/identity/type-graph 检查。Sink outcome 还要求原始返回值的精确类型是 `EventEmitted` 或 `EventDeliveryFailed`，dict、任意 BaseModel 或子类先失败。`model_construct()` 与构造后嵌套 mutation 都不能绕过行为边界复验。

错误优先级固定为：

| 公共操作 | 顺序 |
|---|---|
| state serialize | Python-tree strict preflight → v2 strict fields → public transition consistency → terminal/Artifact invariants → strict JSON round-trip；任意 forged/version/mismatch/validation 失败 → 零参数 `WorkflowStateValidationError` |
| state parse | bytes/UTF-8/JSON/top object → 顶层重复 version → 精确 `"2.0"`（否则 `UnsupportedWorkflowStateVersionError`）→ v2 duplicate/illegal number → v2 strict fields → public transition consistency → terminal/Artifact invariants；版本识别后的任意失败 → 零参数 `WorkflowStateValidationError` |
| terminal registry construction | registration 字段 strict 复验 → duplicate/semantic；任一失败 → `TerminalRegistryConfigurationError` |
| terminal validate | fixed state copy；结构失败 → `TerminalStateMismatchError`；其后 nonterminal 直接成功，terminal code resolve → status → outcome presence → category → recoverable → action |
| artifact registry construction | descriptor 字段 → type graph → duplicate；任一失败 → `ArtifactRegistryConfigurationError` |
| artifact repository construction | strict limits copy；失败 → `ArtifactRepositoryUsageError` |
| artifact put | loop owner → strict request copy → Registry type → version → registered model/shape/canonical → existing operation replay/conflict → per-item/count/total capacity → UUID/Clock dependency |
| artifact lookup | loop owner → strict OperationId copy → found/absent outcome |
| artifact get | loop owner → strict Ref copy → artifact ID found → Ref 七字段 → registered type/version → expected model identity → hash/size → strict decode/recanonical bytes |
| artifact registry decode | strict Ref copy → type/version → expected model identity → canonical bytes type → hash/size → strict decode/recanonical bytes |
| budget manager construction | strict plan/limits copy |
| budget consume | loop owner → strict request copy → configured key → run → phase → existing operation replay/conflict → operation-record capacity → limit |
| budget get_snapshot | loop owner → strict key copy → configured key |
| event registry construction | strict registration copy → duplicate/semantic；任一失败 → `SafeWorkflowEventRegistryConfigurationError` |
| event validate | strict event copy → event type resolve → registered field组合 → standard delta |
| failure-code registry construction | code 格式 → duplicate；任一失败 → `SafeWorkflowEventRegistryConfigurationError` |
| failure-code validate | code 格式 → membership；任一失败 → `EventSinkContractError` |
| sink outcome | strict union copy → event ID → failure code；任一失败 → `EventSinkContractError` |

同一调用同时存在多个错误时只暴露表中最先命中的类型。

| 条件 | 表现形式 | 调用方动作 |
|---|---|---|
| Artifact lookup 未命中 | `ArtifactAbsent` | 可进入预算许可流程 |
| Artifact get 未命中 | exception | 停止并交给模块 13 映射 |
| Artifact conflict/integrity | exception | 失败关闭，禁止覆盖或外部重调 |
| Budget 首次超限 | `BudgetExceeded(rejected)` | 禁止外部调用 |
| Budget 相同请求重放 | consumed/exceeded replayed outcome | 不重复扣减 |
| Budget key/operation 冲突 | exception | 失败关闭 |
| Event 已知投递故障 | `EventDeliveryFailed` | 业务继续，摘要计数失败 |
| Event 合同或身份冲突 | exception | 失败关闭 |

异常和 typed outcome 不得对同一条件随机混用。模块 04 错误不直接写 delivery status 或 terminal code；模块 13 显式映射。

## 18. 敏感数据与 Memory 边界

Artifact 禁止保存：API key、cookie、token、认证头、system/developer Prompt、隐藏 reasoning、完整用户对话、原始 Prompt、原始模型/地图/MCP/供应商响应、HTTP body、异常对象、堆栈、日志转储、任意 metadata dict、二进制和 Base64 文件。

允许内容仅是注册严格 DTO 的明确字段，包括业务所需日期、城市、坐标、约束、已验证事实和结构化结果。DTO 所属模块负责字段白名单；Repository 负责结构、规范化、完整性、幂等与容量。模块 04 不承诺通过正则发现全部秘密。

`TravelSessionState.requirement_state`、`TravelSessionState.requirement_outcome`、`ArtifactWriteRequest.content`、`ArtifactReadResult.content` 使用 `repr=False`。测试使用 canary secret 验证 repr、模块 04 typed exception、事件和摘要不泄露。直接 Pydantic `ValidationError` 可能回显调用方输入，不得写日志；正式行为边界必须按 §17 包装。

Memory 分层固定为：工作状态由 Checkpointer 管理；结构化中间产物由 Artifact Repository 管理；安全历史由 Event/Audit Adapter 管理；长期语义 Memory 留在 Future。三者不得塞入单一 Repository。模块 04/05 的独立参考实现仍为进程内；完整 Demo 由模块 10/11 把前两类状态持久化到 PostgreSQL，安全历史的生产持久化仍由模块 20/21 另行设计。

## 19. 生命周期与资源所有权

- 调用方创建并持有 Registry、Repository、Budget Manager、Clock、UUID Factory 和 Sink。
- 模块 04 不读取 `.env`、不创建全局 singleton、不管理连接池。
- `InMemoryArtifactRepository` 可服务多个 run；OperationId run 字段隔离逻辑键。
- `InMemoryExecutionBudgetManager` 每实例只服务一个 run/Plan；resume 必须复用原实例。
- NoOp Sink 不持有资源，不需要关闭。
- 测试通过创建新实例隔离，不公开 clear。
- PostgreSQL Artifact/checkpoint/执行账本 Adapter 的连接、事务与关闭由模块 10/11 分别冻结；加密、TTL 和删除由模块 19 冻结，安全事件持久化与审计由模块 20/21 冻结；本模块不预设。

## 20. 公共导出

`app/workflow_contracts/__init__.py` 必须显式导入并在 `__all__` 中列出以下名称：

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

禁止 wildcard export。`PreparedArtifact`、canonical helper、内部 record/lock、Recording Sink、测试 Artifact 不从包根导出。合同测试同时验证关键名称可导入和私有名称未泄露。

## 21. 版本升级规则

`TravelSessionState` 当前公共信封只支持 `"2.0"`；`TerminalOutcome` 与其他已声明 `Literal["1.0"]` 的独立 DTO 仍保持其自身 1.0。以下变化必须升级所属 schema：字段增删、类型变化、required/null/default 变化、跨字段不变量变化、封闭 Enum 变化、规范 JSON/hash/OperationId 语义变化、outcome discriminator 或幂等行为变化。

以下变化不升级信封 schema：不改变行为的文档修订、新 Artifact type/version、terminal code、event type、counter name、budget key 注册项、新 Adapter、不可观察性能优化。

未知版本失败关闭，不做隐式迁移。本次不提供 v1→v2 函数、deprecated alias、双版本 parser 或 process-local checkpoint 兼容；模块 05 启动前丢弃旧 v1 checkpoint，并以显式默认 `RequirementDomainState()` 创建新 session。Artifact 内容版本独立于 state 版本。删除或改变已有注册语义属于破坏性变更，必须进入升级设计。

## 22. 测试矩阵

| 测试族 | 必须覆盖 |
|---|---|
| WF-STATE | v2 十字段顺序、required/non-null requirement state、strict/frozen/extra、三轴正交、五分支 transition 一致、invalidation 六字段含 summary、初始 None 唯一例外、Artifact 排序、版本先行、UTF-8 round-trip、v1 拒绝 |
| REQ-CONTINUITY | 当前 `RequirementDomainState` → 模块 01 transition → `TravelSessionState` v2 JSON → 下一轮 `RequirementTurnInput.current` 全字段等值；state/outcome 双向 mutation 与调用输入隔离；NeedConfirmation/invalidation summary 展示后用同一 state-based accept |
| ART-REPO | 类型注册、严格 JSON、hash 向量、Unicode、数字、时间、Ref、lookup/get/put、幂等矩阵、容量；嵌套合规模型、Annotated/Literal、自递归图终止；任意深度 RootModel/bytes/set/Mapping/default/违规 config 拒绝；transforming validator 拒绝；tuple 内 datetime 递归规范化 |
| ART-CONC | 同 key 同内容、同 key 冲突、原子索引、get-after-put、失败无部分记录 |
| EXEC-BUD | Plan/key/snapshot、零 limit、consume/replay/conflict/exceeded、4096 容量、排序 |
| EXEC-CONC | 并发消费不超限、相同 OperationId 单扣、冲突线性化 |
| SAFE-EVT | DTO、Registry 精确字段、八种标准注册、通用 Sink outcome/ID/failure-code；Recording-only 身份/序号/replay/capacity；NoOp 每次 applied |
| RUN-SUM | 安全字段、排序、same-run、JSON round-trip、无 payload |
| SECURITY | canary 不进入 repr、异常、事件、摘要；禁止 import 与私有 export；对各行为边界用 `model_construct()`/嵌套 mutation 注入错误 scalar、datetime、UUID、bytes、tuple、set，必须得到对应 typed error 且不被 JSON dump 洗白 |
| PROPERTY | 确定性表驱动不变量与边界向量，不引入随机属性测试库 |
| REGRESSION | 模块 01–03 全部 unit/contract 离线回归 |

并发测试必须用 `asyncio.Event`、barrier 与 `asyncio.gather()`，禁止依赖 sleep。不得 skip/xfail。

普通模块 04 Gate 的覆盖率命令固定为：

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

`--cov-fail-under=95` 保留 pytest-cov 总门槛；JSON 后处理还必须计算 `combined = 100 * (covered_lines + covered_branches) / (num_statements + num_branches)` 并要求 line、branch、combined 三项分别不低于 95%。`num_branches == 0` 时 branch 单项为 100%，combined 仍使用相同公式；只有 combined 分母为 0 才是配置错误并失败。coverage 数据只能写入已忽略的 `.pytest_cache/`。需求状态连续性 maintenance 的正式 Docker 命令与安全参数以配套 Vibe `RSC-T05` 为准，宿主机结果不作为验收证据。

## 23. 验收标准

模块 04 实现只有同时满足以下条件才可标记完成：

1. 文件树、公共符号、签名、字段、默认值与本文一致。
2. 生产包只导入标准库、Pydantic 与 `app.requirements` 包根。
3. 所有公共 DTO strict、frozen、extra forbid，JSON round-trip 通过。
4. Artifact Registry、规范 JSON、hash 与 Repository 合同全部通过。
5. 幂等、并发、容量、失败无部分记录与安全重放测试全部通过。
6. 执行预算原子消费与 replay disposition 全部通过。
7. 安全事件 Registry、NoOp Sink、Terminal Registry 与 RunSummary 测试全部通过。
8. 模块自身 line/branch/combined coverage 分别不低于 95%。
9. 模块 01–03 完整离线回归无失败。
10. 没有 Graph、数据库、网络、Provider、MCP、CLI 或长期 Memory 实现。

## 24. 停止条件

实施者遇到以下任一条件必须停止并报告：

- 模块 01 包根公共合同与本文不一致。
- 当前分支、HEAD 或工作区不符合实施任务前置条件。
- full rebuild 需要修改模块 01–03 时停止。需求状态连续性 maintenance 只允许修改配套编码契约明确列出的模块 01 文件，以及模块 03 的单一直接消费合同测试；若需要修改这些白名单以外的模块 01–03 文件、改变模块 03 adapter 生产语义、修改上位文档、`pyproject.toml` 依赖或其他白名单外文件，则停止。
- 必须新增第三方依赖才能实现冻结合同。
- Python/Pydantic 实际行为无法同时满足 strict JSON 与规范化向量。
- 需要改变任何已冻结字段、签名、Enum、异常、容量或幂等语义。
- 测试只能通过 skip、xfail、删除断言、放宽 strict 或暴露内部状态。
- 发现会泄露用户内容、Prompt、secret 或供应商 payload 的路径。

## 24.1 状态连续性 maintenance 与 MODULE-CLOSEOUT

模块 01/04 连续性修订固定执行配套 Vibe 的 `RSC-T00..RSC-T06`。阶段 A 允许把 transition 不存在、模块 01 裸 outcome、模块 04 v1 九字段识别为预期旧基线；阶段 A 必须先完成模块 01 transition 和模块 03 直接消费测试。只有阶段 A PASS 且模块 01 包根公开 State/Outcome/Transition 后，阶段 B 才能修改模块 04 state v2、Codec 和连续性/public/import boundary。Artifact、Budget、Event 不属于该 maintenance，不得修改或重新实施。

正式验收必须从当前源码重新构建 runtime/test Docker 镜像，并在新建 `--rm --network none --read-only --tmpfs /tmp` 容器中执行版本核验、模块 01/03/04 定向测试、模块 01/04 各自 line/branch/combined 三项 coverage、compileall、模块 01～04 全 unit/contract 回归及无残留容器检查。宿主机 Python 不能作为验收证据。

全部代码与 Docker Gate 通过后必须执行 `MODULE-CLOSEOUT`：真实同步 `README.md`、`docs/IMPLEMENTATION_STATUS.md`、`Dockerfile`；无 Dockerfile 修改时也必须给出基于当前 COPY/CMD 的具体影响判断；同步后重新构建两镜像并重跑 runtime 默认 CMD 和完整回归。代码实施与 closeout 期间四份模块 01/04 权威设计/Vibe 只读，模块 05 继续暂停。实施模型不得 stage 或 commit；独立审查通过后须取得用户新的明确授权才可提交。

## 25. Production 替换边界

Module 10/11 可以用 PostgreSQL Adapter 替换进程内 Repository、Saver 和执行预算账本，但必须保持端口、typed outcome、幂等、完整性、失败关闭和安全字段合同；具体数据库事务、持久 ID、崩溃恢复与迁移分别由两份模块设计冻结。模块 19 冻结加密、保留、删除与 key 生命周期；20+21 冻结队列、指标、Trace、审计、redaction 与生产装配。

Production 不得用数据库能力反向扩大 Graph state，不得把原始 payload 变成 Artifact 或事件，也不得把跨系统 exactly-once 作为模块 04 已提供保证。

## 26. 决策记录

| ID | 已确认结论 | 落点与所有者 |
|---|---|---|
| D-01 | state 只作小型控制平面信封，大对象只经稳定 outcome 或 Ref | §6，04 |
| D-02 | 直接复用包根 `RequirementDomainState`、`RequirementDomainOutcome` 与 `RequirementDomainTransition` | §6.6，01/04 |
| D-03 | state v2.0、run/trace UUID 全由调用方注入 | §6.6，04 |
| D-04 | 七个 Demo phase 构成封闭 Enum | §6.1，04 |
| D-05 | 六个 delivery status 与 terminal snapshot 矩阵 | §6.2/6.6，04 |
| D-06 | 模块 04 不定义状态机；完整状态机另立模块 13 文档 | §3，下游 05/13 |
| D-07 | TerminalCode 是已注册 lower_snake 受限字符串 | §6.3/7，04 |
| D-08 | TravelSessionState 精确十字段且全部显式传入；requirement_state 位于 requirement_outcome 前 | §6.6，04 |
| D-09 | state 只支持 2.0，v1/unknown version 失败关闭且无迁移 | §6.7/21，04 |
| D-10 | TerminalOutcome 六字段与恢复动作不变量 | §6.5，04 |
| D-11 | ArtifactRef 精确七字段 | §8.1，04 |
| D-12 | Artifact type/version/hash 的正则与显式 Registry | §6.3/8.3，04 |
| D-13 | 写入只接收注册 JSON object，Repository 生成 Ref 元数据 | §8.2/8.3，04 |
| D-14 | 标准库规范 JSON、SHA-256、Unicode/数字/时间规则 | §9，04 |
| D-15 | get 返回 typed envelope，每次复验完整性 | §8.2/8.3，04 |
| D-16 | ArtifactRepository 只有 async put/lookup/get | §10，04 |
| D-17 | OperationId 固定 run+phase+logical_step | §6.4，04 |
| D-18 | 一个 OperationId 对应一个逻辑 Artifact，冲突不覆盖 | §10.1，04 |
| D-19 | 进程内双索引、单 asyncio lock、线性化写入 | §11，04 |
| D-20 | 进程生命周期与 1 MiB/2048/64 MiB 容量 | §11，04 |
| D-21 | 执行预算只提供原子 consume，不退款 | §13，04 |
| D-22 | applied/rejected 与 replayed disposition 明确区分 | §13.2/13.3，04 |
| D-23 | run/phase scope、两段式 key、Plan/Snapshot | §13.2，04 |
| D-24 | 每 run Manager 的 consume/get snapshot 三方法 | §13.3，04 |
| D-25 | lookup→consume→外部调用→put 的失败关闭顺序 | §12，下游消费合同 |
| D-26 | Demo 预算 key/value/消费所有权矩阵 | §13.4，下游 03/05/07–09/13 |
| D-27 | SafeWorkflowEvent/Sink/NoOp 命名与显式事件注册 | §14，04 |
| D-28 | SafeWorkflowEvent 精确十二字段 | §14.1，04 |
| D-29 | Sink typed emission outcome；已知投递失败不影响业务 | §14.3，04 |
| D-30 | per-run sequence、event ID 幂等与冲突语义 | §14.3，04/下游 13 |
| D-31 | RunSummary 精确九字段及安全聚合规则 | §15，04 DTO/13 构造 |
| D-32 | Checkpoint、Artifact、Audit、长期 Memory 分离 | §18，05/10/11/20–21/Future |
| D-33 | 完整成功 terminal code 固定 `workflow_completed` | §7.2，04/13 |
| D-34 | UtcClock、MonotonicClock、UUIDFactory 分离 | §16，04 |
| D-35 | state ArtifactRef same-run、唯一、确定排序 | §6.6，04 |
| D-36 | Artifact 字段白名单与敏感内容禁止项 | §18，04/DTO owner |
| D-37 | Budget operation record 上限 4096 | §13.3，04 |
| D-38 | RunSummary 统计不依赖 Sink 投递成功 | §15，下游 13 |
| D-39 | 事件注册同时约束 counter/operation/duration/terminal | §14.1，04 |
| D-40 | 八种标准安全事件目录 | §14.2，04 合同/13 产生 |
| D-41 | Artifact Repository 公共异常层级 | §17，04 |
| D-42 | 执行预算与安全重放异常层级 | §17，04 |
| D-43 | 运行级事件容量 16,384，不进入模块 04 实现 | §15，下游 13 |
| D-44 | 安全事件 Registry/Sink 异常层级 | §17，04 |
| D-45 | 四个标准 Sink failure code 与无自动重试 | §14.3，04/20 |
| D-46 | 所有权闸门：聚合、状态机、持久化不在模块 04 | §3，跨模块 |
| D-47 | 固定 `app/workflow_contracts` 分层包结构 | §4，04 |
| D-48 | ArtifactSchemaRegistry resolve/prepare/decode | §8.3，04 |
| D-49 | state 正式 UTF-8 JSON bytes Codec | §6.7，04 |
| D-50 | TerminalCodeRegistration/Registry 与两个基础 code | §7，04 |
| D-51 | 模块 04 不定义业务 Artifact DTO 或 generic fallback | §8.3，04 |
| D-52 | 两个进程内实现显式注入 Registry/Provider/limits | §11/13.3，04 |
| D-53 | 顶层包根是唯一稳定公开入口 | §4/20，04 |
| D-54 | 不新增依赖，只用标准库/Pydantic/模块 01 包根 | §2.3，04 |
| D-55 | 离线 deterministic test 与 line/branch 95% Gate | §22/23，04 |
| D-56 | requirement/content 字段 `repr=False` | §5/18，04 |
| D-57 | strict JSON 验证与 bool/int/float 类型保持 | §8.3/9，04 |
| D-58 | state 与 terminal typed error 层级 | §17，04 |
| D-59 | 独立 schema 版本升级规则；TravelSessionState 合同版本为 2.0 且不迁移 v1 | §21，04 |
| D-60 | 冻结设计文档不保存易变实施快照；当前实现与验证事实只在统一状态页维护 | 文档头部，跨模块 |
| D-61 | D-13/D-57 修订：Python-tree strict preflight 与 strict JSON 验证后使用 `mode="python"` typed tree，执行 annotation-aware 逐路径等价检查与递归规范化；拒绝 alias/computed/custom serializer 和值转换 | §8.3/9/17，04 |
| D-62 | 需求连续性：TravelSessionState 同时保存 authoritative requirement_state 和 routing requirement_outcome | §6.6，01/04 |
| D-63 | 非初始 state/outcome 由模块 01 public transition 唯一验证；模块 04 不复制状态机 | §6.6，01/04 |
| D-64 | state/outcome、transition 与调用输入彼此深层 fresh；双向 mutation canary 固定复制语义 | §5/6.6/22，01/04 |
| D-65 | v2 codec 错误顺序固定，serialize 和已识别 v2 parse 失败统一零参数 WorkflowStateValidationError | §6.7/17，04 |
| D-66 | RequirementInvalidated 携带模块 01 renderer 生成的完整 summary；04 只 round-trip，05 展示后以 state/action 确认 | §6.6，01/04/05 |
| D-67 | 连续性 maintenance 使用 RSC-T00..T06、强制 Docker 三项 coverage 与 MODULE-CLOSEOUT；实施模型不提交 | §24.1，01/03/04 |

D-46 是后续所有权过滤器：D-38 和 D-43 只记录为模块 13 接入约束，不产生模块 04 聚合器、limits DTO 或异常。状态机另立模块 13 文档。
