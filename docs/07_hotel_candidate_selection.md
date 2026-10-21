# Module 07：酒店候选、百度 UID 去重与人工选择

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

文档状态：设计候选，待用户批准

## 1. 文档职责、权威层级与冲突处理

本文冻结 Module 07 的产品边界、公共合同、Artifact、执行预算、确定性候选搜索、酒店资格判定、人工选择和离线验收标准。实施模型不得以测试先行、现有代码便利或未来 Agent 设想覆盖本文。

权威层级从高到低为：

1. <code>docs/00_agent_master_design.md</code>；
2. <code>docs/IMPLEMENTATION_ROADMAP.md</code>；
3. <code>docs/SHARED_IMPLEMENTATION_RULES.md</code>；
4. Module 01、04、06 已批准设计及其包根公共合同；
5. 本文；
6. <code>docs/07_hotel_candidate_selection_vibe_coding.md</code>；
7. 实现和测试。

发现冲突时必须停止，不得修改上位文档迁就 Module 07。代码与已批准设计不一致时，将其视为实现问题。本文批准前仍是候选，不授予实施、commit、merge、push、PR 或部署权限。

## 2. 目标与产品边界

### 2.1 目标

Module 07 只完成以下闭环：

1. 只读消费 Module 01 的当前 <code>ConfirmedTravelRequirement</code>；
2. 在 <code>WorkflowPhase.HOTEL_SELECTION</code> 下按确定性计划执行最多三次百度 Place Search；
3. 复用 Module 04 的 Artifact、<code>OperationId</code> 和执行预算合同，保证重放不重复调用地图；
4. 从百度 typed 结果中筛选具有明确住宿类别证据的候选；
5. 以百度 UID 为唯一供应商身份主键去重；
6. 生成 3～5 家候选或固定的候选不足结果；
7. 接收用户对当前候选集的选择或拒绝全部决定；
8. 防止候选集外 UID、过期选择、覆盖式改选和 Artifact 冲突；
9. 向 Module 13 提供稳定 typed outcome，不直接决定 Graph 路由或交付终态。

### 2.2 精确产品声明

“地图已核验酒店候选”只表示：

- 地点来自 Module 06 的百度 Place Search typed success；
- 具有非空 UID、名称、地址和有效 BD-09LL 坐标；
- 百度 category 中存在本文冻结的明确住宿类别 token；
- 其身份字段通过 Module 07 的 UID 一致性检查。

它不表示：

- 实时余房；
- 可订房型；
- 成交价格或费用可信度；
- 取消政策；
- 营业状态；
- 电话、评分或营业时间；
- 无障碍、亲子、宠物、景观、安静等偏好已经满足；
- 供应商顺序是质量排名；
- 系统找到了“最佳酒店”；
- 已完成预订。

用户选择的酒店只是后续旅行规划的地图锚点和人工确认事实。

## 3. 非目标

Module 07 不负责：

- LLM、DeepSeek、Prompt、模型 Profile、tool calling 或 ReAct；
- 新增 <code>ModelRole.HOTEL_SELECTION</code>；
- Place Details、Geocoding 或其他隐藏地图调用；
- MCP session、工具发现、Gateway 生命周期、技术重试或原始响应解析；
- LangGraph 节点、<code>interrupt()</code>、<code>Command(resume=...)</code>、checkpoint 或 thread ID；
- 完整 Graph、跨模块路由、delivery status、terminal mapping 或 <code>demo_ready</code>；
- CLI、Application Service 或展示交互实现；
- 酒店价格、余房、预订、费用可信度或支付；
- 景点、餐厅、行程、路线、预算评估或输出格式化；
- 修改 Module 01 的需求 DTO、状态或确认摘要；
- Provider Discovery、独立 live gate、Secret 或网络配置；
- Module 10/11 的 Artifact 存储、迁移或跨进程恢复，以及 Module 19 的 TTL／删除。

## 4. 依赖、导入与所有权

### 4.1 唯一直接依赖

| 模块 | 只允许消费 |
|---|---|
| 01 | <code>ConfirmedTravelRequirement</code> 及其公开嵌套需求 DTO |
| 04 | <code>WorkflowPhase</code>、<code>OperationId</code>、Artifact Registry/Repository、执行预算 DTO/端口及 typed error |
| 06 | <code>BaiduMapAdapter</code>、<code>PlaceSearchRequest</code>、<code>CitySearchArea</code>、Place Search typed outcome、公开坐标/错误枚举和异常 |

生产代码只能从 <code>app.requirements</code>、<code>app.workflow_contracts</code> 和 <code>app.baidu_map</code> 包根导入这些合同。

### 4.2 禁止依赖

禁止导入：

- <code>app.llm_runtime</code>、LangChain DeepSeek 或任意模型 SDK；
- <code>app.requirement_subgraph</code> 或 Module 05 私有实现；
- <code>app.baidu_map.models.*</code>、<code>app.baidu_map._*</code>；
- MCP SDK、ClientSession、Tool schema、CallToolResult 或 Gateway 私有类；
- Module 08、09、10、11、12、13、14 的实现；
- 任意 raw provider payload、Secret loader 或网络 client。

### 4.3 相邻模块所有权

| 能力 | 所有者 | Module 07 边界 |
|---|---|---|
| 需求收集与确认 | 01/03/05 | 只接受当前 confirmed outcome |
| 地图 typed 单次调用 | 06 | 07 负责业务预算、Artifact 和筛选 |
| 景点/餐厅候选研究 | 08 | 不复用其 ReAct 或候选 Schema |
| 行程规划 | 09 | 只消费已选酒店事实 |
| 行程与路线确定性校验 | 09 | 不在 07 查询或声明 |
| Artifact／执行状态持久化 | 10/11 | 只通过 Module 04 公共端口接入，不改变 07 业务语义 |
| 费用标签与格式化 | 12 | 不在 07 实现；酒店选择不构成价格证据 |
| Graph 与人工中断装配 | 13 | 07 只提供选择请求和 outcome |
| Application Service、应用 API 和前端网页 | 14 | 负责把候选展示为界面或序号 |

## 5. 推荐生产包结构

