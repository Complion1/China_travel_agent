# 05 需求子图、暂停恢复与节点重放规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 网页新任务携带 RequirementDomainState 1.1，并在暂停、确认、命令与检查点往返中保持该版本；旧状态 1.0 的规范字节及读取保持。版本增量只改变需求收集范围，见 [M14 有效输入与历史兼容](14_web_application.md#有效输入与历史兼容)，不改变 interrupt/resume、预算或暂停凭证规则。

> 文档状态：v1.1，已获用户批准，作为模块 05 权威设计
> 当前实现与验证状态：见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)；standalone Runtime 仍是同进程参考实现，embedded driver 另提供供 Module 11 持久化和重建的受控合同
> 上位规范：[`00_agent_master_design.md`](00_agent_master_design.md)
> 依赖与工作包：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 直接前置合同：[`01_requirement_and_dialogue.md`](01_requirement_and_dialogue.md)、[`03_requirement_extraction_adapter.md`](03_requirement_extraction_adapter.md)、[`04_workflow_state_and_artifacts.md`](04_workflow_state_and_artifacts.md)
> 配套执行契约：[`05_requirement_subgraph_vibe_coding.md`](05_requirement_subgraph_vibe_coding.md)
> 规范属性：本文是模块 05 公共合同、同进程参考语义和下游组合边界的唯一真源

```yaml
module_id: "05"
delivery_phase: "Demo"
test_class: "workflow-contract"
depends_on: ["01", "03", "04"]
provides:
  - 需求处理 LangGraph 子图
  - 严格 interrupt/resume DTO
  - Module 05 独立参考实现的同进程 InMemorySaver 生命周期
  - 当前暂停节点重放及副作用去重合同
  - 可由模块 13 组合的无私有 saver 子图构建入口
  - 不含 token 明文的 durable pause DTO 与 embedded driver 重建入口
blocks_demo: true
blocks_production_release: true
not_in_scope:
  - 完整 Demo 主图、跨能力路由和最终终态映射
  - Application Service、CLI、FastAPI、Web 或 GUI
  - DeepSeek Runtime、Prompt、repair、MCP、地图及旅行规划能力
  - PostgreSQL checkpointer、租约/fencing、durable pause 的数据库落地、崩溃恢复策略和长期 Memory
  - Module 10 PostgreSQL Artifact 持久化，以及审计、Trace、Metrics、部署和数据迁移
acceptance_gate:
  - 同一 Python 进程、事件循环和 Runtime 内暂停恢复成功
  - 当前暂停节点重入可检测且预算和领域副作用不重复
  - TravelSessionState v2、checkpoint 和严格 serializer 完整 round-trip
  - 使用同一父 Saver、全新 embedded driver/coordinator 可从 durable pause 继续当前节点
  - 模块 05 line、branch、combined coverage 分别不低于 95%
  - 模块 01、03、04 及完整离线回归无失败、skip 或 xfail
```

## 1. 目标与冻结结论

模块 05 把模块 01 的多轮需求领域服务装进一个小型 LangGraph 子图。它只解决“需求还缺什么、等待用户、恢复处理、确认完成或领域失败”这一段流程，不是完整旅游规划主图。

冻结结论如下：

1. Graph 的唯一业务状态是模块 04 的 `TravelSessionState` v2；不得定义第二套 Graph state。
2. `requirement_state` 是权威状态；每次只连续写入模块 01 返回的 `RequirementDomainTransition.state`，`requirement_outcome` 只用于路由和安全展示。
3. 暂停只发生在无副作用的等待节点中。恢复只重入当前暂停节点，不提供历史回退、分支或已完成副作用节点的任意重放；standalone Runtime 不提供跨进程恢复。
4. Module 05 独立参考实现只保证同一 Python 进程、同一事件循环、同一 `RequirementSubgraphRuntime` 内恢复；embedded driver 可由安全持久证据重建 coordinator 当前暂停记录，但证据落库、租约和崩溃恢复决策属于 Module 11，完整 Demo 由 Module 13/14 装配。
5. 用户完整输入和确认动作保存在 coordinator 的进程内易失槽位中；checkpoint 只保存 `TravelSessionState`、安全 interrupt payload、固定 sentinel 和 LangGraph 安全元数据。已接受但尚未 checkpoint 的命令由 Module 11 另行持久化，不进入 Module 05 checkpoint。
6. 每次暂停同时产生彼此不同的随机 `pause_token` 与 `operation_nonce`。token 只用于调用方持有和恒定时间比较；nonce 只用于稳定 `OperationId`。
7. `requirements.clarification_round` 只在追问回复和确认修改时消费 1；确认接受、首轮输入和纯暂停不消费。
8. 模块 05 只产生安全的本地信号；模块 13 才拥有 `SafeWorkflowEvent` 的 ID、时间、序号、投递、聚合和终态映射。
9. 默认 `InMemorySaver()` 会使已冻结 tuple/DTO 类型失真，因此不合格。Demo 必须固定使用模块 05 的严格 serializer。
10. 同一份子图实现支持两种装配：Demo 由本模块拥有 saver；嵌入模块 13 时通过受控 `RequirementSubgraphEmbeddedDriver` 使用不带私有 saver 的 child，并继承父图 checkpointer。
11. 每个 paused result 同时返回 `RequirementSubgraphDurablePause`：保存 state/token 摘要、nonce 和安全 interrupt，不保存 token 明文、用户命令或 checkpoint 身份；只有 Module 11 已验证 token 并持久接受命令后，才能调用无 token 的 accepted-resume 重建入口。

## 2. 规范优先级、依赖与所有权

### 2.1 优先级

冲突优先级固定为：用户当前对话中的最新明确决定 → 主设计 → 路线图 → 模块 01/03/04 权威设计 → 已验收包根公共合同 → 本规范 → 可执行合同测试 → 配套 Vibe 契约 → 实现状态快照 → 未验收实现。

发现上游设计与已验收公共代码不一致时必须停止，不得通过复制 DTO、私有导入、兼容层或修改模块 01/03/04 消除冲突。

### 2.2 允许依赖

```text
Python 3.12 标准库 ─────────────────────────┐
Pydantic 2.13.4 ────────────────────────────┤
LangGraph 1.2.7 公共 API ───────────────────┤
app.requirements 包根公共合同 ──────────────┤
app.workflow_contracts 包根公共合同 ────────┼─> app.requirement_subgraph
模块 03 提供给装配层的真实 RequirementExtractor ─┘
```

模块 05 不导入 `app.llm_runtime`，不导入模块 01/03/04 私有实现。`RequirementDomainService` 由装配方注入；它内部消费模块 03 满足模块 01 `RequirementExtractor` Protocol 的真实适配器。模块 05 不拥有 DeepSeek Session、Prompt 或 repair。

### 2.3 所有权表

| 能力 | 所有者 | 模块 05 的关系 |
|---|---:|---|
| 需求合并、完整性、summary、confirm | 01 | 调用公共 service，连续消费 transition |
| DeepSeek 适配、单次 session、至多一次 repair | 03 | 由注入的 service 间接消费，不直接管理 |
| TravelSessionState、OperationId、预算端口、事件 DTO | 04 | 直接复用，不复制实现 |
| 需求子图、暂停恢复、当前节点重放、Demo saver | 05 | 唯一所有者 |
| 地图、酒店、候选研究、行程与输出能力 | 06～09、12 | 不导入、不占位、不冻结其接口 |
| PostgreSQL Artifact 持久化 | 10 | 不改变模块 05 checkpoint 或 Graph 语义 |
| PostgreSQL checkpoint、执行账本与跨进程恢复 | 11 | 只实现稳定端口，不把持久化规则写入模块 05 节点 |
| 完整主图、全局路由、全局 operation、事件和终态 | 13 | 消费模块 05 组合边界 |
| Application Service、装配、应用 API 和前端网页 | 14 | 调用模块 13，不绕过 Graph |
| Demo release gate | 15 | 验收 01～14 的真实主链 |
| 局部重排、隐私、观测、生产恢复与质量 Gate | 16、19～23 | 后续扩展/治理，不在本模块实现 |

## 3. 固定包结构与公共导出

未来实现的固定生产包为：

```text
app/requirement_subgraph/
├── __init__.py
├── models.py
├── signals.py
├── errors.py
├── checkpoint.py
├── coordinator.py
├── embedded.py
├── graph.py
└── runtime.py
```

职责固定为：`models.py` 放公开 DTO/union/常量；`signals.py` 放安全本地信号和同步 sink；`errors.py` 放零参数异常；`checkpoint.py` 放严格 serializer；`coordinator.py` 放进程内 run registry、锁、token/nonce、易失输入和预算 manager；`embedded.py` 放无私有 saver 的受控 driver 与 factory；`graph.py` 放节点、路由和 builder；`runtime.py` 放 standalone 调用入口和 Demo 装配。

包根 `app.requirement_subgraph` 只导出下列符号：

