# 05 需求子图 Vibe Coding 精确执行契约

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 权威语义：[`05_requirement_subgraph.md`](05_requirement_subgraph.md)
> 共享规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 依赖真源：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 合同强度：本文中的“必须”“精确”“固定”“禁止”“失败即停止”均无自由裁量权
> 当前状态：v1.1 公共合同已获用户批准；后续实施仍须获得对应任务授权，本文不授权 stage、commit、push、PR 或部署

本文只规定怎样实现和验收。公共语义、所有权、不变量和版本以权威设计为唯一真源；不得从本文复制出第二套设计，也不得用“等价实现”替换已冻结 L0/L1 合同。

## 1. 执行模式与通用边界

### 1.1 三种模式

| 模式 | 使用条件 | 必须执行 |
|---|---|---|
| `full_rebuild` | 模块 05 首次实现；本次固定模式 | `REQ-SG-T00`～`REQ-SG-T05` 全部 |
| `maintenance` | 已验收模块 05 的小范围修复 | T00、最早受影响 Task、其全部下游 Task、T05 |
| `requalification_only` | 生产代码不变，只重新证明资格 | T00、T05；T01～T04 填 `NOT_RUN（模式不适用）` |

首次实施只能是 `full_rebuild`。不得把未运行 Task 写为 PASS；`collected 0 items`、非零退出、coverage 写入失败、skip 或 xfail 都是 FAIL。

### 1.2 强制规则

- 开工必须同时引用本执行契约、权威设计和共享规则；逐条对照，不得只看测试是否通过。
- 不得修改模块 01、03、04，不得导入其私有实现，不得直接导入 `app.llm_runtime`。
- 不得复制 `RequirementDomainState`、`RequirementDomainOutcome`、`TravelSessionState`、summary renderer、确认完整性、预算 manager 或事件实现。
- 不得用 dict 替代冻结 DTO，不得从 outcome 反向重建 state。
- 不得把 provider、extractor session、repository、manager、saver、sink、锁、token、完整 turn/action 或原始 payload 放入 Graph state/checkpoint。
- 不得创建模块 06～23 的 stub、占位 API、空包或未来 DTO。
- 严禁读取、修改、启动、停止、复用或清理 `process_simulation-platform-dev` 的文件、容器、网络、卷或镜像。
- 不得删测试、放宽断言、skip/xfail、自动 rerun 到偶然全绿或只跑绿灯子集。
- 正式验收必须使用从当前最终源码重建的 Docker 镜像；宿主机 Python 结果只能诊断。
- 实施模型不得 stage 或 commit。commit 只能由后续独立审查提示词在用户再次明确授权后执行；push、PR、deploy 均未授权。

### 1.3 实施提示词分组

后续每份实施提示词必须引用权威设计和本执行契约，不重复粘贴文档大段正文，并保留阶段目标、前置条件、分支、文件白名单、允许操作、测试矩阵、停止条件和报告格式。

固定分组：

1. `REQ-SG-T00` 是每次实施前的只读 Gate。
2. 首个编码批次一次执行相邻 `REQ-SG-T01 + REQ-SG-T02`，两者完成后统一跑对应测试。
3. 第二个编码批次一次执行相邻 `REQ-SG-T03 + REQ-SG-T04`，两者完成后统一跑对应测试。
4. `REQ-SG-T05` 是 Docker、coverage、全回归和 closeout 安全门，单独执行。

不得为凑两个 Task 跳过验证。阶段白名单内的小范围明确问题可直接修复；改变确认设计、扩大仓库范围或大改必须停止并征求用户同意。

### 1.4 每个 Task 的输入与报告

后续提示词启动任一 Task 时，必须显式给出：`execution_mode`、期望分支、实际开始 HEAD、设计基线 HEAD、两份已批准文档的 SHA-256、开始工作区与 staged、已批准 dirty 白名单、前序 Task 结果，以及本轮 Docker 唯一后缀。缺任一项不得开工。

每个 Task 结束必须单独报告：`PASS / FAIL / NOT_RUN`、开始/结束分支与 HEAD、实际读写文件、白名单外文件、逐条命令及退出码、pytest 收集数和 `passed/failed/errors/skipped/xfail/xpassed/deselected` 数、Docker 镜像 tag/ID 与容器名、设计偏离、停止原因和后续 Task 状态。未执行必须写 `NOT_RUN（被 <精确 Task/Gate/条件> 阻断）` 或 `NOT_RUN（模式不适用）`，禁止写 PASS、N/A、空白或“预计通过”。

## 2. L0/L1 真源与开始条件

### 2.1 必读引用

每个实施批次开始前读取：

```text
docs/00_agent_master_design.md
docs/IMPLEMENTATION_ROADMAP.md
docs/IMPLEMENTATION_STATUS.md
docs/SHARED_IMPLEMENTATION_RULES.md
docs/05_requirement_subgraph.md
docs/05_requirement_subgraph_vibe_coding.md
```

`IMPLEMENTATION_STATUS.md` 只作为特定历史验证快照，不覆盖实际 Git/命令。直接依赖只能来自路线图 `depends_on: [01,03,04]`，不得另建依赖索引、机器导航、上下文包或生成器。

每次 T00 全部读取、后续 Task 按消费范围复核的直接依赖矩阵固定为：

| 依赖 | 权威设计章节 | 执行契约章节 | 真实代码/测试 |
|---|---|---|---|
| 01 | `01_requirement_and_dialogue.md` §2、§3、§5.1～5.3、§7.1～7.2、§11、§13、§16.3、§17 | `01_requirement_and_dialogue_vibe_coding.md` §2.2、§3～3.1、§6.2～6.6、§7、§11.2～11.3、§13～14、§16、§18～19 | `app/requirements/{__init__,models,extraction_contract,service}.py`；原任务列出的 models/service/renderer/extractor contract tests |
| 03 | `03_requirement_extraction_adapter.md` §2～5、§9～10、§13～14、§16、§18～19 | `03_requirement_extraction_adapter_vibe_coding.md` §1、§3～4、§10～12、§16、§18 | `app/requirement_extraction/{__init__,adapter,session}.py`；adapter/session/extractor contract tests |
| 04 | `04_workflow_state_and_artifacts.md` §2～6.7、§7～8、§10～10.1、§12～19、§20～26 中 05 实际消费条款 | `04_workflow_state_and_artifacts_vibe_coding.md` §1～5、§16～17、§19 | `app/workflow_contracts` 包根及 state/identifiers/errors/artifact/budget/event/provider 文件；原任务列出的 state/security/public/import/repository/budget/event contract tests |

T01 复核 DTO/异常/信号相关行；T02 复核 manager/OperationId/lifecycle；T03 复核模块 01 service/transition 与模块 04 state/budget；T04 复核 state codec/ArtifactRef/TerminalOutcome、模块 03 真实 adapter 装配边界和 import contract。指定文件或章节缺失立即 STOP，不得换成相近历史文档。

### 2.2 固定 Git 前置

首次实现预期：

```text
分支：feature/module-05-requirement-subgraph
设计基线 HEAD：a8479f5d0bb3fa8782286fbf3fee8b25027f6ced
两份模块 05 设计文档：已获用户批准且实施期间只读
工作区：除已批准文档提交状态外无未知 dirty 文件
staged：空
```

实施提示词必须记录实际开始分支、HEAD、工作区和 staged。HEAD 变化时列出与设计基线的关系及新增提交，核验是否影响 01/03/04/05 公共合同；不得自行宣称等价。

### 2.3 L1 必须存在的公共符号

从包根精确消费：