<pre><code>app/hotel_selection/
├── __init__.py
├── artifacts.py
├── errors.py
├── models.py
├── protocols.py
├── query_policy.py
└── service.py</code></pre>

- <code>models.py</code>：公共输入、候选、决定和 outcome。
- <code>artifacts.py</code>：三类 Artifact 正文与注册常量。
- <code>errors.py</code>：Module 07 自有安全异常。
- <code>protocols.py</code>：真实可用的服务 Protocol，不含空 Agent 接口。
- <code>query_policy.py</code>：确定性查询、类别资格和 UID 身份纯函数。
- <code>service.py</code>：Artifact、预算、地图调用和人工决定编排。

不得创建 <code>agent.py</code>、<code>prompts/</code>、模型 adapter、第二套 Graph 或第二套地图 Gateway。

## 6. 包根唯一公共 API

<code>app.hotel_selection.__all__</code> 精确导出：

<pre><code>HOTEL_ARTIFACT_REGISTRATIONS
HOTEL_PLACE_CALL_BUDGET_KEY
HOTEL_SELECTION_SCHEMA_VERSION
HotelCandidate
HotelCandidateDiscoveryFailed
HotelCandidateDiscoveryFailureCode
HotelCandidateDiscoveryOutcome
HotelCandidateOperationIds
HotelCandidateSelectionPort
HotelCandidateSelectionService
HotelCandidatesInsufficient
HotelCandidatesInsufficientRecord
HotelCandidatesReady
HotelCandidatesReadyRecord
HotelCoordinate
HotelDiscoveryArtifact
HotelDiscoveryResult
HotelInputError
HotelMapFailureCode
HotelModuleError
HotelPlaceEvidence
HotelPlaceSearchArtifact
HotelReplayError
HotelCandidateIdentityConflictError
HotelSearchEvidence
HotelSearchFailedEvidence
HotelSearchNoResultEvidence
HotelSearchSuccessEvidence
HotelSelectCommand
HotelDeclineCommand
HotelSelectedDecision
HotelDeclinedDecision
HotelSelectionArtifact
HotelSelectionCommand
HotelSelectionDecision
HotelSelectionDeclined
HotelSelectionOutcome
HotelSelectionRejected
HotelSelectionRejectionCode
HotelSelectionRequest
HotelSelected</code></pre>

禁止包根重导出 Module 01/04/06 符号、私有 helper、类别 token 集、hash helper 或测试 fake。

## 7. 共同 DTO 规则

Module 07 自有 Pydantic DTO 一律：

<pre><code>ConfigDict(strict=True, frozen=True, extra="forbid")</code></pre>

并满足：

- 公共字段顺序按本文表格固定；
- <code>schema_version</code> 精确为 <code>Literal["1.0"]</code>；
- 禁止 alias、computed field、自定义 serializer 和宽松 coercion；
- collection 使用 tuple，保持顺序；
- nullable 字段显式传 <code>None</code>；
- 输入对象在服务边界 strict 复验并 fresh-copy；
- Artifact 可达模型全部字段 required；
- validator 只能做纯断言，不读取环境、时间、随机数、网络或全局可变状态；
- 自有 error 的 <code>str/repr/args</code> 只含固定 code，不含 UID、查询、目的地、偏好、坐标、供应商正文或底层异常。

默认值规则精确为：

- Artifact 可达模型的全部字段（包括 schema version 和 discriminator）required、无默认；
- <code>HotelCandidateOperationIds</code>、<code>HotelSelectionRequest</code> 的 schema version 默认 <code>"1.0"</code>，其余字段 required；
- command 和公共 outcome 的 discriminator 使用对应 Literal 默认值；
- <code>HotelCandidatesReady.disclaimer_code</code> 默认 <code>"map_identity_only"</code>；
- 除明确 nullable 字段外，不提供 <code>None</code> 默认；
- 不使用 <code>default_factory</code>。

<code>HOTEL_SELECTION_SCHEMA_VERSION = "1.0"</code>。

三个公共 Enum 的 Python member 与 value 精确为：

<pre><code>class HotelMapFailureCode(str, Enum):
    REQUEST_ERROR = "request_error"
    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    TIMEOUT_ERROR = "timeout_error"
    TRANSPORT_ERROR = "transport_error"
    SESSION_ERROR = "session_error"
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_SCHEMA_ERROR = "tool_schema_error"
    PROTOCOL_ERROR = "protocol_error"
    RESPONSE_ERROR = "response_error"
    CONCURRENCY_ERROR = "concurrency_error"
    UNVERIFIED_RESULT = "unverified_result"

class HotelCandidateDiscoveryFailureCode(str, Enum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    MAP_CALL_FAILED = "map_call_failed"

class HotelSelectionRejectionCode(str, Enum):
    CANDIDATE_UID_NOT_FOUND = "candidate_uid_not_found"
    CANDIDATE_SET_NOT_READY = "candidate_set_not_ready"
    STALE_CANDIDATE_SET = "stale_candidate_set"
    DECISION_CONFLICT = "decision_conflict"</code></pre>

## 8. 公共事实 DTO

### 8.1 HotelCoordinate

| 顺序 | 字段 | 类型 | 约束 |
|---:|---|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]</code> | required |
| 2 | <code>coordinate_system</code> | <code>Literal["bd09ll"]</code> | required |
| 3 | <code>latitude</code> | <code>float</code> | -90..90，finite |
| 4 | <code>longitude</code> | <code>float</code> | -180..180，finite |

这是 Module 06 <code>BaiduCoordinate</code> 的业务 Artifact 投影，不是第二套坐标转换模型。值只能逐字段复制，禁止计算、纠偏或坐标系转换。

### 8.2 HotelPlaceEvidence

| 顺序 | 字段 | 类型 | 约束 |
|---:|---|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]</code> | required |
| 2 | <code>uid</code> | <code>str</code> | 1..512，strip 后非空 |
| 3 | <code>name</code> | <code>str</code> | 1..512，strip 后非空 |
| 4 | <code>address</code> | <code>str</code> | 1..512，strip 后非空 |
| 5 | <code>coordinate</code> | <code>HotelCoordinate</code> | required |
| 6 | <code>category</code> | <code>str | None</code> | None 或 1..512 |

