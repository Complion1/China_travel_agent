# 01 需求提取与对话领域规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 网页新任务使用领域状态和确认产物版本 1.1，收集范围、版本选择及旧任务兼容见 [M14 有效输入与历史兼容](14_web_application.md#有效输入与历史兼容)。本文件的 1.0 人员、房间、限制和手工模板合同保留供历史任务使用；新手工模板为版本 2，只有有效必填项，不填充默认人数或房间。

> 2026-09-26 用户批准预算金额功能延期：[当前修订与执行合同](12_budget_amount_deferral.md) 优先适用。金额不采集、不作为确认/输出门槛；API 调用额度不变。旧预算案例属于历史合同，不代表本期验收项。

> 文档状态：v2.6（已批准的需求状态连续性合同）
> 当前实现与验证状态：见 [`IMPLEMENTATION_STATUS.md`](VALIDATION.md)
> 上位规范：[`00_agent_master_design.md`](00_agent_master_design.md)
> 依赖与工作包：[`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)
> 共享实施规则：[`SHARED_IMPLEMENTATION_RULES.md`](SHARED_IMPLEMENTATION_RULES.md)
> 配套编码契约：[`01_requirement_and_dialogue_vibe_coding.md`](01_requirement_and_dialogue_vibe_coding.md)
> 规范属性：本文件只定义需求领域语义、公共领域合同与验收行为

```yaml
module_id: "01"
delivery_phase: "Demo"
blocks_demo: true
blocks_production_release: true
depends_on: []
provides:
  - TravelRequirement / RequirementPatch / RequirementDomainState
  - RequirementDomainTransition（下一状态 + 本轮 outcome）
  - RequirementExtractionSession / RequirementExtractor / EvidenceSink Protocol
  - RequirementDomainOutcome 及五个固定 outcome
  - REQ-DLG-001..036
consumes: []
parallel_with: ["02"]
not_in_scope:
  - 具体 DeepSeek Runtime 与 RequirementExtractor 适配
  - 工作流状态、Graph、持久化、安全、入口和跨模块测试
acceptance_gate:
  - 纯领域与 extractor contract 测试通过
  - app/requirements 不导入后续模块实现
test_class: pure-domain
unit_test_command: python -m pytest -q tests/unit/requirements
contract_test_command: python -m pytest -q tests/contract/requirements
allowed_test_dependencies:
  - Python 标准库 / Pydantic / pytest / pytest-asyncio / Hypothesis
  - app.requirements 公共合同
forbidden_test_dependencies:
  - app.llm_runtime / app.requirement_extraction / app.graph / app.persistence / app.agent / app.tools / app.config
  - LangChain / LangGraph / FastAPI / asyncpg / psycopg / 供应商 SDK
required_fakes:
  - RequirementExtractionSession / RequirementExtractor
  - EvidenceSink
  - Clock
stable_contracts:
  - TravelRequirement / RequirementPatch / RequirementDomainState
  - RequirementDomainOutcome / RequirementDomainTransition
  - RequirementExtractor / RequirementExtractionSession Protocol
production_replacement: null
```

## 1. 目标

本模块把每轮中文用户消息转换为可验证的增量旅行需求，并通过确定性规则完成规范化、合并、缺失/冲突判断、统一追问和完整摘要确认。

本模块的终点是产生一个 `RequirementDomainTransition`。其中 `state` 是下一轮唯一权威需求状态，`outcome` 是本轮路由和展示结果；outcome 固定为以下五个分支之一：

```text
RequirementDomainOutcome
├── NeedClarification
├── NeedConfirmation
├── ConfirmedTravelRequirement
├── RequirementExtractionFailed
└── RequirementInvalidated
```

本模块必须保证：

1. 大模型只提交当前轮候选 `RequirementPatch`，不能覆盖完整需求。
2. 每个模型提取的非空叶子字段都有当前消息中的原文子串证据。
3. 业务值只能由确定性规范化、校验和合并函数写入。
4. 缺失、无效、冲突和待确认内容在一条追问中完整展示。
5. 未通过完整摘要确认时不得输出 `ConfirmedTravelRequirement`。
6. 用户在确认时提出修改，必须先修改并生成新摘要，不能同时视为确认。
7. 模型失败不得降级为自由文本、宽松 JSON、正则抽取或静默默认值。
8. 每个正常领域返回都必须同时携带下一状态；调用方不得从 outcome 反向拼装状态。

## 2. 规范优先级与变更

优先级从高到低固定为：

1. 用户明确确认的最新产品决策。
2. `00_agent_master_design.md`。
3. 本领域规范。
4. 可执行合同测试；只证明实现，不反向定义语义。
5. 配套 vibe coding 精确执行契约。
6. `IMPLEMENTATION_STATUS.md` 验证快照。
7. 当前代码。

代码、测试或 Prompt 与本文件冲突时必须停止并按上述层级核验。只有用户明确要求改变需求时，才能先修改规范，再修改编码契约、测试与代码。

## 3. 职责边界

### 3.1 本模块负责

- 定义 `TravelRequirement`、`RequirementPatch`、字段状态和领域问题。
- 从当前轮用户消息提取严格结构化 Patch。
- 验证字段级原文 evidence。
- 规范化日期、时间、金额、人数、枚举和显式空值。
- 增量合并多轮需求，区分未提供、明确纠正和显式清空。
- 计算派生字段并检测跨字段冲突。
- 计算硬字段缺失项。
- 生成确定性统一追问和完整确认摘要。
- 解析和序列化严格人工需求模板。
- 产生已确认需求与关键字段变更事件。
- 定义领域错误和调用方必须处理的 outcome。
- 定义 `RequirementDomainTransition`，并验证下一状态与五种 outcome 的一致性。

### 3.2 本模块不负责

以下内容不得出现在本文件的实现要求、任务或验收门槛中：

- LangGraph 节点、条件边、`interrupt()`、`Command(resume=...)`。
- thread、checkpoint、原生 `Interrupt.id`、stale resume 或并发恢复。
- PostgreSQL、checkpoint saver、snapshot 表、事务或连接池。
- 幂等 claim/lease/result、审计表、artifact store 或 schema migration。
- checkpoint serializer、加密 keyring、密钥轮换、保留期和完整删除。
- CLI、Agent Skill、FastAPI、入口响应 DTO 或完整 Agent smoke journey。
- 酒店、景点、餐馆、百度地图、路线、攻略、预算引擎和 ReAct。
- 全项目 CI、全局覆盖率、真实模型评测平台或跨模块 E2E。
- 共享 LLM 工厂、LangChain `Runnable`、真实提取 Prompt、provider response 适配、router、planner 或其他模型角色的实现。

调用方负责把用户输入和当前领域状态交给本模块，并原样消费 transition：持久保存 `transition.state`，使用 `transition.outcome` 路由和展示。本模块不持有数据库连接、Graph 对象、模型工厂 singleton 或外部工具客户端。

模块 01 定义 `RequirementExtractionSession`、`RequirementExtractor`、`EvidenceSink` 和 `Clock` Protocol，但不得提供会访问模型、数据库、环境变量或系统网络的生产实现。具体 DeepSeek adapter、Prompt、序列化和 Runtime 错误包装属于 03；01 的全部测试只能注入可观测调用次数、固定返回值和固定 repair outcome 的异步 fake。

### 3.3 边界输出

本模块只向上层输出窄领域 transition：

```text
RequirementDomainTransition
├── state: RequirementDomainState        # 下一轮权威状态
└── outcome: RequirementDomainOutcome    # 本轮路由/展示
    ├── NeedClarification
    ├── NeedConfirmation
    ├── ConfirmedTravelRequirement
    ├── RequirementExtractionFailed
    └── RequirementInvalidated
```

`transition.state` 不能由 outcome 推导、重建或省略。outcome 中重复出现的 requirement、metadata 或 issues 只服务当前分支的 typed 展示合同，不取得状态所有权。

上层如何暂停、恢复、持久化、加密、发布事件或回退流程，由后续模块文档定义。

### 3.4 后续模块固定映射

01 只拥有领域合同和纯领域行为。相邻职责已固定映射到以下模块，编码 Agent 不得把它们提前放回 01：

| 01 边界对象或需求 | 唯一后续所有者 | 01 与其交互方式 |
|---|---|---|
| 共享 DeepSeek 工厂、角色 Profile、超时、重试和调用配额 | 02 LLM Runtime | 01 不导入；03 使用 02 实现 01 的 Protocol |
| `RequirementExtractor` 的真实模型适配、异步 Session、提取 Prompt 和调用预算 | 03 Requirement Extraction Adapter | 01 定义 Protocol、判别 outcome 和验证语义；03 单向依赖 01 |
| `TravelSessionState`、phase、命令信封和 interrupt payload | 04 Workflow Contracts | 04 分别保存 01 transition 的 state/outcome，不把 workflow 字段写回领域模型 |
| Demo 需求链路、暂停与恢复 | 05 Graph Kernel | 05 经 04 state 消费 01 transition；对 `NeedConfirmation` 或 `RequirementInvalidated` 展示 outcome 自带的 `summary`，确认时只回传 state/action；01 不感知节点、边、当前 Graph outcome 或 interrupt ID |
| Demo artifact 引用、Repository 端口与进程内参考实现 | 04 | 01 只注入窄 Sink，不导入 Repository；13 只负责装配 04 的实现 |
| 百度地图、酒店、候选研究、行程与输出 | 06–09、12 | 只消费已确认需求，不把地图、规划或展示字段写回 01 |
| PostgreSQL Artifact、checkpoint、执行账本与跨进程恢复 | 10、11 | 实现 04/05 的稳定端口，不改变 01 领域语义 |
| Demo 完整业务编排 | 13 MVP Orchestrator | 13 把 01 与酒店、规划、持久化和输出模块装配 |
| 应用 API 与网页入口 | 14 Application Service、应用 API 和前端网页 | 14 只通过 13 和公共响应 DTO 调用，不直接驱动 01 私有函数 |
| Demo E2E | 15 Demo E2E | 可以消费 01 公共合同，但不得替代 01 纯领域测试 |
| 局部重排、安全与观测 | 16、19–21 | 实现既有端口，不改变 01 领域语义 |
| 生产恢复与质量评测 | 22、23 | 不反向修改 01 公共合同 |

依赖方向固定为 `01 ← 03`、`01 → transition → 04/05/13`。禁止 01 对 02–23 任何实现级 import。

## 4. 约束等级

- **L0 业务不变量**：本文件中的“必须、禁止、固定为”均属于 L0，任何实现不得改变。
- **L1 公共领域合同**：公开 DTO、Protocol、错误类别、outcome 和字段语义固定。
- **L2 参考实现**：私有函数名、内部文件拆分和局部算法组织由编码契约给出默认方案，可在保持 L0/L1 和全部测试的前提下替换。

不得用 L2 的灵活性改变字段语义、验证顺序或错误行为。

## 5. 领域输入

### 5.1 单轮输入

```text
RequirementTurnInput
├── schema_version
├── turn_id
├── user_message
├── session_date
├── timezone = Asia/Shanghai
├── intent: initial | supplement | correction | confirmation_revision
└── current: RequirementDomainState
```

固定规则：

- `RequirementTurnInput.user_message` 是当前轮原文，公共 DTO 只要求至少 1 个 Unicode code point，不设置 8,000 的 Pydantic `max_length`；这样 8,001 字符输入可以到达 service 的 typed failure 分支。
- 不接收完整聊天历史、工具结果、Trace、API Token 或数据库对象。
- `session_date` 和时区由调用方注入；领域代码禁止读取系统当前时间。
- `turn_id` 只用于 evidence 和字段来源关联，不承担幂等或 checkpoint 语义。
- service 在创建 extractor Session、调用 EvidenceSink、merge 或任何其他副作用前检查 `len(user_message) <= 8_000`。超限时返回完整输入前态 fresh copy 与 `RequirementExtractionFailed(input_too_long)` 的 transition；extractor Session 创建数、EvidenceSink 调用数和 Patch 应用数均为 0，禁止静默截断或记录原文。
- 传给模块 03 的 `ExtractionInput.user_message` 可以继续声明 `max_length=8000`，因为它只能在上述 service preflight 通过后构造。

### 5.2 当前领域状态

```text
RequirementDomainState
├── requirement: TravelRequirement
├── metadata: FieldMetadataRegistry
├── issues: tuple[RequirementIssue, ...]
└── confirmation_status: collecting | awaiting_confirmation | confirmed
```

该对象是领域值，不规定由何种基础设施持久化。

### 5.3 领域状态转换

公开类型位置固定为 `app/requirements/models.py`：

```python
class RequirementDomainTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: RequirementDomainState
    outcome: RequirementDomainOutcome
```

字段顺序固定为 `state`、`outcome`；两者必填、不可为 `None`、没有默认值。transition 不增加自己的 `schema_version`，嵌套 `RequirementDomainState` 和各 outcome 保持各自版本合同。outer `frozen=True` 不表示递归冻结嵌套 DTO。

public model 的直接构造边界必须先分别重建 state 与 outcome，再执行一致性校验：对两个入参各自执行 Python-tree strict validation，随后用 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)`、`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` 和各自的 TypeAdapter strict JSON validation 取得两个 fresh 对象。model validator 内禁止递归调用 `TypeAdapter(RequirementDomainTransition)`；直接构造测试必须通过 identity 与双向 mutation canary。

service 出口再对已经构造成功的完整 transition 执行一次相同参数的 JSON 编码，并用 `TypeAdapter(RequirementDomainTransition).validate_json(..., strict=True)` 恢复最终 fresh transition。此时 adapter 从 JSON tree 正常进入上述字段重建，不得在 validator 内再次验证整个 transition。任何验证失败由 DTO/service 编程边界产生 Pydantic `ValidationError`；service 不吞掉或改写为领域 outcome，只能返回最后恢复出的对象。

模块 01 是 transition 一致性的唯一所有者，构造时必须执行以下规则：

| outcome 分支 | `transition.state` 必须满足 |
|---|---|
| `NeedClarification` | `confirmation_status="collecting"`；requirement、metadata、issues 与 outcome 等值 |
| `NeedConfirmation` | `confirmation_status="awaiting_confirmation"`；issues 为空；requirement、metadata 与 outcome 等值 |
| `ConfirmedTravelRequirement` | `confirmation_status="confirmed"`；issues 为空；requirement 与 `outcome.requirement` 等值，metadata 与 `outcome.metadata_without_raw_evidence` 等值 |
| `RequirementExtractionFailed` | 与调用入口收到的完整前态等值，不应用部分 Patch、不清空已有 issues、不改变 confirmation status |
| `RequirementInvalidated` | 保存修改后的完整 requirement/metadata；`confirmation_status="awaiting_confirmation"`；issues 为空；`after_digest` 等于该 state 中修改后 requirement 的规范 digest；`summary` 是修改后 requirement 的完整新确认摘要 |

`RequirementInvalidated` 的公共字段顺序和类型固定为：

```python
class RequirementInvalidated(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome_type: Literal["invalidated"] = "invalidated"
    changed_fields: tuple[RequirementFieldPath, ...]
    before_digest: Annotated[str, StringConstraints(min_length=1)]
    after_digest: Annotated[str, StringConstraints(min_length=1)]
    reason: Annotated[str, StringConstraints(min_length=1)]
    summary: Annotated[str, StringConstraints(min_length=1)]
```

`summary` 必须由模块 01 service 对修改后的 `transition.state.requirement` 调用既有确定性 `render_confirmation_summary()` 生成，且与该 renderer 的返回值逐字相等。它不调用模型，不包含 raw evidence、异常、Prompt 或基础设施信息，也不允许调用方自行拼接、导入私有 renderer、伪造为 `NeedConfirmation`，或额外调用空白 `process_turn()` 取得摘要。

这里的 requirement 规范 digest 固定为：对 `TravelRequirement.model_dump_json(exclude_none=False)` 返回的 UTF-8 文本计算小写十六进制 SHA-256；`before_digest` 对调用入口收到的修改前 requirement 使用同一算法。`changed_fields` 精确等于本次 merge 返回的变更路径集合，转换成 `RequirementFieldPath` 后按 `.value` 排序、去重。public transition validator 只可从当前 state 独立核验 `after_digest` 和结构一致性，不导入 dialogue renderer；`summary` 与 renderer 的语义一致性、`before_digest` 和 `changed_fields` 必须由 service 行为测试结合输入前态与 merge 结果证明。不得以 `repr`、省略 null、outcome JSON 或 workflow state JSON 代替。

service 返回的 state 和 outcome 必须是脱离输入、内部工作对象及彼此的深层 fresh snapshot。即使两边数据等值，也不得共享 requirement、metadata、issues 或其可变子对象；修改任意一边不能影响另一边。不得以“调用方只读”约定替代该复制合同。

`RequirementExtractionFailed(input_too_long)` 同样遵守完整 transition、前态保留和深层解除引用合同；不能因为失败发生在 extractor Session 创建前就返回裸 outcome 或复用输入对象。

只有初始工作流快照可以表示“尚无 outcome”，且其需求状态必须显式等于 `RequirementDomainState()`；这属于模块 04 的 state 信封合同，不允许 `RequirementDomainTransition.outcome=None`。一旦产生 transition，后续工作流快照不得清除 outcome 或丢弃 state。

## 6. 旅行需求模型

### 6.1 字段元数据

```text
FieldMetadataRegistry
└── metadata[RequirementFieldPath]
    ├── status
    ├── source_turn_id
    ├── evidence_ref
    ├── evidence_digest
    ├── normalization
    └── issues
```

`RequirementFieldPath` 必须来自唯一枚举或由 Schema 确定性生成。业务代码禁止散落手写字段路径。

`FieldStatus` 固定为：

```text
missing | extracted | derived | pending_confirmation | confirmed | conflict | invalid
```

本模块验证 raw evidence，但不定义其持久化方式。跨边界 outcome 中不得携带 raw evidence；只携带由调用方提供或写回的 `evidence_ref/evidence_digest`。领域单元测试可以使用内存 evidence sink。

### 6.2 时间窗口

```text
TravelTimeWindow
├── arrival
│   ├── hub_name
│   ├── hub_type: airport | railway_station
│   ├── local_datetime
│   └── is_approximate
├── departure
│   ├── hub_name
│   ├── hub_type: airport | railway_station
│   ├── local_datetime
│   └── is_approximate
├── sightseeing_start_date
├── sightseeing_end_date
├── sightseeing_days
├── hotel_check_in_date
├── hotel_check_out_date
└── hotel_nights
```

固定为 `Asia/Shanghai`。日期格式为 `YYYY-MM-DD`；本地日期时间格式为 `YYYY-MM-DD HH:MM`。

确定性关系：

```text
arrival_datetime <= sightseeing_start_date 当天结束时间
departure_datetime >= sightseeing_end_date 当天开始时间
sightseeing_days = end_date - start_date + 1
hotel_nights = check_out_date - check_in_date
```

入住/退房默认候选为抵达/离开日期，但候选必须在摘要中展示；候选不是模型默认值。

### 6.3 完整需求

```text
TravelRequirement
├── destination
├── time_window
├── budget
│   ├── total_cny
│   ├── is_approximate
│   └── scope = destination_only
├── party
│   ├── adults_count
│   ├── children_count
│   ├── children_ages
│   ├── seniors_count
│   └── total_people [derived]
├── rooms
│   ├── room_count
│   └── room_type_requirements
├── constraints
│   ├── mobility_constraints
│   └── dietary_restrictions
├── preferences
│   ├── interests
│   ├── pace
│   ├── agent_decides_interests
│   ├── agent_decides_pace
│   ├── transport_preferences
│   ├── hotel_preferences
│   ├── must_visit_places
│   ├── avoid_places
│   ├── daily_time_preferences
│   └── free_text_notes
└── inferred_scenarios [derived]
```

### 6.4 尺寸和取值上限

| 字段 | 固定边界 |
|---|---:|
| 单轮用户消息 | 8,000 code point |
| 目的城市 | 1～64 code point |
| 枢纽名称 | 1～128 code point |
| 房型要求 | 1～500 code point |
| 行动限制 | 1～500 code point，或显式“无” |
| 饮食禁忌 | 1～500 code point，或显式“无” |
| 其他自由文本 | 每项 1～500 code point |
| 成人/儿童/老人单项 | 0～100 |
| 总人数 | 1～100 |
| 儿童年龄 | 0～17 |
| 儿童年龄数量 | 等于儿童人数，最多 100 |
| 单字段 evidence | 恰好 1 条，1～1,000 code point |

本表的 8,000 是 service 行为上限，不是 `RequirementTurnInput` 的 Pydantic `max_length`；其他字段继续在 DTO 或确定性规则中执行其上限。超限返回逐字段 `value_out_of_range` 或 `input_too_long`，禁止截断、取模或压缩为自由备注。边界长度按 Unicode code point 计算，不按 UTF-8 byte 或 UTF-16 code unit。

### 6.5 硬必填字段

进入确认摘要前必须有效：

- 目的城市。
- 正式游玩开始、结束日期和天数。
- 抵达枢纽、日期和预计时间。
- 离开枢纽、日期和预计时间。
- 预算金额已延期，不属于硬必填字段。
- 成人、儿童和老人数量。
- 儿童人数大于 0 时，每位儿童年龄；儿童人数为 0 时固定为空列表且不得追问。
- 房间数量和房型要求。
- 行动限制，允许显式“无”。
- 饮食禁忌，允许显式“无”。

`missing`、显式 `null`、空字符串、空列表和“无”不得混为一类。空列表只有在字段语义允许且用户明确表达无内容时才表示已回答。

### 6.6 偏好

兴趣固定为配置枚举：`人文/自然/美食/打卡`，允许多选。节奏固定为：`轻松/标准/紧凑`。

用户可以选择“由 Agent 决定”，必须以布尔字段保存，不能用缺失值代替。

交通、酒店、必去、避开、作息和备注只在用户主动提及时提取，不因缺失而追问。`free_text_notes` 不得代替已有结构化字段。

## 7. 严格提取合同

### 7.1 异步注入边界

模块 01 只依赖异步协议，不创建 Runtime、Operation、Prompt 或共享模型工厂：

```python
class RequirementExtractor(Protocol):
    def start_session(self, request: ExtractionInput) -> RequirementExtractionSession: ...

class RequirementExtractionSession(Protocol):
    async def extract(self) -> ExtractionAttemptOutcome: ...
    async def repair(self, feedback: ExtractionRepairFeedback) -> ExtractionAttemptOutcome: ...
```

`ExtractionInput` 固定包含 `turn_id: UUID`、当前消息、`session_date`、`timezone`、完整当前 `TravelRequirement` 和 `intent`。`turn_id` 只用于内存 Session 与安全事件，模块 03 禁止把它发送给模型。

领域 service 的 `process_turn` 以及经 `confirm(..., decision="revise")` 进入的修订路径必须是 async。`process_turn`、`confirm`、`process_manual_template` 三个公开入口都只返回 `RequirementDomainTransition`，不得保留返回裸 `RequirementDomainOutcome` 的兼容 API。禁止提供第二套同步 API、在线程池中包裹同步模型调用或在事件循环内使用 `asyncio.run()`。

### 7.2 Session 和调用预算

一个 `ExtractionInput` 恰好创建一个 request-scoped Session。Session 不得跨请求复用，不得保存到领域 state。正常状态机固定为：

```text
NEW --extract()--> EXTRACTED_SUCCESS
NEW --extract()--> REPAIR_REQUIRED --repair(feedback)--> REPAIRED_SUCCESS
NEW --extract()--> runtime exception
REPAIR_REQUIRED --repair(feedback)--> repair required（领域 failed）
```

每个 Session 最多调用 `extract()` 一次、`repair()` 一次；任何第三次语义调用、先 repair、重复 extract 或重复 repair 都是编程错误。模块 03 必须用同一个模块 02 `ModelOperation` 完成这最多两次调用。网络层 retry 是模块 02 的内部行为，不增加领域语义调用次数。

### 7.3 输入上下文与序列化所有权

模型业务上下文只允许包含：

- 当前用户消息。
- 注入的 session date 和固定 `Asia/Shanghai`。
- 当前需求的完整结构化 JSON，所有业务字段都出现，包括 `null`。
- 当前意图类型。
- strict Schema 和固定枚举。
- repair 时仅增加固定错误码和排序去重后的字段路径。

模块 03 负责使用标准 JSON 编码器生成动态 user message，参数固定为 `ensure_ascii=False, sort_keys=True, indent=2`。不得压缩、截断、摘要、删除 null、字符串拼接动态 Prompt 或传完整历史。完整 JSON 超过模块 02 byte gate 时，模块 02 的 typed runtime failure 原样冒泡。

`turn_id`、Graph state、数据库记录、工具结果、旧 evidence、先前 DTO、先前 raw 模型输出、异常文本和模型 Trace 均不得发送给模型。

### 7.4 输出 DTO

strict DTO 必须包含全部 Schema 属性；当前轮未提供使用 `null`。每个非空叶子字段恰好对应一条：

```text
FieldEvidence
├── field_path
└── text
```

模型不得生成 turn ID、reference、digest、field status 或完整会话状态。

这里的“叶子字段”只指 `ATTR_TO_PATH` 登记的可提取业务属性，不包括控制属性 `is_correction` 和容器属性 `evidence`。`interests`、`children_ages`、`must_visit_places`、`avoid_places` 等 list-valued 属性整体各算一个叶子并各使用一个字段路径，不按列表元素重复 evidence。该唯一 evidence 子串必须足以覆盖当前消息中对整个列表字段的明确表达。

`RequirementIntent` 是 `is_correction` 的唯一权威来源：`initial/supplement` 必须为 `false`，`correction/confirmation_revision` 必须为 `true`。不一致是可修复错误，禁止相信模型值。

当前消息没有任何可提取旅行字段时，返回合法空 Patch：全部 nullable 字段为 `null`、`evidence=[]`，`is_correction` 仍由 intent 决定。问候、问题、控制文本和提示词注入不得写入 `free_text_notes`。

### 7.5 判别 outcome 与 repair feedback

模型调用本身不使用异常表示“可以 repair”的业务结果：

```python
class ExtractionAttemptSucceeded(BaseModel):
    outcome_type: Literal["succeeded"] = "succeeded"
    value: StrictRequirementPatchDTO

class ExtractionRepairRequired(BaseModel):
    outcome_type: Literal["repair_required"] = "repair_required"
    feedback: ExtractionRepairFeedback

ExtractionAttemptOutcome = Annotated[
    ExtractionAttemptSucceeded | ExtractionRepairRequired,
    Field(discriminator="outcome_type"),
]
```

`ExtractionRepairFeedback` 只包含以下固定 code 和排序、去重后的 `field_paths: tuple[RequirementFieldPath, ...]`：

```text
structured_output_invalid
intent_mismatch
evidence_missing
evidence_duplicate
evidence_not_substring
orphan_evidence
```

feedback 不得包含自由文本、用户原文、旧 DTO、旧模型输出、旧 evidence 或异常字符串。`structured_output_invalid` 和 `intent_mismatch` 允许空字段路径。

模块 01 必须公开不可变 `EXTRACTABLE_REQUIREMENT_FIELD_PATHS = frozenset(RequirementFieldPath(path) for path in ATTR_TO_PATH.values())`。四种 evidence feedback 的每个 path 必须属于该集合；派生字段和未登记字段即使属于 `RequirementFieldPath` enum 也必须 validation fail。

### 7.6 evidence 验证结果与顺序

固定顺序：

```text
strict decode
→ 枚举本轮所有非 null 叶子字段
→ 校验字段路径已登记
→ 校验每个字段恰好一条非空 evidence
→ 校验 evidence 是当前 user_message 的逐字连续子串
→ 执行确定性规范化
→ 返回 EvidenceVerified 或 ExtractionRepairRequired
→ 确定性规范化并构造 RequirementPatch
```

`verify_evidence` 返回显式判别联合 `EvidenceVerified | ExtractionRepairRequired`，不得抛 `EvidenceVerificationError` 驱动正常 repair，不得通过 `str(exception)` 或前缀匹配识别错误。缺失、重复、非子串和孤儿 evidence 必须分别使用独立 code。

禁止从规范化值或默认值反向构造 evidence。示例：

```text
输入：预算五千左右
业务值：5000
is_approximate：true
evidence：五千左右
```

禁止使用 `5000`、`5000.0` 或“是”冒充原文。

### 7.7 repair 与非 repair 错误

每轮最多执行：

1. 一次正常需求提取。
2. 仅当 outcome 或 evidence verifier 返回上述六种 feedback 时，最多一次受约束修复。

因此领域语义调用总数固定不超过 2。网络层是否重试由共享模型基础设施决定，不在本模块重复拥有重试。

repair 必须重新使用原始 `ExtractionInput`，只额外发送安全 feedback；禁止发送失败 DTO 或 raw 输出。repair 后仍返回 `ExtractionRepairRequired` 时，领域返回 outcome 为 `RequirementExtractionFailed`、state 为完整输入前态 fresh copy 的 transition，不得应用部分 Patch。

模块 02 的配置、输入超限、网络、超时、认证、配额、content filter、输出截断和其他 runtime failure 都不是语义 repair。模块 03 使用 `RequirementExtractionRuntimeError(runtime_error: LLMRuntimeError)` 完整保留安全字段并抛出；模块 01 不捕获、不转换，后续 04/05/13 决定工作流行为。

## 8. 确定性规范化与校验

### 8.1 日期时间

- 相对日期以注入的 `session_date` 计算。
- “今天/明天/后天”使用日期加法，必须覆盖月末和年末。
- 相对日期结果标记 `pending_confirmation`。
- “下午三点左右”规范化为 `15:00` 且 `is_approximate=true`。
- 无法得到单一值时标记 `invalid/conflict`，禁止猜测。

### 8.2 人数、儿童和房间

- 人数为整数并满足第 6.4 节上限。
- 总人数必须大于 0 且不超过 100。
- 儿童人数大于 0 时，年龄数量必须完全一致。
- 儿童人数为 0 时，年龄必须为空且状态表示用户明确回答。
- 儿童人数为 0 时禁止生成儿童年龄追问。
- 房间数必须大于 0；明显无法容纳时产生领域 issue，不猜测酒店政策。
- 房型要求允许用户明确回答“无特殊要求”，规范化为固定枚举 `NO_PREFERENCE`，并视为已经回答。

### 8.3 预算（本期延期，以下数值语义仅供兼容类型参考）

- 币种固定人民币元，值必须大于 0。
- 范围固定为抵达目的地后到离开目的地前的当地总费用。
- 近似金额保存基准值和 `is_approximate=true`，不自行扩大区间。

### 8.4 POI 文本

用户指定地点只保存原始名称和 `needs_place_resolution=true`。本模块不得创建坐标、UID、地址或确认地点存在。

## 9. 增量合并

合并器是唯一允许产生新 `TravelRequirement` 的组件。固定规则：

1. Patch 未出现的字段保持原值。
2. 空 Patch 不改变任何业务值或元数据。
3. 新值只在旧值缺失或 `is_correction=true` 时覆盖。
4. 新旧值不同且纠正意图不明确时产生 conflict，不覆盖。
5. 显式清空必须由字段 presence 和当前轮 evidence 共同证明。
6. 依赖字段改变后重新计算派生字段。
7. 每个 Patch 叶子同步更新对应 metadata，禁止只更新 destination。
8. 合并函数不得原地修改输入对象。

关键字段在已确认后改变时，返回 outcome 为 `RequirementInvalidated` 的 transition；其下一状态保存修改结果并回到 `awaiting_confirmation`，等待新摘要确认。本模块只描述改变，不删除或回退下游状态。

该 invalidation 分支必须在同一次 service 返回中生成修改后的完整 `summary`。模块 05 展示此摘要后，可以把该 awaiting-confirmation state 与用户 `accept` 动作直接交给 `confirm()`；不得要求先制造另一轮 `NeedConfirmation`。

## 10. 缺失、冲突和统一追问

每次评估固定按以下顺序聚合：

1. 缺失硬字段。
2. 无效字段。
3. 冲突字段。
4. 相对日期待确认项。
5. 未选择的兴趣。
6. 未选择的节奏。
7. “由 Agent 决定”选项。

一轮最多产生一个 `NeedClarification`。message 由确定性 renderer 根据字段代码、固定标签和配置枚举生成，禁止调用模型自由生成问题。

## 11. 完整摘要与确认

仅当硬字段有效、冲突清零、相对日期已确认、兴趣和节奏已选择或明确交给 Agent 决定时，才能产生 `NeedConfirmation`。

摘要固定展示：

- 城市。
- 抵达、游玩、住宿和离开时间。
- 游玩天数、住宿晚数。
- 预算金额/约数不进入本期确认摘要。
- 人员、儿童年龄、房间和房型。
- 行动限制和饮食禁忌。
- 兴趣、节奏和主动提供的偏好。
- 派生同行场景。
- 尚存假设；正常必须为空。

确认规则：

- `RequirementConfirmation.revision_text` 在公共 DTO 层不设置 8,000 的 `max_length`；`accept` 时必须为 `None`，`revise` 时必须为非空字符串。
- `confirm(state, action)` 不接收也不检查当前 outcome。`accept` 的可执行前置条件只来自入参：`state.confirmation_status == "awaiting_confirmation"`、全部硬字段有效、`state.issues` 为空、没有缺失或冲突、`action.decision == "accept"`、`action.revision_text is None`。
- 满足上述条件时，下一状态固定为 `confirmation_status="confirmed"`，outcome 固定为 `ConfirmedTravelRequirement`；`NeedConfirmation` 和 `RequirementInvalidated` 产生的 awaiting-confirmation state 都可以进入此分支。
- `revise` 必须包含非空文本；service 在创建 extractor Session 或触发其他副作用前检查 `len(revision_text) <= 8_000`。超限返回输入 state 的 fresh copy 与 `RequirementExtractionFailed(input_too_long)`，extractor Session、EvidenceSink 和 Patch 应用均为 0；合法 revise 重新进入提取、验证、合并和摘要流程。
- 只有纯 `accept` 才产生 `ConfirmedTravelRequirement`。
- 模块 05 只对 `NeedConfirmation` 或 `RequirementInvalidated` 展示各自 outcome 的 `summary`，收到确认后传递 state/action，不复制模块 01 的完整性校验。
- 如何把确认动作路由到本模块，由上层 orchestrator 决定；模块 01 不感知 Graph 当前 outcome。
- `confirm` 无论 accept、revise 还是 confirmation-not-ready，均返回完整 transition；纯 accept 的下一状态为 `confirmed`，revise 和 not-ready 使用对应分支状态。

## 12. 人工模板

模板属于需求领域的确定性恢复输入，不定义暂停机制。

serializer 固定输出：

```text
模板版本：1
目的城市：<城市>
正式游玩日期：<YYYY-MM-DD> 至 <YYYY-MM-DD>
正式游玩天数：<正整数>
到达机场/车站、日期、时间：<枢纽>，<YYYY-MM-DD HH:MM>[，大约]
离开机场/车站、日期、时间：<枢纽>，<YYYY-MM-DD HH:MM>[，大约]
成人/儿童/老人：<整数>/<整数>/<整数>
儿童年龄：<中文顿号分隔；儿童为 0 时为空>
房间及房型：<正整数>间，<房型要求>
行动限制：<无或说明>
饮食禁忌：<无或说明>
兴趣：<枚举列表或由 Agent 决定>
节奏：<枚举或由 Agent 决定>
```

parser 固定行为：

- 全部键必须出现一次且顺序固定。
- 接受全角或半角冒号，serializer 只输出全角冒号。
- 未知、重复、缺失键和额外尾随文本逐字段报错。
- 到达/离开拆成 hub、datetime 和 approximate。
- 房间及房型拆成 room count 与 room type。
- parser 输出必须进入与模型 Patch 相同的规范化、合并和校验管线。
- `parse(serialize(requirement))` 必须在全部业务字段上等价。
- `process_manual_template` 的成功和逐字段错误分支都返回完整 transition，不得只返回 outcome。

## 13. 领域错误

错误代码固定为：

| code | 条件 | outcome |
|---|---|---|
| `input_too_long` | `process_turn` 的当前消息或 `confirm(..., decision="revise")` 的 revision text 超过 8,000 code point | 保留完整输入前态的 `RequirementExtractionFailed` transition；任何 extractor/evidence/merge 副作用前返回 |
| `value_out_of_range` | 字段超过尺寸或数值上限 | clarification 或 template field error |
| `strict_schema_error` | strict DTO 无法解码 | 最多修复一次后 failed |
| `intent_mismatch` | DTO 的 `is_correction` 与 intent 不一致 | 最多修复一次后 failed |
| `evidence_missing` | 非空叶子没有唯一 evidence | 最多修复一次后 failed |
| `evidence_duplicate` | 同一非空叶子有多条 evidence | 最多修复一次后 failed |
| `evidence_not_substring` | evidence 不在当前消息 | 最多修复一次后 failed |
| `orphan_evidence` | evidence 指向本轮空字段 | 最多修复一次后 failed |
| `normalization_error` | 无法确定性规范化 | clarification |
| `requirement_conflict` | 新旧值冲突且非明确纠正 | clarification |
| `manual_template_error` | 模板键或值无效 | 逐字段错误 |
| `confirmation_not_ready` | 未达到摘要确认门 | 保持 collecting |

错误对象不得包含完整原文、模型原始响应、Token 或内部堆栈。

五种 outcome 是可预期领域结果，必须包装进 transition。公开 DTO 的非法直接构造仍由 Pydantic 抛 `ValidationError`；模块 02/03 adapter 故障及其他运行时或 programmer exception 原样向上传播，不构造 transition，也不得伪装成 `RequirementExtractionFailed`。

## 14. 性能合同

- 每轮领域模型调用数不超过 2。
- 规范化、验证、缺失检测、冲突检测和 renderer 对登记字段做有界单次遍历。
- 禁止在确定性规则和 renderer 中调用模型或外部工具。
- 输入当前需求 JSON 必须包含所有业务字段和 null，使用 `ensure_ascii=False, sort_keys=True, indent=2`；不得压缩、截断或摘要，也不得包含历史消息和 raw evidence。
- 性能记录只包括领域模型调用数、输入字符数、模型耗时和确定性处理耗时。
- 全 Agent SLO、数据库性能和工具并发不属于本模块。

## 15. 规范 ID

| ID | L0/L1 行为 |
|---|---|
| `REQ-DLG-001` | 模型只返回当前轮 Patch，不直接修改完整需求 |
| `REQ-DLG-002` | strict 结构化输出持续失败不得降级到自由文本、宽松 JSON 或正则 |
| `REQ-DLG-003` | 每个非空提取叶子恰好一条当前轮原文 evidence |
| `REQ-DLG-004` | evidence 必须是当前消息的逐字连续子串 |
| `REQ-DLG-005` | 规范化后仍保留原文 evidence 语义，不反向伪造 |
| `REQ-DLG-006` | 业务值与字段 metadata 分离，字段路径唯一登记 |
| `REQ-DLG-007` | 相对日期使用注入时钟并等待确认 |
| `REQ-DLG-008` | 游玩日期、天数和住宿晚数按确定性公式校验 |
| `REQ-DLG-009` | 人数、儿童年龄和房间约束确定性校验 |
| `REQ-DLG-010` | 本期金额采集/规范化/校验/追问延期；既有字段和低层 helper 保留兼容，业务入口不产生金额 Patch |
| `REQ-DLG-011` | 兴趣和节奏来自固定枚举并支持由 Agent 决定 |
| `REQ-DLG-012` | 主动偏好只在用户提供时提取，不主动追问 |
| `REQ-DLG-013` | POI 只保留文本和待解析标记，不创建地理事实 |
| `REQ-DLG-014` | 合并只更新当前轮明确字段，空 Patch 无副作用 |
| `REQ-DLG-015` | 仅明确纠正覆盖旧值，模糊不一致进入 conflict |
| `REQ-DLG-016` | 显式清空与未提供通过 presence 和 evidence 区分 |
| `REQ-DLG-017` | 依赖字段改变时重算派生字段并产生 invalidation |
| `REQ-DLG-018` | 缺失、无效、冲突和待确认项在一条追问中展示 |
| `REQ-DLG-019` | 追问和摘要由确定性 renderer 生成 |
| `REQ-DLG-020` | 硬字段完整且无冲突后才能生成确认摘要 |
| `REQ-DLG-021` | 确认同时含修改时必须 revise 并重新确认 |
| `REQ-DLG-022` | state 达到 awaiting-confirmation 完整门槛且 action 为纯 accept 时才能产生 ConfirmedTravelRequirement；不依赖当前 outcome 类型 |
| `REQ-DLG-023` | 人工模板使用固定版本语法并全字段往返等价 |
| `REQ-DLG-024` | 模型调用、字段和列表遵守固定尺寸上限 |
| `REQ-DLG-025` | 每轮最多两次需求语义调用 |
| `REQ-DLG-026` | 本模块不得拥有 Graph、数据库、安全基础设施或产品入口 |
| `REQ-DLG-027` | 公开领域 outcome 不携带 raw evidence 或供应商原始响应 |
| `REQ-DLG-028` | 每个实际 Patch 叶子都有完整 metadata 更新 |
| `REQ-DLG-029` | 提取边界全异步且每轮创建独立 Session，不提供同步双轨 |
| `REQ-DLG-030` | 一个 Session 只持有一个 ModelOperation，最多一次 extract 和一次 repair |
| `REQ-DLG-031` | repair 只由六种固定反馈码触发，反馈无自由文本和失败输出 |
| `REQ-DLG-032` | evidence 验证使用判别结果，缺失、重复、非子串和孤儿分别编码 |
| `REQ-DLG-033` | turn_id 进入 Session 与安全事件但绝不进入模型输入 |
| `REQ-DLG-034` | 模块 02 非结构化 runtime 错误完整冒泡且不得语义 repair |
| `REQ-DLG-035` | RequirementIntent 唯一决定 is_correction，不一致必须 repair |
| `REQ-DLG-036` | 无可提取字段时返回合法空 Patch，不把对话控制文本写入 notes |

状态连续性合同使用独立编号，不复用已退役的旧 `REQ-DLG-038..059`：

| ID | L0/L1 行为 |
|---|---|
| `REQ-TRANS-001` | 三个公开 service 入口只返回 `RequirementDomainTransition`，不保留裸 outcome 双 API |
| `REQ-TRANS-002` | `transition.state` 是下一轮权威状态，outcome 只用于路由/展示，禁止反向构造 state |
| `REQ-TRANS-003` | 五个 outcome 与 state 按 §5.3 唯一规则保持一致；invalidation 还携带修改后的确定性完整摘要 |
| `REQ-TRANS-004` | extraction failure（含两个 8,001 字符入口）保留完整前态；invalidation 保存修改后状态并回到 awaiting_confirmation |
| `REQ-TRANS-005` | state/outcome 与输入、内部对象及彼此深层解除引用 |
| `REQ-TRANS-006` | 参数错误、adapter/runtime/programmer exception 不转换成 transition |
| `REQ-TRANS-007` | 当前 state 经 transition、工作流 state JSON 后可无损作为下一轮 `RequirementTurnInput.current` |

旧 v1.1 中涉及 orchestrator、持久化、安全、全局测试和 Agent E2E 的 `REQ-DLG-038..059` 不再属于 01 领域规范，不得被新实现引用。后续模块必须重新定义自己的规范 ID，不继承这些越界条款。

## 16. 领域验收场景

### 16.1 纯领域测试隔离门槛

模块 01 的“通过”只允许由纯领域测试证明，固定要求如下：

- 测试只收集 `tests/unit/requirements/` 和 `tests/contract/requirements/`；不得通过运行单个测试文件绕过目录内失败。
- 单元测试不得导入或启动 LLM Runtime、LangGraph、PostgreSQL、Application Service、CLI、FastAPI、百度地图、Trace 或安全基础设施。
- `tests/contract/requirements/` 只验证 01 定义的 `RequirementExtractor` Protocol、strict DTO 和 fake 一致性，不连接真实 DeepSeek；真实 adapter 合同测试属于 03。
- 异步 `RequirementExtractor` fake 必须记录 Session 数、extract/repair 次数、原始输入和 feedback；`EvidenceSink` fake 必须返回 receipt 且不得保存到数据库；`Clock` 必须固定日期。
- service 合同测试必须覆盖当前 state → transition → 模块 04 `TravelSessionState` JSON → 下一轮 `RequirementTurnInput.current` 的连续链路；该跨模块验证由模块 04 测试白名单拥有，模块 01 纯领域测试只固定 transition 本身。
- 禁止 `sys.path.insert/append`、绝对仓库路径、真实环境变量、真实网络和本地服务依赖。
- 旧 `test_m0_blocking.py` 与旧 `REQ-DLG-038..059` 不得继续位于 01 测试目录。领域行为使用 `REQ-DLG-001..036` 重写；Graph、checkpoint、persistence、entry 和 security 用例分别进入后续模块。
- 测试必须通过公共入口验证行为。只构造对象、只断言“不抛异常”、只调用私有 helper 或复制生产算法的用例不计入验收。
- 模块 01 的 line、branch、combined coverage 三项分别至少 95%；combined 固定为 `(covered_lines + covered_branches) / (num_statements + num_branches)`。`num_branches == 0` 时 branch 单项为 100%，combined 仍使用相同公式；只有 combined 分母为 0 才是配置错误并失败。任何 skip、xfail、收集错误、覆盖率写入失败或零测试均失败。

### 16.2 固定行为场景

最低必须覆盖：

1. “杭州，预算五千左右”得到城市和预算 Patch，evidence 分别来自原文，默认字段不产生 evidence。
2. 首轮提供城市，第二轮补充日期时城市保持不变。
3. 明确纠正城市可以覆盖；无纠正意图的不同城市产生 conflict。
4. 明天、后天在月末和年末正确跨月/跨年。
5. 儿童人数与年龄数量不一致时一次性追问。
6. 用户明确无儿童时空年龄列表表示已回答，不表示 missing。
7. 空 Patch 不改变需求和 metadata。
8. 合法 revise 永远重新生成摘要，不能产生 confirmed outcome；8,001 字符 revise 在任何 extractor/evidence/merge 副作用前返回保留 fresh 前态的 `input_too_long` transition。
9. 全字段人工模板 serialize 后 parse 完全等价。
10. 8,000 字符 `RequirementTurnInput.user_message` 正常进入 extractor；8,001 字符返回 `input_too_long` transition，Session/EvidenceSink/Patch 均为 0，state 与输入前态等值但不共享引用；空字符串仍由公共 DTO `ValidationError` 拒绝。
11. strict/evidence 失败最多修复一次，总领域调用不超过 2。
12. 公开 outcome 不包含 raw evidence、模型原始响应或基础设施对象。
13. 五个 outcome 分支分别验证 §5.3 的 state 一致性；非法配对直接 validation fail；`RequirementInvalidated` 精确字段顺序包含 `summary`。
14. extraction failure 的 state 与输入前态等值但不共享引用；invalidation 的 state 保存修改值、状态回到 awaiting confirmation，digest 匹配，且 `summary == render_confirmation_summary(transition.state.requirement)`。
15. 用双向 mutation canary 证明 transition.state 与 outcome 的 requirement/metadata/issues 彼此深层解除引用，且都不引用调用输入。
16. 参数 `ValidationError` 与 runtime/programmer exception 继续抛出，不产生伪 transition；`RequirementTurnInput` 和 revise action 的空字符串仍属于 DTO 参数错误。
17. `NeedConfirmation` 与 `RequirementInvalidated` 的 awaiting-confirmation state 都可在同一 state-based 门槛下 accept；`confirm()` 不接收 outcome，也不要求调用方伪造 outcome。

### 16.3 状态连续性维护验收与收尾边界

模块 01/04 状态连续性代码修订必须按配套 Vibe 的 `RSC-T00..RSC-T06` 执行：先完成模块 01 transition 与模块 03 直接消费合同，再开始模块 04 state v2 与连续性/public/import boundary。阶段 A 开始时允许 transition 尚不存在、模块 01 返回裸 outcome、模块 04 是 v1 九字段旧基线；阶段 B 只能在模块 01 包根公开 State/Outcome/Transition 且阶段 A 测试通过后开始。不得重做或修改模块 04 的 Artifact、Budget、Event。

正式验收只能使用从当前源码重新构建的 Docker runtime/test 镜像和新建安全容器；宿主机 Python 不作为验收证据。容器必须 `--rm --network none --read-only`，为 `/tmp` 提供 tmpfs，并完成 Python/Pydantic、模块 01 定向、模块 03 直接消费、模块 04 定向、两模块三项 coverage、compileall、模块 01～04 全 unit/contract 回归和无残留容器检查。

代码与 Docker Gate 全部通过后必须执行 `MODULE-CLOSEOUT`：按实际结果检查并同步 `README.md`、`docs/IMPLEMENTATION_STATUS.md`、`Dockerfile`，随后重新构建镜像并重跑 runtime 默认 CMD 和完整回归。代码实施与 closeout 期间，本权威规范和两份模块 01/04 Vibe/权威设计保持只读；模块 05 继续暂停。实施模型不得 stage 或 commit；独立审查通过后也只有获得用户新的明确授权才可提交。

## 17. NOT in scope

- 02 的 LLM Runtime 和 03 的真实 RequirementExtractor 适配。
- 04、05、13 的工作流状态、Graph 骨架、完整编排与暂停恢复。
- 04 的 Demo artifact/Repository 端口与进程内参考实现、10 的 PostgreSQL artifact/evidence 持久实现、11 的 checkpoint/执行账本/恢复，以及 13 的装配。
- 19、20 的加密、脱敏、删除、Trace 和审计实现。
- 06–09、12、16 的地图、酒店、研究、行程、输出和局部重排，以及等待可信价格来源批准的旅行费用判定。
- 14 的产品入口与完整 Agent 流程。
- 15、22、23 的真实依赖 E2E、恢复测试、质量评测平台与全项目质量门槛。
- 对旧原型状态、旧 Graph、旧数据库或旧测试提供兼容层。

这些内容不是被取消，而是必须按 [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) 的职责与依赖，在对应模块开始时重新编写规范，不能从旧 01 文档复制为既定设计。

## 枢纽澄清维护增量（2026-10-07，用户已批准）

本节补充 §6.5、§7、合并与确认规则。抵达和离开均指目的城市的机场/火车站，不表示出发城市。两个端点的 hub_name、hub_type、local_datetime 都必须有效才能进入确认。需求/历史 checkpoint DTO 继续允许未完成值，不在 DTO 反序列化时拒绝历史缺项；新门槛由领域合并、追问和 confirm 同时执行。

公开纯函数 `validate_requirement_hubs(requirement: TravelRequirement) -> tuple[RequirementIssue, ...]` 只检查这六个字段。有限 code 为 hub_name_missing、hub_name_not_specific、hub_type_missing、hub_type_mismatch、hub_time_missing；message 是固定中文，不拼用户值。具体名称必须包含明确机场或铁路站标志；仅城市名、简称/别名或不能唯一判别类型的名称要求补全全称，不猜地域或默认类型。明确机场/站名称沿确定性规则推导类型；名称推导类型和显式类型冲突时同时要求核对名称、类型。真实地点和城市归属仍由下游地图事实核验，不把字符串检查当作地理验证。

missing_fields 纳入两个 hub_type；detect_conflicts 合并上述公开检查。城市名/含糊名称与类型冲突产生 field_path Issue，已填写也必须追问。SUPPLEMENT 可替换当前纯函数判定有问题的同一端点名称/类型，但只能来自当前轮有效 evidence 的明确回答；推导类型可以复用该名称 evidence。有效既有字段仍沿 §merge 非 correction 不覆盖规则，null/空值不清除旧值。修正后旧端点 Issue/metadata Issue 必须清除并重新校验；未解决其他字段仍追问。

两端所有必要信息完整且无问题后才生成 NeedConfirmation；接受旧的 awaiting_confirmation 时也重新执行门槛。未满足时返回 NeedClarification，不调用酒店、研究或规划模型。新增端点问题的中文标签明确枢纽类型，confirmation 摘要保留名称/类型/时间及人工确认。

验收 HUB-01..08：起点城市误填为离开枢纽；单侧/双侧类型缺项；明确机场/车站推导；含糊全称/名称类型矛盾；补充修正无循环；有效字段禁止静默改写；缺项确认拒绝且历史 DTO 仍可读；完整确认供 M09 输入检查通过。执行见配套规范“枢纽澄清维护执行入口”。


## 2026-10-11 批准修订：研究合同修复

状态：用户已批准并授权实施。R01：显式兴趣允许1～4个不同枚举，不自动添项；None/空且未授权Agent属于待补充，显式兴趣与agent_decides同时存在属于冲突。collect、ready/confirm、M08入口使用M01拥有的相同纯判断；DTO仍可读取未完成历史值，不在反序列化时拒绝。缺失/冲突回NeedClarification并展示选项；单兴趣合法。其他既有日期、枢纽、人员、节奏门槛保持，并以confirmed→下游合同矩阵防漂移。

共同依据与验收见 [M08 R08](08_candidate_research_loop.md)及[执行规范 R1～R6](08_candidate_research_loop_vibe_coding.md)，覆盖冲突旧条款。

R01修正闭环：仅对当前纯规则判定无效的兴趣/节奏组，SUPPLEMENT允许当前轮已验证证据支持的修正；选择明确值清除同组Agent标志，选择Agent清除同组无效显式值，同时明确给出双方则仍追问冲突。有效既有选择继续要求显式纠正，不因普通补充静默覆盖。元数据同步清理已解除问题；旧awaiting_confirmation重验完整性。M09使用相同纯规则，包含空兴趣列表+Agent与节奏双选反例。
