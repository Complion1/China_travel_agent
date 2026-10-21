# Module 11：PostgreSQL 持久执行与跨进程恢复规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次仅接入M09的planning.model_invocation=17；通用预算SQL无需新列。阶段计数存M09新版Artifact，专用诊断记录不是恢复证据，模型节点non_recoverable不变。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 文档属性：Module 11 长期权威设计
>
> 当前边界：本文只冻结 PostgreSQL checkpoint、run 协调、执行预算、operation journal 与显式跨进程恢复；不表示实现、测试、迁移或发布已经完成。
>
> 配套执行契约：[`11_durable_execution_and_recovery_vibe_coding.md`](11_durable_execution_and_recovery_vibe_coding.md)

## 1. 文档职责、权威层级与冲突处理

本文拥有 Module 11 的公共门面、持久化结构、事务边界、租约与 fencing、checkpoint 接入、恢复判定、生命周期、安全边界和验收语义。Module 11 是基础设施组件，不拥有旅行领域状态机、完整主图、CLI 文案或最终交付状态映射。

冲突按以下顺序处理：

1. 用户最新明确决定；
2. [`00_agent_master_design.md`](00_agent_master_design.md)；
3. [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)；
4. Module 04、05、08、09、10 的冻结权威设计；
5. 已发布公共合同与合同测试；
6. 本文；
7. 配套 Vibe Coding 执行契约；
8. [`IMPLEMENTATION_STATUS.md`](VALIDATION.md) 与 `README.md`；
9. 当前实现。

外部依赖的真实公共行为属于必须核验的事实，不能由文档臆造。当前锁定的 `langgraph-checkpoint-postgres==3.1.0` 实际由 `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 四张表组成；本文按该事实冻结首版资格基线。依赖升级后如表结构、公开方法或事务行为变化，必须重新资格，不得沿用旧假设。

发现冲突时必须停止，报告精确文件、章节、符号、影响和最小变更范围。不得修改上位文档迁就实现，也不得用兼容层隐藏破坏性差异。

## 2. 初学者术语表

| 名词 | 本文中的通俗含义 |
|---|---|
| Runtime（运行时） | 应用运行期间统一管理数据库连接、Graph 执行、恢复和关闭的对象。 |
| Schema（命名空间） | PostgreSQL 中给一组表划定的独立区域，类似数据库内的专属文件夹。Module 11 固定使用 `durable_execution`。 |
| Checkpoint（检查点） | Graph 执行到某个安全位置时保存的状态快照，供后续继续执行。它不是业务大对象仓库。 |
| Artifact（产物） | 模型或地图等业务操作得到的结构化结果，由 Module 10 保存。 |
| Journal（执行日志） | 只记录某个受管操作已经获准、已有 Artifact、已确认 checkpoint 等恢复进度的追加式小账本；不保存每个 Graph 节点的调试轨迹。 |
| 租约（lease） | 数据库中有到期时间的临时执行权。一个进程崩溃后，执行权会在到期后允许安全接管，不会永久锁死。 |
| Fencing token（隔离令牌） | 每次取得租约都会递增的数据库序号。旧执行者即使晚到，也不能再写 checkpoint。 |
| 幂等 | 同一请求重复执行时不重复扣预算、不重复产生业务副作用，并返回与第一次相同的确定结果。 |
| 事务 | 一组数据库变更要么全部提交，要么全部回滚，避免只写一半。 |
| OperationId | Module 04 定义的操作身份，由 `run_id + phase + logical_step` 组成。 |
| typed error | 类型稳定、无敏感参数的异常；调用方按异常类型处理，不解析数据库原文。 |
| 失败关闭 | 无法证明安全时停止，不猜测成功，也不自动重做可能已经发生的外部调用。 |
| 显式恢复 | 只有调用方主动执行 `recover(run_id)` 才尝试接管；应用启动不会扫描并自动续跑。 |
| Canonical bytes（规范字节） | 同一合法对象总能生成完全相同的字节，用于精确比较和摘要。 |
| Digest（摘要） | 对规范字节计算的不可逆指纹；用于发现变化，不用于恢复原文。 |
| 明文保存 | 数据库中的字节没有由 Module 11 再加一层应用加密；数据库、WAL 和备份都可能保留原文。 |

## 3. 目标、成功标准与非目标

### 3.1 目标

Module 11 必须提供：

- PostgreSQL 持久化的 LangGraph checkpoint；
- 每个 run 单执行者的租约、续租、接管和 fencing；
- 与 Module 04 `ExecutionBudgetManager` 三方法协议兼容的持久预算实现；
- 以 OperationId 为键的最小恢复 journal；
- `start`、`resume`、`recover`、`get_state` 四个受控入口；
- 跨进程重启后不重置预算、不重复已有确定证据的外部副作用；
- 与 Module 10 Artifact Repository 的隔离存储和确定性对账；
- 显式迁移、严格启动检查、真实 PostgreSQL Gate 和安全错误边界。

### 3.2 成功标准

只有同时满足以下条件，Module 11 实现才有资格进入模块收尾：

1. 除 D-11-66 已明确批准并完成的 Module 05 durable pause/embedded driver 小范围合同外，Module 04、05、08、09、10 的公共合同未被改变；Module 01/07 的只读审计事实也未被修改；
2. 官方 `AsyncPostgresSaver` 是唯一 checkpoint 存储实现，Module 11 只增加受控 fencing 装饰层；
3. 同一 run 在任一时刻最多有一个有效执行者；
4. 旧 fencing token 不能提交 checkpoint；
5. interrupt、进程崩溃、连接中断和进程重启后，预算与 journal 不丢失；
6. 已有合法 Artifact 时不重复模型或地图调用；结果不确定时不自动重调；
7. terminal checkpoint、生命周期和容量计数可确定性修复；
8. 真实 PostgreSQL 下迁移、重启、跨进程竞争和故障注入全部通过；
9. `app.durable_execution` 的 line、branch、combined coverage 分别不低于 70%；
10. 审查逐条对照本文与配套执行契约，而不是只看测试是否通过。

### 3.3 明确非目标

Module 11 不实现：

- Module 10 已拥有的 Artifact 正文、evidence、hash 和 Registry；
- Module 13 才拥有的完整主图、跨模块路由、最终 terminal mapping 和预算计划业务公式；
- Module 14 的应用 API、网页交互、用户文案或内部自动重试；
- Module 12 的结果格式化；
- 自动启动扫描、后台恢复、排队系统或长期轮询；
- checkpoint 历史浏览、时间旅行、手选旧 checkpoint、公开 Saver 或裸连接；
- list、delete、clear、TTL、归档、驱逐、重置 token、强制解锁或手工 takeover API；
- 加密、密钥轮换、保留期和删除生命周期；这些属于 Module 19；
- 生产审计、指标、Trace、告警、灾备和部署；这些属于 Module 20～22；
- 跨数据库 exactly-once；Module 10 与 Module 11 之间只提供可检测重复和失败关闭；
- 长期 Semantic Memory、Prompt/策略自修改或 Procedural Memory；
- 修复 Module 09 的 `UF-09-001`。

## 4. 直接依赖、所有权与依赖方向

路线图冻结的直接依赖精确为 Module 04、05、08、09、10，不增改 `depends_on`。

| 能力 | 所有者 | Module 11 的关系 |
|---|---:|---|
| `TravelSessionState`、OperationId、Artifact/预算协议和 shared replay error | 04 | 原样复用，不复制 DTO 或改变语义 |
| 需求子图、interrupt/resume、严格 checkpoint serializer、同进程 Runtime | 05 | 复用 serializer 与嵌入合同；不改变其本地 Runtime 行为 |
| 候选研究的 Artifact、预算、replay 与取消规则 | 08 | 持久化基础设施不得改变业务判断 |
| 行程规划的 Artifact、预算、OperationPlan 与 replay | 09 | 保持既有顺序和失败关闭规则 |
| PostgreSQL Artifact Repository 与 `artifact_store` schema | 10 | 通过公开 Protocol 借用；不创建、不关闭、不跨 schema 建外键 |
| PostgreSQL checkpoint、租约、预算、journal、恢复 | 11 | 本文唯一所有者 |
| 完整根 Graph、node 名称、恢复分类、分阶段预算计划和 codec 注册 | 13 | 注入给 11；13 不接触 Saver/SQL |
| Application Service、应用 API 和前端网页 | 14 | 只调用 11 的公开门面并映射用户文案 |

Module 01 的 `RequirementTurnInput` 是经 Module 05 embedded driver 间接约束的传递合同：Module 11 生产代码不导入或 re-export Module 01，codec 的 adapter 返回 `object`，公共 driver 在任何记录、预算或领域副作用前执行 exact/fresh validation。Module 07 只是为了验证 D-11-67 中既有酒店选择外调 node 的只读审计资料；Module 11 不导入它、不修改它，也不把它写入路线图直接依赖。实施/审查可读取并回归这两项补充资料，但不得据此改变 `depends_on`。

依赖方向固定为：

```text
Module 13 root Graph / Module 14 Application
                 │
                 ▼
      app.durable_execution public facade
          │                    │
          │                    ├── ArtifactRepository protocol ──> Module 10 runtime-owned instance
          │                    └── ExecutionBudgetManager protocol
          ▼