该 DTO 只投影 <code>PlaceSummary</code> 字段，不含 rating、telephone、business hours、brand、price、availability 或 policy。

### 8.3 HotelCandidate

| 顺序 | 字段 | 类型 | 约束 |
|---:|---|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]</code> | required |
| 2 | <code>uid</code> | <code>str</code> | 与证据完全一致 |
| 3 | <code>name</code> | <code>str</code> | 与证据完全一致 |
| 4 | <code>address</code> | <code>str</code> | 与证据完全一致 |
| 5 | <code>coordinate</code> | <code>HotelCoordinate</code> | 与证据完全一致 |
| 6 | <code>category</code> | <code>str</code> | 必须通过住宿类别资格 |

候选字段只能来自 <code>PlaceSummary</code> 的 UID、名称、地址、坐标和类别。不得增加
source ordinal、score、rank、matched_preferences 或 recommendation 字段。tuple 中的位置只是
全局首次合格出现顺序。

## 9. Artifact 合同

本节所有 Artifact 可达字段均 required、无默认；表格或清单没有重复写“required”时仍服从此规则。

### 9.1 Artifact 类型与注册

| artifact_type | version | 正文模型 | Operation 含义 |
|---|---|---|---|
| <code>hotel.place_search</code> | <code>1.0</code> | <code>HotelPlaceSearchArtifact</code> | 一次逻辑 Place Search 的安全结果 |
| <code>hotel.candidate_set</code> | <code>1.0</code> | <code>HotelDiscoveryArtifact</code> | 本次候选发现的 ready/insufficient 最终结果 |
| <code>hotel.selection</code> | <code>1.0</code> | <code>HotelSelectionArtifact</code> | 对精确候选集的一次不可变人工决定 |

<code>HOTEL_ARTIFACT_REGISTRATIONS</code> 是按上表顺序组成的
<code>tuple[ArtifactSchemaRegistration, ...]</code>。Module 13 将它与其他模块注册项组合成唯一 Registry。Module 07 不创建全局 Registry singleton。

### 9.2 搜索证据

<code>HotelSearchEvidence</code> 是以 <code>outcome_type</code> 为 discriminator 的封闭 union：

- <code>HotelSearchSuccessEvidence</code>
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>outcome_type: Literal["success"]</code>，required；
  3. <code>places: tuple[HotelPlaceEvidence, ...]</code>，required，1..10。
- <code>HotelSearchNoResultEvidence</code>
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>outcome_type: Literal["no_result"]</code>，required。
- <code>HotelSearchFailedEvidence</code>
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>outcome_type: Literal["failed"]</code>，required；
  3. <code>failure_code: HotelMapFailureCode</code>，required。

<code>HotelMapFailureCode</code> 包含 Module 06 公共异常的一一映射，以及业务层拒绝未验证结果的固定 code；只保存安全枚举：

<pre><code>request_error
configuration_error
authentication_error
rate_limit_error
timeout_error
transport_error
session_error
tool_unavailable
tool_schema_error
protocol_error
response_error
concurrency_error
unverified_result</code></pre>

<code>HotelPlaceSearchArtifact</code> 字段顺序：

| 顺序 | 字段 | 类型 |
|---:|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]</code>，required |
| 2 | <code>search_ordinal</code> | <code>int</code>，1..3 |
| 3 | <code>request_hash</code> | <code>ContentHash</code> |
| 4 | <code>source</code> | <code>Literal["baidu_maps_mcp"]</code> |
| 5 | <code>verified</code> | <code>bool</code>；success/no_result 必须为 true，failed 必须为 false |
| 6 | <code>retrieved_at</code> | UTC aware <code>datetime</code> |
| 7 | <code>evidence</code> | <code>HotelSearchEvidence</code> |

success/no-result 逐字段复制 Module 06 provenance，且只有 <code>verified=True</code> 才能成为该两种 evidence；<code>verified=False</code> 必须投影为 <code>HotelSearchFailedEvidence(unverified_result)</code>。typed 技术异常的失败证据固定 <code>source="baidu_maps_mcp"</code>、<code>verified=False</code>，<code>retrieved_at</code> 使用 Module 07 注入的 UTC clock；不得使用异常正文。取消不转成失败证据。

### 9.3 候选发现 Artifact

<code>HotelDiscoveryResult</code> 是：

- <code>HotelCandidatesReadyRecord</code>：
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>result_type: Literal["ready"]</code>，required；
  3. <code>candidates: tuple[HotelCandidate, ...]</code>，required，长度 3..5。
- <code>HotelCandidatesInsufficientRecord</code>：
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>result_type: Literal["insufficient"]</code>，required；
  3. <code>candidates: tuple[HotelCandidate, ...]</code>，required，长度 0..2。

两者均按首次合格出现顺序保存候选。

<code>HotelDiscoveryArtifact</code> 字段顺序：

| 顺序 | 字段 | 类型 |
|---:|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]</code>，required |
| 2 | <code>confirmation_summary_digest</code> | 非空 <code>str</code> |
| 3 | <code>query_plan_hash</code> | <code>ContentHash</code> |
| 4 | <code>search_operation_ids</code> | <code>tuple[OperationId, ...]</code>，精确 3 |
| 5 | <code>search_refs</code> | <code>tuple[ArtifactRef, ...]</code>，1..3 |
| 6 | <code>result</code> | <code>HotelDiscoveryResult</code> |

ready 可以在第 1、第 2 或第 3 次搜索后产生；insufficient 只能在三次搜索都完成后产生。
模型 validator 必须纯断言：三个 search OperationId 互不相同且彼此 run/phase 一致；
search refs 不重复、全部为 <code>hotel.place_search/1.0</code>，长度不超过三个
OperationId；每个 ref 的 operation_id 精确等于同位置的 search OperationId；候选 UID
唯一。

service 在 put 前把三个 search OperationId 和 refs 与 candidate-set
<code>ArtifactWriteRequest.operation_id</code> 比较，在 get 后与
<code>ArtifactReadResult.ref.operation_id</code> 比较；candidate-set OperationId 必须同
run/phase 且不同于三个 search OperationId。service 只按实际 ordinal 追加 refs，并在读取
每个 search Artifact 时验证其 <code>search_ordinal</code> 与 tuple 位置一致。