```python
from app.requirements import (
    ConfirmedTravelRequirement,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainOutcome,
    RequirementDomainService,
    RequirementDomainState,
    RequirementDomainTransition,
    RequirementExtractionFailed,
    RequirementInvalidated,
    RequirementTurnInput,
)

from app.workflow_contracts import (
    ArtifactRef,
    BudgetConsumed,
    BudgetConsumeRequest,
    BudgetExceeded,
    ContentHash,
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetManager,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    InMemoryExecutionBudgetManager,
    OperationId,
    OperationResultIndeterminateError,
    TerminalOutcome,
    TravelSessionState,
    WorkflowPhase,
    parse_travel_session_state_json,
    serialize_travel_session_state,
)
```

模块 03 真实构造入口由装配层核验为 `app.requirement_extraction.DeepSeekRequirementExtractor`，但模块 05 生产包不得导入它；模块 05 只接收已构造的 `RequirementDomainService`。

## 3. 总白名单、固定文件树与职责

### 3.1 生产与测试文件

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

tests/requirement_subgraph_fakes.py
```

`REQ-SG-T05` closeout 额外允许：

```text
README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile
```

两份模块 05 设计文档、`pyproject.toml`、`requirements.lock`、compose、模块 01～04 和其他路径始终只读。白名单不够立即 STOP。

### 3.2 文件职责

| 文件 | 唯一职责 |
|---|---|
| `errors.py` | 权威设计 §13 的零参数异常树 |
| `models.py` | interrupt/resume/result/limits DTO、union 和预算常量 |
| `signals.py` | 五个安全信号、union、同步 Protocol、NoOp |
| `checkpoint.py` | 严格 `SerializerProtocol` 实现与私有标签 helper |
| `coordinator.py` | bounded run registry、per-run lock、token/nonce、易失槽、manager、tombstone |
| `embedded.py` | 无私有 saver 的受控 driver、durable accepted-resume 重建与 factory |
| `graph.py` | 三个节点、router、state fresh update、builder |
| `runtime.py` | start/resume/get、结果封装、Demo factory |
| `__init__.py` | 权威设计 §3 的唯一稳定 re-export |

禁止万能 `utils.py`、第二个 state model、第二个 runtime/builder 或 graph-private DTO。

## 4. 固定公共合同

本节是权威设计 §3～§6 的实施索引；字段语义和不变量不得在此另作解释。

### 4.1 DTO 与 union

精确实现并导出：

```python
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
```

所有自有 DTO strict/frozen/extra forbid；版本、discriminator、字段、默认值和 cross-field invariants 必须与权威设计 §4 逐字一致。`pause_token` 精确 32 小写 hex、`repr=False`；clarification `turn`、confirmation `action` 和 envelope `resume` 也必须 `repr=False`，防止 user/revision 原文递归泄漏。durable pause 必须覆盖 run/trace/state digest、token digest、nonce、interrupt，token digest `repr=False` 且不得包含明文 token。行为入口拒绝 dict、subclass 和非精确实例。budget result 负例必须覆盖 replayed、wrong run/key/phase/amount/snapshot/prefix；paused 负例覆盖 branch/source/message/summary/state/digest/token-digest/nonce 不一致。

### 4.2 常量

```python
DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS.max_runs == 256
DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT == 6
REQUIREMENTS_CLARIFICATION_ROUND_KEY.scope is ExecutionBudgetScope.PHASE
REQUIREMENTS_CLARIFICATION_ROUND_KEY.name == "requirements.clarification_round"
REQUIREMENTS_CLARIFICATION_ROUND_KEY.phase is WorkflowPhase.REQUIREMENTS
```

私有常量 `_REQUIREMENT_RESUME_SENTINEL` 精确为 `"requirement_resume_v1"`。不得把 sentinel 当用户 DTO、token 或 operation ID。

### 4.3 信号与 sink

实现权威设计 §5 的五 DTO、discriminated union 和：

```python
class RequirementSubgraphSignalSink(Protocol):
    def accept(self, signal: RequirementSubgraphSignal) -> None: ...

class NoOpRequirementSubgraphSignalSink:
    def accept(self, signal: RequirementSubgraphSignal) -> None: ...
```

NoOp 只做精确 fresh validation 后返回 `None`，不存储、不打印。信号不含 token、独立 nonce 字段、message/summary、checkpoint/native ID 或用户原文；nonce 必须且只能作为 `OperationId.logical_step` 的 32hex 后缀出现。`NodeReplayed` 用 `requirement_pause_` 前缀，budget 信号用 `clarification_round_` 前缀，且 OperationId run/phase 与 signal 一致。token 做全字符串缺席 canary；nonce 做结构化路径断言，禁止用“信号 repr 不含 nonce”这种必然失败的断言。

### 4.4 异常

按权威设计 §13 精确继承；所有类无自定义有数据构造参数，无 cause 链泄漏，调用点使用 `raise ...() from None`。不得包装模块 01/04 的 typed exception；需要原样传播的异常必须保持精确类型。

### 4.5 Serializer

```python
class RequirementCheckpointSerializer(SerializerProtocol):
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]: ...
    def loads_typed(self, data: tuple[str, bytes]) -> Any: ...