```text
RequirementClarificationInterrupt
RequirementConfirmationInterrupt
RequirementInterrupt
RequirementClarificationResume
RequirementConfirmationResume
RequirementResume
RequirementPauseEnvelope
RequirementResumeEnvelope
RequirementSubgraphDurablePause
RequirementSubgraphPaused
RequirementSubgraphConfirmed
RequirementSubgraphDomainFailed
RequirementSubgraphBudgetExceeded
RequirementSubgraphResult
RequirementSubgraphCoordinatorLimits
DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS
REQUIREMENTS_CLARIFICATION_ROUND_KEY
DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT
StartAccepted
ResumeAccepted
NodeReplayed
BudgetConsumed
BudgetExceeded
RequirementSubgraphSignal
RequirementSubgraphSignalSink
NoOpRequirementSubgraphSignalSink
RequirementSubgraphError
RequirementSubgraphConfigurationError
RequirementSubgraphInputError
RequirementSubgraphResumeRejectedError
RequirementSubgraphStateError
RequirementSubgraphRunFaultedError
RequirementSubgraphCapacityExceededError
RequirementCheckpointSerializer
RequirementSubgraphCoordinator
RequirementSubgraphEmbeddedDriver
RequirementSubgraphRuntime
build_requirement_subgraph
create_demo_requirement_subgraph_runtime
create_requirement_subgraph_embedded_driver
```

禁止导出 LangGraph 内部类型、节点函数、router、registry record、token/nonce helper、serializer tag helper、上游异常或上游 DTO 的别名。

## 4. 公共 DTO 共同规则

除 union、Protocol、异常和常量外，模块 05 自有 DTO 一律使用：

```python
ConfigDict(strict=True, frozen=True, extra="forbid")
```

共同规则：

- `schema_version` 固定为 `Literal["1.0"] = "1.0"`。
- discriminator 字段有固定默认值；表中其他字段无默认值。
- 受信边界仍执行两阶段 fresh copy：`model_dump(mode="python")` 后 strict `TypeAdapter.validate_python`，再经规范 JSON 后 strict `validate_json`。
- 行为边界只接受精确 DTO 实例；拒绝 dict、子类、伪造对象、bool/string 猜测和额外字段。
- JSON 使用字段名，保留 `None`，不输出 computed field，不允许 NaN/Infinity、重复 key、BOM 或未知版本。
- 输入 DTO、state、outcome、结果和信号不得保留调用方可变嵌套引用。

### 4.1 interrupt 与 resume DTO

| 类 | 字段 |
|---|---|
| `RequirementClarificationInterrupt` | `schema_version`; `interrupt_type: Literal["clarification"] = "clarification"`; `message: str` |
| `RequirementConfirmationInterrupt` | `schema_version`; `interrupt_type: Literal["confirmation"] = "confirmation"`; `source_outcome_type: Literal["need_confirmation", "invalidated"]`; `summary: str` |
| `RequirementClarificationResume` | `schema_version`; `resume_type: Literal["clarification"] = "clarification"`; `turn: RequirementTurnInput = Field(repr=False)` |
| `RequirementConfirmationResume` | `schema_version`; `resume_type: Literal["confirmation"] = "confirmation"`; `action: RequirementConfirmation = Field(repr=False)` |
| `RequirementPauseEnvelope` | `schema_version`; `pause_token: PauseToken`; `interrupt: RequirementInterrupt` |
| `RequirementResumeEnvelope` | `schema_version`; `pause_token: PauseToken`; `resume: RequirementResume = Field(repr=False)` |
| `RequirementSubgraphDurablePause` | `schema_version`; `run_id`; `trace_id`; `state_digest: ContentHash`; `pause_token_digest: ContentHash = Field(repr=False)`; `operation_nonce: PauseToken`; `interrupt` |

`RequirementInterrupt` 和 `RequirementResume` 分别是以上两个分支按 `interrupt_type`、`resume_type` 判别的 union。

`PauseToken` 是内部受约束类型：精确 32 个小写十六进制字符，公开 token 字段设置 `repr=False`。它不导出为包根符号，不是 run、checkpoint、LangGraph interrupt ID、operation ID 或业务 payload。`operation_nonce` 复用其格式约束但不是 secret。

`RequirementSubgraphDurablePause` 是安全持久证据，不是第二套 Graph state，也不进入 Module 05 checkpoint：

- `state_digest = "sha256:" + sha256(canonical_state_bytes).hexdigest()`；`canonical_state_bytes` 是 `TravelSessionState` 先执行本节两阶段 fresh validation，再对 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)` 使用 UTF-8、`sort_keys=True`、`separators=(",", ":")`、`ensure_ascii=False`、`allow_nan=False` 得到的字节。
- `pause_token_digest = "sha256:" + sha256(b"requirement-subgraph-pause-token-v1\\x00" + str(run_id).encode("ascii") + b"\\x00" + pause_token.encode("ascii")).hexdigest()`。
- `run_id/trace_id/state_digest/interrupt` 必须与 paused state 完全一致；`pause_token_digest` 必须与本次明文 token 一致；`operation_nonce` 必须与 token 不同。
- DTO 可由 Module 11 原样持久化和 strict 恢复；禁止持久化 token 明文、resume command、manager、lease、Graph/native checkpoint ID 或异常。

interrupt payload 只能包含表中的安全字段：追问使用 `NeedClarification.message`；确认使用 `NeedConfirmation.summary` 或 `RequirementInvalidated.summary`。禁止包含 state、完整 outcome、requirement、metadata、issues、evidence、digest、changed_fields、reason、原始用户文本、异常、provider、thread/checkpoint/native interrupt ID。

resume envelope 不包含 `run_id`。运行身份来自 `RequirementSubgraphRuntime.resume(run_id=...)`，防止 envelope 被跨 run 复用。

### 4.2 结果 DTO

| 类 | 字段与不变量 |
|---|---|
| `RequirementSubgraphPaused` | `schema_version`; `result_type="paused"`; `state`; `pause`; `durable_pause`。state 为 requirements/awaiting_user，outcome 为相应等待分支；两份 pause 证据必须逐字段绑定 |
| `RequirementSubgraphConfirmed` | `schema_version`; `result_type="confirmed"`; `state`。outcome 必须为 `ConfirmedTravelRequirement` |
| `RequirementSubgraphDomainFailed` | `schema_version`; `result_type="domain_failed"`; `state`。outcome 必须为 `RequirementExtractionFailed` |
| `RequirementSubgraphBudgetExceeded` | `schema_version`; `result_type="budget_exceeded"`; `state`; `budget_outcome: app.workflow_contracts.BudgetExceeded` |

`RequirementSubgraphResult` 是按 `result_type` 判别的四分支 union。四种 state 都保持 `phase=requirements`、`terminal_code=None`、`terminal_outcome=None` 和原有 `run_id/trace_id/artifact_refs`。paused 使用 `awaiting_user`；其余三种使用 `in_progress`。模块 05 的局部结束不是模块 13 的全局终态。

结果 cross-field validator 固定为：

- paused：state outcome 为 `NeedClarification` 时 interrupt 必须是 clarification 且 `message` 逐值相等；为 `NeedConfirmation` 时必须是 confirmation/source `need_confirmation` 且 `summary` 相等；为 `RequirementInvalidated` 时 source 必须是 `invalidated` 且 `summary` 相等。其他 outcome 非法。
- paused durable binding：durable run/trace/state digest、token digest、interrupt 必须分别等于 state、明文 pause token 和展示 interrupt；nonce 不得等于 token。
- confirmed/domain_failed：state outcome 必须分别是精确 `ConfirmedTravelRequirement` / `RequirementExtractionFailed`。
- budget_exceeded：state outcome 必须仍是被拒绝前的 `NeedClarification | NeedConfirmation | RequirementInvalidated`；`budget_outcome.disposition` 精确为 `rejected`，amount 为 1，key 精确等于 `REQUIREMENTS_CLARIFICATION_ROUND_KEY`，operation/snapshot run_id 等于 state.run_id，operation phase 为 requirements，logical_step 匹配 `^clarification_round_[0-9a-f]{32}$`，snapshot.key 与 outcome.key 相等。`replayed` 不能构造公开结果。

### 4.3 容量 DTO 与常量

```python
class RequirementSubgraphCoordinatorLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    max_runs: int = Field(ge=1, le=2**53 - 1)

DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS = RequirementSubgraphCoordinatorLimits(
    max_runs=256,
)
DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT = 6
```

`REQUIREMENTS_CLARIFICATION_ROUND_KEY` 是模块 04 `ExecutionBudgetKey`，精确为 phase scope、name `requirements.clarification_round`、phase `WorkflowPhase.REQUIREMENTS`。

### 4.4 精确声明索引

以下声明冻结字段类型和默认值；每个类都位于 `models.py`，基类均为 `BaseModel`，并服从本节共同规则。`WorkflowBudgetExceeded` 是文档中为避免与模块 05 同名信号混淆而使用的 `app.workflow_contracts.BudgetExceeded` 别名，不是新公共类型。

```python
PauseToken = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{32}$"),
]