### 9.4 人工决定 Artifact

<code>HotelSelectionDecision</code> 是：

- <code>HotelSelectedDecision</code>：
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>decision_type: Literal["selected"]</code>，required；
  3. <code>selected_candidate: HotelCandidate</code>，required。
- <code>HotelDeclinedDecision</code>：
  1. <code>schema_version: Literal["1.0"]</code>，required；
  2. <code>decision_type: Literal["declined"]</code>，required；
  3. 不含 UID 或自由文本理由。

<code>HotelSelectionArtifact</code> 字段顺序：

| 顺序 | 字段 | 类型 |
|---:|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]</code>，required |
| 2 | <code>candidate_set_ref</code> | <code>ArtifactRef</code>，必须指向 <code>hotel.candidate_set/1.0</code> ready |
| 3 | <code>decision</code> | <code>HotelSelectionDecision</code> |

decline 会生成不可变的选择决定 Artifact，但不会生成或伪造 selected candidate。

### 9.5 ArtifactWriteRequest 构造

三类 Artifact 统一使用私有 helper；不得把 <code>mode="python"</code> 的 datetime、UUID、
Enum 或 tuple 直接放入 Module 04 content：

<pre><code>content = artifact.model_dump(
    mode="json",
    exclude_none=False,
    by_alias=False,
    exclude_computed_fields=True,
)
if type(content) is not dict:
    raise HotelInputError()
request = ArtifactWriteRequest(
    operation_id=operation_id,
    artifact_type=artifact_type,
    schema_version="1.0",
    content=content,
)</code></pre>

search、candidate-set 和 selection 三条 put 路径都必须使用该 helper。测试分别执行
Registry <code>prepare</code>、Repository <code>put/get</code> 和 canonical round-trip。

## 10. 输入与 OperationId

### 10.1 候选发现前置条件

<code>discover_candidates()</code> 必须接收当前 <code>ConfirmedTravelRequirement</code>，并要求：

- exact type，随后 strict fresh-copy；
- <code>outcome_type="confirmed"</code>、<code>schema_version="1.0"</code>；
- <code>destination</code> 非空；
- <code>confirmation_summary_digest</code> 非空；
- Module 01 已保证 issues 为空及确认状态一致；
- 调用方不得传旧 confirmation；需求修订后 Module 13 必须重新路由并提供新 OperationId。

整个 confirmed DTO（包括日期、人数、房间、行动约束和酒店偏好）都经过 strict
fresh-copy；<code>confirmation_summary_digest</code> 绑定该次完整确认版本。destination
用于 city area，hotel_preferences 只用于第一条 query。其余字段不得被提升为百度已验证
筛选条件；人工界面由 Module 13/14 把 Module 01 的确认摘要与候选并列展示。任何完整需求
变化都必须产生新的 confirmed digest 和新的 discovery OperationId，不能复用旧候选 Artifact。

### 10.2 HotelCandidateOperationIds

字段顺序：

| 顺序 | 字段 | 类型 | 约束 |
|---:|---|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]="1.0"</code> | 固定默认 |
| 2 | <code>search_operation_ids</code> | <code>tuple[OperationId, ...]</code> | 精确 3 个 |
| 3 | <code>candidate_set_operation_id</code> | <code>OperationId</code> | required |

所有 OperationId 必须：

- run_id 相同；
- phase 精确为 <code>HOTEL_SELECTION</code>；
- logical_step 非空且四者互不相同；
- 由 Module 13 构造并注入，Module 07 不生成 UUID；
- 同一逻辑动作重放使用同一 OperationId；
- 新一轮候选发现或改选使用新的 logical step、流程版本或 run，不复用旧键。

### 10.3 人工选择输入

<code>HotelSelectionCommand</code> 是：

- <code>HotelSelectCommand</code>：
  1. <code>schema_version: Literal["1.0"]="1.0"</code>；
  2. <code>action: Literal["select"]="select"</code>；
  3. <code>selected_uid: str</code>，required，1..512，strip 后非空。
- <code>HotelDeclineCommand</code>：
  1. <code>schema_version: Literal["1.0"]="1.0"</code>；
  2. <code>action: Literal["decline"]="decline"</code>；
  3. 没有 UID 或自由文本字段。

<code>HotelSelectionRequest</code> 字段顺序：

| 顺序 | 字段 | 类型 |
|---:|---|---|
| 1 | <code>schema_version</code> | <code>Literal["1.0"]="1.0"</code> |
| 2 | <code>selection_operation_id</code> | <code>OperationId</code> |
| 3 | <code>candidate_set_ref</code> | <code>ArtifactRef</code> |
| 4 | <code>command</code> | <code>HotelSelectionCommand</code> |

服务还接收由 Module 13 持有的 <code>active_candidate_set_ref</code>。两者七字段必须完全相等；只有 artifact ID 相同不够。

## 11. 执行预算

<code>HOTEL_PLACE_CALL_BUDGET_KEY</code> 精确为：

<pre><code>ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="hotel.place_call",
    phase=WorkflowPhase.HOTEL_SELECTION,
)</code></pre>

Demo limit 精确为 3，由 Module 13 放入 <code>ExecutionBudgetPlan</code>；Module 07 不动态建 plan、不提额、不退款。

只有即将执行真实 <code>BaiduMapAdapter.search_places()</code> 的 absent search operation 消费 amount=1。以下不消费：

- candidate-set lookup/get；
- 已存在 search Artifact 的 replay；
- 类别过滤和 UID 去重；
- 人工选择；
- selection Artifact；
- future Agent 设想。

成功、no-result、typed 地图失败均已消耗，不退款。三次查询逻辑顺序执行，不并发预取；Module 06 的并发 3 不是本预算。

## 12. 确定性查询计划

### 12.1 构造

从 confirmed requirement 读取：

- <code>region = requirement.destination</code>；
- <code>hotel_preferences = requirement.preferences.hotel_preferences</code>。

非语义清理固定为：对 destination 和非 None preference 各执行一次 <code>strip()</code>，
不折叠内部空白、不做 Unicode normalization、不改写标点；strip 后为空是
<code>HotelInputError</code>。先构造完整 typed <code>PlaceSearchRequest</code>，再以其
fresh <code>request.query</code> 做顺序保持的精确字符串去重。