```

只实现 `SerializerProtocol` 公开的两个方法。六个完整 wire tag、规范 bytes、`requirement_subgraph.` 保留前缀、两个 JsonPlus symbol allowlist、delegate 前后 type-tree 检查和失败优先级精确按权威设计 §10.3；第六个 `requirement_subgraph.interrupt_tuple.1.0` 必须在 delegate 前截获真实 pause 的顶层非空 `tuple[Interrupt,...]` 并精确恢复 tuple。`pickle_fallback=False`；非自有前缀只允许 `null/bytes/bytearray/json/msgpack` 五个 delegate tag，`pickle`/其他 tag 在委托前拒绝。禁止 `model_construct`、`pickle`、`cloudpickle`、`allowed_*_modules=True`、前缀/模块级宽许可、动态 import 和未知标签 fallback。

### 4.6 Coordinator、embedded driver、builder、Runtime 与 factory

签名精确复制权威设计 §6，包括 coordinator 的三个控制方法、embedded driver 的 `start_operation/verify_pause_token/resume_accepted_operation/resolve_result`、两个 factory。不得新增可选 positional 参数、`**kwargs`、公共 reset/delete/list/history/close/retry 方法或 caller supplied checkpoint config。

`build_requirement_subgraph` 必须显式接收 `checkpointer`；`None` 是父图继承模式，不是 builder 创建默认 saver的信号。

operation 方法返回异步上下文管理器，作用域覆盖整次 standalone/parent `ainvoke`；`resolve_result` 只在该 lease 内调用。Runtime 只包装 standalone binding；embedded child 禁止构造 Runtime。Module 13 组合只使用 embedded driver，不直接写 coordinator record。`verify_pause_token` 纯验证、零 manager/record/signal；`resume_accepted_operation` 不接收 token，只能消费 Module 11 已持久接受的 resume，并按 durable pause 匹配或重建 record。真实父图测试必须通过公开 driver 完成输入预置、token 输出、跨 driver resume、budget typed result 和 parent continuation，禁止调用 coordinator 私有方法。

用于 Module 11 durable 组合的 factory 调用必须省略 `signal_sink` 并使用默认 NoOp，防止 scope 已进入但 dispatch marker 未提交时重复投递 accepted 信号；自定义 sink 仍只用于 standalone/非持久 parent 与 Module 05 自身测试。Module 13 durable 事件必须在 Module 11 dispatch/checkpoint 证据之后另行生成，不能直接映射本地 M05 signal。

每个 start/resume lease 必须且只能成功 resolve 一次。正常退出但零次 resolve，或第二次 resolve，均抛 StateError、永久 fault、清敏感槽并保留容量；body/首次 resolve 已抛异常时保留原异常，不再用缺少 resolve覆盖。私有 get_state 读 scope 明确豁免。

## 5. 固定私有结构与算法顺序

### 5.1 coordinator record

允许在 `coordinator.py` 使用一个私有 frozen/slots descriptor 和一个私有 mutable record。每个 record 至少持有：

```text
run_id / trace_id
per-run operation asyncio.Lock
bound event loop
status: running | paused | resume_pending | confirmed | domain_failed | budget_exceeded | faulted
latest fresh TravelSessionState
fresh volatile turn/action slot
current pause_token
current operation_nonce
current safe interrupt descriptor
lease_generation / active ContextVar lease identity
current lease resolved flag / successful resolve count
per-run ExecutionBudgetManager
pending local result metadata
```

coordinator 另持一个 admission/registry lock和一个私有 lease `ContextVar`。admission lock 只包住 loop bind、run 查重、容量、同步 factory 和 record 原子插入，不能跨任何 await；record 插入是容量 commit point。operation scope 持 per-run lock覆盖整个 graph/get，node helper验证 ContextVar lease并禁止重入 operation lock；start/resume scope 的 `__aexit__` 强制 successful resolve count 恰为 1，私有只读 get scope 不计数。字段名可保持私有，但语义不得删减。record、锁、manager、service、sink 和 DTO slot 不得被 Graph 返回或序列化。

### 5.2 fresh copy helper

每个模块 05 DTO/state/outcome/signal边界统一：

1. 精确 `type(value) is ExpectedType` 或 union 分支精确类型检查。
2. `model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)`。
3. `TypeAdapter.validate_python(..., strict=True)`。
4. JSON `sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False`。
5. `TypeAdapter.validate_json(..., strict=True)`。

禁止以 `model_copy()` 或 Pydantic frozen 宣称替代完整 fresh copy。上述 exact identity 不应用于 Protocol/Callable；`ScriptedRequirementDomainService` 必须继承公开 service，manager/sink 按端口 surface 调用，每次返回 DTO再做 exact/fresh。

### 5.3 state 更新

每次 transition 后必须：

1. 构造公共 `RequirementDomainTransition(state=..., outcome=...)`。
2. 分别 fresh state/outcome。
3. 基于旧 `TravelSessionState.model_dump(mode="python")` 构造一个全新 `TravelSessionState`。
4. 只替换 requirement_state/outcome/status；原样保留 schema/run/trace/phase/artifact_refs/terminal。
5. 再通过模块 04 公共 state codec round-trip。

不得在原对象原地修改，不得用 outcome.requirement 或 metadata 构造 state。

### 5.4 pause/replay

等待节点开头固定调用 `_ensure_current_pause`。`running` 的首次进入才生成互异 token/nonce并记录 descriptor；`resume_pending` 的同一节点重入必须原样复用 record，不能调用 `_new_secret_hex()`。随后构造/fresh 验证 interrupt DTO → 转成 JSON 基元规范 tree → `interrupt(tree)`。不得把 Pydantic interrupt DTO 本身交给 saver；在 `interrupt` 返回前不得 consume、发 replay或调用 domain service。

恢复重入：断言 `interrupt()` 返回精确 sentinel → take-once 取已验证 DTO并保留当前 nonce → 清 active pause token/descriptor → 构造 `requirement_pause_<nonce>` OperationId → 发 `NodeReplayed` → 按分支决定是否消费 → 调用 service → 原子更新 state → 动态 Command route。只有路由到下一个新暂停点时才生成下一对 secret。

token 用 `secrets.token_hex(16)` 和 `hmac.compare_digest`；nonce 也用 `secrets.token_hex(16)`，若偶然等于 token 立即重取 nonce。不得使用 UUID、random、hash、时间或递增计数代替。`SequenceSecretHex` 必须证明首次暂停至少消费两值、同暂停 resume 重入消费零值、下一暂停才继续消费。

### 5.5 budget

消费精确构造：

```python
BudgetConsumeRequest(
    operation_id=OperationId(
        run_id=state.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=f"clarification_round_{operation_nonce}",
    ),
    key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
    amount=1,
)
```

manager outcome fresh 后必须先与 request 精确相关：operation_id、key、amount 逐值相等，snapshot.run_id/key 也与 request 一致；任一错配先抛 StateError并 fault，零 budget 信号、零 domain call。相关性通过后：`applied` 用已验证 request OperationId 发一次 consumed 再调用一次 service；`rejected` 发一次 exceeded，写局部 outcome 槽并 `Command(update=fresh_in_progress_state,goto=END)`；任一 `replayed` 直接 `OperationResultIndeterminateError`，不发 budget 信号、不调 service并 fault run。无退款。三个处理节点没有静态/conditional 出边，因此 rejected 的 Command 是唯一调度路径。

## 6. 节点、路由与 fake 固定行为

### 6.1 节点名和拓扑

节点名精确为：

```text
process_initial_turn
await_clarification
await_confirmation
```

只注册 `START -> process_initial_turn` 静态边。三个节点统一返回动态 `Command`；`add_node(..., destinations=...)` 目标精确为两个等待节点和 END。私有 `_destination_for_outcome` 按精确实例映射权威设计 §7/§9 五分支，未知 outcome 或不一致 state 抛 `RequirementSubgraphStateError()`。严禁给三个处理节点调用 `add_edge/add_conditional_edges`，也禁止默认边、字符串前缀、dict key 或 truthiness 路由。

### 6.2 测试 fake 位置

共享测试 fake 固定放在 `tests/requirement_subgraph_fakes.py`；单个用例的常量可以留在对应测试文件。该共享文件属于 T01～T05 测试白名单，但不进入生产包，也不从 `tests` 建公共 API。

固定 fake：

| Fake | 行为 |
|---|---|
| `ScriptedRequirementDomainService` | 继承 `RequirementDomainService` 并覆写两个 async public 方法；记录精确 call 顺序；按脚本返回 fresh transition/非法对象或抛指定异常；不推断输入 |
| `RecordingRequirementSubgraphSignalSink` | fresh copy 信号并按顺序保存；可配置第 N 次抛异常 |
| `ScriptedExecutionBudgetManager` | 记录 request；按脚本返回 applied/rejected/replayed、精确类型但错 operation/run/key/amount/snapshot 的 outcome 或异常；自身不替生产 coordinator 掩盖相关性错误 |
| `PerRunBudgetManagerFactory` | 每 run 恰好创建一次 manager；记录创建顺序；对象提供 manager 三个 callable surface；可返回错误对象/异常 |
| `SequenceSecretHex` | monkeypatch `coordinator.py` 的私有 `_new_secret_hex()` helper，以固定序列产生 token/nonce；不得给公共构造器增加注入参数；生产 helper 只调用 `secrets.token_hex(16)` |
| `AsyncBarrierProbe` | 以 `asyncio.Event`/barrier 精确阻塞和放行 operation；记录同时进入数；测试用 `asyncio.wait_for` 设硬超时，禁止用 `sleep` 猜调度 |
| `MinimalParentGraphHarness` | 使用真实 `StateGraph`、真实 parent saver 和模块 05 embedded builder；只记录 parent continuation，不调用 coordinator 私有方法或绕过 child graph |

不允许 fake 绕过真实 `interrupt`、真实 InMemorySaver、真实 serializer、真实 `TravelSessionState` 或真实模块 04 DTO。

`ScriptedRequirementDomainService` 只用于图路由、顺序和非法端口返回测试，不能作为“真实模块 01 路径”的证据。T03/T04 必须另行实例化生产 `app.requirements.RequirementDomainService`，只读导入 `tests/unit/requirements/fakes.py` 的 `RecordingRequirementExtractor`、`InMemoryEvidenceSink`、`FixedClock`、`strict_dto`，在模块 05 测试中跑 clarification→confirmation→纯 accept confirmed 和 extraction_failed；不得复制模块 01 service/merge/renderer 实现或修改其 fakes。

## 7. `REQ-SG-T00`——只读 Git、依赖、API 与上游合同核验

### 目标

在任何写入前证明分支、HEAD、工作区、直接依赖和 LangGraph 1.2.7 仍满足设计。

### 文件白名单

无写入文件。禁止创建临时 repo 文件、修改依赖或启动 live 服务。

### 执行

1. 记录 repo root、branch、HEAD/subject、status、staged、两设计文档 hash。
2. 核验模块 01/03/04 包根符号、service 签名、state v2、budget/event/OperationId 合同和相关测试入口。
3. 核验模块 05 生产/测试是否不存在或与当前模式匹配；保护已有用户修改。
4. 核验 `requirements.lock` 精确版本、`pyproject.toml` 约束、Dockerfile COPY/CMD/test target。
5. 从当前源码构建唯一 tag `china-travel-agent:module05-t00-<唯一后缀>` 的全新 test 镜像，记录镜像 ID，并在无网络只读容器核验权威设计 §17 的 import（含 `Send/Overwrite`）、签名、thread config、interrupt/re-entry 和 parent saver inheritance。
6. `full_rebuild` 下模块 05 尚不存在：用容器内临时 inline probe 稳定复现默认 saver 对 tuple/DTO 的失真负例，并证明真实 parent→child pause 把顶层非空 `tuple[Interrupt,...]` 交给 serializer、JsonPlus 默认恢复 list、公开 `Interrupt(value,id)` 可精确重建；据此核验六 tag wrapper 方案可行性。不得声称已验证生产 `RequirementCheckpointSerializer`；`maintenance/requalification_only` 才额外 import 并核验既有生产 serializer。
7. 微探针必须同时证明：节点存在静态/conditional 出边时再返回 `Command(goto=END)` 会双路调度，因此该结构被禁止；只有 `START` 静态边、处理节点纯 `Command` 路由时才唯一到达目标。记录两个探针的实际 trace。

正式探针容器必须使用 `--name china-travel-agent-module05-probe-<唯一后缀>`，并带 `--rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --cap-drop ALL`；不传 Secret，不运行 DeepSeek/MCP/地图/PostgreSQL。`<唯一后缀>` 每次执行都不同，禁止复用其他项目容器名。

### PASS

所有引用存在；无上游漂移；LangGraph 正反探针与设计一致；本轮新镜像 tag/ID 和容器 trace 已记录；工作区可安全继续；staged 空。`full_rebuild` 的 serializer 结论只能写“inline feasibility PASS，production serializer NOT_RUN（尚未实现）”。

### STOP

任一引用缺失、HEAD 关系不明、设计文档未批准或被改、未知 dirty、公共符号/版本/API 漂移、需要修改依赖或上游。后续 Task 全部 `NOT_RUN（T00 阻断）`。

## 8. `REQ-SG-T01`——DTO、异常与安全信号

### 前置

T00 PASS；模式为 full_rebuild 或该 Task 被 maintenance 选中。

### 允许文件

```text
app/requirement_subgraph/__init__.py
app/requirement_subgraph/models.py
app/requirement_subgraph/signals.py
app/requirement_subgraph/errors.py
tests/unit/requirement_subgraph/test_models.py
tests/unit/requirement_subgraph/test_signals.py
tests/contract/requirement_subgraph/test_public_api.py
tests/contract/requirement_subgraph/test_security_contract.py
tests/requirement_subgraph_fakes.py
```

### 实现顺序

1. 零参数异常树。
2. 受约束 token、所有 DTO/union/invariants、limits 和预算常量。
3. 五个信号、union、Protocol、NoOp。
4. 包根只导出本阶段已经存在且属于最终列表的符号。
5. DTO/JSON/mutation/repr/异常/signal/public API 定向测试。

T01 实现结束只标“实现完成，等待 T02 统一验证”，不得提前写 PASS。

### PASS

只有 T02 结束后的统一命令满足 §9 全部收集/状态约束，且字段和当前阶段导出与权威设计逐项一致、无原文/token 泄漏时，T01 才与 T02 同时 PASS。

### STOP

需要改变字段、版本、分支、异常、常量、signal 或上游 DTO；T02 及之后 `NOT_RUN`。

## 9. `REQ-SG-T02`——Coordinator、容量与并发控制

### 前置

T01 实现完成；T01+T02 在同一批次，完成 T02 后统一运行两阶段测试。

### 允许文件

T01 白名单，加：

```text
app/requirement_subgraph/coordinator.py
tests/unit/requirement_subgraph/test_coordinator.py
```

### 实现顺序

1. limits/service/factory/sink 按 Protocol/callable surface 构造并绑定单一事件循环；只对 DTO 和行为返回做 exact type + fresh validation。构造依赖或 manager factory 返回值缺少冻结 callable surface 抛 ConfigurationError；factory 自身异常在 commit 前原样传播。
2. 全局 admission lock 原子执行容量检查、manager factory 和 record 插入；record 插入是 start admission commit point，commit 前异常不占槽、不发信号。
3. 每 run operation lock 与 `ContextVar` lease；`start_operation`/`resume_operation` 的异步上下文覆盖整个 Graph `ainvoke/aget_state`，节点 helper 只在当前 active task/lease 中访问且绝不重取同一锁；不同 run 可并发。
4. `start_operation` 准入，`resume_operation` 的 token/branch/current/take-once 校验，`resolve_result` 的四结果封装、per-lease successful resolve count 恰为 1，以及 running/resume_pending/paused/terminal/faulted 状态机。
5. StartAccepted/ResumeAccepted 在 commit 后发送；sink 此时抛出的原异常向上传播，run 永久 fault、清 DTO/token/nonce、保留槽，后续 start/resume 均拒绝。sink 正常返回非 `None` 或内部 signal DTO 失真改抛 StateError并执行相同 fault 清理。构造/factory 在 commit 前失败则零占槽、零信号。
6. turn/action take-once 清空；terminal/fault 清空 DTO/token/nonce；只保留最小 tombstone。
7. token/nonce 生成、恒定时间比较、同 pause 重入复用、下一 pause 旋转与跨 run 隔离。
8. coordinator/compiled graph 私有 binding 元数据和 standalone/embedded mode；错误 graph/coordinator 配对在 service/signal 前拒绝。
9. 用 barrier/event + `asyncio.wait_for` 做并发、容量、错误 factory/sink、mutation 和零副作用测试；禁止 sleep 驱动竞争。

### 统一测试矩阵

- T01 所有模型/信号/异常/公开 API。
- 新 run 第 256 个可准入，第 257 个在副作用前拒绝；使用测试自定义小 limits 加速主要断言。
- 容量满时已准入 run 的 `resume_operation`/lease 内 `resolve_result` 仍可用；无驱逐/TTL/delete/reset。
- 同 run 竞争不可并行，不同 run 可隔离并发。
- operation scope 持锁期间节点 helper 不重入锁且无死锁；非 lease task 的私有访问失败关闭；global admission 竞争不超售。
- start/resume scope 正常退出前 resolve 恰好一次；未 drive、已 drive但未 resolve、第二次 resolve 均 StateError+fault并清敏感槽；body/resolve 原异常不被缺少 resolve覆盖；私有 get scope 不误报。
- stale/duplicate/cross-run/wrong-branch token 全部零预算、零 domain call。
- token/nonce 32 hex且互异；同 pause resume 重入 secret 调用数为 0，只有下一 pause 才旋转。token 在 repr/checkpoint/全部信号中全字符串缺席；nonce 只允许作为冻结 `OperationId.logical_step` 后缀出现并以结构化路径校验。
- turn/action 被节点 take 后清空；terminal/fault record 不保留原始 DTO 或 secret。
- manager factory 每 run 一次且 run scope匹配。
- manager factory 错误 surface→ConfigurationError、factory 异常原样传播，二者在 admission commit 前均不占槽、不发 accepted；accepted sink 第 1 次/第 2 次抛异常或返回非None均 fault 且不允许重试准入。

统一命令固定为：

```powershell
python -m pytest --collect-only -q -p no:cacheprovider `
  tests/unit/requirement_subgraph/test_models.py `
  tests/unit/requirement_subgraph/test_signals.py `
  tests/unit/requirement_subgraph/test_coordinator.py `
  tests/contract/requirement_subgraph/test_public_api.py `
  tests/contract/requirement_subgraph/test_security_contract.py
python -m pytest -q -ra -p no:cacheprovider `
  tests/unit/requirement_subgraph/test_models.py `
  tests/unit/requirement_subgraph/test_signals.py `
  tests/unit/requirement_subgraph/test_coordinator.py `
  tests/contract/requirement_subgraph/test_public_api.py `
  tests/contract/requirement_subgraph/test_security_contract.py