AsyncPostgresSaver + psycopg pools + durable_execution schema
```

Module 11 不导入 Module 08、09 的私有实现，不硬编码 Module 13 业务命令，不自动组合业务 Registry。Module 10 和 Module 11 可连接同一个 PostgreSQL database、使用同一组五个连接环境变量，但必须使用不同 schema、迁移链和连接池。

## 5. 推荐包结构与公开边界

### 5.1 生产包结构

首版推荐结构如下；实施若需要新增文件，必须先证明仍在本模块白名单内且职责单一：

```text
app/durable_execution/
├── __init__.py
├── errors.py
├── models.py
├── protocols.py
├── settings.py
├── serializer.py
├── budget.py
├── repository.py
├── checkpointer.py
├── runtime.py
├── migrate.py
├── alembic.ini
└── migrations/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── m11_0001_create_durable_execution.py
```

禁止增加万能 `utils.py`，禁止把 SQL、pool、Saver 或 Repository 具体类从包根导出。

### 5.2 包根稳定公共导出

`app.durable_execution.__all__` 只允许以下类别；精确列表由合同测试冻结：

- 配置与限制：`DurableExecutionSettings`、`DurableExecutionLimits`、`DEMO_DURABLE_EXECUTION_LIMITS`、`load_durable_execution_settings`；
- 执行契约：`NodeRecoveryClass`、`DurableExecutionNodeRecoveryRule`、`DurableExecutionCommandCodecDescriptor`、`DurableExecutionContract`；
- 注入协议：`DurableExecutionCommandCodec`、`DurableExecutionBudgetPlanProvider`；
- Graph 受控能力：`DurableExecutionGraphCapabilities`、`DurableExecutionNodeCapabilities`；
- 编码与结果 DTO：`DurableExecutionEncodedCommand`、`DurableExecutionPauseEnvelope`、`DurableExecutionPaused`、`DurableExecutionTerminal`、`DurableExecutionResult`；
- 门面与工厂：`DurableExecutionRuntime`、`create_durable_execution_runtime`；
- §5.7 的异常层级。

以下对象永不公开：官方 Saver、fencing 装饰器、两个连接池、数据库连接、Repository、SQL、Alembic helper、journal 行、checkpoint pointer/history、租约 owner/token、pending command record、删除或解锁能力。

### 5.3 设置与固定限制

`DurableExecutionSettings` 是 strict、frozen、extra-forbid 的 Pydantic 模型，只包含：

| 字段 | 来源 |
|---|---|
| `host` | `POSTGRES_HOST` |
| `port` | `POSTGRES_PORT`，1..65535 |
| `database` | `POSTGRES_DB` |
| `user` | `POSTGRES_USER` |
| `password` | `POSTGRES_PASSWORD`，`SecretStr` |

不得接受完整 DSN，不得打印密码或完整连接串。Module 11 不增加 pool、租约或超时环境变量。

loader 签名与 Module 10 一致，只换成 Module 11 类型/错误：

```python
def load_durable_execution_settings(
    *, environ: Mapping[str, str] | None = None
) -> DurableExecutionSettings: ...
```

`environ=None` 才读取当前进程的五个环境变量；显式 mapping 只用于测试。缺失、空值、非十进制端口或范围错误统一为 `DurableExecutionConfigurationError()`。

`DurableExecutionLimits` 同样 strict、frozen、extra-forbid，字段顺序和 Demo 值固定为：

```python
DurableExecutionLimits(
    max_active_runs=4_096,
    max_stored_runs=16_384,
    max_managed_operations_per_run=4_096,
    max_journal_transitions_per_operation=4,
    max_journal_rows_per_run=16_384,
    max_payload_bytes=1_048_576,
    max_lineage_checkpoints=256,
    recursion_limit=256,
    lease_duration_seconds=60,
    lease_renew_interval_seconds=15,
)
```

全部字段都是 strict `int` 且 `>=1`；active<=4096、stored<=16384 且 stored>=active、operations<=4096、payload<=1 MiB、lineage<=256、journal rows=`operations×transitions`。transitions/recursion/lease/renew 首版只能是 `4/256/60/15`。`DEMO_DURABLE_EXECUTION_LIMITS` 是上述默认 fresh 不可变值。

DTO `Field`、DDL CHECK 和下表中的 4096/16384/1 MiB/256 是绝对硬上限，不是忽略实例配置的固定运行值。每个 Runtime 必须使用首次固化并逐项匹配的实例 limits：active/stored 容量分别用 `max_active_runs/max_stored_runs`；每 run 唯一受管 OperationId 和 journal 总行分别用 `max_managed_operations_per_run/max_journal_rows_per_run`；contract bytes、encoded command、pause context 及 checkpoint serializer 的每次读写均用 `min(对应字段硬上限, limits.max_payload_bytes)`；lineage 累计读取精确受 `limits.max_lineage_checkpoints` 限制且永不超过 256。任何 custom-low 值与默认值同样是强制安全门，不能只保存后继续按硬上限执行。

`DEMO_DURABLE_EXECUTION_LIMITS` 固定：

| 限制 | 值 | 语义 |
|---|---:|---|
| 未结束 active run | 4096 | `open` run，包括等待用户输入的 run |
| 累计 stored run | 16384 | `open/closed/blocked` 总数，不自动删除 |
| 每 run 受管 OperationId | 4096 | 精确 replay 不新增计数 |
| 每 OperationId journal transition | 4 | 只允许 §10 的合法转换 |
| 每 run journal 行 | 16384 | 4096 × 4 |
| 单个 checkpoint serializer payload | 1 MiB | 写入和读取都检查，不压缩或截断 |
| 恢复 lineage 最大扫描 | 256 | 只用于校验/修复，不是 run 使用次数 |
| Graph recursion limit | 256 | 每次实际 Graph 调用的防失控上限 |
| 租约时长 | 60 秒 | 使用 PostgreSQL 时间 |
| 续租周期 | 15 秒 | 一次失败立即重试一次 |

4096/16384 容量在空数据库首次初始化时固化。后续启动传入值必须逐项相同；不允许静默改变日常数据库限制。terminal 或 blocked run 释放 active 容量，但仍占 stored 容量。容量满时仍允许读取或精确重放既有 run；只拒绝创建新 run。

### 5.4 执行契约与节点恢复分类

以下四个 DTO 都是 strict、frozen、extra-forbid 的 Pydantic 模型；字符串约束是公共合同，不允许实施时自行放宽：

```python
class DurableExecutionCommandCodecDescriptor(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    codec_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    codec_version: ArtifactSchemaVersion
    codec_digest: ContentHash

class DurableExecutionNodeRecoveryRule(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    node_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    phase: WorkflowPhase
    recovery_class: NodeRecoveryClass

class DurableExecutionContract(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    contract_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
    command_codec: DurableExecutionCommandCodecDescriptor
    budgeted_phases: tuple[WorkflowPhase, ...]
    node_rules: tuple[DurableExecutionNodeRecoveryRule, ...]
    contract_digest: ContentHash

class DurableExecutionEncodedCommand(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    command_kind: Literal["initial", "resume"]
    type_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
    type_version: ArtifactSchemaVersion
    canonical_bytes: bytes = Field(repr=False, min_length=1, max_length=1_048_576)
    digest: ContentHash
    size_bytes: int = Field(ge=1, le=1_048_576)
```

`contract_digest` 是排除自身后其余字段的 canonical JSON SHA-256。该跨 Module 13 producer/Module 11 validator 的字节算法固定为：对 fresh strict `DurableExecutionContract` 执行 `model_dump(mode="json", exclude={"contract_digest"}, exclude_none=False)`；此时 enum 变为其字符串 value、tuple 变为 JSON array、嵌套模型变为 object；再以等价于 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")` 生成 bytes。不得 Unicode normalization，必须拒绝孤立 surrogate/非 JSON 值；输出无 BOM、无空白、无尾随换行。`contract_bytes` 精确保存这份排除 digest 的 bytes，`contract_size_bytes` 是其长度，`contract_digest` 精确为 `"sha256:" + sha256(contract_bytes).hexdigest()`；Module 11 不导入 Module 04 私有 canonical helper，双方按本段同一规则独立生成/复验。`codec_digest` 由 codec descriptor 所描述的 Registry/type/version/canonical 规则计算；encoded command 的 `digest/size_bytes` 必须与 `canonical_bytes` 实值一致。`budgeted_phases` 按 `WorkflowPhase` 声明顺序、唯一且首项必须是 requirements；它精确声明需要 activation 的 phase，无预算 phase 不得伪造占位 key。node rules 按 `node_name` Unicode code point 升序且名称唯一，空规则非法。

canonical qualified node name 使用 `/` 作为设计层唯一分隔符且不做转义：根 Graph 的用户注册可执行 node 名就是单段名 `node`；嵌入在父 node `parent` 下的 child node 是 `parent/child`；继续嵌套则逐段追加，如 `parent/child/grandchild`。每个原始 Graph 注册 key 必须是 `str`、非空，不含设计分隔符 `/`、LangGraph 1.2.7 namespace 分隔符 `|`、namespace task 后缀分隔符 `:` 或任何 Unicode 控制字符，且前后无空白；拼接后总长必须为 1～128。LangGraph 的 `__start__`、`__end__` 等框架虚拟节点不进入规则；承载 embedded graph 的父 node 本身和 child 的每个用户注册可执行 node 都必须各有一条规则。例：父 node `requirements` 及 Module 05 child 的 `process_initial_turn` 对应两条独立规则 `requirements` 与 `requirements/process_initial_turn`。动态 checkpoint namespace 中的 task UUID 只用于 lineage 验证，必须按锁定版本公开产生的 `|`/`:` 结构严格解析；剥离 UUID 后再按上述逐段算法唯一映射到已登记名称，禁止用 namespace 原文、UUID 或自行转义生成新规则。依赖升级若改变 `|`/`:` 语义，必须重新资格并修订设计。

`NodeRecoveryClass` 是封闭枚举：

| 值 | 恢复含义 |
|---|---|
| `replay_safe` | 纯计算或已经验证的等待节点，可从安全 checkpoint 重新进入 |
| `journal_guarded` | 可能调用模型、地图或写 Artifact，必须通过 journal/预算/Artifact 对账 |
| `non_recoverable` | 依赖未持久化的私有模型会话或既有外调节点；crash 后阻断，不从 Artifact 反向猜测或重建 Graph state |

Module 13 提供契约；Module 11 strict 验证、保存 canonical bytes/digest，并在 Graph 构建时验证规则恰好覆盖根 Graph 和注册 embedded child 的全部 canonical qualified node name。动态 checkpoint namespace 不写入契约；它必须解析回一个已登记的静态 qualified node。未列出的 node、名称漂移、多个意外并行 pending node 或恢复分类不一致一律失败关闭。

以下变化必须使用新的 `contract_id` 和 digest：拓扑、node 名称、interrupt 位置/语义、serializer、pending-command codec、恢复状态机、OperationId 规划或任何持久语义变化。不改变持久语义的补丁可保留 ID，但必须有回归和审查证据。旧 run 只按原契约恢复；Module 11 不做自动契约迁移或多版本 fallback。

### 5.5 命令 codec 协议

Module 11 不导入 Module 01、07 或 Module 13 的业务命令类型；只为已登记的 Module 05 child 复用 Module 05 公开的 `RequirementResume` DTO，initial turn 则作为 `object` 交给 driver 的既有 exact validation。`DurableExecutionCommandCodec` 由 Module 13 显式注入，使用显式类型 Registry，禁止动态 import、pickle 或“任意 JSON”回退。

协议签名固定为：

```python
class DurableExecutionCommandCodec(Protocol):
    @property
    def descriptor(self) -> DurableExecutionCommandCodecDescriptor: ...

    def encode_initial(
        self, *, run_id: UUID, trace_id: UUID, command: object
    ) -> DurableExecutionEncodedCommand: ...

    def encode_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        command: object,
    ) -> DurableExecutionEncodedCommand: ...

    def decode_initial(
        self, *, run_id: UUID, trace_id: UUID, encoded: DurableExecutionEncodedCommand
    ) -> object: ...

    def decode_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        encoded: DurableExecutionEncodedCommand,
    ) -> object: ...

    def initial_state(
        self, *, run_id: UUID, trace_id: UUID, decoded_command: object
    ) -> TravelSessionState: ...

    def requirement_turn(
        self, *, run_id: UUID, trace_id: UUID, decoded_command: object
    ) -> object: ...

    def requirement_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        decoded_command: object,
    ) -> RequirementResume: ...
```

每个方法只接受 keyword 参数。实现必须：

1. 返回稳定的 `DurableExecutionCommandCodecDescriptor`；
2. 对 initial/resume 命令执行精确类型和 strict validation；
3. 结合 `run_id`、command kind、当前 node、pause generation 校验命令是否合法；
4. 产生带 type id、schema version、canonical bytes、digest、size 的编码信封；
5. 对编码信封解码，再重新编码，结果必须 byte-for-byte 相同；
6. 从 initial command 构造唯一合法的初始 `TravelSessionState`；对已登记 Module 05 child，分别把同一份 decoded initial/resume command 确定性适配为 fresh、strict 的 Module 01 `RequirementTurnInput`/Module 05 `RequirementResume`，并再次校验 run、trace、node、generation 和分支；`requirement_turn` 的协议返回类型保持 `object` 以避免 Module 11 直接依赖 Module 01，Module 11 不读取其字段，只把它传给 driver，由 driver/coordinator 在任何记录、预算或领域副作用前 exact/fresh 验证；非 Module 05 context 禁止调用这两个适配方法；
7. resume command 不进入 Graph state，而由受控 node capability 在本次调用的私有 task context 中读取，Graph 只接收 driver 或 native path 产生的固定内部 resume sentinel；
8. 单 payload 不超过 `limits.max_payload_bytes`，其绝对硬上限为 1 MiB；失败不截断、不压缩、不宽松转换。

codec 的全部方法都是无 I/O、无时钟、无随机数的同步纯函数；两种 Module 05 适配结果不得共享调用方或 codec 内部可变引用。initial state、turn 与 resume 的适配规则都是 `codec_digest` 的输入；任一字段映射、默认值或分支规则变化必须升级 descriptor/digest 和 execution contract。

codec descriptor 及其 digest 是执行契约的一部分。Registry、type/version 或 canonical 规则变化必须升级执行契约。首版 Module 13 至少注册需求 initial/clarification/confirmation 以及酒店 select/decline；这是 Module 13 的接受范围，不是 Module 11 永久硬编码的 union。Module 11 测试使用固定私有测试 codec 和真实 Module 05 嵌入节点证明边界。

### 5.6 预算 provider、受控 Graph 能力与工厂

预算 provider 不拥有 Manager，也不写数据库。它的精确协议是：

```python
class DurableExecutionBudgetPlanProvider(Protocol):
    def plan_for_start(
        self, *, run_id: UUID
    ) -> ExecutionBudgetPlan: ...

    def plan_for_phase(
        self, *, run_id: UUID, phase: WorkflowPhase, state: TravelSessionState
    ) -> ExecutionBudgetPlan: ...
```

`plan_for_start` 不接收业务 command，必须返回非空 Plan，至少包含一个 `requirements` phase key；除此之外只允许与输入内容无关的 run-scope key，不得包含其他 phase key。`plan_for_phase` 只接受 `budgeted_phases` 中 requirements 之后的 phase，并返回首次进入该 phase 才能从其前置安全 checkpoint 确定的非空 keys。两个方法必须是无 I/O、无时钟、无随机数的同步纯函数。返回 Plan 的 `run_id` 必须匹配，key 排序按 Module 04 快照排序，跨 activation 不得出现重复 key。无预算 phase 不调用 provider，也不创建占位。阶段预算的激活与精确比较见 §10.1。

Module 13 向工厂提供一次性同步 callback。callback 接收 `DurableExecutionGraphCapabilities`，返回一份未编译的完整根 `StateGraph`。Module 11：

1. 校验执行契约与 Graph node 集；
2. 为精确 node name 生成 `DurableExecutionNodeCapabilities`；
3. 在 node capability 中提供只按 Module 04 Protocol 使用的 Artifact Repository 包装器、按当前 task context 绑定的 `ExecutionBudgetManager` 和本次已解码命令只读访问；
4. 使用私有 fencing checkpointer 和官方 Saver 编译根 Graph；
5. 强制每次调用 `durability="sync"` 与 `recursion_limit=256`。

能力 surface 固定为：

```python
class DurableExecutionNodeCapabilities(Protocol):
    @property
    def node_name(self) -> str: ...
    @property
    def artifact_repository(self) -> ArtifactRepository: ...
    @property
    def execution_budget_manager(self) -> ExecutionBudgetManager: ...
    def current_command(self) -> object: ...
    async def ensure_phase_budget(self, *, state: TravelSessionState) -> None: ...

class DurableExecutionGraphCapabilities(Protocol):
    @property
    def requirement_subgraph_limits(self) -> RequirementSubgraphCoordinatorLimits: ...
    def for_node(self, *, node_name: str) -> DurableExecutionNodeCapabilities: ...
    def execution_budget_manager_factory(
        self, *, node_name: str
    ) -> Callable[[UUID], ExecutionBudgetManager]: ...
    def register_requirement_subgraph(
        self, *, node_name: str, driver: RequirementSubgraphEmbeddedDriver
    ) -> None: ...
```

`for_node` 和 factory 只接受契约内名称。capability 与 factory 对象可在 Graph 生命周期内稳定保存；`current_command`、Repository/manager 方法及 `ensure_phase_budget` 的每次调用都从当前 task context 动态取得 run/node/lease/fence，离开一次活跃 Graph task 调用即抛 `DurableExecutionUsageError()`，不得把旧 operation 的执行权固化在对象中。factory 创建的 manager 是 run-bound，可由 Module 05 coordinator 跨该 run 的多次 pause/resume operation scope 保存；每次方法调用仍重新验证当次 task context。

`requirement_subgraph_limits` 固定返回 strict/frozen 的 `RequirementSubgraphCoordinatorLimits(max_runs=limits.max_stored_runs)`；不承诺每次属性访问产生新的对象身份。Module 13 创建 driver 时必须显式把该值传给 Module 05 factory，禁止使用 Module 05 的 256 默认值，并必须省略 `signal_sink` 参数以使用默认 `NoOpRequirementSubgraphSignalSink`；scope-before-marker 的 M05 本地信号不得成为持久/外部事件，Module 13 durable 组合测试与接线审查必须冻结这一点。每个 budgeted phase 的首个业务 node 必须把 fresh state 传给 `ensure_phase_budget`，并在它成功前禁止模型、地图、Artifact 和 budget consume；方法以 rule.phase 和本次 pre-node checkpoint 完成 §10.1 的既有 activation 比较或新 activation，精确重入幂等。requirements 只比较 `plan_for_start` 已写入的 ordinal-0 activation，禁止调用 `plan_for_phase` 或插入第二份 activation；requirements 之后的 budgeted phase 才调用 `plan_for_phase`。`register_requirement_subgraph` 只能在 callback 返回前调用一次，driver 必须来自 Module 05 公共 factory、使用 `checkpointer=None`、公开 `driver.limits` 与 `requirement_subgraph_limits` 精确相同，且其 graph 被同一根图实际嵌入；重复、未嵌入、limits 不符或 raw coordinator 均拒绝。Module 11 保存 driver 的受控引用，用它完成 durable pause 的解析、token 验证和已接受 resume 的跨进程重建，不访问 coordinator 私有状态。

node capability 把 node name 固定进预算/journal 上下文。运行期间以私有 task context 绑定 run、lease owner 和 fencing token；业务 node 无法读取这些内部值。callback 不得取得 Saver、pool、SQL 或 Repository 具体实现，也不得返回已经编译的 Graph。

### 5.7 公开结果、入口与异常

公开 DTO 与入口固定为：

```python
class DurableExecutionPauseEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    pause_token: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")] = Field(repr=False)

class DurableExecutionPaused(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["paused"] = "paused"
    state: TravelSessionState
    pause: DurableExecutionPauseEnvelope

class DurableExecutionTerminal(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["terminal"] = "terminal"
    state: TravelSessionState

DurableExecutionResult = Annotated[
    DurableExecutionPaused | DurableExecutionTerminal,
    Field(discriminator="outcome_type"),
]

class DurableExecutionRuntime:
    async def start(self, *, run_id: UUID, trace_id: UUID, command: object) -> DurableExecutionResult: ...
    async def resume(self, *, run_id: UUID, pause_token: str, command: object) -> DurableExecutionResult: ...
    async def recover(self, *, run_id: UUID) -> DurableExecutionResult: ...
    async def get_state(self, *, run_id: UUID) -> TravelSessionState: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> DurableExecutionRuntime: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool: ...

async def create_durable_execution_runtime(
    *,
    settings: DurableExecutionSettings,
    artifact_repository: ArtifactRepository,
    contract: DurableExecutionContract,
    command_codec: DurableExecutionCommandCodec,
    budget_plan_provider: DurableExecutionBudgetPlanProvider,
    graph_factory: Callable[[DurableExecutionGraphCapabilities], StateGraph],
    limits: DurableExecutionLimits = DEMO_DURABLE_EXECUTION_LIMITS,
) -> DurableExecutionRuntime: ...
```

`start`、`resume`、`recover` 只返回判别 union：

- `DurableExecutionPaused(outcome_type="paused", state, pause)`；
- `DurableExecutionTerminal(outcome_type="terminal", state)`。

所有公开 DTO 都 strict、frozen、extra-forbid，并对嵌套对象做 fresh validation/copy。`DurableExecutionPauseEnvelope` 只含 schema version 与一次性明文 `pause_token`；token 字段 `repr=False`，不得持久化明文或写日志。业务提示从经过验证的 `TravelSessionState` 及上游公共 DTO 得出，不向结果塞入 Saver checkpoint、原始 interrupt 或内部 node 详情。`get_state` 只返回 fresh、严格验证的 state，不返回 token。

工厂固定接收 settings、调用方拥有的 `ArtifactRepository`、执行契约、command codec、预算计划 provider、Graph callback 和 limits。工厂返回前完成连接、schema/revision/结构、官方 Saver 版本、容量与 Graph 合同检查；任何失败关闭自己已创建的资源，不关闭 Artifact Repository 或 Module 10 Runtime。

异常均为零参数、安全 marker，`str(error)` 只显示类名且不链出底层异常：

```text
DurableExecutionError
├── DurableExecutionConfigurationError
├── DurableExecutionDependencyError
├── DurableExecutionUsageError
├── DurableExecutionRunNotFoundError
├── DurableExecutionRunAlreadyExistsError
├── DurableExecutionRunBusyError
├── DurableExecutionRunAwaitingInputError
├── DurableExecutionResumeRejectedError
├── DurableExecutionCapacityExceededError
├── DurableExecutionIncompatibleRunError
├── DurableExecutionRunBlockedError
└── DurableExecutionStateError
```

已由 Module 04 冻结的外部结果不确定语义继续使用 `OperationResultIndeterminateError`，不复制同义异常。`asyncio.CancelledError` 原样传播；`KeyboardInterrupt`、`SystemExit` 不包装。未知异常在能证明没有外部不确定性时映射为 `DurableExecutionStateError`；若外部调用可能已经发生，则把 run 置为 blocked，并在首次发现时抛 `OperationResultIndeterminateError`。原始 psycopg、LangGraph、Pydantic 或业务异常不得越过门面。

## 6. PostgreSQL 命名空间、表与约束

### 6.1 Schema 隔离

Module 11 固定使用 `durable_execution`。Module 10 继续使用 `artifact_store`。两者可在同一 database 中，但：

- 各自拥有独立 Alembic version table 和迁移链；
- 不建立跨 schema foreign key、trigger 或 cascade；
- Module 11 的 SQL 全部 schema-qualified；
- 官方 Saver 使用固定 `search_path=durable_execution,pg_catalog`；
- 业务关联只通过经过 strict validation 的 `OperationId` 和 `ArtifactRef`；
- 任一模块故障不得自动回退到内存实现或双写。

### 6.2 七张 Module 11 业务表

首版恰好包含以下七张业务表，另有 `durable_execution.alembic_version`：

#### 6.2.1 `capacity_state`

| 列 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `singleton_id` | `smallint NOT NULL` | PK，且只能为 `1` |
| `max_active_runs` | `integer NOT NULL` | 默认 `4096`，`1..4096` |
| `max_stored_runs` | `integer NOT NULL` | 默认 `16384`，`max_active_runs..16384` |
| `max_managed_operations_per_run` | `integer NOT NULL` | 默认 `4096`，`1..4096` |
| `max_journal_transitions_per_operation` | `smallint NOT NULL` | 默认且首版必须为 `4` |
| `max_journal_rows_per_run` | `integer NOT NULL` | 默认 `16384`，等于前两项乘积 |
| `max_payload_bytes` | `integer NOT NULL` | 默认 `1048576`，`1..1048576` |
| `max_lineage_checkpoints` | `integer NOT NULL` | 默认 `256`，CHECK `max_lineage_checkpoints BETWEEN 1 AND 256` |
| `recursion_limit` | `integer NOT NULL` | 首版必须为 `256` |
| `lease_duration_seconds` | `integer NOT NULL` | 首版必须为 `60` |
| `lease_renew_interval_seconds` | `integer NOT NULL` | 首版必须为 `15` 且小于 lease duration |
| `active_run_count` | `integer NOT NULL` | 默认 `0`，`0..max_active_runs` |
| `stored_run_count` | `integer NOT NULL` | 默认 `0`，`active_run_count..max_stored_runs` |
| `created_at` | `timestamptz NOT NULL` | 默认 `clock_timestamp()` |

`m11_0001` 只创建空表，不猜运行参数。第一个 Runtime 在所有结构检查通过后，以短事务和固定 advisory 锁初始化唯一一行并固化本数据库的 limits，但“单例缺失”只有在 `runs` 与五张从表全部为空，且官方 Saver 的 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 全部为空时才可视为首次初始化；`checkpoint_migrations` 只允许存在官方 setup 版本行。任一业务行或 checkpoint 数据已存在而单例缺失，属于全局 configuration/integrity failure：不得从现有数据重算计数、插入 0、删除或修复。并发首次启动只有一个值获胜，其他 Runtime 必须读取并逐项一致，否则配置失败，绝不覆盖。每次 Runtime 启动还必须在同一固定 capacity advisory lock 和同一一致性读取事务内只读验证 `stored_run_count = count(runs)`、`active_run_count = count(runs WHERE lifecycle='open')`；即使两个计数各自在 CHECK 范围内，只要与实际 runs 不等即全局 configuration/integrity failure，不重算、不更新、不自动修复。创建 run、关闭或阻断 run 与计数变化在同一事务中完成。

#### 6.2.2 `runs`

| 列 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `run_id` | `uuid NOT NULL` | PK |
| `trace_id` | `uuid NOT NULL` | 不可变 |
| `lifecycle` | `varchar(8) NOT NULL` | `open|closed|blocked` |
| `bootstrap_state` | `varchar(24) NOT NULL` | `bootstrap_pending|bootstrap_checkpointed` |
| `contract_id` | `varchar(128) NOT NULL` | 符合 §5.4 |
| `contract_schema_version` | `varchar(16) NOT NULL` | 首版 `1.0` |
| `contract_bytes` | `bytea NOT NULL` | 1..1 MiB canonical bytes 的 DDL 硬上限；Runtime 另强制 `limits.max_payload_bytes` |
| `contract_digest` | `char(71) NOT NULL` | `sha256:` + 64 lowercase hex |
| `contract_size_bytes` | `integer NOT NULL` | 等于 `octet_length(contract_bytes)` |
| `latest_root_checkpoint_ns` | `text NULL` | 有值时只能是空字符串 root namespace |
| `latest_root_checkpoint_id` | `text NULL` | 与 namespace/digest all-or-none，1..200 字符 |
| `latest_root_checkpoint_digest` | `char(71) NULL` | safe-projection digest |
| `pause_generation` | `integer NOT NULL` | 默认 `0`，`>=0` |
| `managed_operation_count` | `integer NOT NULL` | 默认 `0`，`0..4096` |
| `journal_row_count` | `integer NOT NULL` | 默认 `0`，`0..16384` |
| `blocked_reason_code` | `varchar(64) NULL` | 只允许下文私有封闭枚举；仅 blocked 非空 |
| `created_at` | `timestamptz NOT NULL` | 默认 `clock_timestamp()` |
| `finalized_at` | `timestamptz NULL` | closed/blocked 必须非空，open 必须为空 |

契约字节是唯一契约正文，不另存 JSONB。`bootstrap_checkpointed` 和 `closed` 都必须有完整 root pointer；blocked 可以保留已有 pointer，但不公开旧 state。三个 pointer 列严格 all-or-none。

`blocked_reason_code` 的数据库 CHECK 闭集精确为：`contract_mismatch|budget_mismatch|checkpoint_invalid|checkpoint_lineage_invalid|journal_invalid|artifact_invalid|pending_command_invalid|node_classification_invalid|non_recoverable_dispatch|operation_indeterminate|recursion_limit_exceeded|state_invalid`。这些值只用于内部确定性分类，不包含异常文本、供应商内容、标识符或 Secret；新增或改名必须新 migration 并修订设计，不能把任意 `varchar(64)` 当回退。

映射也固定：contract/codec/Graph digest 不一致用 `contract_mismatch`；activation/plan/limit/consume ledger 不一致用 `budget_mismatch`；单个 checkpoint 解码、state 或 pointer 无效用 `checkpoint_invalid`；parent/namespace/fork/lineage 超过固化实例边界（硬上限 256）或验证失败用 `checkpoint_lineage_invalid`；journal FSM、字段、ordinal 或 transition 冲突用 `journal_invalid`；Artifact 损坏/Ref 冲突，或 journal 已确认 Artifact 却缺失，用 `artifact_invalid`；pending command 的 status/context/bytes/CAS 证据矛盾用 `pending_command_invalid`；未登记/重复 qualified node、分类漂移或意外并行 pending 用 `node_classification_invalid`；non-recoverable node 已 dispatch 且无前进 checkpoint 用 `non_recoverable_dispatch`；仅有 permission/调用可能已发出但结果或 Artifact 不可证明用 `operation_indeterminate`；Graph 命中 recursion 256 用 `recursion_limit_exceeded`；仅在以上更具体类别均不适用时，其他 `TravelSessionState`/lifecycle 不变量失败才用 `state_invalid`。不得把 `state_invalid` 当任意异常回退。

#### 6.2.3 `run_leases`

| 列 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `run_id` | `uuid NOT NULL` | PK；FK `runs(run_id) ON DELETE RESTRICT` |
| `lease_owner` | `uuid NULL` | 每次实际执行新生成，不公开 |
| `fencing_token` | `bigint NOT NULL` | 默认 `0`，`>=0`，取得租约时单调 `+1` |
| `expires_at` | `timestamptz NULL` | 与 owner/renewed all-or-none |
| `renewed_at` | `timestamptz NULL` | PostgreSQL 时间 |

释放租约只清 owner/expiry/renewed，不删除行，因此 token 永不倒退。所有时间比较使用 `clock_timestamp()`。

#### 6.2.4 `budget_limits`

| 列 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `run_id` | `uuid NOT NULL` | FK `runs(run_id) ON DELETE RESTRICT` |
| `scope` | `varchar(8) NOT NULL` | `run|phase` |
| `name` | `varchar(65) NOT NULL` | Module 04 `ExecutionBudgetName` |
| `phase_code` | `varchar(32) NOT NULL` | run scope 固定空字符串；phase scope 为 `WorkflowPhase.value` |
| `limit_value` | `bigint NOT NULL` | `0..2^53-1`，写入后不可变 |
| `consumed_value` | `bigint NOT NULL` | 默认 `0`，`0..limit_value` |
| `activation_ordinal` | `smallint NOT NULL` | `0..6`；start/requirements 为 0，其余六个 phase 按声明顺序为 1..6 |
| `activation_digest` | `char(71) NOT NULL` | 本次 `ExecutionBudgetPlan` canonical digest |
| `activation_checkpoint_ns` | `text NULL` | ordinal 0 为空；后续固定为空字符串 root namespace |
| `activation_checkpoint_id` | `text NULL` | ordinal 0 为空；后续 1..200 字符 |
| `activation_checkpoint_digest` | `char(71) NULL` | ordinal 0 为空；后续为 phase 前 root checkpoint safe-projection digest |
| `created_at` | `timestamptz NOT NULL` | 默认 `clock_timestamp()` |

PK 为 `(run_id, scope, name, phase_code)`；scope/phase 编码必须相符。同一 activation 的所有行必须有相同 digest/checkpoint pointer/ordinal，跨 activation 不得重复 key。整数范围沿用 Module 04 安全整数上限。

#### 6.2.5 `budget_operations`

| 列组 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `run_id` | `uuid NOT NULL` | FK `runs`；也是 OperationId.run_id |
| `operation_phase` | `varchar(32) NOT NULL` | `WorkflowPhase.value` |
| `operation_logical_step` | `varchar(64) NOT NULL` | Module 04 `LogicalStep` |
| `budget_scope` | `varchar(8) NOT NULL` | `run|phase`；与 budget key 一致 |
| `budget_name` | `varchar(65) NOT NULL` | Module 04 `ExecutionBudgetName` |
| `budget_phase_code` | `varchar(32) NOT NULL` | run scope 固定空字符串；phase scope 为 `WorkflowPhase.value` |
| `amount` | `bigint NOT NULL` | `1..2^53-1` |
| `outcome` | `varchar(8) NOT NULL` | `consumed|exceeded` |
| `snapshot_limit` | `bigint NOT NULL` | `0..2^53-1` |
| `snapshot_consumed` | `bigint NOT NULL` | `0..snapshot_limit` |
| `snapshot_remaining` | `bigint NOT NULL` | 等于 `snapshot_limit-snapshot_consumed` |
| `created_at` | `timestamptz NOT NULL` | 默认 `clock_timestamp()` |

PK 为 `(run_id, operation_phase, operation_logical_step)`；`(run_id, budget_scope, budget_name, budget_phase_code)` 是指向 `budget_limits` 的复合 FK，`ON DELETE RESTRICT`。相同 OperationId 同参数重放；任一字段不同冲突。每 run 最多 4096 行，已存在精确重放在容量满时仍可返回。

#### 6.2.6 `operation_journal`

| 列组 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `run_id` | `uuid NOT NULL` | FK `runs(run_id) ON DELETE RESTRICT`；也是 OperationId.run_id |
| `operation_phase` | `varchar(32) NOT NULL` | `WorkflowPhase.value` |
| `operation_logical_step` | `varchar(64) NOT NULL` | Module 04 `LogicalStep` |
| `transition_ordinal` | `smallint NOT NULL` | `1..4` |
| `node_name` | `varchar(128) NOT NULL` | 必须命中执行契约 |
| `journal_state` | `varchar(24) NOT NULL` | §10.3 六值之一 |
| `artifact_id` | `uuid NULL` | ArtifactRef.artifact_id |
| `artifact_type` | `varchar(65) NULL` | ArtifactRef.artifact_type |
| `artifact_schema_version` | `varchar(16) NULL` | ArtifactRef.schema_version |
| `artifact_content_hash` | `char(71) NULL` | ArtifactRef.content_hash |
| `artifact_created_at` | `timestamptz NULL` | ArtifactRef.created_at，必须为 UTC |
| `artifact_size_bytes` | `bigint NULL` | `1..2^53-1`；ArtifactRef.size_bytes |
| `checkpoint_ns` | `text NULL` | checkpoint 三列 all-or-none；root 固定空字符串 |
| `checkpoint_id` | `text NULL` | 1..200 字符 |
| `checkpoint_digest` | `char(71) NULL` | safe-projection digest |
| `reason_code` | `varchar(64) NULL` | 仅 `indeterminate` 必须非空，且只允许下文闭集 |
| `created_at` | `timestamptz NOT NULL` | 默认 `clock_timestamp()` |

PK 为 `(run_id, operation_phase, operation_logical_step, transition_ordinal)`；唯一约束 `(run_id, operation_phase, operation_logical_step, journal_state)` 阻止同一状态重复。六个 `artifact_*` 列 all-or-none，ArtifactRef.operation_id 复用本行前三列；Artifact 字段只允许 `artifact_confirmed/checkpoint_confirmed/completed` 携带。三个 `checkpoint_*` 列 all-or-none，只允许 `checkpoint_confirmed/completed` 携带。禁止 generic JSONB、错误文本、Prompt、响应或调试 payload。

#### 6.2.7 `pending_commands`

| 列组 | PostgreSQL 类型 / null | 约束与含义 |
|---|---|---|
| `run_id` | `uuid NOT NULL` | FK `runs(run_id) ON DELETE RESTRICT` |
| `pause_generation` | `integer NOT NULL` | `>=0`；与 run 组成 PK |
| `command_kind` | `varchar(8) NOT NULL` | generation 0 只能 `initial`，其余只能 `resume` |
| `node_name` | `varchar(128) NOT NULL` | generation 0 固定 `__start__`；其余命中契约 |
| `command_type_id` | `varchar(128) NULL` | 与 command type version/digest/size all-or-none；接受后永久保留 |
| `command_type_version` | `varchar(16) NULL` | `ArtifactSchemaVersion` |
| `command_bytes` | `bytea NULL` | 仅 accepted/dispatch_started 保存；DDL 硬上限 1 MiB，Runtime 另强制 `limits.max_payload_bytes` |
| `command_digest` | `char(71) NULL` | accepted/dispatch_started/checkpointed 必须非空 |
| `command_size_bytes` | `integer NULL` | 等于接受时正文长度；清正文后保留 |
| `status` | `varchar(16) NOT NULL` | `awaiting_input|accepted|dispatch_started|checkpointed|cleared` |
| `pause_token_digest` | `char(71) NULL` | generation 0 必须为空，其余必须非空 |
| `pause_context_type` | `varchar(64) NULL` | 与 context version/digest/size all-or-none；generation>0 永久保留 |
| `pause_context_version` | `varchar(16) NULL` | `ArtifactSchemaVersion` |
| `pause_context_bytes` | `bytea NULL` | generation 0 始终为空；generation>0 时 awaiting/accepted/dispatch_started 必须存在、checkpointed/cleared 必须为空；DDL 硬上限 1 MiB，Runtime 另强制 `limits.max_payload_bytes` |
| `pause_context_digest` | `char(71) NULL` | 接受时必须与 context bytes 实值一致，清正文后保留 |
| `pause_context_size_bytes` | `integer NULL` | `1..1048576`，等于接受时正文长度 |
| `pre_checkpoint_ns` | `text NULL` | generation>0 必须完整；root 固定空字符串 |
| `pre_checkpoint_id` | `text NULL` | generation>0 为 1..200 字符 |
| `pre_checkpoint_digest` | `char(71) NULL` | generation>0 的暂停点 safe-projection digest |
| `created_at` | `timestamptz NOT NULL` | 默认 `clock_timestamp()` |
| `accepted_at` | `timestamptz NULL` | accepted/dispatch_started/checkpointed 必须非空；cleared 时若曾接受则保留，否则为空 |
| `dispatch_started_at` | `timestamptz NULL` | dispatch_started/checkpointed 必须非空；cleared 时若曾派发则保留，否则为空 |
| `checkpointed_at` | `timestamptz NULL` | 仅 checkpointed 必须非空 |
| `cleared_at` | `timestamptz NULL` | 仅 cleared 必须非空 |

每个 run/pause generation 一行。状态语义固定为：

- `awaiting_input`：已有暂停点、token digest 和 durable pause context，无业务命令元数据或正文；
- `accepted`：业务命令已验证并暂存，但 Graph invoke 尚未开始；command bytes 必须非空，generation>0 的 context bytes 也必须非空；
- `dispatch_started`：Runtime 已在有效 lease/fence 下以独立短事务、CAS 从 accepted 标记“即将调用 Graph”；该提交严格先于 Graph invoke，因此 crash 后不能证明外部节点未开始；正文暂不清除，只能用于对账，不能据此重提 non-recoverable 节点；
- `checkpointed`：后续 checkpoint 已确认，command/context bytes 必须为 `NULL`，两者的 type/version/digest/size 与 pointer 保留；
- `cleared`：run 被阻断或安全清理，command/context bytes 必须为 `NULL`，已有元数据保留。

initial command 使用 generation 0、无 pause-token/context/pre-checkpoint，创建时即 accepted。后续 generation 严格递增。首版 context kind 是封闭二值：

- `requirement_subgraph.pause/1.0`：canonical 保存 `RequirementSubgraphDurablePause`，逐字段复验 run/trace/state digest、token digest、nonce 和 interrupt；
- `durable_execution.root_pause/1.0`：canonical 保存 run/trace/state digest、静态 node name、generation 和 pre-checkpoint pointer/digest，不保存原始 interrupt；由 Module 11 为非 Module 05 的根等待节点生成。

不得动态注册第三种 context。暂存绝对硬上限 1 MiB，实际实例上限为 `limits.max_payload_bytes`；已清除正文不因“恢复需要”重新生成。该表保存的是短期恢复输入，不是聊天记录。

### 6.3 官方 Saver 四张表

锁定的官方 Saver 在相同 schema 下管理：

1. `checkpoint_migrations`；
2. `checkpoints`；
3. `checkpoint_blobs`；
4. `checkpoint_writes`。

这些表没有到 `runs` 的数据库外键，因为它们由上游依赖拥有。Module 11 通过 `thread_id == str(run_id)`、root 与受控 descendant namespace、执行契约和最新 lineage 做逻辑验证。禁止复制官方 DDL、修改官方 SQL、添加业务列或把 Saver 表当公开查询接口。

### 6.4 索引与权限原则

除各表 PK/unique/FK 自动索引外，只创建四个显式索引：`runs(lifecycle)`、`run_leases(expires_at) WHERE lease_owner IS NOT NULL`、`operation_journal(run_id, operation_phase, operation_logical_step, transition_ordinal DESC)`、`pending_commands(run_id, pause_generation DESC)`。不得为未公开的 list、历史浏览或删除预建 API。

七张业务表中，`runs` 是父表；除全局单例 `capacity_state` 与父表自身外，其余五张从表都以 `run_id` 直接 FK 指向 `runs`，统一 `ON DELETE RESTRICT`；`budget_operations` 另以四列复合 FK 指向 `budget_limits`。首版没有删除路径。应用角色只能执行设计需要的 DML；日常 Runtime 不拥有 schema/DDL/DELETE/TRUNCATE 权限，`operation_journal` 还不得拥有 UPDATE 权限。`budget_limits` 只允许更新 `consumed_value`，不可更新 key、limit 或 activation 字段。具体生产角色创建属于部署阶段，但权限不足必须安全失败，不能扩大权限绕过。

## 7. 迁移、初始化与启动检查

### 7.1 独立迁移链

Module 11 初始 Alembic revision 固定为 `m11_0001`，version table 固定为 `durable_execution.alembic_version`。迁移只管理七张业务表及其约束，不复制官方 Saver migration。已发布 revision 只向前追加，不改写；`downgrade()` 明确拒绝，不删除数据。

普通 Runtime 绝不运行迁移或 Saver setup。它先检查：

- schema 和 Alembic head 精确匹配；
- 七张表、列、类型、约束、索引和 version table 精确匹配；
- 官方 Saver migration version 与四张表结构符合锁定依赖；
- `capacity_state` 表结构正确；若单例缺失，必须在同一个受锁初始化流程中证明 `runs`、五张从表以及 Saver 三张业务数据表都为空，才允许执行 §6.2.1 的一次性 limits 数据初始化；否则全局失败且不重算/修复；随后单例必须存在且逐项一致，并在固定 capacity advisory lock + 同一一致性读取事务内只读证明 stored/active 计数分别等于实际全部/open runs；范围内漂移也全局失败，不重算/修复；
- search path、UTC、事务与连接选项正确。

任一未迁移、落后、超前、多 head、缺表、多表、结构漂移或 limit 漂移都在执行 Graph 前失败。初始化单例不是 migration：它不创建/修改 schema，不调用 Saver setup，也不修复已有行。

### 7.2 显式迁移入口与全局锁

唯一迁移入口为模块命令 `python -m app.durable_execution.migrate`，不从包根导出数据库 helper。流程固定：

1. 用独立 direct PostgreSQL connection 获取 §9.1 确定性 `migration` domain 的固定 session advisory lock，最多等待 5 秒；超时抛 `DurableExecutionDependencyError`；
2. 在持锁期间引导 `durable_execution` schema；
3. 执行 Module 11 Alembic `upgrade head`；
4. 为专用 Saver setup connection 固定 `search_path=durable_execution,pg_catalog`；
5. 调用锁定依赖的官方 setup，使其执行自己的 migration；
6. 重新检查 Alembic、七张业务表、四张官方表、约束和版本；
7. 释放 advisory lock并关闭 direct connection。

该流程不是一个跨 Alembic 与官方 Saver migration 的总事务，因为官方 migration 包含自身事务/并发索引行为；它依靠全局锁和可重入前进式步骤保证失败后可再次显式执行。任何一步失败不得假装完成，Runtime 仍会拒绝启动。

## 8. 连接池与生命周期

### 8.1 两个私有 pool

一个 Runtime 拥有两个互不复用的私有异步池：

| 项目 | coordination pool | checkpoint pool |
|---|---:|---:|
| 最小/最大连接 | 1 / 4 | 1 / 1 |
| connect/open | 10 秒 | 10 秒 |
| 普通 borrow | 5 秒 | 10 秒 |
| lease 操作 borrow | 2 秒 | 不适用 |
| statement timeout | 5 秒 | 10 秒 |
| lock timeout | 2 秒 | 5 秒 |
| close timeout | 5 秒 | 5 秒 |
| autocommit | 关闭，显式事务 | 开启 |
| isolation | `READ COMMITTED` | 官方 Saver 要求 |
| prepare threshold | 默认/不预制 | `0` |
| row factory | 私有严格读取 | dict rows |

两个池都使用 UTC 和 `search_path=durable_execution,pg_catalog`。Module 11 SQL仍使用 schema-qualified 标识符；Saver 的固定 unqualified SQL 由 search path 隔离。除 fencing adapter 为一次 Saver 数据库写入持有短 advisory transaction lock 外，不得持有 coordination connection 跨模型、地图、Artifact、用户等待或任意业务 await。

### 8.2 Runtime 生命周期

Runtime 绑定创建它的 event loop，可服务多个 run。`close()` 幂等：

1. 拒绝新调用；
2. 取消本进程内活跃 Graph task并停止续租；
3. 最多等待 5 秒收束；
4. 若尚未给出 execution permission，可安全释放租约；若外部结果可能发生，则不抢先释放，等待数据库 expiry；
5. 依次关闭 checkpoint pool 和 coordination pool；
6. 不关闭调用方提供的 Artifact Repository/Module 10 Runtime。

活跃调用观察到 `CancelledError`。业务异常与关闭异常同时发生时保留业务异常；无业务异常时关闭失败映射为安全 dependency error。工厂中途失败只清理由 Module 11 已创建的资源。

## 9. 租约、fencing 与并发

### 9.1 单执行者原则

每次 `start`、`resume` 或真正续跑的 `recover` 都只为一次实际 Graph 执行取得租约。等待用户和 terminal 不持有租约；run 仍可处于 `open` 并占 active 容量。

全部 migration、capacity、per-run 和 fencing advisory lock 共用跨进程确定性 key 算法：`int.from_bytes(sha256(b"durable-execution-advisory-v1\x00" + domain.encode("ascii") + b"\x00" + payload).digest()[:8], "big", signed=True)`。domain 只允许 `migration|capacity|run`；前两者 payload 固定空 bytes，run payload 固定为 canonical lowercase-hyphen UUID ASCII。禁止 Python `hash()`、对象 identity、进程随机种子或平台相关编码。不同进程对同一 domain/payload 必须得到相同 PostgreSQL signed-bigint key，三个 domain 即使 payload 相同也必须隔离；migration 用 session lock，capacity/per-run/fencing 用 transaction lock。

取得/续租/接管/释放流程均：

1. 开启短 coordination transaction；
2. 取得由上述 `run` domain 和 canonical run UUID 导出的 per-run advisory transaction lock；
3. 锁定并读取 `runs`、`run_leases`；
4. 使用 PostgreSQL `clock_timestamp()` 判断 expiry；
5. 以 owner + fencing token 做 compare-and-set；
6. 提交后立即归还连接。

`start` 永远先按 run ID 判重：只要已有行，无论租约是否有效都抛 `DurableExecutionRunAlreadyExistsError`。同一 Runtime 已把 run 标为 `local_driver_poisoned` 时，`resume`、`recover`、`get_state` 必须先抛 `DurableExecutionStateError`，优先于该 Runtime 自持或遗留的有效租约；这是唯一的 Busy 优先级例外。其他 Runtime 或未 poisoned 的 run 上，`resume`、`recover`、`get_state` 遇有效租约立即抛 `DurableExecutionRunBusyError`。不等待、不轮询、不在连接上睡眠，也不暴露 owner、token、数据库时间或剩余秒数。调用方只能稍后重新显式调用。

### 9.2 续租与失租

租约为 60 秒，每 15 秒续租。一次瞬时连接失败只允许立即重试一次；CAS 未命中、两次失败或提交结果未知都视为失租：

- 取消当前 Graph task；
- 禁止新的模型、地图、预算、journal、checkpoint 和进度写入；
- 不立即释放不确定租约，等待数据库 expiry；
- 只有 §11.5 的受限 Artifact put 例外。

### 9.3 官方 Saver fencing 装饰器

`AsyncPostgresSaver` 没有 lease/fencing 参数，因此 Module 11 必须使用私有装饰器作为 Graph checkpointer。官方 Saver仍是唯一存储实现；装饰器不得拥有新 checkpoint 表、SQL、serializer 或 migration。

每次 `aput`/`aput_writes` 必须：

1. strict 校验 config 中 canonical `thread_id` 等于当前 run；
2. root namespace 只允许空字符串；embedded child 的非空 namespace 只允许由本次已注册 Graph 调用实际产生，并能由 `StateSnapshot.tasks` 的静态 node name + task UUID 精确解析到执行契约和已注册 driver，禁止调用方提供或自行拼接 namespace；
3. 在 coordination pool 取得同一个 per-run advisory transaction lock；
4. 用 owner、fencing token 和数据库 expiry 重新验证执行权；
5. 只在锁仍持有时委托官方 Saver 完成该次数据库写；
6. 释放锁；失败或不确定时停止执行，后续由 lineage 校验判断。

锁内只允许本次官方 Saver 数据库委托，不允许模型、地图、Module 10 或其他业务 await。租约 acquire/renew/takeover 使用同一 advisory lock，因此新执行者不可能在旧 checkpoint 写入过程中完成接管。若只能修改官方私有 SQL/API 才能满足 fencing，实施必须停止，不得 fork 或复制 Saver。

公开读取只允许 canonical run 的 root namespace，且不接受 caller supplied namespace/checkpoint ID。内部恢复先用已编译 Graph 的公开 `aget_state(..., subgraphs=True)` 取得 root pending task 和 child config，再以 Saver 的公开 `aget_tuple/alist` 验证 descendant latest：同 thread、namespace 必须等于注册 node name + task UUID、child `metadata.parents[""]` 必须等于当前 root checkpoint、`checkpoint_map` 中 root/child pointer 必须相符。累计最多读取 `limits.max_lineage_checkpoints`（绝对不超过 256）个 root+descendant checkpoint。孤立、跨 thread、未登记 path、额外分叉或引用缺失一律阻断。公开层不转发 `alist`、`adelete_thread`、namespace 或 checkpoint ID。

Saver 写 config 的 `configurable` key 闭集为 `thread_id`、`checkpoint_ns`、可选 `checkpoint_id`、可选 `checkpoint_map`。最后一项只能来自本次 Graph 内部调用，是 namespace→checkpoint ID 的有限字符串映射，并按上一段验证；Runtime 公共入口不接收 `RunnableConfig`。官方 `checkpoints.metadata` 虽是 JSONB，也不能成为任意 metadata 通道。首版写入前只接受 LangGraph 1.2.7 实测所需的三个 key：`source`（仅 `input|loop`）、`step`（安全整数，`>=-1`）和 `parents`（从受控 namespace 到 checkpoint ID 的有限映射）；`run_id`、`counters_since_delta_snapshot`、调用方 metadata、自定义 key、`update/fork` source 均拒绝。资格测试必须对 root、Module 05 embedded pause/resume 和 terminal 的实际 config/metadata 做 canary 扫描；锁定版本若需要额外 key，必须 `STOP` 并先修订设计，不能静默放宽。

## 10. 持久预算与 operation journal

### 10.1 预算计划

完整预算不能在 `start` 一次算完：Module 09 的 Direction 上限依赖需求阶段之后才存在的 confirmed `sightseeing_days`。因此采用“按阶段首次激活、激活后永久冻结”：

1. `start` 调用不依赖业务 command 的 `plan_for_start(run_id=...)`；结果必须非空、至少含一个 requirements phase key，只允许额外 run-scope key，出现空 Plan、没有 requirements key 或任何其他 phase key都在建 run/Graph/外调前拒绝；合法 Plan 在创建 run 的同一事务写入 activation ordinal 0。
2. 根 Graph 进入 `contract.budgeted_phases` 中某 phase 的首个业务 node 后，该 node 的第一项动作必须调用 `ensure_phase_budget`。若 phase 是 `requirements`，它只使用进入 node 前已经 sync 提交的 checkpoint strict 读取 state，并重算、比较 ordinal-0 的 `plan_for_start` activation；禁止调用 `plan_for_phase`，也禁止插入新 activation。若是 requirements 之后的 budgeted phase，才以该 pre-node checkpoint state 调用 `plan_for_phase`，确认返回 Plan 非空且只含该 phase 的新 key。未列入的无预算 phase 直接跳过 activation。
3. requirements 之后的新 activation 在有效 lease/fence 下以短 coordination transaction 插入；提交成功后才允许该 phase 第一个业务 node、模型、地图或 Artifact 写入。requirements 的 ordinal-0 activation 比较成功后才允许其首节点继续，同样不得插入第二份 activation。
4. requirements/ordinal-0 activation 一旦存在任一 row，就直接重调 `plan_for_start(run_id=...)`，完整比较 key、limit、activation ordinal/digest，并要求三列 checkpoint pointer 全为 `NULL`。requirements 之后的某 phase 一旦存在任一 row，才必须通过它保存的 root checkpoint pointer 读取当时的安全 state，调用 `plan_for_phase` 后完整逐行比较 key、limit、activation ordinal/digest/pointer；两类都不得补半份、改额度、删除或以“最新 state”重写旧计划。
5. `resume/recover` 直接重算不依赖 command 的 requirements/ordinal-0 start activation；其余每个已激活 phase 使用各自绑定的安全 checkpoint state 重算该 activation。只比较已激活计划，尚未进入的 phase 不预生成、不占位。后续 activation pointer 是受控内部定点读取，不是旧 checkpoint fallback 或公开 time travel。
6. 当前 state 显示已经进入某个已声明 budgeted phase，但该 phase activation 缺失，说明越过预算安全门，立即阻断；未声明的无预算 phase 没有 activation 是合法状态，未来 budgeted phase 尚未激活也合法。

这不改变 Module 04：每次 provider 返回的仍是合法、不可变 `ExecutionBudgetPlan`，持久 manager 的三个公共方法也不变。CLI 不能传入或修改计划；业务预算公式、activation 边界或 provider 规则变化都属于执行契约变化，必须新 run/新 contract ID。

持久预算 manager 精确实现 Module 04 的 `consume`、`get_snapshot`、`get_all_snapshots`。排序、typed outcome、错误和 run/phase scope 保持不变。

### 10.2 consume 原子算法

`consume(request)` 在一个短事务中：

1. strict 复制 request，验证 run/node context、key、scope 与 lease/fencing；
2. 查找 `budget_operations`；精确存在则返回 replayed，冲突则失败；
3. 检查每 run 4096 operation capacity；
4. 锁定对应 `budget_limits`；
5. 可消费时递增 consumed、插入 consumed operation，并同时向 journal 追加 `execution_permitted`；
6. 超限时不递增，插入 exceeded operation，并同时追加 `budget_rejected`；
7. 提交并返回 fresh Module 04 outcome。

预算记录与对应 journal 状态必须同事务，不退款、不 reserve/commit 两段化。精确 replay 在容量满时仍成功。

### 10.3 journal 状态与合法路径

journal 只允许六种状态：

```text
execution_permitted
budget_rejected
artifact_confirmed
checkpoint_confirmed
completed
indeterminate
```

完整 FSM 固定如下；表外任何边、跳过前置状态、终态后追加或第 5 行都非法：

| 当前最后状态 | 唯一允许的下一状态 | 适用条件 |
|---|---|---|
| 无 | `execution_permitted` | 首次预算足额并原子消费 |
| 无 | `budget_rejected` | 首次预算不足并原子记录拒绝 |
| `execution_permitted` | `artifact_confirmed` | strict Module 10 put/lookup 已得到同 OperationId 的合法 Artifact |
| `execution_permitted` | `checkpoint_confirmed` | 该受管操作按契约不产生 Artifact，或它属于既有 `non_recoverable` node 且合法 checkpoint 已在同一连续执行/可证明 crash-gap 中反映结果；后一种即使同 OperationId 存在 Artifact 也不记录 Artifact 字段 |
| `execution_permitted` | `indeterminate` | 外部调用可能发出但无可证明结果；reason code 必填 |
| `artifact_confirmed` | `checkpoint_confirmed` | checkpoint 已引用同一 Artifact/结果 |
| `budget_rejected` | `checkpoint_confirmed` | checkpoint 已反映同一拒绝结果 |
| `checkpoint_confirmed` | `completed` | latest 已越过该 node，pointer 必须与前一行相同 |
| `completed` 或 `indeterminate` | 无 | 终态 |

因此全部完整路径只有：

```text
execution_permitted → artifact_confirmed → checkpoint_confirmed → completed
execution_permitted → checkpoint_confirmed → completed
budget_rejected → checkpoint_confirmed → completed
execution_permitted → indeterminate
```

`artifact_confirmed` 必须携带 Artifact 字段且不携带 checkpoint/reason；`checkpoint_confirmed/completed` 必须携带 checkpoint 字段，并仅在前序 journal 状态是 `artifact_confirmed` 时携带完全相同的 Artifact；旁侧存在但从未写入 journal 的 non-recoverable Artifact 不算“前序有 Artifact”。`execution_permitted/budget_rejected` 不携带三类可选字段；`indeterminate` 只携带 bounded reason。`reason_code` 的数据库 CHECK 闭集精确为 `external_result_unknown|cancelled_after_permission|lease_lost_after_permission|artifact_missing_after_permission`；它不能保存错误文本、供应商响应、ID 或 Secret。每次状态变化追加一行，保留完整恢复历史；禁止 UPDATE/DELETE journal。相同 transition 的精确重试不新增行并返回原结果；ordinal、state、node、OperationId、ArtifactRef 或 checkpoint pointer 冲突会阻断 run。journal 不是每个 Graph node 的执行日志，不保存输入、输出、错误文本或审计事件；未参与外部副作用/预算/恢复的普通节点不写 journal。

对既有 `non_recoverable` budgeted service，正常成功的 journal 固定走不携带 Artifact 的 `execution_permitted → checkpoint_confirmed → completed`：`checkpoint_confirmed` 只证明该 OperationId 的结果已经进入同一 run 的合法 checkpoint，不把旁侧 Artifact 升格为可恢复证据，也不允许据此在 checkpoint 缺失时继续。若 crash 发生在 permission 后、合法 checkpoint 确认前，无论 Artifact 是否存在都按 §11.2 阻断；若 checkpoint 已写但 journal 尚未推进，只能在 §12.2 的唯一连续 lineage 证明成立后补 `checkpoint_confirmed`。因此正常路径闭合，crash 路径仍失败关闭。

indeterminate reason 的判定优先级固定为：观察到 lease/fence 丢失用 `lease_lost_after_permission`；否则观察到调用方取消用 `cancelled_after_permission`；否则已知外调返回或被上游声明完成、但原 OperationId Artifact 缺失用 `artifact_missing_after_permission`；其余“外调已可能发出但结果不可证明”用 `external_result_unknown`。

### 10.4 commit 结果未知

对 Module 11 自有纯数据库事务，commit 结果未知时固定：

1. 丢弃原连接；
2. 用 fresh connection 读取权威行；
3. 精确目标已存在则视为成功；
4. 目标不存在且前置条件仍完全相同，只重试一次同一事务；
5. 重试后再做一次最终读取；
6. 冲突、部分状态或仍无法确定则阻断，不做第三次写入。

此规则不得触发 Graph、模型、地图或 Artifact 外部调用重放。预算 consume 可以在确认后返回原 applied/rejected；后续相同请求只返回 replayed。

## 11. Artifact 协作与外部副作用

### 11.1 隔离与受控包装

工厂借用调用方提供的 Module 04 `ArtifactRepository`。Module 11 不构造、不迁移、不关闭 Module 10 Runtime。Graph 只取得 node-bound 私有包装器，包装器仍按 `ArtifactRepository` 使用：

- `lookup/get` strict 验证 Artifact 与 run/OperationId；
- 对 `journal_guarded` node，`put` 前必须已有同 OperationId 的 `execution_permitted`；委托 Module 10 成功并验证 Ref 后，才在独立 Module 11 事务追加 `artifact_confirmed`；
- 对现有 `non_recoverable` service node，包装器仍 strict 委托/验证，但无论同 OperationId 是否曾因 budget consume 写入 `execution_permitted`，都不追加 `artifact_confirmed`，因为整个 node 没有接入完整的调用前许可/恢复接缝。已有 budget journal 的 OperationId 仍是受管 operation并计入上限； uninterrupted 正常成功或可证明 checkpoint crash-gap 只按 §10.3 追加不携带 Artifact 的 `checkpoint_confirmed/completed`。action、judgment、state、terminal 等完全没有预算 journal 的 OperationId 不得虚构 permission，且不计入受管上限；
- `replay_safe` node 不得写 Artifact；`journal_guarded` 缺 permission、已有 budget rejection 或字段冲突时在调用 Module 10 前拒绝；
- 两个 schema 之间没有总事务和外键；
- fake Repository 可用于 unit/contract，真实 PostgreSQL Gate 必须覆盖 Module 10 实现。

因此“每次成功 put 后追加 journal”只适用于 `journal_guarded` node 中已经由 Module 11 journal 管理的 OperationId，不适用于 `non_recoverable` node 或上游全部 Artifact。Module 11 不修改 Module 07/08/09 的 OperationPlan 或预算调用顺序。

### 11.2 恢复对账矩阵

恢复必须先应用 §11.3 的 node class，再应用下表；Artifact/journal 组合不得把 `non_recoverable` 升格为 `journal_guarded`：

| node class | Artifact | journal/预算证据 | 固定行为 |
|---|---|---|---|
| `replay_safe` | 不存在 | 不存在 | 可在有效租约下继续纯计算/路由 |
| `replay_safe` | 任意存在或损坏 | 任意，或存在任何副作用 journal | 分类/证据冲突；阻断 |
| `journal_guarded` | 不存在 | journal-managed operation 全部不存在、尚未许可 | 可在有效租约下首次执行 |
| `journal_guarded` | 合法存在 | `execution_permitted`，但尚无 `artifact_confirmed` | 验证后补记 `artifact_confirmed`，复用 Artifact，不再外调 |
| `journal_guarded` | 合法存在 | 合法完整/后续 journal | 复用，不再外调 |
| `journal_guarded` | 存在 | 已声明 journal-managed，但 journal 完全缺失或与 managed operation 冲突 | 数据损坏；阻断，不伪造 permission |
| `journal_guarded` | 不存在 | `execution_permitted`、可能已发出、结果 claimed 或预算 consumed | 结果不确定；阻断，禁止再次外调 |
| `journal_guarded` | 不存在 | journal 已 `completed` 或声明 Artifact 已确认 | 结果不确定；阻断 |
| `journal_guarded` | 不存在 | `budget_rejected` | 重放拒绝，不执行外调 |
| `journal_guarded` | 损坏/Ref 不一致 | 任意 | integrity failure；阻断 |
| `non_recoverable` 且无合法 post-node checkpoint | 任意合法 Artifact（包括同 OperationId 已有 `execution_permitted`、`artifact_confirmed` 或完整 journal） | 任意 | 证据只说明外调可能发生；不补 Artifact journal、不反向构造 state、不重放 node，阻断 |
| `non_recoverable` 且无合法 post-node checkpoint | 不存在或损坏 | 任意或完全无证据 | 无法证明外调未发生或 integrity failure；阻断 |

`non_recoverable` 永远不得从该 node 开头重放。唯一安全越过方式是独立存在且通过 root+descendant lineage 验证、并能证明已经越过该 node 的 post-node checkpoint：非 terminal 时只从 checkpoint 后续位置继续，并可按 §10.3 修复无 Artifact 字段的 checkpoint journal；terminal 时只按 §12.7 修复关闭。两者都不是从 Artifact 或 journal 重建 state。

Artifact 是业务结果，journal 是恢复进度，预算是执行许可；三者不得互相冒充。Module 11 不承诺模型/地图与 PostgreSQL 的跨系统 exactly-once。

### 11.3 node 恢复分类

恢复读取最新 checkpoint 的 pending node：

- `replay_safe`：纯计算/纯路由节点可直接重新进入；已到等待用户且 pending status 仍为 `awaiting_input` 时走 §12.4；
- `journal_guarded`：只适用于整个外部操作都通过本模块 node capability 完成“预算许可 → 外调 → strict Artifact put”的节点；恢复时只允许“合法 Artifact + 可修复 journal”的 replay 路径继续。“已许可但无 Artifact”必须 indeterminate，不能再次外调；
- `non_recoverable`：节点可能调用模型、地图或其他外部能力，但当前冻结模块没有在调用前接入上述完整许可/Artifact 协议；crash 后只要没有可证明已越过该 node 的合法 post-node checkpoint就一律阻断，绝不从节点开头自动重放，也不从 Artifact 反向构造 `TravelSessionState`。若已有该 post-node checkpoint，非 terminal 只从其后续位置继续并修复 checkpoint journal；terminal 走 §12.7 的关闭修复；二者都不重放该 node。

首版生产分类的最低要求固定为：Module 05 实际 child node `process_initial_turn`、`await_clarification`、`await_confirmation` 都会调用 Module 01/03 的外部模型路径（后两者在 interrupt 恢复后调用），因此在其注册父段下形成的三个 qualified rule（例如 `requirements/process_initial_turn`、`requirements/await_clarification`、`requirements/await_confirmation`）都必须是 `non_recoverable`；只有父图纯 router 等确实无外调的节点可为 `replay_safe`。合法 waiting checkpoint 且 pending status 仍为 `awaiting_input` 时由 §12.4 直接返回 AwaitingInput，不靠重放 await node。Module 07 酒店选择、Module 08 候选研究的 action、judgment、地图 wave 所在 Graph node，以及 Module 09 的模型 action、geocode/matrix/directions/supplement 所在 Graph node，若 Module 13 把整个服务调用放在一个 node 中，整个 node 必须是 `non_recoverable`。其他包含外部调用的 node 同理。只有未来某个精确拆分的 node 已满足上一条完整 capability 协议，才可声明 `journal_guarded`；不得因此修改 Module 04/07/08/09。

这意味着“没有 journal/Artifact 证据”只证明数据库里没有证据，不能证明外部调用从未发出。任何未列出 node、错误分类、多个意外并行 node、无法唯一关联的受管 OperationId，或试图把上述 service node 乐观标成 replay-safe/journal-guarded，均阻断。

### 11.4 取消

在 execution permission 前且能够证明尚未进入任何外部调用时，取消可以安全释放租约。permission 后、`non_recoverable` node 已开始或外部调用是否发出不明时：停止续租、禁止后续新动作并让租约自然过期。`CancelledError` 原样传播，不把取消改写为普通 Failed，也不通过立即释放让另一个进程过早接管。

### 11.5 失租后的唯一 Artifact 例外

旧执行者失租后，只有在失租信号到达前已经进入“原外部结果已返回、同一原 OperationId 的 Module 10 `put` 已开始”这一受控 critical section，才允许该 put 收束；不得在失租后新进入 put。Runtime 以独立 task 跟踪并 shield 这一段：普通失租只等 Module 10 自身已冻结的调用超时；若同时显式 `close()`，它只能使用 §8.2 的共同 5 秒收束期限，期限到达即取消仍未结束的 put，不在后台遗留 task。put 的提交结果因此不确定时，由后续 `recover` 查询原 OperationId 对账，绝不补发第二次 put。此时：

- 不允许新的外部调用；
- 不允许预算、journal、checkpoint、状态或 terminal 写入；
- 必须使用原 OperationId；
- 只等待这一笔已开始的 put；Graph 主 task、续租和所有 sibling 工作立即取消；
- 冲突、取消或失败进入安全错误路径，不启动补偿 put；
- 后续 `recover` 通过 Artifact + 既有许可 journal 对账并补记进度。

## 12. checkpoint、pending command 与公共操作

### 12.1 严格 serializer

Module 11 的私有 bounded serializer 委托 Module 05 `RequirementCheckpointSerializer`，不启用 pickle fallback。每次 `dumps_typed` 和 `loads_typed` 都检查单 payload 不超过实例 `limits.max_payload_bytes`（绝对硬上限 1 MiB），并执行 delegate round-trip、类型和值相等校验。超限、未知 tag、宽松转换、非法对象或读取时超限均映射为 state error 并阻断；不截断、不压缩、不降级。

执行契约必须证明完整根 Graph 的 checkpoint 值都在 serializer 白名单内。业务命令通过 pending codec 存储并转换为 Graph input，不要求把任意业务 DTO加入 checkpoint serializer。

### 12.2 checkpoint pointer 与 lineage

官方 Saver保留全部版本；Module 11 对外只恢复/返回 latest，不提供历史 API。root namespace 固定为空字符串；Module 05 等 embedded child 会产生非空 descendant namespace，这些不是另一条 run，也不能被 root-only 规则误拒绝。每次 Graph 返回后：

1. 读取 root namespace 最新 checkpoint；
2. strict 恢复 `TravelSessionState`，验证 run ID、执行契约和 Graph 状态；
3. 用 `StateSnapshot.tasks`、child config/checkpoint_map 和 child metadata parent 验证全部 pending descendant checkpoint，再计算只含 canonical state、pending node/interrupt 结构、root/descendant pointer 和 parent 关系的 safe projection digest；
4. 在有效 lease/fence 下保存 pointer，并向相关 operation journal 追加 `checkpoint_confirmed`；
5. 再清理已经被该 checkpoint 反映的 pending plaintext；
6. 只有确认完成后才向调用方返回。

以上第 4～5 步描述持有执行 lease 的正常 Graph 返回路径。§12.5 已穷举的 repair-only crash gap 是例外：它只能在确认无有效租约并持有 per-run advisory lock 的同一短事务内修复 pointer、pending、`checkpoint_confirmed` journal、terminal close/capacity，不取得执行 lease；不得借此写预算 permission、Artifact、Saver 或任何未在该 repair 分支列明的状态。

恢复可沿 root parent lineage 和 root 实际引用的 descendant 闭包累计读取最多 `limits.max_lineage_checkpoints` 个 checkpoint（绝对不超过 256），用于证明 latest 与已确认 pointer 的唯一连续关系或修复“checkpoint 已写、确认行未写”的 crash gap。不得枚举无关 namespace，也不得把旧 checkpoint 作为 fallback 继续业务。latest/descendant 损坏、缺失、跨 thread、未登记 namespace、出现 fork/divergence、超过实例界限仍不能证明关系时阻断。

### 12.3 `start`

顺序固定：

1. 只对 `run_id` 本身做 exact UUID/fresh 安全解析；不得调用 codec、provider、Graph callback 或解析业务 command；
2. 立即按该 run ID 检查重复；任一已有 run 都优先抛 `DurableExecutionRunAlreadyExistsError`，即使 trace/command/codec/contract 后续本会非法；
3. 仅确认 run 不存在后，strict 校验 trace/command、codec descriptor和执行契约；此时尚未声称已有 graph input；
4. 对 generation 0 encoded command 做 encode→decode→re-encode；由 codec 从同一 decoded command 生成 fresh initial state 与 turn object，strict 校验 initial state，随后由第 6 步 driver exact preflight 验证 turn 并产生真正的 `graph_input`；独立调用 `plan_for_start(run_id=...)` 生成并 strict 复制非空 start activation；
5. 一个 coordination transaction 锁定 capacity，再次确认 run 仍不存在，检查 active/stored 容量，插入 run、lease、start budget limits 和 generation 0 `accepted` initial pending command，并将 `bootstrap_state=bootstrap_pending`；并发插入冲突仍映射为 AlreadyExists，不泄漏 SQL；
6. 进入已登记 Module 05 driver 的 `async with driver.start_operation(state=initial_state, turn=requirement_turn) as graph_input`；`__aenter__` 完成后、任何 Graph 调用前，在有效 lease/fence 下用独立短事务将 generation 0 从 `accepted` CAS 为 `dispatch_started`；
7. 只有 marker 提交成功才以 scope yield 的 `graph_input`、`thread_id=str(run_id)`、root namespace、`durability="sync"`、recursion 256 调用完整父 Graph；父 Graph invoke、父层对 child terminal 的 `resolve_result`，以及 Graph 返回 child pause后 Module 11 对该 pause 的 `resolve_result` 都必须发生在同一个 operation scope 内，任何路径都不得先退出 scope 再解析 child 结果；
8. 确认首个 checkpoint 后改为 `bootstrap_checkpointed` 并清 initial plaintext；
9. 在 scope 内取得并持久化所需的 paused/terminal 结果后退出 scope，再按公开结果规则结算。scope 进入失败或 marker CAS 失败均不得调用 Graph；marker 已提交后的 scope/Graph 不确定失败按 dispatch_started 规则处理，不伪装成 accepted。

崩溃恢复规则：

- generation 0 仍为 `accepted`，且 Module 11 可达的 root/descendant checkpoint、run/pending pointer、journal、预算 operation 以及 journal 中保存的 ArtifactRef 证据全部不存在：结合 accepted marker，这证明受控 Graph invoke 尚未开始；租约过期后解码 initial command、重算并精确比较 start activation，再精确重提；
- generation 0 已是 `dispatch_started` 且没有后续 checkpoint：Graph/外调是否开始不可证明，阻断，不重提 initial；
- 已有合法首 checkpoint：只修复 bootstrap marker/清 pending，不重提 initial；
- 没有 checkpoint但已有上述任一 Module 11 可达受管证据，或无法证明外部 node 尚未开始：阻断。Module 11 不拥有 M10 的 run/list/scan API，不得访问其 SQL 或枚举“任意 Artifact”；M10 中没有被 M11 journal/Ref 引用的不可达行不作为 accepted preflight 的可枚举前置，也不能被拿来证明成功。

M05 operation scope 一旦成功进入，若随后 `dispatch_started` CAS 明确失败、commit-unknown 最终不能确认，或 scope 进入过程已经创建并 fault 了本地 record，M05 的永久 fault 合同意味着同一 driver/coordinator 不能再次使用该 run。Module 11 必须把该 run 加入本 Runtime 的私有 `local_driver_poisoned` 集合，禁止同一 Runtime 对它再次进入 M05 scope；后续同 Runtime 的 `resume/recover/get_state` 抛零参数 `DurableExecutionStateError`，`start` 仍按既有 run 优先返回 AlreadyExists，`close` 仍可调用，其他 run 不受影响。若 fresh DB read 证明 row 仍为 `accepted`、没有 Graph/checkpoint/受管副作用证据且租约可安全释放，则只释放租约，调用方必须关闭该 Runtime 并创建新 Runtime，由新 driver 按 accepted 分支恢复；若 row 已是 `dispatch_started`、冲突或已有不一致证据，则按相应规则阻断；数据库不可达或结果未知时停止续租、不调用 Graph，让租约过期后由新 Runtime 对账。禁止为此给 M05 增加 reset 或替换 driver 的后门。

真实 PostgreSQL fault injection 必须证明 LangGraph 会在第一个业务 node 前留下可识别的 input checkpoint；若锁定版本不能证明，实施停止并重新设计，不能猜测安全。

### 12.4 暂停、token 与 `resume`

Graph 到合法等待用户 checkpoint 后，Module 11 在 lease 下：

1. 确认 latest checkpoint；
2. 增加 pause generation；
3. 若等待点来自已注册 Module 05 driver，调用 `resolve_result` strict 取得 `RequirementSubgraphPaused`，沿用它的 token 和 durable pause；其他已登记根等待节点由 Module 11 调用 `secrets.token_hex(16)` 生成 token 和 native root-pause context；
4. 两类 token 都必须是 32 位小写 hex。Module 05 类型沿用其 §4.1 digest；native 类型固定为 `sha256(b"durable-execution-pause-token-v1\x00" + str(run_id).encode("ascii") + b"\x00" + token.encode("ascii"))`，外部字符串仍加 `sha256:` 前缀；
5. canonical 编码并保存 context、token digest 和 pre-checkpoint pointer，绝不保存 token 明文；
6. 插入 `awaiting_input` pending row；
7. 释放租约；
8. 把该明文 token 包入 `DurableExecutionPauseEnvelope`，只在本次调用返回一次。

waiting checkpoint 与第 6 步 `awaiting_input` pending/context/token-digest 持久化之间存在不可合并的 crash window：若 checkpoint 已 sync 提交，但 matching `awaiting_input` 行（同 generation/node/branch/pre-checkpoint、合法 context 与 token digest）不存在或不完整，新 Runtime 无法从 checkpoint 重建 Module 05 durable pause，也不得为 Module 05 或 native pause 重新生成 token/context。`recover` 必须以 `pending_command_invalid` 原子阻断；可以确认原 dispatch command 已越过 node，但这只用于保留/清理其证据，不能把 waiting gap 变成 repair-only 可继续路径。不得调用 Graph、driver 或 token generator。

`resume` 先检查本 Runtime 的 `local_driver_poisoned`；命中即抛 `DurableExecutionStateError`，不受遗留租约影响，也不访问 driver。否则必须在任何 Graph/业务副作用前开启短事务并锁定 pending row，依次校验：run 为 open、无有效他人租约、generation/node/branch/pre-checkpoint/最新 checkpoint 完全一致；Module 05 类型用已注册 driver 的 `verify_pause_token`，native 类型按上述 domain 计算后用 `hmac.compare_digest`；codec 接受命令；执行契约和全部已激活预算逐项匹配。通过后在同一事务取得新租约、以 `WHERE status='awaiting_input'` 一次性改成 `accepted` 并保存 command canonical bytes。事务提交后先 strict decode；Module 05 类型由 codec 的 `requirement_resume(...)` 产生 fresh `RequirementResume`，再进入 `async with driver.resume_accepted_operation(state=state, durable_pause=context, resume=requirement_resume) as graph_input`；native 类型进入对应受控 operation scope 并取得固定内部 resume sentinel。scope preflight 成功后才以独立短事务 CAS 为 `dispatch_started`，只有 marker 提交成功才以 scope yield 的 `graph_input` 调用完整父 Graph。两类 scope 都覆盖父 Graph invoke、父 router 与 Module 11 对 child 结果的全部 `resolve_result`，并把 decoded command 放入本次 task context；不得先退出 scope 再解析结果。验证、scope preflight 或 CAS 失败都无 Graph、预算、Artifact 或领域外调；durable 组合强制 M05 NoOp sink，所以 scope 进入本身也不投递持久/外部信号。

Module 05 resume scope 已进入后 marker 失败的本地 poisoning、fresh read、租约释放/过期和“只能由新 Runtime 的新 driver 继续 accepted 命令”规则与 §12.3 完全相同；不能在同一 Runtime 里重建或替换 driver。native scope 不产生 M05 fault tombstone，但 marker 未确认时仍不得调用 Graph，并按 fresh DB evidence 对账。

checkpoint 已反映 command 后把正文置 `NULL` 并标记 `checkpointed`。旧、错误、跨 run、错误 branch、错误 node、重复 token 或 terminal run 均失败关闭；不重置 token，不生成替代 token。

若 `recover` 发现合法 waiting-user checkpoint 且对应 pending status 仍为 `awaiting_input`：不调用 Graph、不重新生成 token、不返回 paused DTO、不阻断，抛 `DurableExecutionRunAwaitingInputError`。用户必须用原 token 调 `resume`；token 丢失只能创建新 run。`accepted` 与 `dispatch_started` 必须分别走 §12.5 的不同分支，不能误判为仍在等用户。

### 12.5 `recover`

`recover(run_id)` 是唯一接管入口，且只在调用时工作：

1. 先检查本 Runtime 的 `local_driver_poisoned`，命中即抛 `DurableExecutionStateError`，优先于自持/遗留租约且不进入后续分类；否则 run 不存在：not found；blocked：run blocked；closed：只按 §12.7 验证已确认 terminal pointer/state 后返回，不调用 provider/Graph/预算 manager/journal/Artifact；open 且有效租约：busy；
2. 对 open run strict 比较执行契约；重算并比较不依赖 command 的 start activation；每个已激活 phase 从其绑定 checkpoint state 重新生成并逐项比较，绝不拿 latest state 改写历史预算；
3. 只读验证 checkpoint pointer 与最多 `limits.max_lineage_checkpoints`（绝对不超过 256）的 lineage，形成“无需修复 / repair-only / 必须阻断”候选；此步不得写入，实际 pointer/marker 修复只能在第 10 步重新锁定并复验后完成；
4. terminal checkpoint + open：在无租约冲突且证据一致时只标记为 repair-only close/容量候选，不调用 Graph；本步仍然只读，实际修复只能在第 10 步锁事务复验后原子完成；
5. waiting user 且存在 §12.4 精确 matching、完整的 `awaiting_input` pending/context/token digest：按 §12.4 返回 awaiting-input error；waiting checkpoint 已存在但该 row 缺失、不完整或错配时标记为必须以 `pending_command_invalid` 阻断，绝不补生 token/context、调用 Graph 或继续；
6. generation 0、`bootstrap_state=bootstrap_pending` 且 pending 为 `accepted`：它没有 pause context 或命令前 checkpoint，必须单独按 §12.3 的 bootstrap accepted 分支做只读 preflight。只有 Module 11 可达的 root/descendant checkpoint、run/pending pointer、journal、budget operation、journal ArtifactRef 证据全不存在且 generation-0 context 列为 `NULL` 时，才 strict `decode_initial`/re-encode，重建 fresh initial state 与 `RequirementTurnInput`，重算 start Plan 并全比，标记为可续跑候选；不得扫描 M10 的任意 Artifact。任一可达额外证据、context 非空或字段冲突都标记为必须阻断。此步不得进入 driver scope、CAS 或调用 Graph，也不得把 generation 0 送入 resume adapter；
7. generation 大于 0 的 `accepted` resume command：它证明 dispatch marker 尚未提交、Graph invoke 尚未开始；只有 latest 恰为同 generation 的命令前暂停点，且 durable pause context、version/hash/branch/node 全部匹配时，才可 strict decode，并由 codec 的 `requirement_resume(...)` 生成 fresh DTO，标记为可续跑候选；不需要、不得恢复 token 明文。此步同样不得进入 driver scope、CAS 或 Graph；
8. `dispatch_started`：若合法 checkpoint 已可证明越过原 node，则先形成“清原 pending/修 checkpoint journal”的 repair 候选，再以该 latest checkpoint 的真实后续 pending node重新分类，绝不回到原 node；若后续状态是 waiting，却没有第 5 步要求的 matching `awaiting_input` row，则整体不是 repair-only 可续跑分支，而是 `pending_command_invalid` 必须阻断。其他后续 node 才进入第 9 步。checkpoint 未前进则按 §11.3 的 recovery class 与 §11.2 只读对账。Module 05 三个真实节点均为 non_recoverable，因此在无 post-node checkpoint 时标记为必须阻断，绝不因命令正文仍在而重调外部服务；只有 `replay_safe` 或经完整对账可继续的 `journal_guarded` 才是可续跑候选；
9. 其他 pending node 同样先按 §11.3 与 §11.2 完成只读分类，不在此时写入或调用 Graph；
10. 分类后先在 per-run advisory lock 事务中再次校验 lifecycle、latest pointer/digest、pending status 和无有效租约。必须阻断的分支直接按 §13.2 原子阻断；terminal close、checkpoint 已前进后的 pointer/pending/`checkpoint_confirmed` journal/close/capacity 等预先穷举的 repair-only 分支在该事务内完成并先提交，不取得执行 lease且不调用 Graph，其他写仍禁止。若 repair 后已 terminal 或 waiting，按相应结果返回；若还要从 repaired checkpoint 的真实后续 node 继续，则提交 repair transaction 并释放锁后，重新执行第 2～9 步只读校验，再在第二个 per-run advisory lock 事务中复验 repaired pointer/lifecycle/pending 与无有效租约，竞争失败者返回 Busy，获胜者才取得并提交新 lease/fencing token。无需 repair 的可续跑候选可在第一次锁事务中直接取得并提交 lease。提交后先绑定本次 task context；对所有可续跑或实际执行分支，lease/fence commit 必须早于后续任何 driver/native operation scope、`dispatch_started` CAS、预算/journal/Artifact 写、Saver 写或 Graph 调用；
11. 取得 lease/fence 后，generation 0 候选进入 `start_operation(...)` scope，generation>0 Module 05 候选进入 `resume_accepted_operation(...)` scope；scope preflight 成功后才 CAS `dispatch_started`，提交成功才调用完整父 Graph并在同 scope 解析 child 结果。其他安全候选也只在已绑定的新 lease/fence context 中继续。scope preflight、CAS 或后续执行失败均按其实际 marker/checkpoint 证据处理，不得退回 accepted 假象。

不在 Runtime 内等待租约、不自动轮询、不扫描其他 run、不自动多次 recover。

### 12.6 `get_state`

`get_state` 不执行 Graph，也不执行 recovery class、预算、journal 或 Artifact 对账。本 Runtime 的 run 若已在 `local_driver_poisoned` 集合中，直接抛 `DurableExecutionStateError`，调用方必须换新 Runtime。否则为避免 lease 检查后再返回陈旧 state，它先在 per-run advisory lock 下读取无有效租约的生命周期与 latest pointer，释放锁后读取并 strict 验证 checkpoint，再重新取得同一锁，要求仍无有效租约且 lifecycle/pointer/digest 未变化；第二次校验时出现有效租约就立即 busy，字段变化或证据矛盾则 state error/阻断，完全一致的第二次校验点是本次读取的线性化点。否则它按以下穷举处理：

- open waiting run：返回 fresh state，不返回 token；
- open、无有效租约且 latest 是合法的非 waiting、非 terminal 中间 checkpoint：返回该 fresh state，但不声称 run 可恢复、不改变 pending/lifecycle/lease；调用方必须另行显式 `recover` 才会分类、对账或继续；
- terminal checkpoint + open：安全修复为 closed 后返回 terminal state；
- closed：返回同一 terminal state；
- blocked：抛 `DurableExecutionRunBlockedError`，不返回旧 state；
- `bootstrap_pending` 且没有任何 checkpoint：抛 `DurableExecutionStateError`，不阻断、不清 generation 0 command，调用方仍可稍后显式 `recover`；
- latest 损坏/冲突：首次发现时阻断并抛具体 state/incompatible/integrity error。

### 12.7 terminal 与重复调用矩阵

终态顺序固定为：Graph 以 sync durability 提交 terminal checkpoint → Module 11 读取并严格验证 terminal state → 关闭 run、释放 lease/active capacity。崩溃在 checkpoint 与 close 之间由下一次显式操作修复。

| 已 closed terminal run 上的调用 | 结果 |
|---|---|
| `start` | `DurableExecutionRunAlreadyExistsError` |
| `resume` | `DurableExecutionResumeRejectedError` |
| `recover` | 不调用 Graph/预算/journal/外部能力，返回 terminal |
| `get_state` | 返回 terminal state |

结果必须可重复且逐字段相同。

## 13. run 生命周期、阻断与容量

### 13.1 生命周期

```text
open ──合法 terminal──> closed
open ──不可安全恢复───> blocked
```

不允许 reopen。`open` 包含正在执行和等待用户两种状态，并始终占 active 容量；lease 是否存在是独立维度。`closed/blocked` 释放 active 容量但保留全部证据并占 stored 容量。

### 13.2 阻断事务

确认必须阻断时，一个短事务：

- 若阻断原因可唯一关联到当前合法受管 OperationId，且其 journal 最后状态恰为 `execution_permitted`，必须先按 §10.3 的固定优先级在同一事务追加一次 `indeterminate` 及对应 bounded reason；没有受管 operation、无法唯一关联、最后状态不是 `execution_permitted` 或已经终态时均不得伪造/追加该 transition；
- 把 lifecycle 改为 blocked并保存 bounded reason enum；
- 将每个尚未 `checkpointed` 的 pending 行原子改为 `status='cleared'`、设置 `cleared_at=clock_timestamp()`，并把 command/context plaintext 置 `NULL`；已是 `checkpointed`/`cleared` 的行保持其合法终态；
- 清 lease owner/expiry并使旧 token 失效；
- 释放 active capacity；
- 保留契约、预算、journal、checkpoint、Artifact logical refs，以及 pending 的 type/version/digest/size、既有 `accepted_at`/`dispatch_started_at` 和其他非明文证据；“保留 status”只指保留合法的最终 `checkpointed|cleared`，绝不让 `accepted|dispatch_started` 在正文清空后继续存在。

首次发现返回最具体的安全异常：例如 incompatible、state error 或 `OperationResultIndeterminateError`；之后 `resume/recover/get_state` 统一返回 `DurableExecutionRunBlockedError`。`start` 仍为 already exists。禁止通过公开 API读取 blocked run 的旧 state。对上一条能够唯一关联且末态 permitted 的 operation，journal 封口、lifecycle、pending、lease 与 capacity 变化必须同提交/同回滚，不能留下部分状态；无法唯一关联的一个或多个既有 permitted 证据保持原样，并以 `node_classification_invalid` 或 `journal_invalid` 阻断，绝不伪造 transition。

### 13.3 全局故障与单 run 故障

- 数据库不可连接、schema/revision/官方 Saver 结构错误、容量单例损坏：整个 Module 11 Runtime 拒绝启动或进入 dependency/configuration failure；
- 某一 run 的 checkpoint、contract、journal、Artifact 对账损坏：只阻断该 run，不扫描或修改其他 run；
- 不因一个 run 损坏自动重建全库或改写迁移。

## 14. 安全、隐私、日志与数据最小化

### 14.1 凭据与错误

- Secret 只从进程环境或被 Git 忽略的本地配置进入 settings；
- 不读取、打印、散列或验证根目录 `.env` 中的真实 Secret；
- 日志、异常、repr、测试快照和报告不得含密码、完整连接串、pause token、pending plaintext、Prompt、用户敏感原文、模型原始响应、供应商 payload、reasoning、SQL 参数或底层异常文本；
- typed error 均无参数并 `raise ... from None`；
- lease owner、fencing token、数据库时间和内部 blocked reason 不向调用方公开。

### 14.2 允许持久化的内容

允许：严格 `TravelSessionState` checkpoint、规范化 Graph 内部安全值、执行契约 canonical bytes、预算标量、journal 状态、ArtifactRef 标量、pending command canonical bytes及其 digest/size。

禁止：API key、cookie、认证头、system/developer Prompt、隐藏推理、完整聊天、原始模型/地图/MCP/HTTP payload、异常/堆栈、任意 metadata dict、业务大对象副本。

pending command 与 checkpoint 中的合法业务状态首版按明文数据库字节保存。本文不声称应用层 encryption at rest；PostgreSQL 数据文件、WAL、备份和副本都可能保留其历史版本。私有 command codec/serializer 边界为 Module 19 的加密与密钥生命周期保留替换点，但 Module 19 必须通过前进迁移和新执行契约引入，不能假装旧明文已被擦除。

### 14.3 数据概念分离

| 数据 | 所有者 | 用途 |
|---|---|---|
| Checkpoint | Module 11 / 官方 Saver | Graph 小状态与继续位置 |
| Artifact | Module 10 | 结构化业务结果/evidence |
| Operation journal | Module 11 | 恢复与幂等进度 |
| Safe event/audit | Module 04 端口、20/21 持久化 | 可观察性与审计 |
| 长期 Memory | Future | 跨任务语义记忆 |

禁止把这些概念合并到一张万能表或一个 Repository。

## 15. Docker 与真实 PostgreSQL 隔离

Module 11 的真实数据库测试必须：

- 使用本项目专属、每次运行唯一命名的临时 PostgreSQL 16 容器、network 和 volume；
- 数据库名包含随机测试前缀和显式 identity token；
- 由宿主编排器创建/销毁资源，pytest fixture 只消费已核验连接身份；
- 与日常 `postgres_data`、Module 10 普通数据库和其他项目完全隔离；
- migration 与 Runtime 使用相同五个 PostgreSQL 环境变量，但不得输出值；
- 不停止、删除、复用 `process_simulation-platform-dev` 的任何资源；
- 不把容器重启等同于清空 volume，重启资格必须验证数据仍存在。

模型和地图在 Module 11 Gate 中使用 fake；Module 11 不拥有真实 DeepSeek/Baidu live 资格。

## 16. 测试分类与必测矩阵

### 16.1 分层

| 层级 | 必测内容 |
|---|---|
| unit | settings、精确 DTO/Protocol 签名、错误、canonical/digest、codec 往返、serializer 默认 1 MiB 硬边界与 custom-low 实例边界、完整 journal FSM、分阶段预算算法、commit-unknown reconciliation、生命周期纯逻辑 |
| contract | 包根精确导出/导入边界、四公开入口、factory/capability surface、Module 04 BudgetManager 三方法、ArtifactRepository wrapper、零参数异常、Module 05 embedded driver |
| PostgreSQL integration | Alembic、七表逐列/约束/index、迁移锁、官方 setup、结构漂移、两 pool、容量、lease/fence、budget+journal 事务、root+descendant checkpoint、restart、terminal repair |
| cross-process | 两进程争同 run、busy、expiry 后接管、stale fence 拒写、并发容量、相同请求幂等 |
| fault injection | bootstrap 每个 crash gap、budget/journal commit unknown、Artifact/journal gap、checkpoint/confirmation gap、terminal/close gap、续租失败、取消 |
| regression | 直接依赖 Module 04/05/08/09/10 的受影响合同；另跑 Module 01 传递合同与 Module 07 只读审计回归；全部离线 unit/contract |

生产测试 Graph 只允许：

1. Module 11 测试目录内的最小私有 Graph，覆盖三种 recovery class；
2. 真实 Module 05 以 `checkpointer=None` 嵌入根测试 Graph。

禁止在 `app/` 创建假的生产主图、Module 13 占位实现或临时业务 DTO。

### 16.2 核心场景

测试至少覆盖：

- 空库显式迁移、并发迁移锁、迁移中断后重入、Runtime 不自动迁移；
- 官方四表和七张业务表精确结构；
- limits 首次固化及漂移拒绝；
- start bootstrap 三分支，codec 生成 fresh M01 turn，M05 start scope 覆盖父 Graph invoke 与 child 结果解析；
- `start` 已存在 run 时始终优先 AlreadyExists；本 Runtime 的 local-driver poison 使 `resume/recover/get_state` 先于其自持/遗留租约返回 StateError，其他 Runtime 或未 poisoned 时有效租约才 Busy；
- pause token 只返回一次、只存 digest、错误/stale/cross-run 拒绝；
- Module 05 token domain/driver 验证、native root token domain、`awaiting_input→accepted→dispatch_started` 两次事务 CAS，dispatch marker 必须先于 Graph invoke；
- waiting-user recover 返回 AwaitingInput；
- start Plan 非空且至少含 requirements key、start/phase activation、confirmed-days 公式、旧 activation 精确重算、future phase 缺席合法、越过 activation 阻断；
- budget applied/rejected/replayed/conflict 和 4096 容量；
- journal 全部合法边与全部非法边、四步上限、16384 上限、追加历史和冲突阻断；
- §11.2 每一格；
- active/stored 容量并发不超售，closed/blocked 只释放 active；
- 60/15 租约语义，不依赖 `sleep` 碰运气；通过受控 SQL 将隔离测试 lease 置为已过期；
- stale executor 的 Saver 写被 fencing 拒绝；
- 失租前已进入 critical section 的原 OperationId Artifact put 可收束；失租后新 put 不得开始；
- checkpoint root+Module 05 descendant namespace、qualified node `/` 分段映射及原始 key 的 `/|:` 拒绝、latest/lineage、默认 256 与 custom-low 实例总边界、跨 thread/孤立/fork/损坏/旧 fallback 拒绝；
- metadata JSONB 只能出现 `source/step/parents`，canary 值不能从 config 泄漏；
- `durability="sync"`、recursion 256 不可被调用方覆盖；
- 确定性自循环真实命中 recursion 256 后阻断，后续 recover 不能分段绕过；
- Module 05 三个 qualified 真实 node 及 Module 07/08/09 外调 service node 被标为 non-recoverable；dispatch_started 且 checkpoint 未前进时 crash 后外部调用计数不增加；
- `bootstrap_pending` 无 checkpoint 的 `get_state` 只报 StateError，不销毁 recover 证据；
- 无租约合法中间 checkpoint 的 `get_state` 只返回 fresh state、不分类或续跑，并以 pointer/lease 二次锁校验确定线性化；
- terminal checkpoint + open 修复；
- close 幂等、5 秒边界、异常优先级、M10 不被关闭；
- canary secret 不进入 repr/异常/日志/报告/数据库非预期列。

并发测试使用 barrier、数据库锁、`asyncio.Event`、子进程握手与确定性 fault hook；禁止用任意 sleep 猜时序。禁止 skip、xfail、自动 rerun、只跑绿灯子集、放宽断言或读取对象私有状态证明行为。

### 16.3 coverage 与真实数据库 Gate

Module 11 production source 使用单进程采集，line、branch、combined 三项分别不低于 70.00%。combined 固定为：

```text
(covered_lines + covered_branches) / (num_statements + num_branches)
```

必须检查未舍入分子/分母；分母为零失败。`collected 0`、非零退出、coverage 文件缺失、failed、skipped、xfail、rerun 任一非零均失败。

真实 PostgreSQL Gate 必须从空库迁移开始，经过运行、暂停、关闭 Runtime、创建新 Runtime/新进程、恢复与读取，再证明 Artifact、checkpoint、预算、journal 和生命周期一致。不能用同一 Python 对象保留的内存状态替代重启证据。

## 17. 下游 Module 12–15 的消费边界

### 17.1 Module 12

只消费已验证 state 和 Module 10 Artifact 生成结果；不读取 checkpoint 表、journal、pending command、lease 或历史，不新增事实，也不触发 recover。

### 17.2 Module 13

负责：

- 提供未编译完整根 `StateGraph`；
- 提供 execution contract、精确 node recovery map 和 command codec Registry；
- 提供确定性、无 I/O 的 start/phase 预算计划 provider；
- 规划全局 OperationId 和 node-bound capabilities 的接线；
- 写入 `TravelSessionState.delivery_status/terminal_code/terminal_outcome`；
- 只通过 `create_requirement_subgraph_embedded_driver` 创建 Module 05 child，用 capability 提供的 manager factory 接线，把 `capabilities.requirement_subgraph_limits` 显式传为 factory limits，并省略 `signal_sink` 以保留默认 NoOp 后注册 driver；父 Graph 独占 Module 11 checkpointer，不直接持有 raw coordinator。

不得取得 Saver、pool、SQL、journal Repository 或删除能力。完整主图变化按 §5.4 升级 contract。

### 17.3 Module 14

原四个 Runtime 方法保持语义；网页应用另可使用 §22 的取消和任务查询接口。Application Service/Web API 负责以下事项，暂停凭证交接以 §22.5 为准。它负责：

- 通过受保护凭证端口托管一次性 pause token；前端只传业务命令和当前暂停代次，不显示或手抄 token；
- 把 Busy/AwaitingInput/ResumeRejected 等 typed error 映射为稳定中文文案和退出码；
- 决定用户何时再次显式调用；
- 按组合生命周期先关闭 Module 11，再关闭 Module 10。

它不得轮询租约、读取数据库、重建 token、改变预算或绕过 recover。

### 17.4 Module 15

在完整网页主链中重新验证真实 PostgreSQL、真实外部能力和跨进程恢复；不能替代 Module 11 组件 Gate，也不能降低本文的故障/幂等测试。

## 18. 验收标准

Module 11 实现验收必须逐条证明：

- 公共 API、零参数错误和导入边界符合 §5；
- 七张业务表、独立 Alembic 和四张官方表符合 §6～7；
- pool、生命周期、租约、fence 符合 §8～9；
- budget/journal 原子性和 commit-unknown 规则符合 §10；
- Artifact 对账、取消和失租例外符合 §11；
- start/resume/recover/get_state、bootstrap、pending command、terminal repair 符合 §12；
- open/closed/blocked 与双容量符合 §13；
- 安全、明文边界和数据分离符合 §14；
- Docker 隔离与全部测试/coverage Gate 符合 §15～16；
- Module 12–15 接线没有越权；
- 无自动迁移、自动扫描、公开 Saver、history/delete 或内存 fallback；
- 独立代码审查与 `MODULE-CLOSEOUT` 均完成。

测试全绿只是必要证据，不是充分条件。

## 19. STOP 条件

出现以下任一情况立即停止：

- 需要修改 Module 04/07/08/09/10，或继续扩大 D-11-66 已批准的 Module 05 durable pause/embedded driver 公共合同；
- 锁定 LangGraph/Saver 的公开行为无法满足 fencing、sync durability 或首 checkpoint 安全条件；
- 只能 fork、复制或修改官方 Saver 私有 SQL/API；
- 需要在 `artifact_store` 写 checkpoint/预算/journal，或建立跨 schema FK；
- 无法区分 Artifact、checkpoint、journal、audit 或长期 Memory；
- 需要保存 Secret、Prompt、原始响应、reasoning、供应商 payload 或任意 metadata；
- 需要自动重做结果不确定的外部调用；
- 需要开放 history/delete/reset/unlock/takeover 或超出 §22.4 的 list；
- 需要新增依赖、扩大白名单或修改 Docker/上位文档而当前阶段未授权；
- 数据库、migration、测试或 coverage 只能靠弱化断言、skip、xfail、rerun、私有状态或 sleep 才通过；
- 工作区、分支或 staged 与阶段前置条件不符；
- 准备执行未授权 commit、merge、push、PR 或 deploy；
- 准备触碰其他项目资源。

小范围、明确且不改变本文语义的实现缺陷可在当前阶段白名单内修复；改变设计、扩大范围或大改必须先取得用户批准。

## 20. 已确认决定目录

| ID | 已确认结论 |
|---|---|
| D-11-01 | PostgreSQL 可续租 lease + 单调 fencing token；每 run 单执行者，过期后仅按安全检查接管。 |
| D-11-02 | 与 Module 10 共用 database/账号但分 schema；11 固定 `durable_execution` 且独立迁移。 |
| D-11-03 | 官方 AsyncPostgresSaver 是唯一存储实现；不自造 checkpointer。 |
| D-11-04 | 只允许显式 `recover(run_id)`，无启动扫描或自动续跑。 |
| D-11-05 | lease 只覆盖实际执行；waiting/terminal 释放，崩溃等待 expiry。 |
| D-11-06 | lease 60 秒、每 15 秒续租，使用 PostgreSQL 时间。 |
| D-11-07 | pause token 仅持久化 digest；明文只交调用方，丢失后只能新 run。 |
| D-11-08 | journal 是最小恢复/幂等账本，不记录每个 node 的调试历史。 |
| D-11-09 | M10/M11 仅通过 OperationId/ArtifactRef 逻辑关联，无跨 schema FK。 |
| D-11-10 | 复用五个 PostgreSQL 环境变量，但 Module 10/11 各自拥有 pool。 |
| D-11-11 | 预算按 start/phase activation 首次固化；每个已激活 key 永久不可变，后续按绑定 checkpoint 精确重算。 |
| D-11-12 | 只恢复安全持久点；私有模型上下文或外部结果不确定时失败关闭。 |
| D-11-13 | 全局 schema/依赖故障阻止 Runtime；单 run 损坏只阻断该 run。 |
| D-11-14 | active 4096、stored 16384；terminal/blocked 只释放 active，无 TTL/delete。 |
| D-11-15 | budget operation 与对应 journal state 同一 Module 11 事务；不宣称跨系统 exactly-once。 |
| D-11-16 | Artifact/journal/budget 对账按 §11.2 固定。 |
| D-11-17 | pending command 暂存严格 canonical 明文字节，checkpoint 反映后清正文、留 digest/status。 |
| D-11-18 | recover 只对仍为 accepted、从未提交 dispatch marker 且 checkpoint/generation/node/branch/hash 完全一致的 command 自动重提；dispatch_started 不按正文重提。 |
| D-11-19 | 失租后只允许失租前已进入 critical section 的原 OperationId M10 幂等 put 收束；不得新开始 put。 |
| D-11-20 | 有效租约立即 Busy，不等待或轮询；唯一例外是同一 Runtime 的 local-driver poison，`resume/recover/get_state` 先返回 StateError。 |
| D-11-21 | journal 每次合法变化追加一行；精确重试不重复，冲突阻断。 |
| D-11-22 | 不提供公开 journal history API。 |
| D-11-23 | 首版只保证程序级完整性，不宣称密码学防篡改。 |
| D-11-24 | journal 状态固定为六种，且只记录受管 OperationId；既有 non_recoverable budgeted 正常成功以无 Artifact 字段的 permitted→checkpoint→completed 闭合，checkpoint 前 crash 仍阻断。 |
| D-11-25 | 每 run 4096 OperationId、每 op 4 transition、每 run 16384 journal row。 |
| D-11-26 | active 与累计 stored capacity 分离；stored 满只拒绝新 run。 |
| D-11-27 | 默认 stored 16384，只有七张业务表及 Saver 三张业务数据表全空时可首次固化，且不得小于 active 4096；单例缺失但已有数据时禁止重算或插 0。 |
| D-11-28 | 官方 Saver 保留全部 checkpoint；11 只公开 latest，无 time travel/delete。 |
| D-11-29 | 除同一 Runtime 的 local-driver poison 先返回 StateError 外，`get_state` 遇有效租约立即 Busy；无租约合法中间 checkpoint 只在两段锁校验未变化后返回 fresh state，不分类、对账或续跑。 |
| D-11-30 | latest checkpoint 损坏/冲突时阻断，不回退旧版本。 |
| D-11-31 | permission 后取消不立即释放 lease；permission 前可安全释放。 |
| D-11-32 | run 生命周期仅 `open|closed|blocked`，单向且不 reopen。 |
| D-11-33 | terminal checkpoint 先确认再 close；crash gap 由下一次显式操作修复。 |
| D-11-34 | 除全局 capacity 单例与 runs 父表自身外，五张从表以 UUID FK 关联 runs；budget operation 另以复合 FK 关联 limit；官方 Saver 无 FK，以 canonical thread ID 逻辑验证。 |
| D-11-35 | 显式 migration 依次执行 Alembic、官方 Saver setup 与结构检查；Runtime 只检查。 |
| D-11-36 | 续租 CAS；瞬时连接失败只立即重试一次，未知/失租即停止新工作。 |
| D-11-37 | 只公开受控 Runtime/DTO/error/factory/capabilities，不公开 Saver/pool/Repository/SQL/history/delete。 |
| D-11-38 | coordination 与 checkpoint 使用两个私有 pool。 |
| D-11-39 | 两 pool 的大小、超时、UTC、READ COMMITTED、search path 与 Saver 连接选项按 §8.1 固定。 |
| D-11-40 | 所有 Graph 调用强制 `durability="sync"`，调用方不能覆盖。 |
| D-11-41 | recursion limit 固定 256；命中后阻断，不能靠多次 recover 绕过。 |
| D-11-42 | bounded serializer 委托 Module 05 strict serializer；单 payload 按固化的 `limits.max_payload_bytes` 读写都检查，1 MiB 只是绝对硬上限。 |
| D-11-43 | execution contract ID/digest 首次固化；破坏持久语义的变化必须新 ID。 |
| D-11-44 | Module 13 一次性 callback 返回未编译完整根 Graph，并通过 capability 注册使用默认 NoOp sink 的 Module 05 embedded driver；Module 11 私有编译并注入 saver；node rule 按 §5.4 的 `/` 分段 qualified name 精确覆盖。 |
| D-11-45 | Module 13 的预算 provider 必须确定性；start Plan 非空且至少含 requirements key；start/phase 分次激活，resume/recover 对每个已激活计划重算全比。 |
| D-11-46 | 公开入口仅 start/resume/recover/get_state；前三者结果仅 paused/terminal。 |
| D-11-47 | close 幂等，取消本地工作、最多等 5 秒，只关闭 M11 资源。 |
| D-11-48 | bootstrap 使用 accepted→dispatch_started→checkpointed，并以 marker 区分“Graph 未启动”与“可能已外调”的 crash；M05 start/resume operation scope 必须覆盖完整父 Graph invoke 与全部 child `resolve_result`；scope 后 marker 失败使同 Runtime 的该 run 本地 poisoned，只能由新 Runtime 对账。 |
| D-11-49 | 私有 Artifact wrapper 委托 M10；只有 journal_guarded 的 permission+Artifact 才追加 artifact_confirmed；既有 non_recoverable service 不伪造 Artifact journal，其 budgeted 正常成功只以 checkpoint journal 闭合。 |
| D-11-50 | 每个 node 必须精确声明 replay_safe/journal_guarded/non_recoverable；既有外调 service node 默认 non_recoverable，未列出或意外并行阻断。 |
| D-11-51 | 公开错误是有限零参数层级；复用 OperationResultIndeterminateError；取消/进程异常不包装。 |
| D-11-52 | closed run 的四入口行为按 §12.7 固定。 |
| D-11-53 | waiting checkpoint 的 pending status 仍为 awaiting_input 时 recover 抛 AwaitingInput，不重生 token。 |
| D-11-54 | migration 使用固定 session advisory lock，5 秒获取上限，持有到最终结构检查。 |
| D-11-55 | 恰好七张业务表 + 独立 Alembic version；锁定官方 Saver 实际为四张表；lineage 上限与 blocked/journal reason CHECK 闭集按 §6.2 固定。 |
| D-11-56 | 纯 DB commit unknown 使用 fresh read、至多一次重试、最终 read，不做第三次写。 |
| D-11-57 | 保存私有 latest pointer/digest；lineage 最多回看固化的 `limits.max_lineage_checkpoints` 且绝不超过 256，只验证/修复，不旧版恢复。 |
| D-11-58 | Module 13 前不创建生产主图；测试使用私有最小 Graph 与真实 Module 05 embedded child。 |
| D-11-59 | Module 11 借用 ArtifactRepository，不创建/关闭 M10；组合关闭顺序 11 后 10。 |
| D-11-60 | blocked 清 pending 明文、失效 lease、释放 active、保留证据；后续统一 RunBlocked。 |
| D-11-61 | 允许私有 fencing checkpointer decorator，但不允许自定义 checkpoint 存储实现。 |
| D-11-62 | command codec 是 Module 13 注入的显式 registry 协议，M11 不硬编码业务 command union；它为已注册 M05 child 确定性产生 fresh M01 turn/M05 resume DTO，但 turn 的协议返回 object 并由 driver 验证，以保持 Roadmap 直接依赖不含 M01。 |
| D-11-63 | 实施按 DE-T00..DE-T09 组织，相邻阶段配对，高风险 Gate 独立。 |
| D-11-64 | 初始授权仅为创建分支和两份 Module 11 文档；该范围后来由 D-11-66 明确扩展到必要的 Module 05 文档、代码与测试变更。始终不授权 commit、push、PR 或部署。 |
| D-11-65 | 依赖事实纠错：Saver 3.1.0 创建四张而不是五张表，不增设虚构表。 |
| D-11-66 | 为跨进程 requirement resume，先完成 Module 05 durable pause/embedded driver 的文档、代码和测试变更，再继续 Module 11 文档。 |
| D-11-67 | 不为 Module 11 修改 Module 04/07/08/09；无法证明安全的既有外调 node 归为 non_recoverable，未来 pre-call permission seam 延期。 |
| D-11-68 | 完整预算不在 start 猜测；按 start/phase 首次激活并永久冻结，规划额度使用 confirmed sightseeing_days。 |

## 21. 延期清单

- Module 12 的中文攻略与费用未核验展示；
- Module 13 的具体业务 command DTO、完整 root Graph、终态映射和生产 node 名称；
- Module 14 的应用 API、网页、文案与加密凭证实现（端口见 §22.5）；
- Module 15 的真实完整主链发布 Gate；
- Module 16 的用户主动局部重排；
- Module 19 的 encryption at rest、密钥生命周期、TTL、删除和合法保留策略；
- Module 20～21 的审计、指标、Trace、告警、队列和生产装配；
- Module 22 的长期运行、灾备、删除与恢复系统验证；
- checkpoint 历史浏览、手工回滚、强制 takeover、token reset 和 run 删除均没有未来承诺，需另行设计；
- Module 09 `UF-09-001` 继续由用户暂缓，不属于 Module 11。

当前没有允许实施者自行决定的 Module 11 架构空白。若实现所需选择会改变本文公开合同、持久结构、安全门或下游所有权，必须停止并请求设计变更授权。

## 22. 网页 Demo 协议扩展（D-13-11/14/15/17）

本节是新增合同，必须经专门实施和验证后才可消费，不代表当前代码已有能力。它对 §5、§6、§11.4、§12、§17.3、D-11-07/33/37/46 的修改仅限本节明确范围；历史故障证据和旧 Gate 不改写为通过。M13 当前契约 ID 以 Module 13 §9 及“本次诊断装配与静态容量更新（2026-09-30）”为准；本节初版 demo_workflow.web.v1 仅作为旧契约识别依据。旧 run 的契约不自动升级，旧 Runtime 方法签名保持兼容。全局数据库结构可向前迁移，run 的 execution contract 不变。

### 22.1 公共入口和严格 DTO

新增包根导出如下。所有 DTO 固定 schema_version="1.0"，strict、frozen、extra=forbid；嵌套对象 fresh copy，repr 不显示 state、token 或用户内容。除注明外无可省字段。

    async Runtime.request_cancel(*, run_id: UUID) -> DurableCancellationReceipt
    async Runtime.settle_cancel(*, run_id: UUID) -> DurableCancellationReceipt
    async Runtime.get_run(*, run_id: UUID) -> DurableRunView
    async Runtime.list_runs(*, request: DurableRunListRequest) -> DurableRunPage
    async Runtime.get_terminal_budget_snapshots(*, run_id: UUID) -> tuple[ExecutionBudgetSnapshot, ...]

DurableCancellationReceipt 字段：run_id；status 为 requested/cancelled/already_terminal/blocked；state 为 TravelSessionState|None。cancelled 必须含 cancelled 状态；already_terminal 必须含已确认终态；requested/blocked 不含 state。重复请求幂等，没有取消预算退款。request_cancel 不等待模型或地图结束；只有本来已安全结束时才同步返回终态。

DurableRunListRequest：limit:int（1..100，默认20）；before:DurableRunCursor|None（默认None）。
DurableRunCursor：created_at:UTC datetime；run_id:UUID。
DurableRunPage：items:tuple[DurableRunView,...]（最多100）；next_cursor:DurableRunCursor|None。
DurableRunView：run_id、trace_id、created_at、finalized_at:UTC datetime|None；lifecycle:open/closed/blocked；activity:executing/awaiting_user/recovery_required/cancelling/terminal/blocked/incompatible；phase:WorkflowPhase|None；delivery_status:DeliveryStatus|None；terminal_code:TerminalCode|None；pause_generation:int|None；cancel_requested:bool。不含原文、凭证、checkpoint、lease owner、SQL、用户需求或 Artifact 内容。phase/status 仅来自可验证的已提交状态，active 时允许 None，绝不伪造百分比。

新增有限零参数异常 DurableExecutionCancellationPendingError。start/resume/recover 在适用 run 有取消意图时抛此异常，不消费新命令或预算；get_state 对尚未收束取消也抛此异常。此控制协议优先于因该次用户取消产生的本地driver poison；只开放取消查询/维护，不允许以此解除poison继续业务。没有取消意图时原poison优先级不变。已取消 closed run：get_state 返回 §22.3 保存的终态；recover 返回 DurableExecutionTerminal；resume 按原 closed 规则拒绝；start 仍 AlreadyExists。blocked、契约不兼容和基础设施错误保持原错误优先级，不藏在成功 DTO 中。request_cancel 对 blocked 返回 blocked；对契约不兼容仍拒绝写入。

### 22.2 持久结构及并发裁决

只在 durable_execution.runs 增加列，不另建历史任务索引表：

| 列 | 类型/默认 | 约束 |
|---|---|---|
| cancel_requested_at | timestamptz NULL | 一旦设置不可清除 |
| cancel_terminal_bytes | bytea NULL | 只允许规范 TravelSessionState v2 取消终态 |
| cancel_terminal_digest | text NULL | sha256 前缀；与 bytes 同空或同时非空 |
| cancel_base_checkpoint_id | text NULL | 与取消终态同时写，指向原 latest root checkpoint |
| cancel_base_checkpoint_digest | text NULL | 对应原 pointer digest |

新列组合 CHECK：有 cancel_terminal 必有 cancel_requested_at、lifecycle=closed、finalized_at；bytes/digest/base ID/base digest 同空同有。应用严格校验 bytes 的 run、trace、cancelled/cancelled_by_user 和所有 ArtifactRef；容量算法不增减 stored，关闭只释放一次 active。迁移新增按 created_at DESC, run_id DESC 的索引供分页；旧数据列为 NULL，旧 checksum/契约字节不重写。禁止修改已发布 m11_0001；新增 m11_0002_web_controls，前向迁移及严格结构检查必须在 Runtime 启动前完成。不提供数据删除型 downgrade。

request_cancel 的线性化点是 runs 行锁内持久设置 cancel_requested_at。普通 terminal close、接受 resume、execution permission、取消终态提交均锁同一 run 并重查该值：
- 普通终态已经完成 close 事务：取消返回 already_terminal。
- 取消意图先提交：禁止新业务 dispatch/permission；普通 close 不能再获胜，即使旧图已形成结果 checkpoint，也由取消收束协议处理。
- 已获 permission 的工作可能已发往供应商，无法保证撤回。通知本 Runtime 对应 graph task 取消；不调用 Runtime.close。跨进程由续租和每次受控入口检测意图，禁止取得新工作。
- request_cancel 自身不抢占有效 lease、不释放别人的 lease、不改写已接受命令为未接受。客户端断线不撤销已提交意图。

纯 DB commit unknown 继续按 §6/§10 的 fresh-read、最多一次重试规则，不产生虚假的确认回执。故障后查询仍以已提交数据库证据为准。

### 22.3 安全收束，禁止重放业务来取消

settle_cancel 是显式取消维护入口，不执行 graph、M05、M08、M09 或供应商；它不属于自动 recover 队列。一次用户取消意图提交成功后，M14必须为该run创建或复用唯一的取消维护任务，持续负责安全收束，无需用户第二次点击。若对应本地执行尚未停止，维护任务先等待其退出；其后立即尝试settle_cancel。返回requested（包括仍有有效lease）或应用执行名额Busy时，释放本次维护名额，按1、2、4、5秒且以后最多5秒的退避继续尝试；本地执行结束或名额释放可提前唤醒，禁止忙循环、并发重复settle或抢占他人lease。每次实际维护仍受M13 §13单执行名额约束；等待期间不占名额。这只是已持久取消意图的控制维护，不排队接受start/resume/recover业务命令。

维护在cancelled/already_terminal/blocked回执时结束；依赖/契约等有限异常时停止本轮维护并向应用报告明确维护故障，保留实际cancelling或blocked状态，不伪造取消完成。浏览器断线不结束维护；后端退出会结束进程内维护，重启后查询仍只读展示cancelling，用户再次取消或显式恢复才重新建立同一纯维护任务。未知外部结果仍按下述规则阻断，不能为完成取消而重放业务。

若仍有有效 lease，返回 requested。持有者已完成安全停止并释放，或原 lease 已过期后，按现有 fencing 取得维护租约。检查最后确认 checkpoint、pending command、journal、Artifact 和契约：
1. 不存在合法 checkpoint、尚有未知外部执行结果、lost lease 后证据不全或持久内容冲突：保持原 fail-closed 规则，必要时 blocked，保留取消意图，返回 blocked；不得生成取消完成状态。
2. 只有安全暂停/未 dispatch 命令，或所有已执行工作均由 checkpoint/journal/Artifact 对账闭合：可取消。已接受但尚未 dispatch 的命令不执行，终结 pending 并清正文；waiting token 失效。
3. Runtime 注入的纯函数 cancellation_state_factory(state:TravelSessionState)->TravelSessionState 由 M13 提供，只保留 run/trace/phase/requirement/refs 并写 cancelled/cancelled_by_user、input、recoverable=False、user_action_code=None。Runtime 验证以上全部字段及仅允许的差异。
4. 不调用 Graph update_state、不重执行 interrupt。经校验的取消终态 canonical bytes、digest、基 checkpoint ID/digest 和 close/capacity/pending 收束在同一个 fenced 事务提交，返回 cancelled。

这是 D-11-33 的明确特例：普通终态仍以 terminal checkpoint 为准；安全取消使用 runs 内的不可变取消终态作为权威结果，原 checkpoint 原样保留作来源证据。所有 get_state/get_run/recover/terminal repair 必须优先识别这一终态；不再要求最后 graph checkpoint 本身为 terminal，也不能重新恢复原等待节点。损坏的取消终态、基 pointer 不匹配按原损坏规则失败关闭，不回退原暂停状态。

取消意图只禁止未来工作，不抹掉已消耗预算、已存 Artifact 或未知供应商副作用。运行中的 raw CancelledError/进程退出仍不自动等于用户取消；没有持久意图的旧取消测试语义全部保留。

### 22.4 查询和分页

get_run/list_runs 是只读公共查询，不调用 recover、不续租、不获取执行名额、不调用模型地图。复用 runs、pending 和 lease 信息；不得用 open 直接显示“执行中”。

活动分类优先级：契约不兼容→incompatible；blocked→blocked；可验证的 closed→terminal；取消意图→cancelling；有效 lease→executing；有效 awaiting_input 记录且 checkpoint 处于等待→awaiting_user；其余 open→recovery_required。无有效 lease 时仍用 §12.6 的前后版本检查读取 state，冲突则保守返回 activity=executing、状态字段 None，不能回退旧 checkpoint。查询不执行阻断写事务；单run损坏只在查询视图中保守表示activity=blocked、状态字段None，lifecycle仍是实际存储值；后续显式执行入口按原规则持久阻断。不因一项损坏中止其他合法项，基础设施故障不吞掉。

分页采用 (created_at,run_id) 严格降序 keyset，before 排除该游标及更新项，取 limit+1 决定 next_cursor，不能 offset 翻页。列表允许旧契约条目仅显示有限元数据及 incompatible；不解码或运行旧图。任务标题、需求摘要、详情和结果由 M14 在打开任务后经公开 state/Artifact 读取能力构造；无跨用户检索或删除承诺。get_state 原 Busy 语义不改，网页轮询使用 get_run。

### 22.5 暂停凭证的受保护交接

新增包根 Protocol DurablePauseCredentialSink：

    async store(
        *, run_id: UUID, pause_generation: int,
        contract_digest: ContentHash, pause_token: str
    ) -> None

factory 增加 keyword-only pause_credential_sink:DurablePauseCredentialSink|None=None 和 cancellation_state_factory:Callable[[TravelSessionState],TravelSessionState]|None=None。旧调用可省略；M13 网页装配要求两者存在。缺少cancellation_state_factory的旧Runtime调用request_cancel/settle_cancel时，在写意图之前返回DurableExecutionConfigurationError；不产生无法收束的半启用取消。M11 不拥有加密库、密钥或应用凭证表；M14 实现 sink 和读取接口。明文 token 只能短暂存在内存中用于验证，不入日志、repr、浏览器、pending body 或明文持久存储。store 必须在成功返回前完成受保护持久化；同 run/generation/contract、同 token 幂等，冲突拒绝。

对 M05 和原生酒店暂停，顺序固定：确认 checkpoint 和本轮受管操作→取得下代 generation→用原始 token 调用 sink.store→写 waiting pending（仅 token digest）并释放 lease→返回原 Paused DTO。sink 失败时不返回“可继续”，不得产生新 token 重试；按现有 crash gap 失败关闭。sink 成功、pending 未完成时，凭证孤儿不可据此恢复；sink 成功且 pending 已确认后即使 HTTP 响应丢失，M14 可读取原凭证并恢复。store 的精确重试只能使用原 token；不增加 get_token/reset_token API。

原 Paused 返回 token 保留用于旧调用兼容；M14 不把它转发前端。M14 使用 generation 和契约作为索引，提交 resume 时仍接受 M11 全部 digest/state/lineage/fencing 校验。凭证存储或密钥不可用时只影响该等待任务可恢复性，不允许伪造 token。整库 encryption-at-rest、TTL/删除仍属 M19；本最小 token 保护是提前启用的特定范围。

### 22.6 必须重新证明的 Gate

DE-W01..W05 见执行规范 §8。真实 PostgreSQL 验证：旧数据前向迁移；cancel/complete 与 cancel/resume 两种事务先后；重复取消；permission 前/后；取消响应丢失；后台进程重启；原生酒店已激活 phase 的 pause/resume；M05 与原生 pause 的 store/commit 各 crash gap；查询与执行竞争；分页；篡改终态；lease expiry/未知结果不冒充 cancelled；新旧 factory 公共 API。

UF-11-003/004 在上述实际触发范围必须补齐；未触发的历史资格缺口保持原记录。此节通过不等于全部 M11 延期已消除，不得仅修改历史报告宣称通过。
### 22.7 原生暂停的真实恢复接线

M13 酒店等待使用真实 interrupt，不能用普通根 state 再次 ainvoke 冒充恢复。现有 _NativeOperation 直接返回 state 的行为不足以证明该路径。扩展实施必须在 native resume 和 accepted-command recovery 分支，完成 token/context/预算/lease/accepted-marker 全部检查后，以 LangGraph 公开 Command(resume=True) 调用同一 thread/root namespace；业务命令仍通过 node-bound current_command读取，True只是解除本次原生interrupt的固定标记，不能携带token或任意DTO。native start仍使用初始state，recovery of dispatch_started仍遵守原不确定性规则，不能一律resume=True。

M05 embedded driver 的operation scope和恢复分支保持原合同。必须用真实 root Graph 的酒店暂停测试验证“恢复恰好进入原等待节点下一段、不重跑requirements/hotel_discover、不重复预算、不残留interrupt”；多重原生interrupt在本期禁止。此接线修复处于本节必要M11扩展范围，不授权修改M05或第三方LangGraph。
### 22.8 节点受控只读 Artifact 视图

DurableExecutionNodeCapabilities 新增 property read_only_artifact_repository:ArtifactRepository。这是实现M04形状的M11受限适配器：lookup/get验证当前node/run绑定和same-run，委托借用M10公开仓储做完整strict/hash/schema校验；put始终在任何I/O前抛DurableExecutionUsageError。调用前后验证当前fence未失效；不取得新的execution permission、不消费预算、不创建/提升/修改任何journal。

该视图用于消费已由图state或其验证后的来源目录引用的上游证据，包括non_recoverable服务保存而没有独立journal的合法Artifact，以及其他publish节点保存的day。它不是当前节点执行证据：不能把只读get成功当成本节点permission或将其他节点Artifact晋升为本节点journal。当前节点重放仍必须使用原artifact_repository的lookup/get/put路径与自身OperationId对账；原写适配器的严格归属规则不放宽。

M12 prepare/build_index使用这个只读视图；M13 day/index的lookup/consume/put使用原写适配器。视图不暴露原M10 runtime、SQL、pool或不受绑定的仓储。新增测试必须证明：合法上游无journal可读；跨day节点可读；同run伪造Ref/hash错误被M10拒绝；跨run/失效绑定/写入被拒绝；读取前后journal行数不变；没有本节点permission仍不能put。

### 22.9 终态预算快照读取

get_terminal_budget_snapshots只返回该run全部已激活预算的fresh M04 ExecutionBudgetSnapshot tuple，按M04规定排序，包含零消费但已激活key，不含未激活阶段的猜测值。仅在验证相同contract且run确认为closed（含§22.3取消终态）后，从同一只读一致性事务验证最终state来源和budget ledger/limit/activation关系，再返回；不获取业务lease、不补记录、不重算修复数据库。

open有效执行lease→Busy；open尚在取消→CancellationPending；其余非终态open→UsageError；blocked→RunBlocked；损坏或不一致→既有有限State/Dependency错误。closed结果不可再消费新预算，所以响应后的终态一致性不会随新工作漂移。该接口不开放journal历史或可写manager。

M14只在Runtime返回确认终态后调用该接口并交给M13 diagnostics.finish；取消、酒店decline、成功、业务失败使用同一路径。M13只有在本进程整次观测历史完整时生成RunSummary；跨进程仍按D-13-10输出历史不可用。不能为了缺少接口而扩大摘要例外，也不能在图的候选终态尚未完成M11裁决前输出完整终态摘要。