按以下顺序生成字符串：

1. hotel_preferences 非 None 时：<code>"{hotel_preferences} 酒店"</code>；
2. <code>"酒店"</code>；
3. <code>"宾馆"</code>；
4. <code>"住宿"</code>。

保持顺序，按上述最终 typed query 去重，取前三条并要求结果精确为三条。不得分词、
翻译、同义改写、关键词评分或地理解析。Module 01 的长度约束必须保证构造后 query
不超过 Module 06 的 512 字符上限；超限是 <code>HotelInputError</code>，不得静默截断。

每条请求固定：

<pre><code>PlaceSearchRequest(
    schema_version="1.0",
    query=query,
    tag=None,
    area=CitySearchArea(
        schema_version="1.0",
        area_type=PlaceSearchAreaType.CITY,
        region=destination,
    ),
    result_limit=10,
)</code></pre>

实现只从 <code>app.baidu_map</code> 包根导入所需符号。

### 12.2 request/query plan hash

不得把 query、hotel_preferences 或完整确认摘要写入 Artifact、错误、日志或报告。

对每个 typed request 的
<code>model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)</code>
JSON-safe tree，以及由这些 tree 组成的有序 query plan tree，使用本模块私有确定性 JSON 编码：

<pre><code>json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")</code></pre>

分别生成 <code>request_hash</code> 和 <code>query_plan_hash</code>：

<pre><code>"sha256:" + sha256(encoded).hexdigest()</code></pre>

hash 只用于重放请求一致性，不替代 Module 04 的 Artifact content hash，不从包根导出。

## 13. 酒店资格与 UID 去重

### 13.1 住宿类别资格

category 唯一切分表达式为
<code>re.split(r"[;；,，/|>]+", category)</code>。分隔符精确为 U+003B、U+FF1B、
U+002C、U+FF0C、U+002F、U+007C、U+003E；逐 token strip 并丢弃空 token。U+FF0F
全角斜线、反斜线及其他字符不是分隔符。不做模糊匹配或 substring 推断。

冻结允许 token：

<pre><code>住宿服务
酒店
星级酒店
快捷酒店
经济型酒店
宾馆酒店
宾馆
旅馆
客栈
民宿
度假村
公寓式酒店
青年旅舍</code></pre>

至少一个 token 精确命中才合格。category 为 None、空、无命中或只有餐饮、购物、景点、企业等非住宿 token 时排除。名称包含“酒店”不能覆盖 category 缺失或非住宿类别。

### 13.2 身份字段

合格 occurrence 必须具有：

- 非空 UID；
- 非空名称；
- 非空地址；
- 有效 BD-09LL 坐标；
- 合格 category。

Module 06 已在 typed 边界验证前三个文本和坐标；Module 07 fresh-copy 后再次执行自身资格断言。

### 13.3 UID 去重

- UID 是百度供应商命名空间内的唯一身份键；
- 以首次合格 occurrence 决定顺序和 candidate category；
- 相同 UID 且 name、address、latitude、longitude 完全一致：正常重复，忽略后续 occurrence；
- 比较前只依赖 Module 06 已完成的 strip，不做大小写、Unicode、地址、坐标近似或名称模糊规范化；
- 相同 UID 的 name、address、latitude 或 longitude 任一不同：立即抛 <code>HotelCandidateIdentityConflictError</code>；
- 不同 UID 即使文本相似也必须保留为不同候选；
- 每个返回 occurrence 都先登记/比较 UID、name、address 和 coordinate，再判断 category；
  category 不合格只阻止生成候选，不能绕过 UID 身份冲突检查；
- 处理一份已返回 success 的全部最多 10 条 evidence，以便发现与已见 UID 的身份冲突；仅候选输出截断为前 5。

## 14. 候选发现状态机

固定顺序：

1. strict 复验 confirmed requirement 和 <code>HotelCandidateOperationIds</code>；
2. 构造三条确定性请求及 query plan hash；
3. lookup candidate-set OperationId；
4. found：get <code>HotelDiscoveryArtifact</code>，比较 confirmation digest、query plan
   hash 和完整三个 search OperationId；
5. 匹配则按 artifact result 返回 ready/insufficient；不匹配抛 <code>HotelReplayError</code>；
6. absent：按 ordinal 1..3 依次执行 §15；
7. success evidence 按供应商顺序先执行 UID 身份一致性检查，再执行 category 资格和去重；
8. 完整处理本次结果后，累计合格候选达到 3 时停止后续搜索；
9. 输出候选只保留全局首次出现的前 5；
10. 三次后仍少于 3，形成 insufficient result；
11. put <code>hotel.candidate_set/1.0</code>；
12. 返回带 ArtifactRef 的 typed outcome。

不得：

- 并行发出三次调用；
- 为凑满 5 家继续搜索；
- 在 no-result 之外增加隐藏重试；
- 调用 details/geocode；
- 对供应商顺序做评分；
- 复制候选凑数；
- 在 candidate-set Artifact put 前进入人工 await。

## 15. 单次外部调用与重放顺序

对每个 search OperationId：

1. <code>await repository.lookup(operation_id=operation_id)</code>；
2. found：<code>await repository.get(found.ref,
   expected_model=HotelPlaceSearchArtifact)</code>；
3. 比较 search ordinal 和 request hash；不匹配抛 <code>HotelReplayError</code>；
4. absent：构造完整 <code>BudgetConsumeRequest(operation_id=operation_id,
   key=HOTEL_PLACE_CALL_BUDGET_KEY, amount=1)</code>，再
   <code>await budget_manager.consume(request)</code>；
5. <code>BudgetExceeded</code>：禁止调用地图，返回 <code>HotelCandidateDiscoveryFailed(reason=budget_exhausted)</code>；
6. consumed/replayed 且 Artifact absent：抛 <code>OperationResultIndeterminateError</code>；
7. consumed/applied：调用一次 <code>await adapter.search_places(request)</code>；
8. success/no-result 且 provenance.verified 为 true：投影为安全 evidence；
9. provenance.verified 为 false：投影为 <code>unverified_result</code> failed evidence；
10. 捕获公开 <code>BaiduMapError</code> 子类：只映射固定 <code>HotelMapFailureCode</code>；
11. 按 §9.5 构造完整 <code>ArtifactWriteRequest</code>，再
    <code>await repository.put(write_request)</code> 写搜索 Artifact；