```

首条 collect-only 输出必须出现五个文件各自至少一个 nodeid，用该输出计算并报告逐文件收集数和总数；两条命令退出码均为 0。正式执行的 `failed/errors/skipped/xfail/xpassed/deselected` 均为 0；否则 T01、T02 同时 FAIL。

### PASS

上述固定批次命令组满足精确收集/状态约束，调用顺序精确，白名单外无修改；此时才同时把 T01、T02 标 PASS。

### STOP

无法在易失 coordinator 外保存完整 resume、需要公开 reset/delete/history、无法 per-run 串行或需要降低容量/安全合同。

## 10. `REQ-SG-T03`——Graph、interrupt/resume 与预算重放

### 前置

T01/T02 PASS。

### 允许文件

```text
app/requirement_subgraph/graph.py
app/requirement_subgraph/coordinator.py
app/requirement_subgraph/__init__.py
tests/unit/requirement_subgraph/test_graph.py
tests/contract/requirement_subgraph/test_interrupt_resume.py
tests/contract/requirement_subgraph/test_replay_budget_contract.py
tests/requirement_subgraph_fakes.py
```

仅允许为明确 graph 协作缺口小改 coordinator；不得改变已通过的公共合同。

### 实现顺序

1. fresh transition/state update helper。
2. 私有 `_destination_for_outcome` 只计算五种精确目标；仅注册 `START -> process_initial_turn` 静态边，三个处理节点均返回 `Command(update=..., goto=<exact target>)`，不注册任何处理节点静态/conditional 出边。
3. `process_initial_turn` 一次 service 调用后以 Command route。
4. 两个等待节点开头调用 `_ensure_current_pause`：首次生成 token/nonce，同一 pause 的 resume re-entry 原样复用且不调用 RNG；把规范 JSON tree 交给真实 `interrupt()`。
5. sentinel 返回后的 replay signal、take-once、预算顺序、accept/revise 分支和下一 pause 才旋转 secret。
6. applied 返回 service transition 后动态路由；rejected 以唯一 `Command(..., goto=END)` 终止；replayed fault。builder 不自建 saver并记录私有 binding/mode。
7. 加入静态+Command 双调度反例、Command-only 唯一路由正例，以及 rejected 后无二次 interrupt 的回归测试。

T03 实现结束只标“实现完成，等待 T04 统一验证”，不得提前写 PASS。

### PASS

只有 T04 结束后的统一命令满足 §11 全部收集/状态约束，且拓扑、顺序、状态连续性与真实 LangGraph interrupt/re-entry 逐项一致时，T03 才与 T04 同时 PASS。

### STOP

需要第二套 Graph state、把完整 resume 放 Command/checkpoint、interrupt 前副作用、复制 summary/confirm/merge、默认 route 或未来主图路由。

## 11. `REQ-SG-T04`——严格 checkpoint、Runtime 与组合合同

### 前置

T03 实现完成；T03+T04 在同一批次，完成 T04 后统一运行两阶段测试。

### 允许文件

T03 白名单，加：

```text
app/requirement_subgraph/checkpoint.py
app/requirement_subgraph/embedded.py
app/requirement_subgraph/runtime.py
tests/unit/requirement_subgraph/test_checkpoint.py
tests/unit/requirement_subgraph/test_runtime.py
tests/contract/requirement_subgraph/test_checkpoint_contract.py
tests/contract/requirement_subgraph/test_composition_contract.py
tests/contract/requirement_subgraph/test_public_api.py
tests/contract/requirement_subgraph/test_import_boundaries.py
tests/contract/requirement_subgraph/test_security_contract.py
```

### 实现顺序

1. 实现六个精确 tag：`requirement_subgraph.travel_session_state.2.0`、`requirement_subgraph.requirement_domain_state.1.0`、`requirement_subgraph.requirement_domain_outcome.1.0`、`requirement_subgraph.terminal_outcome.1.0`、`requirement_subgraph.artifact_ref_tuple.1.0`、`requirement_subgraph.interrupt_tuple.1.0`，以及权威设计 §10.3 的规范 bytes/codec/TypeAdapter/Interrupt public constructor。
2. interrupt tuple tag 只接受顶层非空 exact tuple，每项 exact Interrupt、32hex id和规范 JSON value tree；精确恢复 tuple并 equality。空/混合/嵌套 tuple、顶层 list[Interrupt]、非法 id/value 失败。JsonPlus delegate 只能配置 `pickle_fallback=False`；JSON allowlist 只能含 `("app","workflow_contracts","identifiers","WorkflowPhase")`、`("app","workflow_contracts","state","DeliveryStatus")`；msgpack allowlist 只能含 `("app.workflow_contracts.identifiers","WorkflowPhase")`、`("app.workflow_contracts.state","DeliveryStatus")`。delegate 前后递归 type-tree 只能接受权威设计 §10.3 闭合列出的 scalar/container/UUID/date-time/两个上游 enum/四个 LangGraph 控制类型及对应字段；对列出的每个正类型和每组明确拒绝的 safe-set 类型做正/负测试。tag 分派逐项覆盖六个自有 tag和 `null/bytes/bytearray/json/msgpack`，并证明 `pickle`/其他 tag 在 delegate 前失败；dynamic import、其他 BaseModel/constructed object、unknown version和 constructor fallback 全部失败关闭。
3. Runtime start/resume/get 只用 `thread_id=<小写 run UUID>`；进入 Graph 前校验 compiled graph 私有 binding 的 coordinator identity、`standalone` mode、`InMemorySaver` 和严格 serde。mismatched graph/coordinator、默认 saver或 embedded graph 构造 Runtime 都在 service/signal 前抛 `RequirementSubgraphConfigurationError()`。
4. Runtime 用 coordinator 三个公共 control 方法驱动 Graph、读取 checkpoint 并封装四 result；节点不重取 operation lock。unknown/terminal/faulted/loop/state/service 非法返回按权威设计 §13 精确映射。
5. durable pause：按权威设计 §4 固定 canonical state/token digest；paused result 同时绑定明文 envelope 与 durable evidence，fault/settled/next replay 清理旧 evidence。
6. embedded driver：严格验证 embedded binding 并独占 graph/coordinator pair；公开只读 `limits`；token verification 零状态变更；accepted-resume 对现存 paused record 全量匹配，或在新 coordinator 中原子重建 manager/record，再进入同一 replay/resolve 状态机。
7. Demo factory：单一严格 saver+graph+coordinator，多 run budget factory；每个 manager 精确使用 `InMemoryExecutionBudgetManager(plan=ExecutionBudgetPlan(...), limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)`，禁止复制或硬编码 limits。embedded factory 只构造 `checkpointer=None` child，不拥有 saver。
8. 父图 `checkpointer=None` 继承组合合同、默认 saver 负例、父 serializer 资格 Gate；完成包根最终 `__all__`。

### 统一测试矩阵

#### Serializer/checkpoint

- 默认 `InMemorySaver()` 对模块 05 state tuple/DTO round-trip 的失真负例必须稳定复现；不能删掉负例来通过。
- 严格 serializer 以六个完整 tag 恢复 `TravelSessionState`、Requirement state/outcome、TerminalOutcome、`tuple[ArtifactRef,...]`、非空 `tuple[Interrupt,...]` 的精确类型和值；六个 tag 的规范 bytes 与两个 allowlist 逐项正/负断言，不只测试 round-trip。
- pause、`aget_state`、history 内部读取、resume 后仍为 fresh `TravelSessionState`。
- checkpoint 全字节/递归扫描：interrupt value 是可 strict 恢复为精确 DTO 的规范 JSON tree，包含安全 message/summary 和 sentinel；不含模块 05 Pydantic interrupt/resume 对象、完整 turn/action/envelope 结构或 tag、`user_message`/`revision_text` 字段名、token/nonce、service/manager 的专用非持久 canary。模块 01 已有意写入 `RequirementDomainState` 的规范化业务值、`NormalizationRecord.raw_value`、evidence ref/digest 是权威 state，允许与原输入子串相同；禁止用这些值做全 checkpoint 字符串缺席断言。
- 保留前缀的 unknown tag/version 必须先于 delegate 失败；`null/bytes/bytearray/json/msgpack` 五个 delegate tag正例逐个成立；非前缀 unknown delegate tag与 `pickle` 在 delegate 前失败；interrupt tuple 只经第六 tag精确恢复，其他 tuple→list、DTO→dict、其他 BaseModel、其他 constructed object、重复 key、BOM、NaN 和 constructor fallback 均失败。

#### Runtime/流程

- 纯 router helper 用构造的五种内部合法 transition 穷举五目标；这只是防御性路由测试，不声称模块 01 每个 public call 都能产生五类结果。
- 生产 `RequirementDomainService` + 模块 01 只读 fakes 的调用路径遵守上游合同：首轮覆盖 clarification、confirmation、extraction_failed；纯 accept 才产生 confirmed；合法 revise 只覆盖模块 01 允许的非confirmed结果，绝不把 revise 编排为 confirmed。scripted service 的合成 transition 不能计入这条证据。
- clarification 多轮后再 confirmation/accept；domain_failed 由合法 extraction failure 路径产生；invalidated 只在模块 01 明确允许的状态转换中断言。
- wrong token、old token、cross-run、wrong branch、stale current、duplicate resume、unknown run。
- durable token digest 正/负例；tampered run/trace/state digest/nonce/interrupt/resume 在 Graph、manager 以外副作用前拒绝；accepted-resume 无需 token 明文。
- start/resume accepted 信号顺序和 preflight 零副作用；accepted sink 第 1/第 2 次抛异常均原样传播并永久 fault、清敏感槽、保留容量，不得再次 start/resume。
- graph 异常后 fault；preflight 拒绝不 fault；terminal/faulted tombstone。
- 一个 Runtime 多 run；同 run 并发；不同 run 隔离；错误 event loop。

#### Replay/budget

- clarification/revise：replay → consume applied signal → 一次 domain call。
- 精确但错 operation/run/key/amount/snapshot 的 applied/rejected/replayed outcome：StateError、fault、零 budget 信号、零 domain call；不得只看 disposition。
- accept/首轮/纯 interrupt 不消费。
- rejected：replay → exceeded signal → 零 domain call → budget result；返回值无新的 `__interrupt__`，`snapshot.next == ()`，旧等待 outcome/state 保持且只有 status 为 in_progress，后续 domain call 与信号计数不增加。
- replayed：无 budget signal、零 domain call、`OperationResultIndeterminateError`、fault。
- service 异常后不 retry、不退款、不生成第二 operation。

#### Composition/import/security

- Demo saver 是 `InMemorySaver` 且 serde 是严格 serializer。
- `MinimalParentGraphHarness` 以真实 parent saver、真实 embedded driver 走完整 `start → child pause → resolve pause/token+durable evidence → resume → child confirmed → parent continuation`，父/子 namespace可读且 parent continuation 恰好一次；另覆盖 child domain_failed 与 budget_exceeded 的父层分流。禁止调用私有方法、直接预置 record 或用 standalone Runtime 封装父图。
- M05 的 fresh-driver 模拟复用同一 parent saver，另建全新 driver/coordinator；后半段先丢弃首套 parent/driver/coordinator/service 引用，只用 paused state、durable pause 和已接受的 `RequirementResume` 继续；断言 initial service 零重复、revise 首次预算 applied 与 confirm/process 精确一次、`operation_nonce` 同时稳定映射到 `NodeReplayed` 和 budget OperationId、accepted recovery 不读取 token 明文。另用跨 fresh driver 复用的 manager 模拟 applied 后故障，再以同 nonce 收到 replayed 并在领域调用前抛 `OperationResultIndeterminateError`，证明预算副作用不重复。该测试不命名或宣称为真实进程重启；真正的子进程、新 Runtime、新 PostgreSQL Saver 实例证据属于 Module 11 Gate。
- Module 11 durable composition contract 以 factory spy/静态接线审查证明 `signal_sink` 参数被省略、默认 NoOp 生效；scope-before-marker fault injection 不产生 M05 sink 调用，durable 全局事件只在 marker/checkpoint 后由父层产生。
- standalone 与 MinimalParentGraphHarness 都覆盖 operation scope：未 drive、drive后未 resolve、同 lease第二次 resolve 为 StateError+fault；一次 resolve 正常；首次 resolve/body 原异常保持；私有 get_state scope不触发 resolve计数。
- 带 Demo 私有 saver 的 compiled graph 不作为父图节点的合格装配。
- production imports 无 `app.llm_runtime`、`app.requirement_extraction` 私有实现、模块 06+、数据库或网络 SDK。
- 包根导出与权威设计 §3 集合完全相等。

统一命令固定为：

```powershell
python -m pytest --collect-only -q -p no:cacheprovider `
  tests/unit/requirement_subgraph/test_coordinator.py `
  tests/unit/requirement_subgraph/test_graph.py `
  tests/unit/requirement_subgraph/test_checkpoint.py `
  tests/unit/requirement_subgraph/test_runtime.py `
  tests/contract/requirement_subgraph/test_interrupt_resume.py `
  tests/contract/requirement_subgraph/test_replay_budget_contract.py `
  tests/contract/requirement_subgraph/test_checkpoint_contract.py `
  tests/contract/requirement_subgraph/test_composition_contract.py `
  tests/contract/requirement_subgraph/test_public_api.py `
  tests/contract/requirement_subgraph/test_import_boundaries.py `
  tests/contract/requirement_subgraph/test_security_contract.py
python -m pytest -q -ra -p no:cacheprovider `
  tests/unit/requirement_subgraph/test_coordinator.py `
  tests/unit/requirement_subgraph/test_graph.py `
  tests/unit/requirement_subgraph/test_checkpoint.py `
  tests/unit/requirement_subgraph/test_runtime.py `
  tests/contract/requirement_subgraph/test_interrupt_resume.py `
  tests/contract/requirement_subgraph/test_replay_budget_contract.py `
  tests/contract/requirement_subgraph/test_checkpoint_contract.py `
  tests/contract/requirement_subgraph/test_composition_contract.py `
  tests/contract/requirement_subgraph/test_public_api.py `
  tests/contract/requirement_subgraph/test_import_boundaries.py `
  tests/contract/requirement_subgraph/test_security_contract.py