class RequirementClarificationInterrupt(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    interrupt_type: Literal["clarification"] = "clarification"
    message: str

class RequirementConfirmationInterrupt(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    interrupt_type: Literal["confirmation"] = "confirmation"
    source_outcome_type: Literal["need_confirmation", "invalidated"]
    summary: str

RequirementInterrupt = Annotated[
    RequirementClarificationInterrupt | RequirementConfirmationInterrupt,
    Field(discriminator="interrupt_type"),
]

class RequirementClarificationResume(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    resume_type: Literal["clarification"] = "clarification"
    turn: RequirementTurnInput = Field(repr=False)

class RequirementConfirmationResume(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    resume_type: Literal["confirmation"] = "confirmation"
    action: RequirementConfirmation = Field(repr=False)

RequirementResume = Annotated[
    RequirementClarificationResume | RequirementConfirmationResume,
    Field(discriminator="resume_type"),
]

class RequirementPauseEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    pause_token: PauseToken = Field(repr=False)
    interrupt: RequirementInterrupt

class RequirementResumeEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    pause_token: PauseToken = Field(repr=False)
    resume: RequirementResume = Field(repr=False)

class RequirementSubgraphDurablePause(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    trace_id: UUID
    state_digest: ContentHash
    pause_token_digest: ContentHash = Field(repr=False)
    operation_nonce: PauseToken
    interrupt: RequirementInterrupt

class RequirementSubgraphPaused(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["paused"] = "paused"
    state: TravelSessionState
    pause: RequirementPauseEnvelope
    durable_pause: RequirementSubgraphDurablePause

class RequirementSubgraphConfirmed(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["confirmed"] = "confirmed"
    state: TravelSessionState

class RequirementSubgraphDomainFailed(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["domain_failed"] = "domain_failed"
    state: TravelSessionState

class RequirementSubgraphBudgetExceeded(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["budget_exceeded"] = "budget_exceeded"
    state: TravelSessionState
    budget_outcome: WorkflowBudgetExceeded

RequirementSubgraphResult = Annotated[
    RequirementSubgraphPaused
    | RequirementSubgraphConfirmed
    | RequirementSubgraphDomainFailed
    | RequirementSubgraphBudgetExceeded,
    Field(discriminator="result_type"),
]
```

这些结果类的 cross-field validator 必须实施 §4.2 的全部 outcome/status/terminal/budget/display 不变量；不能只检查 discriminator。`RequirementSubgraphCoordinatorLimits` 没有 `schema_version`，与模块 04 limits DTO 的风格一致；它只含 §4.3 的 `max_runs`。

## 5. 安全本地信号

模块 05 信号 DTO 服从第 4 节共同规则：

| 信号 | 固定字段 |
|---|---|
| `StartAccepted` | `schema_version`; `signal_type="start_accepted"`; `run_id`; `trace_id` |
| `ResumeAccepted` | `schema_version`; `signal_type="resume_accepted"`; `run_id`; `trace_id` |
| `NodeReplayed` | `schema_version`; `signal_type="node_replayed"`; `run_id`; `trace_id`; `operation_id` |
| `BudgetConsumed` | `schema_version`; `signal_type="budget_consumed"`; `run_id`; `trace_id`; `operation_id`; `amount: Literal[1] = 1` |
| `BudgetExceeded` | `schema_version`; `signal_type="budget_exceeded"`; `run_id`; `trace_id`; `operation_id` |

`RequirementSubgraphSignal` 是按 `signal_type` 判别的五分支 union。sink 固定为同步端口：

```python
class RequirementSubgraphSignalSink(Protocol):
    def accept(self, signal: RequirementSubgraphSignal) -> None: ...

class NoOpRequirementSubgraphSignalSink:
    def accept(self, signal: RequirementSubgraphSignal) -> None: ...
```

五个信号均位于 `signals.py`、公开、基于 `BaseModel` 且 strict/frozen/extra forbid；`run_id/trace_id` 类型为 `UUID`，`operation_id` 类型为模块 04 `OperationId`。`NodeReplayed.operation_id` 必须与 signal.run_id 相同、phase=requirements 且 logical_step 匹配 `^requirement_pause_[0-9a-f]{32}$`；两个 budget 信号必须同 run/phase 且匹配 `^clarification_round_[0-9a-f]{32}$`。本文件内信号类与模块 04 同名 budget outcome 在实现中必须使用显式导入别名，例如 `BudgetConsumed as WorkflowBudgetConsumed`，不得用运行时模块名猜测类型。

模块 05 不创建 `SafeWorkflowEvent`。非持久 standalone/parent 组合中，模块 13 可映射：start/resume/replay → `workflow.started/resumed/replayed`，预算 applied/rejected → `budget.consumed/exceeded`；事件 ID、UTC 时间、per-run sequence、counter、sink、聚合和投递失败全部属于模块 13。Module 11 durable 组合是例外：由于 accepted command 会在 Graph dispatch marker 前进入 operation scope，必须使用 factory 默认的 `NoOpRequirementSubgraphSignalSink`，禁止把这些可重复本地信号直接映射为持久/外部事件；全局 durable 事件只能由后续模块在 Module 11 的 dispatch/checkpoint 证据之后生成。模块 05 不发 `workflow.terminal`。

信号顺序固定：

1. `start_accepted`：start 全部 preflight 和容量准入完成后、进入 Graph 前。
2. `resume_accepted`：run、token、branch、fresh DTO 和锁校验通过后、发送 `Command` 前。
3. `node_replayed`：`interrupt()` 返回固定 sentinel 后、预算和领域调用前。
4. `budget_consumed`：仅预算首次 `applied` 后。
5. `budget_exceeded`：仅预算首次 `rejected` 后。

预算返回 `replayed` 不再发 consumed/exceeded；它表示调用结果不确定，按第 13 节抛异常。

## 6. 公共运行入口

### 6.1 `RequirementSubgraphCoordinator`

该类是同进程 mutable control plane。它保存每个 run 的异步锁、状态、当前易失输入、token/nonce、预算 manager、局部结果和 terminal/faulted tombstone；这些对象均不得进入 Graph state/checkpoint。turn/action 槽是 take-once：对应节点取出 fresh DTO 后立即清空。进入 confirmed/domain_failed/budget_exceeded/faulted 时清空完整 DTO、token、nonce 和安全展示 descriptor，只保留 run/trace、状态枚举、预算 manager 和判定重复调用所需的最小 tombstone。

```python
class RequirementSubgraphCoordinator:
    def __init__(
        self,
        *,
        domain_service: RequirementDomainService,
        execution_budget_manager_factory: Callable[[UUID], ExecutionBudgetManager],
        signal_sink: RequirementSubgraphSignalSink,
        limits: RequirementSubgraphCoordinatorLimits,
    ) -> None: ...

    def start_operation(
        self,
        *,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> AbstractAsyncContextManager[TravelSessionState]: ...

    def resume_operation(
        self,
        *,
        state: TravelSessionState,
        envelope: RequirementResumeEnvelope,
    ) -> AbstractAsyncContextManager[Command]: ...

    def resolve_result(
        self,
        *,
        state: TravelSessionState,
        interrupt_value: object | None,
    ) -> RequirementSubgraphResult: ...
```

精确类型身份规则只适用于 DTO 和端口返回值，不适用于 Protocol/Callable 依赖对象。`domain_service` 由测试 fake 继承公共 service 后覆写两个 async 方法；manager factory 返回对象按 `consume/get_snapshot/get_all_snapshots` 三个 callable surface 使用，不能对未标 `runtime_checkable` 的 Protocol 做 `isinstance`。每次 service/manager/sink 返回或接收的 DTO 仍须精确类型和 fresh validation。factory 对每个首次准入的 `run_id` 恰好调用一次，返回只属于该 run、当前事件循环的 manager；错误返回类型失败关闭。

三个 coordinator 控制方法是 standalone Runtime 和包内 driver 的底层控制面；Module 13 的 durable 组合必须使用 §6.5 的 embedded driver，不得直接重建 coordinator 私有 record：

- `start_operation` 在异步作用域进入时完成 start preflight、容量原子准入、易失 turn 写入和 `StartAccepted`；yield 一个 fresh state，调用方在作用域内驱动 standalone 子图或模块 13 父图。
- `resume_operation` 在持锁后把 caller 提供的最新 checkpoint state 与 coordinator 最新 state 逐值比较，再完成 token/branch/current preflight、易失 resume 写入和 `ResumeAccepted`；yield 的 `Command` 只能含 `resume="requirement_resume_v1"`。
- `resolve_result` 只能在当前 operation lease 内调用。paused 时它 strict 解析 LangGraph `Interrupt.value` 并附上 token；局部结束时返回 confirmed/domain_failed/budget_exceeded。它不暴露 checkpoint/native interrupt ID。

每个 `start_operation`/`resume_operation` lease 必须且只能成功调用一次 `resolve_result`：成功后在 record 标记本 lease 已 resolved。第二次调用立即把 run 永久 fault、清 DTO/token/nonce、保留容量并抛 `RequirementSubgraphStateError()`；即使第一次已得到 paused/settled result 也不允许重复读取。作用域 body 正常返回但一次都未成功 resolve 时，`__aexit__` 执行同样 fault 清理并抛 `RequirementSubgraphStateError()`；这样不能留下拿不到 token 的 paused run或未观察的局部结果。若 body/resolve 本身已抛异常，按原异常规则退出，不用“缺少 resolve”覆盖原异常。Runtime 的私有只读 `get_state` scope 不受本计数约束。

异步作用域持有该 run 的 operation lock，覆盖调用方整次 `ainvoke` 与结果解析。一个私有 `ContextVar` lease 随 asyncio task context 传播到父/子 Graph 节点；节点 helper 必须验证 coordinator/run/lease generation，只在该 lease 内访问 record，绝不再次获取同一非重入 operation lock。除上一段“缺少/重复 resolve”的显式协议违例外，作用域异常退出时仅当模块 05 仍为 running/resume_pending 才永久 fault并清理；若已经成功 resolve 子图 settled result 后父图后续节点失败，不反向改写模块 05 的局部结果。

coordinator 另有一个全局 admission/registry lock，只保护事件循环绑定、run 查重、容量、同步 factory 调用和 record 原子插入；不得在持有它时 await Graph、service、manager 或 sink。首次双 start 和容量边界因此不能超售。节点协作、Runtime 的只读 state scope 和 graph binding helper 均保持包内私有。

### 6.2 `build_requirement_subgraph`

```python
def build_requirement_subgraph(
    *,
    coordinator: RequirementSubgraphCoordinator,
    checkpointer: BaseCheckpointSaver[str] | None,
) -> CompiledStateGraph: ...
```

`checkpointer` 必须显式传入：Demo 传严格配置的 `InMemorySaver`；模块 13 嵌入模式传 `None`，让父图拥有 saver。禁止 builder 自行构造 saver、service、manager、sink 或全局单例。返回值是以 `TravelSessionState` 为 state schema 的同一份可组合 compiled subgraph。builder 在包内绑定表记录 compiled graph、coordinator identity 和 `standalone`/`embedded` mode；该绑定不是 checkpoint 内容或公共导出。

### 6.3 `RequirementSubgraphRuntime`

```python
class RequirementSubgraphRuntime:
    def __init__(
        self,
        *,
        graph: CompiledStateGraph,
        coordinator: RequirementSubgraphCoordinator,
    ) -> None: ...

    async def start(
        self,
        *,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> RequirementSubgraphResult: ...

    async def resume(
        self,
        *,
        run_id: UUID,
        envelope: RequirementResumeEnvelope,
    ) -> RequirementSubgraphResult: ...

    async def get_state(self, *, run_id: UUID) -> TravelSessionState: ...
```

Runtime 是三个 coordinator 公共控制方法与 standalone graph 的薄适配器。构造阶段必须在零 run/零 signal 状态校验：graph 由同一个 coordinator 构建、mode 为 standalone、checkpointer 是精确 `InMemorySaver` 且 serde 是 `RequirementCheckpointSerializer`、该 graph/coordinator pair 尚未绑定另一 Runtime。任一不满足抛 `RequirementSubgraphConfigurationError()`；`checkpointer=None` 的 embedded child 不得构造 Runtime。

一个 Runtime 绑定一个 graph、coordinator、Python 进程和事件循环，可服务多个 run。调用必须 per-run 串行；resume/get 在同一 operation lock 上依次等待，等待后按最新 state/token 重新校验；重复或 stale resume 因而失败关闭。重复 start 在 admission lock 下直接拒绝。`get_state` 只接受已登记 run，不接受 caller supplied `checkpoint_id/checkpoint_ns`，容量已满时仍允许读取现有 run。

### 6.4 Demo factory

```python
def create_demo_requirement_subgraph_runtime(
    *,
    domain_service: RequirementDomainService,
    signal_sink: RequirementSubgraphSignalSink = NoOpRequirementSubgraphSignalSink(),
    limits: RequirementSubgraphCoordinatorLimits = DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
) -> RequirementSubgraphRuntime: ...
```

factory 创建一个 coordinator、一个严格 `InMemorySaver(serde=RequirementCheckpointSerializer())` 和一份 compiled graph。其 budget factory 为每个 run 创建只含 `requirements.clarification_round=6` 的 `ExecutionBudgetPlan`，再精确调用 `InMemoryExecutionBudgetManager(plan=plan, limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)`；模块 05 不复制或 re-export 模块 04 的 manager limit。默认对象不得保留调用方可变引用。

### 6.5 `RequirementSubgraphEmbeddedDriver`

```python
class RequirementSubgraphEmbeddedDriver:
    def __init__(
        self,
        *,
        graph: CompiledStateGraph,
        coordinator: RequirementSubgraphCoordinator,
    ) -> None: ...

    @property
    def graph(self) -> CompiledStateGraph: ...

    @property
    def limits(self) -> RequirementSubgraphCoordinatorLimits: ...

    def start_operation(
        self,
        *,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> AbstractAsyncContextManager[TravelSessionState]: ...

    def verify_pause_token(
        self,
        *,
        durable_pause: RequirementSubgraphDurablePause,
        pause_token: PauseToken,
    ) -> None: ...

    def resume_accepted_operation(
        self,
        *,
        state: TravelSessionState,
        durable_pause: RequirementSubgraphDurablePause,
        resume: RequirementResume,
    ) -> AbstractAsyncContextManager[Command]: ...

    def resolve_result(
        self,
        *,
        state: TravelSessionState,
        interrupt_value: object | None,
    ) -> RequirementSubgraphResult: ...

def create_requirement_subgraph_embedded_driver(
    *,
    domain_service: RequirementDomainService,
    execution_budget_manager_factory: Callable[[UUID], ExecutionBudgetManager],
    signal_sink: RequirementSubgraphSignalSink = NoOpRequirementSubgraphSignalSink(),
    limits: RequirementSubgraphCoordinatorLimits = DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
) -> RequirementSubgraphEmbeddedDriver: ...
```

factory 必须创建同一 coordinator 与 `build_requirement_subgraph(..., checkpointer=None)` child，再构造 driver。driver 构造时验证 graph/coordinator identity、`embedded` mode、graph 与 binding 的 checkpointer 都是 `None`，并独占该 pair；错配、standalone graph 或重复绑定在 service/signal 前抛 `RequirementSubgraphConfigurationError()`。`limits` 只读返回该 coordinator 已 strict/frozen 验证的 `RequirementSubgraphCoordinatorLimits`，供 Module 11 注册时核对持久 Runtime 容量；不会暴露 record、manager 或 checkpoint。

Module 13 不得把 Demo compiled graph嵌入父图，也不得直接操作 coordinator 私有 record。用于 Module 11 durable 注册时，它调用 `create_requirement_subgraph_embedded_driver` 必须省略 `signal_sink` 参数以使用精确默认 NoOp；自定义 sink 的 driver 不符合 durable 组合合同。它只把 `driver.graph` 接入父图，并用 driver 的 operation scope 包住父图 invoke：

1. 首次执行使用 `start_operation`；父图暂停后用 `resolve_result` 取得明文 token 和 durable pause，Module 11 先持久化 durable pause，再把 token 返回调用方。
2. 用户 resume 时，Module 11 用 `verify_pause_token` 做 strict fresh validation 和恒定时间摘要比较，再在自己的事务中一次性接受 token、持久化 canonical resume command；验证本身不登记 run、不创建 manager、不发信号。
3. 上述事务提交后只把 state、durable pause 与 `RequirementResume` 传给 `resume_accepted_operation`。同进程已有 paused record 时必须逐字段匹配；新进程无 record 时由 driver 在 admission lock 下创建 manager 和 paused record，用 durable nonce/interrupt/state 重建，生成只在进程内存在的临时 token 后进入相同 resume preflight。
4. 崩溃发生在命令事务提交后、Graph checkpoint 前时，Module 11 可从已接受命令和 durable pause 再次调用 `resume_accepted_operation`，不需要也不得恢复 token 明文。没有“命令已被 Module 11 持久接受”的证据时禁止调用此入口。
5. 子图结束后的父层 router 仍在同一 operation scope 内调用 `resolve_result` 区分 confirmed/domain_failed/budget_exceeded，再由 Module 13 继续或映射全局终态。

embedded driver 只提供重建机制，不判断数据库租约、fencing、命令是否已持久接受或 checkpoint 是否可安全恢复；这些均由 Module 11 负责。Module 13 的未来 Application DTO 和终态 code 仍不由本文冻结。

## 7. Graph 拓扑与节点职责

固定拓扑：

```text
START
  └─> process_initial_turn
        └─> _destination_for_outcome
              ├─ NeedClarification ─────> await_clarification ─┐
              ├─ NeedConfirmation ─────> await_confirmation ──┤
              ├─ RequirementInvalidated -> await_confirmation ┤
              ├─ ConfirmedTravelRequirement ───────────────> END
              └─ RequirementExtractionFailed ──────────────> END
```

图中 `route` 是私有纯函数，不是第四个 Graph node。LangGraph 1.2.7 已实测：同一节点同时存在静态 conditional edge 和 `Command(goto=END)` 时两路都会调度。因此固定接线只允许一条静态边 `START -> process_initial_turn`；三个处理节点都返回 `Command(update=fresh_state, goto=_destination_for_outcome(outcome))`，且都不得注册 `add_edge`/`add_conditional_edges` 出边。builder 的 `add_node(..., destinations=("await_clarification", "await_confirmation", END))` 只提供精确动态目标提示。预算超限直接返回 `Command(update=fresh_in_progress_state, goto=END)`，不会再按旧等待 outcome 暂停。

私有签名固定为：

```python
RequirementDestination = Literal[
    "await_clarification",
    "await_confirmation",
    "__end__",
]

def _destination_for_outcome(
    outcome: RequirementDomainOutcome,
) -> RequirementDestination: ...

async def _process_initial_turn(
    state: TravelSessionState,
) -> Command[RequirementDestination]: ...

async def _await_clarification(
    state: TravelSessionState,
) -> Command[RequirementDestination]: ...

async def _await_confirmation(
    state: TravelSessionState,
) -> Command[RequirementDestination]: ...
```

`_destination_for_outcome` 的五分支映射精确等于图示；未知/不一致对象抛 state error，不存在默认目标。两个等待节点恢复处理后调用同一个 helper。

| 节点 | 首次进入 | 恢复重入 | 禁止行为 |
|---|---|---|---|
| `process_initial_turn` | 取已验证首轮 DTO，调用一次 `process_turn` | 不可恢复进入 | 自己提取、修正、合并或渲染 |
| `await_clarification` | 生成 token/nonce，把 interrupt DTO 转为规范 JSON tree 后调用 `interrupt(tree)` | replay 信号 → consume 1 → `process_turn` | interrupt 前产生领域/预算副作用 |
| `await_confirmation` | 生成 token/nonce，把 interrupt DTO 转为规范 JSON tree 后调用 `interrupt(tree)` | replay 信号；accept 直接 `confirm`；revise 先 consume 1 再 `confirm` | 自己判断完整性或拼 summary |
| `_destination_for_outcome` | 按精确 outcome 类返回动态目标 | 同左 | 注册 conditional edge、字符串猜测、默认兜底成功、创建终态 |

节点输入和更新均为 fresh `TravelSessionState`。节点只替换 `requirement_state`、`requirement_outcome` 和本节规定的 `delivery_status`；保持 schema/run/trace/phase/artifact_refs/terminal 字段。

## 8. start、暂停与 resume 合同

### 8.1 start preflight

`start` 在任何 Graph、service、预算或信号副作用前依次验证：

1. state 与 turn 是精确实例且通过两阶段 strict+JSON fresh copy。
2. state 是模块 04 合法初始快照：v2、requirements/in_progress、terminal 空、outcome 空、默认 RequirementDomainState、artifact_refs 空。
3. `turn.current` 与 checkpoint 初始 `state.requirement_state` 严格相等。
4. `run_id` 未登记；Runtime/事件循环身份正确；容量未满。
5. 以 `thread_id=str(state.run_id)` 的小写规范 UUID 形成唯一 LangGraph config。

manager factory 成功、record 在 admission lock 下原子插入是容量 commit point；随后保存 fresh turn、发 `StartAccepted` 并调用 graph。commit 前 factory 构造/返回失败不占槽、不发信号；commit 后 signal 构造或 sink 抛错因投递结果不确定而永久 fault、清 DTO/token/nonce、保留容量槽并原样传播异常。正常准入、paused、settled 和 faulted record 都占槽直到 Runtime 销毁。

### 8.2 暂停

等待节点开头必须调用私有 `ensure_current_pause`。若当前 lease 是第一次进入该暂停点，它才生成并保存 token/nonce/安全 descriptor；若状态为 `resume_pending` 的同一暂停点重入，它必须返回原 record，CSPRNG 调用次数为 0，不能覆盖 token、nonce 或 descriptor。节点随后把 fresh interrupt DTO `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)` 成只含 JSON 基元的规范 tree，再把该 tree 交给 `interrupt()`；不得直接 checkpoint Pydantic DTO。在 `interrupt()` 返回 sentinel 之前不消费预算、不调用模块 01/03、不发 replay/budget 信号。

`resolve_result` 收到 LangGraph `Interrupt.value` 后，把它按精确 union strict 验证回 fresh DTO，并校验其与 coordinator 当前暂停记录一致；随后按 §4.1 的固定算法生成 `RequirementSubgraphDurablePause`，与含明文 token 的 envelope 一起返回 `RequirementSubgraphPaused`。这是 LangGraph 内部 checkpoint adapter 的唯一 dict 边界，不放宽公开 start/resume 行为入口的“只接受精确 DTO”规则。恢复 DTO take-once 后清除 active pause secret 和 durable pause；只有处理结果进入下一个新暂停点时才生成下一对 token/nonce/durable evidence。

### 8.3 resume preflight

`resume` 在 `Command(resume="requirement_resume_v1")` 前依次验证：

1. run 已登记、处于 paused、未 terminal/faulted，且当前事件循环/Runtime 一致。
2. envelope 是精确 fresh DTO；token 用 `hmac.compare_digest` 恒定时间比较。
3. 当前 checkpoint state.run_id 与参数一致，thread 固定且当前 checkpoint 是该 run 最新暂停点。
4. 当前 outcome 决定唯一合法分支：NeedClarification 只接受 clarification；NeedConfirmation/Invalidated 只接受 confirmation。
5. clarification `turn.current` 必须严格等于 checkpoint `requirement_state`；模块 05 不推断 turn_id、日期、timezone 或 intent。
6. confirmation action 直接使用模块 01 DTO；不接受 bool/string/dict 或自行生成 revision turn。

全部通过后把 fresh DTO 存入易失槽、把状态置为 `resume_pending`、发 `ResumeAccepted`，`resume_operation` 只 yield 固定 sentinel Command。accepted signal 构造或 sink 抛错时永久 fault、清槽和 secret、保留容量槽并原样传播；不得重试。错误 token、旧 token、跨 run token、错误 branch、stale current、unknown/terminal run、未知 schema 或重复 resume 都在任何预算/领域副作用前抛 `RequirementSubgraphResumeRejectedError()`；faulted run 单独抛 `RequirementSubgraphRunFaultedError()`。

### 8.4 durable accepted-resume preflight

`RequirementSubgraphEmbeddedDriver.resume_accepted_operation` 只用于 Module 11 已原子持久接受命令之后。它不接收 token，而按固定顺序：

1. 对 state、durable pause、resume 分别执行精确类型和两阶段 fresh validation。
2. 验证 requirements/awaiting_user、terminal 空、durable run/trace/state digest、interrupt/outcome 分支与 resume 分支；clarification 的 `turn.current` 仍须等于 state requirement。
3. 若当前 coordinator 已有 run，等待同一 operation lock 后要求其处于 paused，latest state、nonce、interrupt、完整 durable pause 全部一致；任何冲突拒绝，不覆盖 record。
4. 若 run 不存在，在 admission lock 下检查容量、构造该 run 的 manager、插入以 durable nonce/interrupt/state 为权威的 paused record；仅生成一个不出进程的临时 32-hex token，使后续路径复用同一 coordinator 状态机。
5. 进入与普通 resume 相同的易失 command slot、`resume_pending`、lease、`ResumeAccepted` 和 fixed-sentinel Command 路径。Graph 节点重入后继续使用 durable nonce 生成完全相同的 `NodeReplayed` 和预算 OperationId。

错误 state/digest/interrupt/nonce/resume、同进程 record 冲突、容量或 manager contract 错误必须在 Graph、预算和领域副作用前失败。driver 不从 token digest反推出 token，不为尚未持久接受的命令提供绕过验证的入口。

## 9. state 连续性与结果映射

每次模块 01 调用返回后必须：

1. 用公共 `RequirementDomainTransition` 再验证 state/outcome 一致性。
2. 分别做 fresh strict+JSON copy。
3. 原子替换 `TravelSessionState.requirement_state` 与 `requirement_outcome`。
4. 按 outcome 决定 `delivery_status` 和下个节点。

固定映射：

| outcome | state status | 子图行为/结果 |
|---|---|---|
| `NeedClarification` | awaiting_user | 暂停并展示其 `message` |
| `NeedConfirmation` | awaiting_user | 暂停并展示其 `summary` |
| `RequirementInvalidated` | awaiting_user | 暂停并展示其 `summary` |
| `ConfirmedTravelRequirement` | in_progress | `RequirementSubgraphConfirmed` |
| `RequirementExtractionFailed` | in_progress | `RequirementSubgraphDomainFailed` |

禁止从 outcome 的 requirement/metadata 反向构建 state，禁止清空已有 outcome，禁止复制 renderer 或确认规则。`RequirementExtractionFailed` 是 typed business result，不触发 interrupt。

## 10. thread、checkpoint 与严格 serializer

### 10.1 身份

Runtime 为所有 invoke/get 固定使用：

```python
{"configurable": {"thread_id": str(run_id)}}
```

调用方不能传 `checkpoint_id`、`checkpoint_ns`、LangGraph interrupt ID 或历史 checkpoint。模块 05 不暴露 state history、time travel、fork、reset、delete、list 或 import/export API。

### 10.2 checkpoint 允许内容

允许：`TravelSessionState`、LangGraph 1.2.7 自有安全控制元数据、上述 interrupt DTO 的规范 JSON tree 和固定 sentinel `requirement_resume_v1`。

禁止：完整 start/resume DTO、turn/action、user_message/revision_text、pause_token、operation_nonce、service/extractor/session/provider、manager/sink、repository/checkpointer、lock、exception、原始模型/工具 payload、secret。

### 10.3 `RequirementCheckpointSerializer`

```python
class RequirementCheckpointSerializer(SerializerProtocol):
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]: ...
    def loads_typed(self, data: tuple[str, bytes]) -> Any: ...
```

serializer 自有 tag 前缀固定为 `requirement_subgraph.`；type tag、bytes 和恢复方式精确为：

| `dumps_typed` type tag | bytes | 恢复方式 |
|---|---|---|
| `requirement_subgraph.travel_session_state.2.0` | 模块 04 规范 state JSON bytes | `parse_travel_session_state_json` |
| `requirement_subgraph.requirement_domain_state.1.0` | UTF-8 规范 JSON object | 精确 TypeAdapter，两阶段 strict+JSON |
| `requirement_subgraph.requirement_domain_outcome.1.0` | UTF-8 规范 JSON object | 精确 discriminated TypeAdapter，两阶段 strict+JSON |
| `requirement_subgraph.terminal_outcome.1.0` | UTF-8 规范 JSON object | 精确 TypeAdapter，两阶段 strict+JSON |
| `requirement_subgraph.artifact_ref_tuple.1.0` | UTF-8 规范 JSON array | `TypeAdapter(tuple[ArtifactRef, ...])`，保持 tuple/嵌套类型 |
| `requirement_subgraph.interrupt_tuple.1.0` | UTF-8 规范 JSON array，每项精确为 `{"id":<32hex>,"value":<JSON tree>}` | 逐项构造公开 `Interrupt(value=..., id=...)` 并恢复非空精确 tuple |

`interrupt_tuple` 只截获 `dumps_typed` 顶层、非空、每项 `type(item) is Interrupt` 的 tuple；每个 id 必须匹配 `^[0-9a-f]{32}$`，value 只能是 `None/bool/int/finite float/str/list/dict[str,...]` 组成的规范 JSON tree。顶层 `list[Interrupt]` 必须在 delegate 前直接抛 `RequirementSubgraphStateError()`；空 tuple、混合 tuple、其他 tuple 和嵌套 Interrupt tuple 也不能冒充该 tag；`artifact_ref_tuple` 仍单独负责空或非空的精确 ArtifactRef tuple。

规范 JSON 固定 `sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False`，无 BOM/重复 key；每次 dump 在返回前执行 loads round-trip、精确类型和 equality。`Interrupt` 恢复只能调用已核验的公开 `Interrupt(value, id)` 构造器，不使用 delegate、动态 import、kwargs fallback 或 model construction。任何以 `requirement_subgraph.` 开头但不在表中的 tag（包括未知版本）直接 state error，禁止交给 delegate。

其余 LangGraph 控制值委托以下精确实例：

```python
JsonPlusSerializer(
    pickle_fallback=False,
    allowed_json_modules=(
        ("app", "workflow_contracts", "identifiers", "WorkflowPhase"),
        ("app", "workflow_contracts", "state", "DeliveryStatus"),
    ),
    allowed_msgpack_modules=(
        ("app.workflow_contracts.identifiers", "WorkflowPhase"),
        ("app.workflow_contracts.state", "DeliveryStatus"),
    ),
)
```

LangGraph 1.2.7 内建 safe set 只是 delegate 的上限，不是模块 05 的最终许可；模块 05 不传 `True`，不增加其他 symbol。wrapper 在 delegate 前后执行同一闭合递归检查，允许类型精确为：`NoneType/bool/int/float/str/bytes/bytearray`（float 必须有限）、`list/dict`（key/value 都递归检查）、`UUID`、`datetime/date/time/timedelta/timezone`、`ZoneInfo`、精确 `WorkflowPhase`、精确 `DeliveryStatus`，以及单个 `Interrupt/Command/Send/Overwrite`。通用 list 规则不适用于顶层 `list[Interrupt]`，该特判必须先于 delegate 和递归检查执行。四种 LangGraph 控制对象仅按其公开字段递归检查：Interrupt.value/id；Command.graph/update/resume/goto；Send.node/arg；Overwrite.value。六个自有 tag负责的五类业务 DTO/tuple和 control interrupt tuple只能作为 `dumps_typed` 顶层值，不能穿过 delegate；其他 tuple 在委托前失败。

其余 safe-set 成员也明确拒绝，包括未被两个 tuple tag截获的 tuple、set/frozenset/deque、Decimal、IP/Path、LangChain message/document、store op、PregelTask、StateSnapshot、TimeoutPolicy、其他 constructed object/enum/BaseModel；同样拒绝被 block 后退化的 kwargs dict、delegate 的 tuple→list、DTO→dict 或 constructor fallback。`datetime.datetime.fromisoformat` 是唯一允许的 LangGraph safe method，但恢复结果仍必须通过上述闭合类型树。测试用真实 pause/history/parent graph证明该闭合集合覆盖当前执行；发现需要新增类型立即停止并修改设计，禁止运行时自动扩白。

`loads_typed` 的 tag 分派顺序固定：先检查 `requirement_subgraph.` 前缀并只接受表中六个完整 tag；非该前缀只允许委托精确 `null`、`bytes`、`bytearray`、`json`、`msgpack`。`pickle` 与任何其他 delegate tag 必须在调用 JsonPlus 前拒绝；`dumps_typed` 也只允许产生六个自有 tag或上述五个 delegate tag。delegate 返回后再执行闭合类型树和 round-trip 检查。

任何未知标签、未知版本、重复 key、非法 JSON、类型失真、delegate异常、constructor fallback 或 round-trip 不相等都统一抛零参数 `RequirementSubgraphStateError()` from `None`。默认 `InMemorySaver()`、默认 `JsonPlusSerializer` 以及 pickle serializer 均不合格。模块 13 的父 checkpointer 必须使用相同 serializer；声称兼容的后续 serializer 必须逐个通过上述 tag/allowlist/真实父图合同，不能只实现同名方法。

## 11. 当前节点重放、token/nonce 与 OperationId

每次暂停生成：

- `pause_token = secrets.token_hex(16)`：32 小写 hex，只交调用方，`repr=False`，不进入信号/日志/checkpoint/OperationId。
- `operation_nonce = secrets.token_hex(16)`：同样 32 小写 hex，必须与 token 不同；同进程留在 coordinator，durable 组合时可进入 `RequirementSubgraphDurablePause`、OperationId 与安全信号。

同一暂停点固定两个 OperationId：

```python
OperationId(
    run_id=run_id,
    phase=WorkflowPhase.REQUIREMENTS,
    logical_step=f"requirement_pause_{operation_nonce}",
)

OperationId(
    run_id=run_id,
    phase=WorkflowPhase.REQUIREMENTS,
    logical_step=f"clarification_round_{operation_nonce}",
)
```

恢复成功后当前节点由 LangGraph 从头重入。`interrupt()` 返回 sentinel 后，节点必须先 take-once 取出 coordinator 中的当前 DTO并确认 nonce，再发一次 `NodeReplayed`，之后才允许预算或模块 01 调用。下一次暂停旋转 token 与 nonce；旧值只具有“永远不可再接受”的状态机语义，不要求保留旧 secret 本身，也不得重新激活。

本模块不提供已完成副作用节点重放。异常发生于 graph 执行后，run 标记 faulted；不能 start/resume 重试，因为上游副作用完成状态可能不确定。

## 12. 预算固定顺序

预算消费点：

| 交互 | 消费 `requirements.clarification_round` |
|---|---:|
| 首轮 `process_turn` | 0 |
| 进入 interrupt | 0 |
| clarification 回复 | 1 |
| confirmation `accept` | 0 |
| confirmation `revise` | 1 |

顺序固定：resume 全部校验 → node replay 信号 → 构造 nonce 对应 `BudgetConsumeRequest(amount=1)` → `consume` → 处理 disposition → 才调用模块 01。

manager 返回后先 fresh 验证精确 `BudgetConsumed | BudgetExceeded`，再与本次 request 做逐字段相关性校验：`outcome.operation_id == request.operation_id`、`outcome.key == request.key`、`outcome.amount == request.amount`、`outcome.snapshot.run_id == request.operation_id.run_id`、`outcome.snapshot.key == request.key`。任一不一致抛 `RequirementSubgraphStateError()` 并 fault run，不发 budget 信号、不调用领域 service；即使 disposition 合法也不能放行。通过相关性校验后才按下表处理，并始终用已验证 request 的 OperationId 构造本地信号。

| budget outcome | 模块 05 行为 |
|---|---|
| `BudgetConsumed(disposition="applied")` | 发 `BudgetConsumed` 信号，允许一次领域调用 |
| `BudgetExceeded(disposition="rejected")` | 发 `BudgetExceeded` 信号，不调用领域 service，返回局部 budget_exceeded |
| 任一 `disposition="replayed"` | 不发 budget 信号，抛模块 04 `OperationResultIndeterminateError`，run faulted |
| manager typed exception | 原样传播，run faulted |

预算无退款。预算 applied 只代表允许调用，不代表领域调用成功。模块 04 manager 拥有幂等账本；模块 05 不复制账本，但必须在端口边界执行上述 response↔request 相关性校验。

## 13. 错误、typed failure 与 run 状态

公共异常均为零参数、无正文数据、`str(exc)` 不包含输入、token 或内部原因：

```text
RequirementSubgraphError
├── RequirementSubgraphConfigurationError
├── RequirementSubgraphInputError
│   └── RequirementSubgraphResumeRejectedError
├── RequirementSubgraphStateError
├── RequirementSubgraphRunFaultedError
└── RequirementSubgraphCapacityExceededError
```

| 情况 | 结果/异常 | 副作用 |
|---|---|---|
| 等待用户 | paused result | 仅安全 interrupt/信号 |
| confirmed/extraction_failed/budget rejected | typed result | 按合同完成的局部结果 |
| start 非精确/非法 state 或 turn、重复 run | InputError | admission commit 前为零 |
| resume 的 unknown/terminal run、token/branch/stale/current/schema/duplicate 错 | ResumeRejectedError | 零预算、零领域调用 |
| get_state unknown run 或非精确 UUID | InputError | 零新增副作用 |
| 任一入口命中 faulted run | RunFaultedError | 零新增副作用 |
| wrong event loop、graph↔coordinator/runtime/saver mode 配置错 | ConfigurationError | run 准入前为零；既有跨 loop run 不改写 |
| service/manager 返回非精确 DTO、不一致 transition 或 budget outcome↔request 错配 | StateError | 已开始 run 永久 fault；错配预算零信号/零领域调用 |
| coordinator 构造依赖缺少冻结 callable surface；manager factory 返回错误 surface | ConfigurationError | 构造阶段或 admission commit 前为零 |
| manager factory 在 commit 前抛异常 | 原样传播 | 不创建 run、不占槽、不发信号 |
| sink 正常返回非 `None` 或内部信号 DTO 构造失真 | StateError | commit 后永久 fault、清敏感槽、保留容量 |
| resolve_result 在无 lease/错误 state/错误 interrupt value 下调用 | StateError | active run 永久 fault；未准入 run 不创建 |
| start/resume lease 正常退出未成功 resolve，或同 lease 第二次 resolve | StateError | 永久 fault、清敏感槽、保留容量；原异常退出不被覆盖 |
| serializer/checkpoint/state 失真 | StateError | 失败关闭 |
| 容量满的新 run | CapacityExceededError | 零信号、零 Graph/预算/领域调用 |
| 上游 typed exception | 原样传播 | graph 已开始则 run faulted |
| accepted signal sink 异常 | 原样传播 | commit 后永久 fault、清敏感槽、保留容量 |
| 其他程序/基础设施异常 | 原样传播或规定的本地异常 | active run 永久 fault |

preflight/admission commit 前拒绝不创建或 fault run。commit 后直到 pause/局部 settled 的 active lease 中任一异常把 run 永久标为 faulted；accepted signal 即使位于 `ainvoke` 前也已越过 commit point，投递可能不确定，不能回滚重试。terminal result 与 faulted tombstone 都保留到 Runtime 销毁；没有 reset/retry/reopen。

## 14. 容量、并发与生命周期

- 新 run 在 manager factory 成功并由 admission lock 原子插入 record 时占 1 个槽；paused、confirmed、domain_failed、budget_exceeded、faulted 全部保留槽位。
- 满容量时现有 run 的 `resume/get_state` 仍工作；下一个新 run 在任何副作用前抛 `RequirementSubgraphCapacityExceededError()`。
- 不驱逐、不 TTL、不 LRU、不公开 delete/clear；Demo 上限 256。
- 每个 run 同时最多一个 operation lease；resume/get 等待同一 operation lock 后按最新状态重新校验，节点通过 ContextVar lease 访问且不重入该锁。不同 run 可以并发，预算 manager、token、nonce、input slot 和 checkpoint 必须隔离。
- admission/registry lock 与 per-run operation lock 的顺序固定为“短暂 admission → 释放 → 整次 operation”；永不反向获取，永不在 admission lock 内 await 外部端口。并发证明使用 barrier/event 与 `asyncio.wait_for`，禁止依赖 sleep 碰运气。
- coordinator、saver、graph 和所有 manager 绑定首次使用的事件循环；跨事件循环调用失败关闭。
- 无 `close/aclose` 公共方法。销毁 Runtime 即释放 Demo 内存；生产生命周期属于后续模块。

## 15. 状态、Artifact、checkpoint、Memory 与审计分离

| 存储 | 模块 05 内容 | 生命周期/所有者 |
|---|---|---|
| Graph state | 小型 `TravelSessionState` v2 | checkpointed，04 定义 |
| checkpoint | state + LangGraph 控制元数据 + 安全 interrupt/sentinel | Demo 05；生产后续模块 |
| coordinator | token/nonce、完整恢复 DTO、锁、manager、tombstone | 进程内易失，05 |
| durable pause | token/state 摘要、nonce、安全 interrupt、run/trace | DTO 由 05 定义；数据库落地与生命周期由 11 |
| Artifact Repository | 大型业务结果与 ref | 04 端口；05 不写新 artifact |
| Safe event/audit | 版本化安全事件 | 13/19～21，不放 checkpoint |
| 长期 Memory | 跨 run 语义记忆 | Future，不属于 05 |

模块 05 只原样保留已有 `artifact_refs`，不创建、读取或删除 Artifact。checkpoint 可恢复 Graph 控制位置；durable pause 只可重建 coordinator 当前暂停证据；两者都不等于 Artifact、审计记录或完整崩溃恢复。

## 16. 安全与隐私

- token 使用 CSPRNG、恒定时间比较、`repr=False`；不记录、不发信号、不进入 operation ID。
- token digest 使用 §4.1 的固定 domain separation 与 run ID 绑定算法；只允许恒定时间比较。digest 不能替代 Module 11 的一次性接受事务，也不得作为公开 token 查询接口。
- 完整 turn/action 只在等待 Graph 节点消费期间短暂保留；节点 take 后、run 局部结束或 fault 时立即清空引用。
- interrupt 只带安全 message/summary；完整 state/outcome 和原始输入留在受控对象中。
- 异常零参数；日志/测试报告只使用 run/trace、类型、计数和安全 OperationId。
- 禁止 secret、原始模型响应、reasoning、Prompt、完整 turn/action、未被模块 01 有意规范化进 `RequirementDomainState` 的 evidence/user/revision 原文、provider payload 进入 checkpoint、信号或异常。模块 04 state 中已冻结的规范化 requirement、evidence ref/digest 和安全 message/summary 不因本条被删除或另存。
- 所有输入/result/signal/state 做 mutation canary，调用方后改对象不得改变内部记录。
- 不允许 pickle、任意对象反序列化或动态模块加载。
- `process_simulation-platform-dev` 是另一个项目；模块 05 的设计、实现、测试和 Docker 操作均不得读取、修改、启动、停止、复用或清理它的文件及资源。

## 17. LangGraph 1.2.7 资格事实

从基线 `a8479f5d0bb3fa8782286fbf3fee8b25027f6ced` 当前源码构建的新 test 镜像已核验：Python 3.12.13、Pydantic 2.13.4、LangGraph 1.2.7。允许公共 import 精确为：

```python
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command, Interrupt, Overwrite, Send, interrupt
```

已核验 `StateGraph.compile(checkpointer=...)`、`CompiledStateGraph.ainvoke/aget_state`、`Command(resume=...)`、`interrupt(value)`、公开 `Interrupt(value, id)` 构造、`Send/Overwrite` 公共导入、thread_id 必填、子图 `checkpointer=None` 继承父 saver、暂停节点恢复时重入，以及无静态出边节点的 `Command(goto=<node|END>)`。真实父子暂停探针还确认 saver 会把非空顶层 `tuple[Interrupt, ...]` 交给 serializer，而 JsonPlus msgpack 默认恢复为 list；因此第六个 `interrupt_tuple` 自有 tag 是强制兼容层，不能删去或改为 delegate 放行。另已核验“静态 conditional edge + Command goto”会双重调度，因此本模块明确禁止该接线。依赖版本或这些公开路径改变时必须重新构建镜像并重新资格验证；不得按其他版本教程猜测。

## 18. 上游兼容矩阵

| 上游 | 已冻结提供 | 模块 05 精确消费 | 禁止 |
|---|---|---|---|
| 01 | state/outcome/transition、turn、confirmation、service | process_turn/confirm；transition 连续性；message/summary | 复制 merge、renderer、确认完整性 |
| 03 | 满足 RequirementExtractor 的 DeepSeek adapter，每 turn session、至多一次 repair | 装配到注入的 RequirementDomainService | import llm_runtime、管理 session/prompt/repair |
| 04 state | TravelSessionState v2/codec/phase/status | 唯一 Graph state、fresh update、严格 checkpoint | 第二套 state、终态映射 |
| 04 budget | key/request/outcome/manager/OperationId | consume 1、处理 applied/rejected/replayed | 复制账本、退款、重置 |
| 04 event | SafeWorkflowEvent 语义目录 | 只向 13 提供本地信号 | 自建 event id/sequence/sink |
| 04 Artifact | ArtifactRef/Repository | 只保留 refs | 把 checkpoint 当 Artifact |

上游期望业务失败作为 `RequirementExtractionFailed` 进入局部结果；上游 runtime/programmer/typed 异常原样传播并 fault run。

## 19. 下游衔接矩阵

| 下游 | 模块 05 提供 | 本轮不冻结 |
|---|---|---|
| 06 | 仅由模块 13 在主图中与已确认需求衔接；06 自身只直接依赖 04 | 百度地图 typed adapter、MCP Gateway、规范化和技术限流 |
| 07 | confirmed state 经模块 13 路由后可供其消费 | 酒店候选、UID 去重、人工选择及其 DTO |
| 08 | confirmed state 经模块 13 路由后可供其受控研究循环消费 | 候选研究循环、工具白名单和停止合同 |
| 09 | confirmed state 经模块 13 路由后可供其规划循环消费 | 行程草案、路线优化和 Direction 预算 |
| 10 | Artifact／Repository 公共合同和既有 checkpoint 边界 | PostgreSQL Artifact／evidence schema、迁移和持久 Repository |
| 11 | serializer、interrupt/resume、durable pause、embedded driver、预算和同进程生命周期语义 | PostgreSQL checkpoint、durable pause/accepted command 落地、run 协调、持久执行账本和跨进程恢复判定 |
| 12 | confirmed state 仅作为未来主链前置事实 | 结构化结果、中文攻略、费用未核验标签和“不新增事实”输出 |
| 13 | embedded driver、无私有 saver child、本地信号 | 完整主图结构、driver 生命周期接线、全局 operation、事件序号、终态映射 |
| 14 | Runtime 语义和同进程限制 | Application Service、应用 API 和网页交互 DTO |
| 15 | 离线 workflow-contract 证据 | 真实网页 release case |
| 19～21 | 安全信号语义 | 隐私生命周期、Trace/Metrics、审计 adapter |
| 22～23 | 可观测的测试边界 | Production 发布和长期演进细节 |

没有独立权威设计的未来模块只按主设计和路线图保留职责边界；本文不为其发明 DTO、Protocol、stub 或占位代码。

## 20. 版本、迁移与 Production 替换

- 模块 05 自有 DTO/信号使用 1.0；TravelSessionState 保持 2.0；sentinel 为 `requirement_resume_v1`。
- interrupt/resume 字段或分支、token/nonce 规则、OperationId 格式、预算点、信号字段、checkpoint allowlist/标签发生 breaking change，必须新版本并回到用户批准的设计阶段。
- LangGraph 锁定 1.2.7；升级或依赖约束变化必须重新运行 API、pause/resume、serializer、父子 checkpointer 和完整回归 Gate。
- Module 05 standalone 参考实现无迁移：进程退出丢弃所有 checkpoint/coordinator/预算 tombstone；embedded 模式只定义 strict durable DTO 和重建机制，不拥有数据库 migration。
- 仅替换 saver 为 PostgreSQL 不构成崩溃恢复；Module 11 必须同时持久化 durable pause、token 摘要、accepted command、预算和 lease/fence，并在安全 checkpoint 上调用 embedded driver。Artifact 正文由 Module 10 持久化。
- 不创建旧版本 parser、双写、fallback、兼容 alias 或 migration 脚本。

## 21. 测试矩阵

未来实现至少覆盖：

| 类别 | 必测内容 |
|---|---|
| models | DTO/union/version/discriminator/strict/frozen/extra/repr/fresh/JSON/mutation；durable run/trace/state/token digest/nonce/interrupt cross-field |
| coordinator | admission 原子容量、operation lease/ContextVar、resolve exactly once、无重入死锁、loop、token/nonce、stale/cross-run、tombstone、factory once、accepted sink fault |
| graph | 合成合法 transition 穷举五 outcome 动态 Command router、仅一条 START 静态边；另实例化生产 `app.requirements.RequirementDomainService` 并只读复用 `tests/unit/requirements/fakes.py` 的 extractor/evidence/clock fakes，覆盖 clarification→confirmation→纯 accept confirmed 与 extraction_failed；scripted service 仅测图编排/非法返回；fresh transition、status/terminal/ref preservation |
| interrupt/resume | safe payload、sentinel only、错误 branch/current/token、duplicate resume、node re-entry复用 pause record、secret调用次数 |
| replay/budget | 固定顺序、applied/rejected/replayed、response↔request run/op/key/amount/snapshot 相关性、错配 StateError、fresh-driver durable nonce 稳定 OperationId、跨 driver 共用 manager 的 applied 后 replayed 在领域调用前 indeterminate、无退款、无重复 service/signal |
| checkpoint | 六个精确 wire tag/bytes、interrupt tuple 非空/id/value/精确恢复、两个精确 allowlist、默认 saver 失真、严格 serializer pass、tuple/enum/union/ref、unknown tag/version/pickle/fallback/canary |
| composition | Demo 私有 saver、Runtime/embedded binding 负例、父图继承、公共 operation scope、resolve exactly once 正反例；同一 parent saver + 全新 driver/coordinator 从 durable pause 执行 accepted resume，保持 nonce/NodeReplayed，零首轮重复调用 |
| concurrency | barrier + wait_for 验证同 run 双 start/resume/get、容量边界、不同 run 隔离、无死锁、事件循环错误 |
| errors/security | 四 result、异常层级、upstream propagation、fault、repr/log/checkpoint canary |
| boundaries | package exports/import boundary、无 llm_runtime/private/future stub |

固定测试目录：

```text
tests/unit/requirement_subgraph/
├── test_models.py
├── test_signals.py
├── test_checkpoint.py
├── test_coordinator.py
├── test_graph.py
└── test_runtime.py

tests/contract/requirement_subgraph/
├── test_public_api.py
├── test_import_boundaries.py
├── test_interrupt_resume.py
├── test_checkpoint_contract.py
├── test_replay_budget_contract.py
├── test_composition_contract.py
└── test_security_contract.py
```

只用 fake 外部能力；不运行 DeepSeek/MCP/地图/数据库/live。生产包 line、branch、combined coverage 分别 ≥95%，不能用其他 coverage source 冒充。定向通过后运行模块 01/03/04 和完整 unit/contract 离线回归；failed/skipped/xfail 必须为 0。

## 22. 验收标准

1. 固定包/API/DTO/异常/信号与本文一致，无额外公共扩展。
2. 私有 router 用合成合法 transition 防御性覆盖全部五种 outcome；真实模块 01 路径断言只有纯 accept 产生 confirmed。
3. checkpoint 扫描不含完整 turn/action/resume 结构、token/nonce 或非权威原始 payload，且严格 round-trip 保持精确业务类型；模块 01 已有意规范化进 state 的 value/raw_value/evidence 服从 §16，不做错误的全字符串禁令。
4. 同一暂停节点重入可检测，预算与领域调用顺序固定，重放不产生重复副作用。
5. stale/cross-run/wrong-branch/duplicate resume 失败关闭且零预算/领域副作用。
6. 一个 Demo Runtime 可隔离服务多个 run，容量与 tombstone 行为确定。
7. 真实最小父图使用公共 operation scope 与无私有 saver child 完成 start→pause→resume→child confirmed→parent continuation，并区分 domain/budget局部结果；另用同一父 Saver 和全新 embedded driver/coordinator 从 durable pause 继续，token 明文不参与 accepted recovery，nonce 与 NodeReplayed 保持稳定；模块 05 未占有主图/终态职责。
8. 所有定向、coverage、Docker、受影响上游和完整离线 Gate 通过，无 skip/xfail。
9. `MODULE-CLOSEOUT` 同步 README、状态页和 Dockerfile 后，从最终源码重建并重跑全部 Gate。
10. 实施期间两份批准设计保持只读；变更设计先停止并获用户同意。

## 23. 停止条件

实现或重资格验证发现以下任一情况必须停止：

- 需要修改主设计、路线图、模块 01/03/04 或本规范已冻结语义。
- 需要扩大阶段文件白名单、增加未批准公共符号或导入私有实现。
- LangGraph 1.2.7 公开 API、serializer 或父子 saver 行为无法由新镜像证明。
- 必须把完整输入、token、provider/session/manager 或原始 payload 写入 state/checkpoint 才能工作。
- 无法保证当前暂停节点 replay 不重复预算或领域副作用。
- 需要在 Module 05 范围内实现 PostgreSQL、租约/fencing、durable DTO 落库、恢复安全判定、真实网络、Secret 或未来模块占位；这些需求必须交给 Module 10/11 或对应外部能力设计。Module 05 仅实现已批准的 embedded 重建适配器。
- coverage、Docker、上游或完整离线回归未通过，或出现 skip/xfail/0 tests。
- dirty 工作区包含无法安全保留的用户改动，或准备 stage/commit/push/PR/deploy 而未获明确授权。

## 24. 决策记录

| ID | 决策 |
|---|---|
| D-05-01 | 只重入当前暂停节点；无历史 time travel 或副作用节点任意 replay |
| D-05-02 | 唯一 Graph state 为 TravelSessionState v2 |
| D-05-03 | 完整 turn/action 进易失 coordinator，Command 只携带固定 sentinel |
| D-05-04 | run/thread 一一对应，thread_id 为小写 run UUID |
| D-05-05 | 每次 pause 使用独立 token 与 nonce；token 不进 OperationId |
| D-05-06 | 追问回复和确认 revise 消费 1，accept/首轮/interrupt 不消费 |
| D-05-07 | 模块 05 发本地信号，模块 13 拥有 SafeWorkflowEvent |
| D-05-08 | 四分支局部 result 与模块 13 全局终态分离 |
| D-05-09 | 一个 Runtime 多 run、per-run 串行、容量 256、无驱逐/删除/TTL |
| D-05-10 | 一份 builder 两种装配；嵌入父图时不带私有 saver |
| D-05-11 | 默认 saver 不合格，必须使用版本化严格 serializer |
| D-05-12 | Module 05 独立参考实现仅同进程/同事件循环；退出即丢弃，无迁移；完整 Demo 的持久恢复由 Module 10/11 接管 |
| D-05-13 | 仅 START 使用静态边；三个处理节点统一用私有五分支 helper 返回动态 Command |
| D-05-14 | standalone Runtime 使用 coordinator 控制面；Module 13 durable 组合只使用受控 embedded driver，不写 coordinator 私有 record |
| D-05-15 | admission/registry lock 与 per-run operation lease 分层；节点不重入 operation lock |
| D-05-16 | accepted signal 位于容量 commit 后，sink 异常永久 fault且不重试 |
| D-05-17 | serializer 六个 wire tag和两个 JsonPlus symbol allowlist 固定；interrupt tuple 使用独立 tag精确恢复；未知 tag/version/fallback失败关闭 |
| D-05-18 | 每个 start/resume operation lease 必须且只能成功 resolve 一次；遗漏或重复永久 fault |
| D-05-19 | paused result 携带不含 token 明文的 durable pause；state/token 摘要、nonce、interrupt 使用固定 1.0 合同 |
| D-05-20 | accepted command 落库前必须验证 token；落库后 embedded driver 可用 durable pause + resume 在新 coordinator 重建当前暂停点，不恢复 token 明文 |

批准本文只表示设计完成，不表示代码、测试、迁移、提交或发布完成。