12. 只有 put 成功后才能继续下一搜索或返回 failure。

<code>asyncio.CancelledError</code>、<code>KeyboardInterrupt</code>、<code>SystemExit</code> 不得捕获为业务失败；取消时不得写猜测结果。若预算已 applied 而 Artifact 尚未写入，重放按 Module 04 固定为 indeterminate，不得再次调用地图。

Artifact conflict、integrity、registry、repository dependency/usage 和 budget contract 异常保持 Module 04 原类型并失败关闭，不包装成普通候选不足。

## 16. 公共候选发现 outcome

<code>HotelCandidateDiscoveryOutcome</code>：

### 16.1 HotelCandidatesReady

字段：

1. <code>schema_version: Literal["1.0"]="1.0"</code>；
2. <code>outcome_type: Literal["ready"]="ready"</code>；
3. <code>candidate_set_ref: ArtifactRef</code>，required；
4. <code>candidates: tuple[HotelCandidate, ...]</code>，required，3..5；
5. <code>disclaimer_code: Literal["map_identity_only"]="map_identity_only"</code>。

### 16.2 HotelCandidatesInsufficient

字段：

1. <code>schema_version: Literal["1.0"]="1.0"</code>；
2. <code>outcome_type: Literal["insufficient"]="insufficient"</code>；
3. <code>candidate_set_ref: ArtifactRef</code>，required；
4. <code>qualified_candidates: tuple[HotelCandidate, ...]</code>，required，0..2；
5. <code>searches_attempted: Literal[3]</code>，required。

### 16.3 HotelCandidateDiscoveryFailed

字段：

1. <code>schema_version: Literal["1.0"]="1.0"</code>；
2. <code>outcome_type: Literal["failed"]="failed"</code>；
3. <code>reason: HotelCandidateDiscoveryFailureCode</code>，required；
4. <code>failed_search_ordinal: int</code>，required，1..3；
5. <code>qualified_count: int</code>，required，0..5；
6. <code>failure_search_ref: ArtifactRef | None</code>，required，无默认。

<code>HotelCandidateDiscoveryFailureCode</code>：

- <code>budget_exhausted</code>；
- <code>map_call_failed</code>。

map failure 的精确安全 code 已存在于 search Artifact，不在 outcome 重复暴露自由文本。failed 不创建 candidate-set Artifact。
<code>budget_exhausted</code> 要求 failure_search_ref=None；
<code>map_call_failed</code> 要求 failure_search_ref 指向本 ordinal 的
<code>hotel.place_search/1.0</code> failed evidence。

## 17. 人工选择、恢复与幂等

### 17.1 选择状态机

固定顺序：

1. exact type 与 strict fresh-copy <code>HotelSelectionRequest</code> 和 active ref；
2. 请求 candidate ref 与 active ref 七字段不完全相等时返回 <code>stale_candidate_set</code>；
3. get active candidate-set Artifact；
4. 校验 selection OperationId：phase=HOTEL_SELECTION，与 candidate-set 同 run，且不同于
   candidate-set OperationId 和 Artifact 保存的三个 search OperationId；
5. candidate-set result_type 不是 ready 时返回 <code>candidate_set_not_ready</code>；
6. lookup selection OperationId；
7. found：get <code>HotelSelectionArtifact</code>；
8. 既有 artifact 与本次候选 ref/command 完全等价：返回原 outcome；
9. 同 OperationId 不同决定：返回 <code>HotelSelectionRejected(decision_conflict)</code>；
10. absent 且 select UID 不在候选 tuple：返回 <code>candidate_uid_not_found</code>；
11. select 时从 candidate set 复制精确候选；decline 时不构造候选；
12. put <code>hotel.selection/1.0</code>；
13. put 成功或同内容幂等时返回 selected/declined outcome；
14. 只捕获该 put 的 <code>ArtifactConflictError</code>，立即重新 lookup/get 同一 selection
    OperationId；若得到同候选 ref 和等价决定，返回幂等 selected/declined，若决定不同则
    返回 <code>decision_conflict</code>；
15. conflict 后 lookup absent、类型错误或完整性错误不伪装成 decision conflict：
    absent 抛 <code>HotelReplayError</code>，其余保留 Module 04 typed error。

选择不消费执行预算，不调用地图，不改写 candidate-set Artifact。
并发测试必须用 barrier 让两个请求都观察 selection lookup absent，覆盖相同决定和不同决定；
不得用 sleep 证明竞争。

### 17.2 HotelSelectionOutcome

- <code>HotelSelected</code>
  1. <code>schema_version: Literal["1.0"]="1.0"</code>；
  2. <code>outcome_type: Literal["selected"]="selected"</code>；
  3. <code>selection_ref: ArtifactRef</code>，required；
  4. <code>selected_candidate: HotelCandidate</code>，required。
- <code>HotelSelectionDeclined</code>
  1. <code>schema_version: Literal["1.0"]="1.0"</code>；
  2. <code>outcome_type: Literal["declined"]="declined"</code>；
  3. <code>selection_ref: ArtifactRef</code>，required。
- <code>HotelSelectionRejected</code>
  1. <code>schema_version: Literal["1.0"]="1.0"</code>；
  2. <code>outcome_type: Literal["rejected"]="rejected"</code>；
  3. <code>reason: HotelSelectionRejectionCode</code>，required。

<code>HotelSelectionRejectionCode</code>：

<pre><code>candidate_uid_not_found
candidate_set_not_ready
stale_candidate_set
decision_conflict</code></pre>

非法 Pydantic 形状是调用方 contract error，不降级为 rejection。Module 13 决定 rejection 后是否再次 interrupt；Module 07 不实现 interrupt。

### 17.3 改选

已经 put 的 selection Artifact 不可覆盖、删除或原地修订。相同决定可幂等重放。改变 UID、selected/declined 动作或候选集必须由 Module 13 创建新的选择 OperationId、流程版本或 run。

## 18. Error、失败优先级与取消