```

首条 collect-only 输出必须出现十一个文件各自至少一个 nodeid，用该输出计算并报告逐文件收集数和总数；两条命令退出码均为 0。正式执行的 `failed/errors/skipped/xfail/xpassed/deselected` 均为 0；否则 T03、T04 同时 FAIL。

### PASS

上述固定批次命令组满足精确收集/状态约束；默认 saver 负例和严格 serializer 正例同时成立；无敏感泄漏或白名单外修改；此时才同时把 T03、T04 标 PASS。

### STOP

serializer 需要 pickle/宽 allowlist/model_construct；父 saver 不能继承；Runtime 必须暴露 checkpoint ID/history；无法保证完整 resume 不进 checkpoint；durable resume 需要 token 明文落库、私有 record 注入或绕过 state/digest/nonce/interrupt 校验；需要改 LangGraph 版本或依赖。

## 12. `REQ-SG-T05`——覆盖率、Docker、完整回归与 MODULE-CLOSEOUT

### 前置

full_rebuild 下 T00～T04 全 PASS；maintenance 下所有适用上游 Task PASS；requalification_only 下 T00 PASS且生产树无修改。

### 允许文件和动作

允许读取全仓；允许在 `app/requirement_subgraph/**`、模块 05 测试白名单内修复明确小问题。只有在所有生产/测试 Gate 通过后，才允许同步：

```text
README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile
```

两份模块 05 设计文档只读。发现实现需要改变设计立即 STOP。

### Gate A：静态、导入与禁止项

宿主机 repo 只读执行 `git diff --check` 并记录结果；该项依赖 `.git`，不得伪称在不含 `.git` 的镜像内执行。其余静态项在最终 test 容器内验证：

```text
PYTHONPYCACHEPREFIX=/tmp/pycache python -m compileall -q app tests
包根 __all__ 精确集合
生产包私有/未来/llm_runtime/网络/数据库 import 扫描
pickle/model_construct/dynamic import/宽 allowlist 扫描
repr/异常/信号/checkpoint 结构化 canary：完整 turn/action/resume 类型/tag与 `user_message`/`revision_text` 字段名缺席；允许权威 state 中模块 01 规范化后的 value/raw_value/evidence；token 全字符串缺席，nonce 只按 OperationId 后缀结构断言
模块 06～23 stub/占位文件扫描
源码树 __pycache__/.pyc/.coverage/htmlcov 缓存扫描
skip/xfail/rerun/弱断言扫描
```

### Gate B：模块 05 定向与 coverage

容器内执行：

```powershell
python -m pytest --collect-only -q -p no:cacheprovider tests/unit/requirement_subgraph
python -m pytest -q -ra -p no:cacheprovider tests/unit/requirement_subgraph
python -m pytest --collect-only -q -p no:cacheprovider tests/contract/requirement_subgraph
python -m pytest -q -ra -p no:cacheprovider tests/contract/requirement_subgraph
python -m pytest -q -ra -p no:cacheprovider `
  tests/unit/requirement_subgraph `
  tests/contract/requirement_subgraph `
  --cov=app.requirement_subgraph `
  --cov-branch `
  --cov-report=term-missing `
  --cov-report=json:/tmp/module05-coverage.json
```

从 coverage JSON 分别计算并报告：

```text
line = 100 * covered_lines / num_statements
branch = 100 * covered_branches / num_branches
combined = 100 * (covered_lines + covered_branches) / (num_statements + num_branches)
```

三者各自必须 ≥95.00%；使用未四舍五入原值判断，报告至少保留两位小数。两条 collect-only 输出分别提供 unit/contract nodeid 与精确总数；五个命令都必须收集/执行大于 0且退出码为 0，正式执行的 `failed/errors/skipped/xfail/xpassed/deselected` 均为 0，并分别报告收集数与状态计数。分母为 0、报告缺字段或命令非零均 FAIL。不能使用 `pyproject.toml` 其他 source 或全项目高覆盖率冒充。

### Gate C：受影响上游与完整离线回归

至少执行：

```powershell
python -m pytest -q -ra -p no:cacheprovider tests/unit/requirements tests/contract/requirements
python -m pytest -q -ra -p no:cacheprovider tests/unit/requirement_extraction tests/contract/requirement_extraction
python -m pytest -q -ra -p no:cacheprovider tests/unit/workflow_contracts tests/contract/workflow_contracts
python -m pytest -q -ra -p no:cacheprovider tests/unit tests/contract
```

每条必须收集大于 0、退出码 0，`failed/errors/skipped/xfail/xpassed/deselected` 均为 0，并报告精确收集数与状态计数。不得运行 DeepSeek、MCP、百度地图、PostgreSQL 或网络 live。

### Gate D：MODULE-CLOSEOUT

所有 A～C 通过后逐项检查并只同步真实事实：

1. `README.md`：当前完成模块、可执行命令和同进程限制。
2. `docs/IMPLEMENTATION_STATUS.md`：实际分支/HEAD/日期、命令、测试数、三种 coverage、Docker 结果和已知限制。
3. `Dockerfile`：
   - `requirements.lock` 已含 LangGraph 1.2.7 等实际依赖；本 Task 不修改 lock。
   - `COPY app` 覆盖新生产包。
   - test target 的 `COPY tests`/pytest 覆盖模块 05 测试。
   - runtime 默认 CMD/import self-check 从真实 modules 01～04 更新为真实 modules 01～05。
   - 不新增网络、数据库、Secret 或未实现 runtime 入口。

即使某项无需改，也要在报告引用当前 COPY/CMD/target 的具体证据，禁止写 `N/A`。

### Gate E：同步后最终 Docker 证明

从 closeout 后最终源码分别构建唯一新 tag 的 runtime/test 镜像并记录 tag 与不可变 image ID；不得沿用 T00 或同步前镜像。每个验收容器使用唯一名称并满足：

```text
--name china-travel-agent-module05-<gate>-<唯一后缀>
--rm
--network none
--read-only
--tmpfs /tmp:rw,nosuid,nodev,size=256m
--security-opt no-new-privileges:true
--cap-drop ALL
不传 Secret
```

依次重跑：runtime 默认 CMD、Python/Pydantic/LangGraph 版本、Gate A 中除 Git 外的容器项、模块 05 unit/contract、三种 coverage、模块 01/03/04 回归、完整 unit/contract；pytest 参数和零状态要求与 Gate B/C 完全相同。容器全部退出后在宿主 repo 重跑 `git diff --check`、设计文档 hash与 status/staged。最后按 `china-travel-agent-module05-` 名称前缀证明无本轮验收容器残留，并证明无源码缓存、skip/xfail/xpassed/deselected 和设计文档变更；只检查本项目资源，不读取或清理其他项目。

### PASS

所有适用 Task、A～E、closeout 后重建与回归全部 PASS；白名单外无异常；staged 空；无 commit/push/PR/deploy。才能报告模块 05 实现完成并等待独立审查。

### STOP / NOT_RUN

任一前置失败立即把当前 Gate 写 FAIL，未进入的后续项写 `NOT_RUN（被 <精确 Gate> 阻断）`。不得先 closeout、先 live、先提交或以旧镜像结果替代。

## 13. 固定测试断言清单

实施与代码审查必须逐条勾选，不得只看 pytest 退出码：

```text
[ ] 五种模块 01 outcome 路由精确
[ ] 只有 START 静态边；三个处理节点只用 Command 动态路由，无 static/conditional 双调度
[ ] transition.state 连续消费；无 outcome 反建 state
[ ] summary 直接来自 outcome；confirm 直接交模块 01
[ ] 初始/每轮 state fresh；artifact_refs 和 terminal 字段保持
[ ] interrupt 前零预算/领域副作用
[ ] Command 只含 requirement_resume_v1
[ ] 完整 turn/action/token/nonce 不进 checkpoint
[ ] thread_id 只等于小写 run UUID；caller 无 checkpoint identity
[ ] token 恒定时间比较；token/nonce 互异且按 pause 旋转
[ ] durable pause 的 state/token 摘要使用固定 domain/canonical 算法；不含 token 明文或 command
[ ] 同一 pause resume re-entry 复用 token/nonce且零 RNG；下一 pause 才生成新 secret
[ ] NodeReplayed 在预算和 domain 之前且每次恢复一次
[ ] clarification/revise consume 1；accept/首轮/pause consume 0
[ ] applied/rejected/replayed 三种 budget 行为精确
[ ] budget/domain 异常无 retry、无退款、run faulted
[ ] stale/duplicate/cross-run/wrong-branch/current 失败且零副作用
[ ] default saver 负例与 strict serializer 正例同时存在
[ ] tuple/enum/union/ArtifactRef/TerminalOutcome 精确恢复
[ ] 第六 interrupt-tuple tag 对真实非空 tuple 精确恢复；list/空/混合/非法 id/value/其他 tuple 失败
[ ] unknown tag/version/pickle/model_construct 失败关闭
[ ] 单 Runtime 多 run；同 run 串行；不同 run 隔离
[ ] admission 不超售；operation lease 不重入锁；barrier/wait_for 证明无死锁且不使用 sleep
[ ] start/resume lease resolve 恰好一次；遗漏/重复 fault，原异常不被覆盖，get_state 豁免
[ ] 容量满保留 tombstone；existing resume/get 不受新准入影响
[ ] Demo saver ownership 与 parent saver inheritance 均验证
[ ] 全新 embedded driver/coordinator 复用同一父 Saver，从 durable pause + accepted resume 恢复，token 明文不参与
[ ] durable rehydrate 保持 operation_nonce/NodeReplayed，且不重复 initial service/预算副作用
[ ] graph/coordinator/mode/saver 错配在副作用前 ConfigurationError
[ ] 真实 parent 仅经公共 control API 完成 start→pause→resume→child result→parent continuation
[ ] 四种结果不写全局 terminal mapping
[ ] result/signal 的 run/phase/key/prefix/disposition/outcome cross-field invariants 全覆盖
[ ] 本地信号无事件 ID/时间/sequence/terminal/token/原文
[ ] accepted sink 异常在 commit 后永久 fault、清敏感槽、保留容量且不可重试
[ ] import boundary 和未来模块占位扫描通过
[ ] line/branch/combined 各 ≥95.00%
[ ] 模块 01/03/04 与完整离线回归无失败/skip/xfail
```

## 14. 版本与迁移执行规则

- 模块 05 DTO/信号只允许 1.0；state 只允许 2.0；sentinel 只允许 v1。
- 未知版本失败关闭，不实现旧版 fallback、dual read/write、deprecated alias 或迁移器。
- LangGraph/Pydantic/Python 或 serializer allowlist 变化必须重新执行 T00 和 T05 全资格。
- Demo checkpoint 无迁移；standalone 进程退出丢弃。embedded durable pause 只定义 1.0 DTO 和受控重建，不拥有数据库 migration。
- PostgreSQL、跨进程租约/fencing、token 明文持久化、accepted-command/预算事务或删除/TTL 需求立即 STOP，交后续模块设计；Module 11 只允许持久化 token digest，并在自己的事务提交后调用 accepted-resume。

## 15. 代码审查规则

独立审查必须逐条对照权威设计 §1～§24、本执行契约适用 Task 和第 13 节断言；测试通过只能作为证据之一。每条 finding 写文件、行号、对照条款、后果、最小修复和是否改变设计。

小范围明确缺陷可在对应 Task 白名单内修复并重跑从最早受影响 Task 到 T05 的全部 Gate。需要改变 public API、route、token/nonce、budget、signal、checkpoint、error、lifecycle 或下游所有权时停止并取得用户同意。

## 16. 最终报告模板

```text
任务：模块 05 需求子图实现 / 维护 / 重资格验证
执行模式：full_rebuild / maintenance / requalification_only
最终状态：COMPLETE / INCOMPLETE

Git：
- 开始分支：
- 结束分支：
- 开始 HEAD：
- 结束 HEAD：
- 工作区开始状态：
- 工作区结束状态：
- staged：空 / 精确异常

引用与设计：
- 权威设计版本/hash：
- 执行契约版本/hash：
- 模块 01/03/04 漂移：无 / 精确列出
- LangGraph API 重资格：命令、版本、结果
- 设计偏离：无 / 精确列出并附授权

修改文件：
-

白名单外文件：
- 无 / 精确列出

迁移：
- 无 / 精确列出（未经新设计不得存在）

Task：
- REQ-SG-T00：PASS / FAIL / NOT_RUN（精确原因）
- REQ-SG-T01：PASS / FAIL / NOT_RUN（精确原因）
- REQ-SG-T02：PASS / FAIL / NOT_RUN（精确原因）
- REQ-SG-T03：PASS / FAIL / NOT_RUN（精确原因）
- REQ-SG-T04：PASS / FAIL / NOT_RUN（精确原因）
- REQ-SG-T05：PASS / FAIL / NOT_RUN（精确原因）

测试：
- module05 unit：命令、收集数、结果
- module05 contract：命令、收集数、结果
- line coverage：精确百分比
- branch coverage：精确百分比
- combined coverage：精确百分比
- module01 regression：命令与结果
- module03 regression：命令与结果
- module04 regression：命令与结果
- 完整 unit/contract：命令、收集数、结果
- failed/errors/skipped/xfail/xpassed/deselected：

静态与安全：
- compileall：
- git diff --check：
- public/import boundary：
- checkpoint/token/raw-text canary：
- pickle/unknown-tag：
- 未来模块占位：
- 源码缓存：

Docker/live：
- preflight test image：唯一 tag、image ID、容器名、命令、结果
- closeout 后 runtime image：唯一 tag、image ID、容器名、默认 CMD、结果
- closeout 后 test image：唯一 tag、image ID、容器名、命令、结果
- safe container parameters：PASS / FAIL
- acceptance containers remaining：0 / 精确名称
- DeepSeek live：NOT_RUN（模块 05 不拥有）
- MCP/百度地图 live：NOT_RUN（模块 05 不拥有）
- PostgreSQL：NOT_RUN（模块 05 不拥有）
- 网络测试：NOT_RUN（模块 05 离线 Gate）

MODULE-CLOSEOUT：
- README：精确变更/无需变更的具体证据
- IMPLEMENTATION_STATUS：精确变更
- Dockerfile：COPY/CMD/test target 精确证据和变更
- 两份模块 05 设计文档保持只读：PASS / FAIL
- 同步后重建与全回归：PASS / FAIL

异常文件：
- 无 / 精确列出

外部操作：
- commit：未执行 / commit hash（仅有明确授权时）
- merge main：未执行
- push：未执行
- PR：未创建
- deploy：未执行

其他项目：
- process_simulation-platform-dev：未触碰

剩余问题：
- 无 / 精确列出

最终状态说明：
- COMPLETE 只表示本次适用 Task 与 Gate 全部通过并等待独立审查
- 不表示已 commit、push、PR、deploy 或具备 Production 崩溃恢复
```

## 17. 完成定义

- `full_rebuild`：T00～T05、closeout 后 Docker/coverage/上游/全回归全部 PASS，才可报告“模块 05 实现完成并等待独立审查”。
- `maintenance`：T00、最早受影响 Task 到 T05 全 PASS，其余精确标 `NOT_RUN（模式不适用）`，才可报告“本次维护完成并等待独立审查”。
- `requalification_only`：T00/T05 PASS，T01～T04 标 `NOT_RUN（模式不适用）`，才可报告“本次重资格验证完成”。

完成后停止。不得自动生成下一阶段提示词、stage、commit、merge、push、PR 或部署。