### 18.1 自有异常

<code>HotelModuleError</code> 为基类；公开子类：

- <code>HotelInputError</code>，class-level <code>code="invalid_input"</code>；
- <code>HotelReplayError</code>，class-level <code>code="artifact_replay_mismatch"</code>；
- <code>HotelCandidateIdentityConflictError</code>，class-level
  <code>code="candidate_identity_conflict"</code>。

三个公开子类构造函数均无参数。实例只保存只读 code；<code>args == (code,)</code>；
<code>str/repr</code> 固定，不保存冲突 UID 或字段值。

### 18.2 失败优先级

候选发现路径的优先级：

1. 输入 exact type/schema/confirmed/OperationId 不合法；
2. candidate-set Artifact integrity/expected model/digest/query-plan/operation-set 不匹配；
3. search Artifact integrity/expected model/ordinal/request hash 不匹配；
4. budget exceeded 或 indeterminate；
5. Module 06 typed 技术失败或 unverified result；
6. UID 身份冲突；
7. 三次后的候选不足。

人工选择路径的优先级：

1. 输入 exact type/schema/ref 形状不合法；
2. stale candidate set；
3. candidate-set Artifact integrity/expected model 不匹配；
4. selection OperationId 与 candidate/search OperationId 冲突；
5. candidate set not ready；
6. selection Artifact integrity/expected model 不匹配；
7. 已记录的不同决定或并发 put 不同决定；
8. 候选集外 UID。

实际状态机尚未到达的后续条件不得抢占先发生条件。取消总是传播，不参与业务失败优先级。
测试至少构造每个相邻优先级的一组碰撞输入，并断言只返回最高优先级结果。

## 19. 安全、隐私与事实可信

- 不在 Module 07 Artifact、error、日志、测试快照或最终报告中保存独立 query、destination
  请求字段或其输入副本；候选的百度地址业务事实可能自然包含目的地文字，不属于请求字段泄漏；
- Artifact 只保存 request/query plan hash、规范化地点字段、安全 provenance 和固定 code；
- 不保存 Module 01 完整用户原文、metadata evidence、确认摘要正文或自由文本 decline 理由；
- 不保存 raw MCP/供应商 payload、URL、headers、Secret、底层异常或 stack；
- UID、地址和坐标只在候选/Artifact 的业务必要字段中出现；安全 error 和统计事件不重复携带；
- 测试使用显然虚构的 UID、地址和坐标；
- 不记录模型私有推理，因为本模块没有模型；
- 不以搜索相关性、名称或用户偏好生成事实评分；
- 所有展示必须携带固定 <code>map_identity_only</code> disclaimer。

## 20. 并发、容量与生命周期

- 三次业务搜索严格串行；
- Module 06 的 semaphore 继续独立保护技术并发；
- service 不保存跨调用可变 candidate cache，幂等状态只在注入的 Repository/BudgetManager；
- 单次地图结果最多 10 条；
- 单个 search Artifact 最多 10 条 evidence；
- candidate set 最多 5 个候选和 3 个 search refs；
- Module 04 的单 Artifact 1 MiB 与进程内总容量继续生效；
- Demo Repository/BudgetManager 生命周期由 Module 13 composition 管理；
- Module 07 不 close 共享 adapter/repository/manager；
- Module 10/11 的持久化和跨进程恢复只能通过 Module 04 端口实现，不修改 1.0 业务语义；TTL 和删除仍由 Module 19 另行设计。

## 21. 下游衔接

- Module 08 可读取 selected candidate 作为研究位置上下文，但不得回写候选事实；
- Module 09 使用 selected candidate 的 BD-09LL 坐标作为酒店规划锚点；
- Module 10 可持久化 selection Artifact，但不得改变其 UID、坐标或选择语义；
- Module 11 可在跨进程恢复时读取 selection Ref，但不得把酒店选择当作价格证据；
- Module 12 只按已经校验的 selection Ref 呈现 UID／坐标关联，并把酒店费用标记为未核验；不得重新判定或改变酒店选择有效性；
- Module 13 组合 Registry、Plan、OperationId、当前 candidate ref、interrupt/resume 和终态；
- Module 14 将 UID 映射为界面序号，但提交给 Module 07 的仍是 UID；
- declined/insufficient/failed/rejected 到 delivery/terminal 的映射全部留给 Module 13。

## 22. 未来专用酒店 Agent 演进边界

未来若经单独上位设计变更批准，可新增专用 <code>ModelRole.HOTEL_SELECTION</code> 和完整受控 Agent。该升级必须先修改主设计、路线图、Module 02 和 Module 04 合同，不属于 1.0 实施。

为降低升级返工，1.0 保持：

- 地图执行、预算与 Artifact 重放独立于确定性查询策略；
- 酒店资格、UID 冲突、候选 DTO 和人工选择门保持确定性；
- candidate facts 不含模型 score；
- Agent 只能替换候选发现控制层，不得绕过 <code>hotel.place_call</code> 或直接操作 Gateway；
- 未来排名、Action/Observation 和模型预算使用新增版本/Artifact，不回填或重解释 1.0 字段；
- 人工最终选择仍保留。

1.0 不创建空 Agent Protocol、模型字段、Prompt 占位或 Module 02 import。

## 23. 版本与迁移

- 公共 DTO 和三类 Artifact 初始 schema version 为 <code>1.0</code>；
- 未知 artifact type/version 由 Module 04 失败关闭；
- 未知 Module 07 DTO version strict validation 失败；
- 字段增加、删除、改名、顺序变化、nullable/default 变化、类别资格语义、候选容量、Artifact 正文或 outcome discriminator 变化均为 breaking change；
- breaking change 必须新增版本、更新注册和合同测试；
- 不提供兼容 alias、双 parser、隐式迁移或“尽力读取”；
- Demo 无 migration；
- 未来 Agent 不得把旧 candidate set 静默迁移为带模型排名的新版本。

## 24. 测试分类与矩阵

全部正式测试离线，使用 fake <code>BaiduMapAdapter</code>、in-memory Module 04 实现和注入 clock；禁止网络、<code>.env</code>、Secret、sleep、Provider Discovery 和 live。

### 24.1 deterministic unit

- 查询构造、精确去重、三条上限和 512 字符失败；
- category token 切分、允许集、None/非酒店/名称诱导拒绝；
- UID exact 去重、不同 UID 相似名称保留、四类身份冲突；
- 供应商顺序、每次 10 条、候选 3..5、达到 3 后停止；
- DTO strict/frozen/extra-forbid、字段顺序、tuple、nullable 和 repr；
- 三个 Enum 的 member 名和值；
- map error 到固定 failure code 的穷尽映射；
- request/query plan hash 确定性且不泄漏原文。

### 24.2 public/contract

- 包根 <code>__all__</code> 精确集合；
- 禁止私有导入、LLM、LangGraph、MCP 和下游依赖；
- JSON strict round-trip；
- 三项 Artifact registration、canonical round-trip、fresh-copy、未知版本和 expected model；
- 三类 Artifact 使用 mode=json 构造 ArtifactWriteRequest.content；
- candidate-set ready/insufficient 不变量；
- selection selected/declined Artifact。

### 24.3 replay/budget

- lookup found 不 consume、不调用 adapter；
- absent → consume → applied → call → put；
- exceeded 不调用；
- replayed+absent 抛 indeterminate；
- success/no-result/typed map failure 都先 put；
- put 冲突/integrity 失败关闭；
- 三次 no-result 恰好 consume 3；
- 第一或第二次达到 3 后停止；
- 前两次不足、第三次达到 3 时生成带三个 search refs 的 ready；
- candidate-set replay 精确绑定完整三个 search OperationId；
- interrupt 前 candidate-set 已 put；
- 重放不增加预算和 adapter call count。

### 24.4 人工选择

- 候选内 UID selected；
- decline 无 selected candidate；
- 候选外 UID rejection；
- insufficient candidate set 返回 candidate_set_not_ready；
- stale ref 七字段比较；
- 相同决定幂等；
- 同 OperationId 改 UID、改动作或改候选 ref conflict；
- barrier 并发同决定幂等、不同决定稳定 decision_conflict；
- selection OperationId 不得等于 candidate-set 或任一 search OperationId；
- insufficient artifact 不可选择；
- 改选需要新 operation。

### 24.5 技术失败、安全和取消

- Module 06 每个公开 typed error 的安全映射及 verified=false 拒绝；
- cancellation 原样传播且不伪造 Artifact；
- error/repr 不含 UID、query、destination、坐标或异常正文；
- Artifact 不含 query、完整确认摘要、raw payload、Secret；
- 容量边界；
- 不使用 skip、xfail、rerun 或弱断言。
- discovery 与 selection 两套相邻失败优先级碰撞矩阵。

### 24.6 coverage

<code>app.hotel_selection</code> 的 line、branch、combined coverage 必须分别至少 95.00%。combined 固定为：

<pre><code>(covered_lines + covered_branches)
/
(num_statements + num_branches)</code></pre>

不得只使用 coverage.py 单一 total 冒充三个指标。

## 25. 验收标准

全部同时满足才可通过 Module 07 独立验收：

1. 生产包和公共 API 与本文精确一致；
2. 直接依赖仅为 01/04/06；
3. 无 LLM/ReAct、details/geocode、MCP 或 Graph 越权；
4. 确定性三查询和 3 次预算可机器验证；
5. 严格 category 资格和 UID 身份冲突失败关闭；
6. 3～5 候选、供应商顺序和早停准确；
7. 三类 Artifact 注册、重放和 indeterminate 合同通过；
8. selected/declined/rejected/stale/conflict 全部覆盖；
9. 无事实虚构和敏感内容泄漏；
10. fake adapter 的 unit/contract 全通过；
11. line、branch、combined 分别至少 95%；
12. 完整 unit/contract 上游回归通过；
13. 无 skip、xfail、rerun；
14. runtime 默认 import 在 closeout 后诚实覆盖模块 01～07；
15. README、状态页和 Dockerfile 只在独立 MODULE-CLOSEOUT 授权中同步。

## 26. 实施与验收停止条件

出现任一项必须停止：

- 上位文档或直接依赖冲突；
- 必须修改 Module 01/04/06 才能实现；
- 必须增加 Module 02、LLM、ReAct、details/geocode 或外部依赖；
- 需要保存 query、完整用户原文、raw payload、Secret 或底层异常；
- 无法遵守 Artifact lookup/budget/call/put 顺序；
- 需要重复外部调用消除 indeterminate；
- 需要模糊 category 或 UID 才能凑够候选；
- 需要 Module 07 实现 Graph、interrupt、terminal 或 CLI；
- 测试需要网络、live、真实 Key 或 sleep；
- 任一 coverage 指标低于 95%；
- 独立审查仍有 P0/P1/P2；
- 需要扩大阶段文件白名单；
- 未取得对应实施、修复、closeout、commit 或 merge 授权。

## 27. 决策记录

### 27.1 上位合同冻结

- 一个核心城市、全程一家酒店；
- 酒店选择是人工门；
- 最多三次 Place Search；
- 直接依赖为 01/04/06；
- Module 13 独占完整 Graph 和终态；
- Module 07 不拥有 LLM、MCP、价格、预订或 live。

### 27.2 用户明确确认

- 初版保持无 LLM，同时为未来专用酒店模型角色保留可替换控制层；
- 只调用 Place Search，不调用 details/geocode；
- preference 原文字段优先查询，随后酒店/宾馆/住宿，最多三条不同查询；
- category 必须明确提供住宿证据；
- 同 UID 身份事实冲突时整体失败；
- 每次 result_limit=10，最终 3～5 家，达到 3 家停止；
- 供应商首次出现顺序不是排名；
- 人工可 select 或 decline；
- 已提交决定不可覆盖，改选使用新 operation/流程版本。

### 27.3 仓库合同推导

- confirmed outcome 是唯一入口；
- no-result 继续，typed 技术错误停止；
- Artifact 和预算顺序复用 Module 04；
- 正式测试只用 fake adapter；
- 不新增依赖、Secret、live、迁移或数据库。

### 27.4 当前无未决公共合同

本文批准只表示设计冻结候选可进入后续实施授权流程，不自动授予任何实施或 Git 写操作授权。
