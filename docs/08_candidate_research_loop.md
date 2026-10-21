# Module 08：受控候选研究循环

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次消费M02专用交互记录，仅增加调用用途/工具结果关联；需求业务语义及M08原6轮/48Place/12逻辑调用不扩大。普通日志/业务Artifact禁正文的约束不变。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 状态：已批准并冻结；`MODULE08-DESIGN-ERRATA-01`、`D-08-32` 与 `D-08-34` 修订稿。
>
> 本文是 Module 08 公共合同、业务不变量和验收语义真源。它不表示 Module 08 已实施或已验收。
>
> 发布前设计勘误基线：本文原 SHA-256 为 `C43586BAE1B14A9DA2A721055B3F5A965CE655FE99EF84398301850BCA602281`。用户先后授权修正“无相关 Place Observation 时 Incomplete gap 的证据表示”“缺少 avoid-resolution supplemental 分支”，曾将 Module 08 coverage 门槛调整为 80%，并于 2026-09-11 将当前 line/branch/combined 门槛统一调整为 70%；公共 DTO 与 Artifact schema version、四类 Artifact type 及 Prompt version/hash 均保持 `1.0`/原值。修改后的 hash 由独立复审交接报告记录，不写入本文以避免自引用。

## 1. 文档职责、权威层级与冲突处理

本文冻结 Module 08 的输入输出、受控 ReAct、地图工具白名单、候选证据、预算、Artifact、重放、失败及下游边界。实现步骤以 `docs/08_candidate_research_loop_vibe_coding.md` 为准，但执行文档不得改变本文语义。

上位真源按以下顺序解释：

1. `docs/00_agent_master_design.md` 第 1～11 节；
2. `docs/IMPLEMENTATION_ROADMAP.md` 第 2、3、4、6、8 节，其中 `depends_on` 是直接依赖唯一真源；
3. Module 01、02、04、06、07 的冻结设计及其公共合同；
4. 本文；
5. 公共合同测试；
6. Vibe Coding 执行文档；
7. 当前实现。

上位合同与本文发生真实冲突时必须停止，不得用实现便利性解释合同。当前为 Module 08 特别批准的候选研究 Place 预算是 48；该值已同步到主干设计、Module 04 预算接线表和 Module 06 下游衔接表。其他上游语义不得由 Module 08 反向修改。

## 2. 目标、产品声明与边界

### 2.1 目标

Module 08 在严格工具权限、迭代预算、Place 预算、模型技术上限和候选容量内，通过受控 ReAct 研究真实景点与餐厅，形成身份稳定、证据可追溯、数量尽量充足的候选池，供 Module 09 规划行程。

它回答：有哪些真实地点；地点的百度 UID、名称、地址、BD-09 坐标和可空类别是什么；可承担什么研究角色；与哪些兴趣、餐次、must/avoid 条目相关；详情是否取得；候选池还缺什么。

### 2.2 明确产品声明

- Module 08 与 Module 09 工程边界分开，但由 Module 13 形成“研究候选—尝试规划—发现缺口—定向补充”的产品闭环。
- Module 08、09 各有自己的局部 ReAct；Module 13 是确定性编排器，不建立跨模块共享隐藏上下文的全局 ReAct。
- 候选数量是供应量，不是最终日程数量；所有 pace 使用同一候选目标。
- 真实供给不足时返回已验证部分结果和 `Incomplete`，不重复、不凑数、不伪造。
- `Incomplete` 不等于流程终止：至少存在一个 eligible 候选时，Module 13 可以把候选集交给 Module 09；`Failed` 不得自动进入 Module 09。

### 2.3 非目标

Module 08 不决定每天安排几个地点、日期、顺序、路线、交通、午晚餐最终选择或“最佳”排名；不评估旅行费用、预算适配、饮食限制、行动限制、pace、交通偏好、酒店房型、每日时间偏好、抵离细节；不负责酒店选择或回写；不负责天气、路线矩阵、Directions、Geocoding 或地图调起；不直接写 `demo_ready/degraded/failed`。

## 3. 直接依赖、导入与所有权

### 3.1 直接依赖

唯一直接依赖为 Module 01、02、04、06、07：

| 模块 | Module 08 消费内容 | 精确真源 |
|---|---|---|
| 01 | `ConfirmedTravelRequirement` 及其 confirmed requirement | `docs/01_requirement_and_dialogue.md` 第 3.3～3.4、5.2～5.3、6.3～6.6、11、13、15～17 节 |
| 02 | `ModelRole.CANDIDATE_RESEARCH`、`CANDIDATE_RESEARCH_PROFILE`、`ModelOperation.ainvoke_structured`、strict decode、输入容量、重试、usage 和 typed error | `docs/02_llm_runtime_and_model_profiles.md` 第 4.2～4.3、5～12、15～18 节 |
| 04 | `WorkflowPhase.CANDIDATE_RESEARCH`、`ArtifactRef`、Repository、OperationId、执行预算和安全顺序 | `docs/04_workflow_state_and_artifacts.md` 第 2～3、5～6、8～14、17～21、22～26 节 |
| 06 | `BaiduMapAdapter.search_places`、`get_place_details`、公共 Place DTO 与 `BaiduMapError` | `docs/06_baidu_map_adapter_and_mcp_gateway.md` 第 3.2、4.3、6～7、10、15、18～22、24～29 节 |
| 07 | `hotel.selection/1.0` Artifact 中已选酒店 UID、名称、地址和坐标 | `docs/07_hotel_candidate_selection.md` 第 3～11、17～27 节 |

只允许从这些模块的包根或文档明确声明的稳定公共面导入。Module 08 不导入 Module 05 私有 Graph/checkpoint/coordinator、Module 06 私有 Gateway/MCP/session/parser、Module 07 私有实现、供应商 SDK、MCP SDK、HTTP client 或 Module 09+ 未来实现。

### 3.2 相邻模块所有权

- Module 01 负责确认需求及其列表上限；Module 08 不修正或重新解释 confirmed requirement。
- Module 02 负责模型供应商、技术重试、严格结构解码、字节 Gate 和语义调用计数；Module 08 负责业务 Prompt、Action/Judgment schema 和研究循环。
- Module 04 负责 Artifact、预算、OperationId 与事件基础设施；Module 08 负责自有 Artifact 内容和预算消费时机。Module 08 不建立事件 sink，Module 13 负责工作流事件序号和聚合。
- Module 06 负责 typed 地图事实、供应商错误和全能力并发保护；Module 08 负责只选择 Place Search/Details 以及更低的并发波次。
- Module 07 负责酒店候选和人工选择；Module 08 只读已选酒店。

## 4. 输入边界与冻结研究上下文

### 4.1 Initial 输入

`CandidateResearchInitialRequest` 字段顺序固定为：

1. `schema_version: Literal["1.0"] = "1.0"`；
2. `confirmed: ConfirmedTravelRequirement`；
3. `hotel_selection_ref: ArtifactRef`；
4. `operation_plan: CandidateResearchOperationPlan`。

启动前必须验证 requirement 已 confirmed；目的地和 `sightseeing_days` 存在；酒店 Ref 精确指向同 run 的 `hotel.selection/1.0`；该 Artifact 的 decision 是 selected；OperationId 全部属于同一 run、`WorkflowPhase.CANDIDATE_RESEARCH`，且相互唯一。拒绝调用方复制酒店名称、地址或坐标。

### 4.2 Supplemental 输入

`CandidateResearchSupplementalRequest` 字段顺序固定为：

1. `schema_version`；
2. `predecessor_candidate_set_ref: ArtifactRef`，必须精确指向同 run 的 `candidate_research.candidate_set/1.0`；
3. `gaps: tuple[SupplementalGap, ...]`，1..128，保持 Module 13 顺序且语义指纹唯一；
4. `operation_plan`。

Supplemental 不重复携带 confirmed requirement 或酒店事实。服务从不可变前驱和私有 run context 恢复已冻结输入摘要；run context 不进入公共 DTO。完全已满足的 gaps 不调用模型和地图、不消费预算，但写入 no-op successor 候选集以保留审计与精确重放；部分已满足时只处理剩余 gaps。

### 4.3 模型可见字段

硬输入为目的地、`sightseeing_days`、冻结后的 `effective_interests`、must-visit、avoid，以及已选酒店 UID/名称/坐标。人员结构、儿童年龄、`free_text_notes`、`inferred_scenarios` 仅是非阻断软提示，只能影响查询词、研究方向和相关性，不能变成供应商事实或新硬要求。

预算、饮食限制、行动限制、pace、交通偏好、酒店房型、每日时间偏好和抵离信息不进入 Module 08 模型上下文。普通可选字段为空表示无额外提示，不猜默认值。

`interests is None` 且 `agent_decides_interests=true` 时，Action 模型只能从人文、自然、美食、打卡中选择 2～4 项；证据不足时确定性使用全部四项。`effective_interests` 一经形成，在 initial 和所有 supplemental 中冻结。其他非法空值在任何 LLM、地图或预算调用之前拒绝。

## 5. 推荐生产包与包根公共 API

推荐未来生产树：

```text
app/candidate_research/
  __init__.py
  models.py
  artifacts.py
  errors.py
  protocols.py
  prompt_loader.py
  prompting.py
  fingerprints.py
  policy.py
  judgments.py
  service.py
  prompts/
    action_v1.txt
    observation_v1.txt
```

包根是唯一公共导入面，最终 `__all__` 按以下顺序精确导出，不允许额外符号：

```text
CANDIDATE_RESEARCH_SCHEMA_VERSION
CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY
CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY
CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
ResearchRole
RoleVerdict
CandidateState
MatchVerdict
MealIntent
ResearchInvocationKind
ResearchTool
SearchAreaMode
ResearchMode
ResearchObjectiveKind
CandidateResearchStopReason
RoleReasonCode
InterestReasonCode
MealReasonCode
RequirementReasonCode
CandidateResearchControlFailureCode
HotelAnchorRef
CandidateAnchorRef
ResearchAnchorRef
ResearchIterationOperationIds
CandidateResearchOperationPlan
CandidateResearchInitialRequest
CandidateResearchSupplementalRequest
CandidateCountShortfallGap
InterestCoverageMissingGap
AreaCoverageMissingGap
MealCoverageMissingGap
MustVisitResolutionMissingGap
AvoidResolutionMissingGap
CandidateDetailsMissingGap
ReplacementCandidateNeededGap
SupplementalGap
ProposedSearchAction
ProposedDetailsAction
ProposedAction
ProposedActionBatch
NoUsefulActionProposed
ActionModelOutcome
EvidenceHandle
RoleJudgmentUpdate
InterestJudgmentUpdate
MealJudgmentUpdate
RequirementVerdictException
RequirementGroupJudgmentUpdate
JudgmentPatch
CandidateRecord
ResearchCountSummary
ResearchCoverageSummary
KnownLLMUsage
ResearchBudgetSummary
ResearchGapSummary
LLMResearchFailure
MapResearchFailure
ControlResearchFailure
CandidateResearchFailure
CandidateResearchCompleted
CandidateResearchIncomplete
CandidateResearchFailed
CandidateResearchOutcome
CandidateResearchModelActionArtifact
CandidateResearchModelJudgmentArtifact
CandidateResearchPlaceObservationArtifact
CandidateResearchArtifact
CandidateResearchError
CandidateResearchConfigurationError
CandidateResearchUsageError
CandidateResearchPort
CandidateResearchService
```

Prompt loader、指纹函数、控制器 policy、judgment 合并器和其他 helper 保持私有，不从包根重导出。各具体 `*Summary` 类是 `ResearchGapSummary` 的实现分支，不逐一从包根导出；这同样适用于 `AvoidResolutionMissingSummary`。因此本次公共包根只新增输入类型 `AvoidResolutionMissingGap`，公共终态继续通过 `ResearchGapSummary` 暴露。

`CandidateResearchPort` 只声明：

```python
async def research_initial(
    self, request: CandidateResearchInitialRequest
) -> CandidateResearchOutcome: ...

async def research_supplemental(
    self, request: CandidateResearchSupplementalRequest
) -> CandidateResearchOutcome: ...

async def close_run(self, run_id: UUID) -> None: ...
```

`close_run` 幂等清除该 run 的 `ModelOperation` 和私有研究上下文；已持久化 Artifact 的精确 replay 在关闭后仍可用。服务整体 `aclose()` 是最终兜底，不代替逐 run 关闭。

`CandidateResearchService` 构造签名精确为 `__init__(*, runtime: DeepSeekRuntime, map_adapter: BaiduMapAdapter, artifact_repository: ArtifactRepository, budget_manager: ExecutionBudgetManager) -> None`；`aclose(self) -> None`。实现内部可定义不导出的 `_CandidateResearchRuntimePort`/`_CandidateResearchModelOperationPort` 以支持结构化 fake：runtime port 只声明同步 `start_operation(ModelRole)`；operation port 只声明 `ainvoke_structured(PromptEnvelope, schema)` 和同步 `close()`。`close_run` 与服务 `aclose()` 都调用各 run 的 operation `close()`；不得调用注入 runtime 的 `aclose()`，不得复制 ProviderAdapter 或读取 ModelOperation 私有计数。

## 6. DTO 共同规则

所有 Module 08 公共和 Artifact Pydantic DTO 必须 `strict=True`、`frozen=True`、`extra="forbid"`，根为 object；字段按本文顺序声明；discriminated union 使用显式 literal discriminator；序列使用 tuple；可空、默认值和 required 不得互换；容量使用 Pydantic 约束；bool 不得冒充 int；浮点必须有限。DTO 不使用会改变输入的 validator/serializer。

Artifact 注册类型图采用更严格的 `ArtifactSnapshotModel` 规则：`model_config` 必须精确等于上述三个键，图内每个字段都 required，nullable 也必须显式传 null，schema version 和 discriminator 不设默认值；只允许纯断言式 after validator，不允许 before/wrap/plain validator、serializer、alias 或 computed field。服务边界仍消费 Module 01/02/06 公共 DTO，但持久化前必须显式复制成 Module 08 自有 snapshot，Artifact 类型图不得直接嵌入 `MapModel`、`BaiduCoordinate`、Place outcome、`LLMRuntimeError` 或其他配置不兼容的上游 BaseModel。

所有边界执行 fresh strict Python 验证和 canonical JSON round-trip；不得信任 `model_construct()`、构造后嵌套 mutation、调用方对象身份或宽松转换。Artifact 遵守 Module 04 的注册、规范 JSON、hash、大小和 fresh-copy 规则。内部模型输出也必须 strict decode，不做正则提取、代码围栏剥离、JSON repair 或默认字段补齐。

公共短标识、枚举和容量必须封闭。自由文本只在业务确有需要且有上限的位置存在；错误、事件和 outcome 不携带任意异常文本。

## 7. 核心枚举、值对象与候选

### 7.1 封闭枚举

- `ResearchRole = attraction | restaurant`；
- `RoleVerdict = supported | contradicted | unresolved`；
- `CandidateState = eligible | withheld | excluded | conflicted`；
- `MatchVerdict = confirmed | no_match | ambiguous | unresolved`；
- `MealIntent = lunch | dinner`；
- `ResearchInvocationKind = action | judgment`；
- `ResearchTool = search_places | get_place_details`；
- `SearchAreaMode = city | nearby`；
- `ResearchMode = initial | supplemental`。
- `ResearchObjectiveKind = supplemental_gap | must_visit_resolution | avoid_resolution | initial_city_attraction | initial_hotel_restaurant_nearby | meal_lunch | meal_dinner | interest_coverage | attraction_count | restaurant_count | details_maturity | diversity`；
- `CandidateResearchStopReason` 的声明顺序精确为 `candidate_capacity_reached | place_budget_exhausted | iteration_limit_reached | no_progress | no_legal_action | useful_supply_not_found`。

### 7.2 身份与事实

`CandidateCore` 顺序固定为 UID、名称、地址、BD-09 坐标、可空 `provider_category`。前四项来自 Module 06 已验证 Place DTO；`provider_category` 是供应商事实，模型不得改写。Search 结果即可建立候选，不要求先取 Details。

`CandidateRecord` 至少包含首次核心事实、当前状态、每个角色的当前 verdict、关联兴趣、餐次意图、must/avoid group 引用、Details 状态、Observation refs 和首次准入序号。预算、价格、饮食和行动适配固定输出为 `not_evaluated`，不得生成其他结论。

百度 UID 是全局身份键；同 UID 只保存一个实体，可同时关联多个角色、兴趣、餐次、搜索来源和 must 条目。候选按首次有效发现顺序保存，supplemental 新 UID 追加，顺序仅供确定性重放，不表示排名。

数量目标按角色分别统计 unique eligible UID：同一 UID 若 attraction 和 restaurant 两个角色都为 supported，可以分别计入两个角色目标，但候选容量和 Details 成熟度仍只按一个 UID 计算。

### 7.3 状态推导

控制器按 `conflicted > excluded > withheld > eligible` 确定当前总体状态：

- `conflicted`：核心供应商事实冲突，或同一 UID 同时存在 confirmed must 与 confirmed avoid；
- `excluded`：confirmed avoid，或 attraction/restaurant 两个角色均 contradicted；
- `withheld`：存在 ambiguous/unresolved avoid，或没有 supported 角色且至少一个角色 unresolved；
- `eligible`：至少一个角色 supported，且不存在上述阻断。

未解决 must 不阻止候选作为普通 eligible 候选。供应商类别若明确反驳角色，角色不得 supported；类别为空保持未知。角色由模型基于名称、类别和证据语义判断，允许名称本身提供充分证据，不建立自造类别字典。

同 UID 后续出现不同名称、地址或坐标时，不覆盖首次事实，保留 Observation，标记 `provider_fact_conflicted` 并隔离。坐标核心一致性采用经纬度精确相等，不引入自造距离容差。Details 可选字段差异作为不同证据保留，不静默覆盖。被隔离的额外 `conflicted` 候选不计入 eligible、数量、兴趣、餐次、must 或 Details 覆盖；只要其未被任何完成目标采用且其余候选已经满足全部目标，它本身不阻止 Completed。若隔离导致某项目标不满足，控制器只派生该目标已有的 typed gap，不新增核心冲突 gap，也不得把业务状态改写为 `artifact_error`。

## 8. must-visit、avoid、兴趣与餐次

Module 01 当前 `must_visit_places` 和 `avoid_places` 各最多 100 条；Module 08 原序接收、不截断、不在输入边界去重。每个列表位置用“字段类型 + 原始索引 + 文本摘要 + confirmed requirement 摘要”派生稳定 requirement item ID。完全相同的原始字符串可共享一次地图研究和匹配集合，但 must 与 avoid 不跨字段合组；每个列表位置仍保留独立 ID。

一条 must 或 avoid 均可匹配多个 UID。匹配使用压缩的 semantic requirement group：组记录已评估候选范围、四态默认 verdict、必要例外及已校验安全证据引用，禁止展开完整笛卡尔积。新累计证据产生新的当前判断，历史 judgment Artifact 追加且不可变。

must group 的 confirmed UIDs 是 Module 09 可选备选；Module 08 不替 Module 09 选最终 UID。若用户要求多个具体地点全部去，Module 01 应产生多个条目；Module 08 不把模糊搜索的全部结果解释为“全部必须安排”。avoid 的全部 confirmed UID 排除，ambiguous/unresolved UID withheld。

完成 must 研究义务要求每个原始 must item 经其 semantic group 映射后至少拥有一个 confirmed 且当前 eligible 的 UID；只有 no-match、ambiguous、unresolved 或仅有被阻断 UID 时形成 gap，不能 Completed。avoid 研究义务要求所有已准入 UID 对适用 avoid group 均不存在 ambiguous/unresolved，且 confirmed 命中已经排除。

只要某个 avoid group 对当前已准入候选仍有 ambiguous/unresolved verdict，控制器就必须派生 `avoid_resolution_missing` typed gap；其 target 是该 group 的稳定 group/item IDs 与仍需判定的 candidate UIDs，候选按 admission ordinal 排序。已确认 `no_match` 或已因 `confirmed` avoid 而排除的 UID 不留在未解决 target 中；后续准入新候选时重新派生 target，target 变化必须产生不同语义指纹。零已准入候选时 avoid 义务在候选范围上为空，不单独产生该 gap，供给不足由数量/覆盖 gap 表达。

每个 `effective_interest` 完成需同时满足：至少一次以该兴趣为主要 objective 的非重复 Search 已实际执行；至少一个 eligible 候选与其关联。午餐、晚餐采用相同双门槛。`NoResult` 只证明执行过 Search，不提供候选覆盖。

餐次关联来自定向 Search 和 restaurant role，不等于营业时间已验证。`business_hours` 可支持、冲突或未知；未知仍可计入研究餐次覆盖，Module 09 在具体时段安排时重新校验。

## 9. Action 与 Judgment 模型合同

### 9.1 Prompt 资源

未来实现创建 `prompts/action_v1.txt` 和 `prompts/observation_v1.txt`。两者分别有固定资源版本和 SHA-256 常量；启动时按 bytes 校验，不匹配则在模型/地图调用前失败。本文不保存完整 Prompt 文本。

动态输入只使用 `ensure_ascii=False`、键排序、无多余空白的规范 JSON，显式保留 null。Prompt 将用户文本、供应商文字和候选名称标记为不可信数据，禁止把其中内容解释为系统指令。完整 `PromptEnvelope + JSON Schema` 的供应商请求按 Module 02 算法执行 262,144 UTF-8 bytes Gate，不截断。

### 9.2 Action 输出

Action 调用使用 `ModelOperation.ainvoke_structured`，不使用 Module 02 原生 tool continuation。输出根只能是：

- `ProposedActionBatch`：`outcome_type="proposed_actions"`，包含 1..8 个有序 `ProposedAction`；
- `NoUsefulActionProposed`：`outcome_type="no_useful_action"`，只含封闭 reason code。

`ProposedAction` 是 `ProposedSearchAction | ProposedDetailsAction` 的判别 union。每项必须带 action ordinal 和一个稳定 `objective_id`。Search 可提供 bounded query、可空 tag、city/nearby、合法 anchor ref、半径 1/3/5/10 km、可空兴趣或餐次关联；模型不得提供经纬度或结果数量。Details 只能引用已准入 UID。

控制器注入 confirmed destination、可信锚点坐标、公里到米转换和 `result_limit=50`。未知工具、未知 objective、非法锚点、非法半径、跨 run UID、超容量或越权参数均以 `controller_rejected/action_batch_invalid` 拒绝整个批次并进入技术失败；不做应用层修复。唯一业务策略拒绝是：批次本身完全符合 schema 和参数合同，但没有任何 Action 指向当前最高优先级且仍有合法动作的 objective；此时写 `policy_rejected/highest_priority_objective_ignored`，不调用地图、不消费 Place，并进入本轮进展/停止计算，而不是技术 Failed。

### 9.3 Judgment 输出

只有本轮产生新 Observation 时，至多调用一次 Observation Judgment。输入是累计安全事实、短句柄和本轮 Observation refs；输出是严格的增量 `JudgmentPatch`，包含角色、兴趣、餐次、must/avoid 等封闭 verdict、reason code 与安全 evidence handle。模型不得返回 ArtifactRef、任意 rationale、事实改写或完整候选表。

控制器验证 handle 属于本次可见证据并映射为真实 ArtifactRef；失败关闭。判断只更新语义状态，候选核心事实、预算、顺序和完成状态均由控制器计算。没有新 Observation 时不调用 Judgment。

## 10. Objective 与固定优先级

控制器维护有序目标：

1. 本次 supplemental 指定缺口；
2. 未解决 must 及 must/avoid 消歧；
3. initial 强制空间搜索和午晚餐搜索；
4. 未覆盖兴趣；
5. 景点或餐厅数量不足；
6. Details 三分之一成熟度；
7. 其他多样性补充。

每轮至少一个 Action 必须处理当前最高优先级且仍有合法动作的目标。一个 Action 可产生多重效果但只有一个主要 objective。完全忽略最高目标、但除此之外合同合法的批次写为业务 `policy_rejected`；该研究迭代已消费，Place 预算不消费，本轮进展为 false，并继续检查 Completed/停止条件；若尚可继续则进入下一轮，连续两轮可触发 `Incomplete(no_progress)`。控制器决定必须解决什么，模型决定具体怎么查。

## 11. Search、Details 与空间策略

所有 Search 固定 `result_limit=50`；一次调用不论返回多少地点只消费一次 Place 预算。initial 景点至少一次城市范围 Search，initial 餐厅至少一次以已选酒店为圆心的 Nearby Search。Nearby 可用于景点和餐厅；supplemental 圆心可为酒店或已有 eligible 候选 UID。

半径是地理搜索范围，不是路线距离、步行距离或交通时间。模型不得提供坐标；控制器从可信 Artifact 注入。Search 超过剩余候选槽位时保存完整规范化 Observation，并按“模型 Action 顺序—供应商结果顺序—UID 首次发现顺序”准入至 128，其余标记 `not_admitted_candidate_capacity`，不得让模型临时排名。

Details 优先级为 must/avoid 消歧、supplemental 指定目标、餐厅营业信息、其他成熟度。成熟门槛为 `ceil(unique eligible UID count / 3)`。同一 UID 只成功计数一次；`PlaceDetailsNoResult` 消耗 Place 预算、保持候选原资格、不计成熟度且本 run 不重试，改查其他未尝试 UID。可选字段缺失保持 unknown。

## 12. 指纹、去重与候选容量

Search 指纹包含：分别只执行首尾 `strip` 后的 query 和可空 tag、实际城市范围或锚点和半径、固定 result limit。不得做 Unicode、大小写、内部空白或标点归一化；`objective_id`、兴趣和 meal intent 不进入指纹。Details 指纹只含 UID。`model_action` 保存的完整 query/tag 是经过字段验证及上述首尾 strip 后的实际有效值，不降格为只存 hash。

指纹集合覆盖同一 run 的 initial 和 supplemental。重复 Action 不调用地图、不消费 Place 预算、不生成 Observation、不算进展，仅在 `model_action` Artifact 记录 `duplicate_action` 并引用已有 Observation。重复 Search 不重新建立专门 objective 覆盖；要满足新的专门 Search 门槛，模型必须提出请求指纹不同的合法 Search。

UID 首次合法纳入时占一个累计槽位；被排除、withheld 或 conflicted 后仍不回收。达到 128 后禁止继续 Search 新候选，仍允许对已有 UID 查 Details 和处理冲突。未达成所有条件时返回 `Incomplete(candidate_capacity_reached)`。

## 13. 预算、模型调用与消费顺序

固定业务预算：

| 预算 | 上限 | 所有者 |
|---|---:|---|
| `research.iteration` | 6 | Module 08 经 Module 04 消费 |
| `research.place_call` | 48 | Module 08 经 Module 04 消费 |
| semantic invocation | 12 | Module 02 `CANDIDATE_RESEARCH_PROFILE` |

每轮至多一次 Action 调用和一次 Judgment 调用，`6 × 2 = 12`。同一 run 的 initial 和 supplemental 共享同一个 `ModelOperation`、剩余业务预算、语义调用计数、Action 指纹和 128 槽位；不得重置。一次语义调用最多有两次供应商技术尝试，因此最坏 24 次物理请求，但仍是 12 次 semantic invocation。

研究迭代在 Action 模型调用前消费。Module 02 在完整请求字节 Gate 通过、供应商调用前消费语义计数。Place Action 通过全部业务校验后，在任何地图调用前进入“完整前缀预占屏障”：按 Action 顺序使用每个 Place Observation OperationId 分别执行 `amount=1` 的 Module 04 consume，只有前缀全部返回 applied/replayed 且与现有 Observation 一致后才允许发出地图请求。Module 04 没有跨多个 OperationId 的事务 API，因此这里的“原子”精确定义为对外部地图副作用全有或全无，不宣称预算账本多记录事务。屏障中途发生**非取消**基础设施异常时不发地图，已经 applied 的记录不回滚，并为其写 `not_issued_reservation_barrier_failed` Observation 后技术失败；若在第 N 个 consume 前后收到 `CancelledError`，立即停止后续 consume、Artifact 和地图调用并原样传播，不补写 not-issued，取消前已经 confirmed-applied 且缺少 Observation 的槽位在未来 replay 时确定性返回 `operation_result_indeterminate`。请求一经发出，不论 success、no-result、限流、超时或取消均不退还。Artifact replay 和重复 Action 不产生新消费。

Action 数量超过剩余 Place 预算时，按模型顺序执行可容纳的最长合法前缀，其余记录 `not_admitted_budget_exhausted`，不再次调用模型重排。执行后若未 Completed，则以预算耗尽 Incomplete。

Action Prompt 超限发生在任何地图调用之前。Observation Prompt 只有在地图结果形成后才能计量；若超限，先持久化已经取得的 Observation，不调用 Judgment、不消费该次语义额度，返回技术 Failed，聚合状态保持上一份一致 candidate set。

## 14. OperationId 计划与输入完整性

所有 OperationId 由 Module 13 在公共调用前提供，Module 08 不接收 factory callback，也不自行生成。`CandidateResearchOperationPlan` 是不可变的有界槽位计划：initial 必须提供 6 个迭代槽；supplemental 必须提供调用开始时 `research.iteration` 的全部 remaining 数量（0..6），只有全部 gaps 已预先满足时允许 0。每个槽提供 Action、Judgment、candidate-set OperationId 以及 0..8 个 Place Observation OperationId；另提供 terminal successor OperationId。未用槽保持未消费。全部 ID 必须同 run、同 phase、逻辑步骤合法且全局唯一。

OperationId 与副作用一一映射：Action OperationId 同时消费该轮一个 `research.iteration` 并写 model-action Artifact；每个 Place Observation OperationId 同时消费一个 `research.place_call` 并写对应 Observation；Judgment OperationId 只写 model-judgment Artifact，semantic quota 由 Module 02 持有；slot candidate-set OperationId 只写仍需下一轮的中间 successor；terminal successor OperationId 是公共调用 singleflight/快速 replay 键，并只写最终 completed/incomplete/failed/no-op successor。Module 04 的 Budget Manager 和 Artifact Repository 是不同端口，同一 OperationId 在这两个端口分别作为同一逻辑副作用的幂等键是预期用法。

slot index 是本次公共调用内的 `invocation_slot_index`；run 级 `iteration_index` 等于该 Action 消费成功前的 `research.iteration.consumed`，范围 0..5。supplemental 的本地 slot 可从 0 重新开始，但 run 级 index 不重置。

入口首先 lookup terminal successor OperationId；它就是同请求 singleflight key。若找到 terminal candidate-set record，必须校验请求摘要、Ref 链和终态 payload 并逐字段重建原公共 outcome，检查发生在要求私有 run context 之前，因此 Completed/Incomplete/Failed/no-op 在 `close_run` 后仍可精确重放。若 terminal 不存在，再按固定 slot 顺序 lookup 已有 Action/Judgment/Observation/中间 candidate-set 以判断是可安全继续还是 indeterminate。发现已消费 OperationId 但对应 Artifact 缺失、部分写入无法证明、lookup 内容不符或同 key 不同内容时，返回 `operation_result_indeterminate`，禁止重新调用外部系统。

## 15. 确定性研究循环

一次迭代按以下顺序执行：

1. 公共入口先按 §14 查 terminal replay，再验证请求、前驱、run context、Prompt 资源、OperationId 和剩余预算；
2. 控制器计算候选目标、最高优先级 objective 和允许动作；
3. 用 Action OperationId 消费一个 `research.iteration`；
4. 构造 Action Prompt，执行 Module 02 字节 Gate 和 strict Action 调用；
5. 若得到 strict success，先完成整个批次的合同校验、最高目标策略校验、去重、预算准入和有序前缀计算；随后写 `accepted`、`policy_rejected` 或 `controller_rejected` model-action record；若为 LLM failure，写 `llm_failure` record。任何 record 都不含原始响应；
6. `controller_rejected` 或 LLM failure 进入 terminal Failed 写入，不执行 Place；`policy_rejected` 不执行 Place，按 progress=false 进入本轮完成/停止计算并在允许时继续下一轮；accepted NoUsefulAction 直接进入完成/停止计算；
7. accepted Place 前缀按 §13 完成全部独立预算记录的预占屏障；
8. 以最多 3 个 Action 为一 wave 执行，wave 不重叠；每个 issued、no-result、map-failure、unverified-result 或 reservation-barrier-not-issued 均保存独立 `place_observation`；
9. 同一 wave 的地图结果全部返回并收束后，按“模型 Action 顺序—供应商结果顺序—UID 首次发现顺序”计算 Search 中每个地点的容量 disposition；再按 Action 顺序持久化完整 Observation，之后才允许开始下一 wave；聚合也只按此确定顺序，不按网络返回顺序；
10. 若有新 Observation，构造并 strict 调用一次 Judgment；先验证所有 evidence handle 和 patch 业务不变量，再写 `accepted`、`controller_rejected` 或 `llm_failure` model-judgment record；rejected/failure 进入 terminal Failed；
11. 控制器按 UID 确定性组装 next state，并在内存中先计算进展、no-progress streak、Completed、全部 gaps 和有序 stop reasons；
12. 若仍可下一轮，使用本 slot candidate-set OperationId 写 `in_progress` successor；若 Completed/Incomplete，使用 terminal successor OperationId 写含精确终态 payload 的 terminal candidate-set record；
13. Failed 在不执行更低副作用的前提下，使用 terminal successor OperationId 写 failed terminal record，保存主失败、last-consistent ref、failure ref、预算和 retry/duplicate；
14. Repository 写入成功后，公共 outcome 必须逐字段从所写 terminal record 重建；未停止时才开始下一轮。

控制器完成检查不调用模型；模型建议结束不等于结束。

同一 wave 中任一非取消技术错误发生后，已经发出的 sibling 用 `asyncio.gather(..., return_exceptions=True)` 收束，成功结果照常持久化；不启动后续 wave、不执行 Judgment、不推进聚合候选集。收束并尝试持久化所有可保存证据后，按 §23 全序选一个主失败并写 terminal Failed。取消必须取消未完成 sibling、只等待已经开始的协程完成资源清理，不再写新 Artifact，然后原样传播 `CancelledError`。

## 16. 进展与停止

进展只包括：新增 eligible UID；完成 must 匹配；解决 avoid、角色或核心事实冲突；取得有效 Details enrichment；覆盖此前缺失兴趣；满足 supplemental gap。重复 UID、NoResult、结果全部排除、无有效 Details、重复 Action、无效批次或模型建议停止均不算进展。每个已完成迭代都必须重新派生状态、全部 typed gaps、进展、streak、Completed 与停止原因；即使本轮没有新 Observation、Place 调用或 Judgment，也不得跳过该步骤。

连续两个已完成迭代没有进展返回 `Incomplete(no_progress)`。`NoUsefulActionProposed` 只有在至少已有一轮无进展、没有任何合法白名单动作或剩余预算不能容纳任何合法动作时才可接受；否则是非法模型输出技术失败。

停止原因采用必要且充分判定，可并存并按 §17.2 排序：`candidate_capacity_reached` 当且仅当 admitted=128 且仍有未满足的新候选目标；`place_budget_exhausted` 当且仅当 Place remaining=0 且仍有可由 Place 动作推进的目标；`iteration_limit_reached` 当且仅当 iteration remaining=0 且尚未 Completed；`no_progress` 当且仅当 streak=2；`no_legal_action` 当且仅当仍有未满足目标、预算/槽位尚允许至少一个动作，但控制器枚举后的白名单动作集合因锚点、UID、容量、已尝试 Details 或精确重复而为空；`useful_supply_not_found` 当且仅当每个当前供应类 gap 都至少执行过一个合法专门 Search，而这些已执行动作只产生 NoResult、重复 UID、被阻断 UID 或无有效 enrichment，仍没有满足该 gap。后两者可以与 no-progress 同时出现，但 `no_legal_action` 不能仅因预算为零成立。

## 17. Completed、Incomplete、Failed 与取消

### 17.1 完成条件

`CandidateResearchCompleted` 只有同时满足下列条件才成立：

- eligible attraction 达到 `min(6 × sightseeing_days, 60)`；
- eligible restaurant 达到 `min(4 × sightseeing_days, 40)`；
- 候选身份稳定；
- 每个 effective interest 达成 Search + eligible 双门槛；
- must 研究义务完成；
- 无影响当前完成目标的未解决 must/avoid 或核心身份冲突，且不存在会阻断目标计数或指定 supplemental gap 的 unresolved 角色；未被任何完成目标采用的额外 `conflicted` 候选已经隔离时不阻止完成。候选已由一个 supported 角色取得资格时，另一个未被本次目标采用的 unresolved 角色本身不阻断完成；
- Details 成功覆盖至少 `ceil(eligible UID / 3)`；
- initial 城市景点 Search 已执行；
- initial 酒店 Nearby 餐厅 Search 已执行；
- lunch、dinner 各达成双门槛；
- supplemental 调用时，本次全部未预先满足的 typed gaps 均已满足。

模型不参与最终完成判断。Completed 只返回 candidate-set ref、数量摘要、覆盖摘要和预算摘要，不复制候选列表。

### 17.2 正常不完整

`CandidateResearchIncomplete` 保留已核验候选，不是技术故障。它包含 candidate-set ref、数量与覆盖摘要、预算摘要、剩余 gaps 和有序 `stop_reasons`。`gaps` 仍为非空 1..128；每个 `ResearchGapSummary.evidence_indexes` 的结构容量为 0..8、tuple 内唯一，并按相关 Observation 首次产生顺序排列。

空 `evidence_indexes` 只在以下上下文不变量全部成立时合法：控制器能仅凭规范化请求、不可变 requirement、当前候选状态、预算状态或已成立的停止条件证明该 typed gap 未满足；当前 candidate-set 的 Observation Ref 目录中不存在与该 gap target 相关且已验证的 Place Observation；并且没有合法相关 Ref 可用。只要存在一份相关且已验证的 Place Observation，就必须保存 1..8 个相关 indexes。不得为满足最小基数而虚构 Ref、复用无关 Observation，或用 model-action、model-judgment、hotel、OperationPlan Artifact 代替 Place evidence。

“相关”必须由已验证结构关系确定，不做文本相似度猜测：Observation 对应的 accepted Action/objective/fingerprint 或 `SearchCoverageRecord` 命中 gap 的 role/interest/meal/area target；Observation 被目标 candidate 的 evidence/details 目录引用；或它经 accepted Judgment 被目标 must/avoid group state 引用。area、replacement 等需要 Action 参数才能证明的分支，校验器必须解析同 run 已验证的 model-action/Observation Ref 链；无法证明关联就视为不相关。构造终态和 terminal replay 都使用同一规则。

上述空证据 gap 仍是业务性 `Incomplete`，不得改写为技术 `Failed`。它只是可重放的终态摘要，不自动成为合法 supplemental 输入：`SupplementalGap.evidence_refs` 继续要求 1..8 个相关且已验证的 Place Observation Ref；在 Module 13 取得合法证据前，不得从空 indexes 合成 Ref、自动调度该 gap 或制造新证据。该限制不放宽 Judgment/JudgmentPatch 的证据合同。

`stop_reasons` 是非空 tuple，按以下优先级排列并允许同时出现：

1. `candidate_capacity_reached`；
2. `place_budget_exhausted`；
3. `iteration_limit_reached`；
4. `no_progress`；
5. `no_legal_action`；
6. `useful_supply_not_found`。

must、兴趣、餐次、Details 或 supplemental 未满足的具体内容属于 gaps，不用继续扩张 stop reason 枚举。只要 candidate set 中至少一个 eligible UID，Module 13 可把 Incomplete 交给 Module 09；eligible 为零时不得调用 Module 09。

核心事实冲突不新增第九种 gap。冲突候选被隔离后，若数量、兴趣、餐次、must、Details 或指定 replacement 目标因此未满足，使用对应既有 typed gap；若全部完成目标仍由其他 eligible 候选满足，则不产生 gap，并允许 Completed。

### 17.3 技术失败

`CandidateResearchFailed` 包含：失败判别 union、`last_consistent_candidate_set_ref | None`、`failure_artifact_ref | None`、安全预算摘要、顶层 `retryable` 和 `possible_duplicate`。顶层标记必须与具体分支一致。

失败分支固定为：

- `llm_failure`：`invocation_kind` 为 action/judgment，保留 Module 02 `LLMErrorCode`、安全 retry/duplicate 和适用的 actual/max bytes；
- `map_failure`：tool、action ordinal、Module 06 `BaiduMapErrorCode`、capability 和失败 Observation ref；
- `control_failure`：使用 Module 08 封闭 `CandidateResearchControlFailureCode`。

控制失败码使用附录 A.7 的完整封闭集合。未知上游异常映射为 `dependency_contract_error`，不暴露类名或自由文本。

技术失败不得由 Module 13 改写为 Incomplete，不得自动重启，也不得自动把最后一致 Artifact 交给 Module 09。

### 17.4 取消

`asyncio.CancelledError`、`KeyboardInterrupt`、`SystemExit` 不包装为 typed outcome，完成不产生新持久副作用的必要资源收束后原样传播。取消以后不得启动新模型、地图、预算、Artifact、判断、聚合或事件副作用；已发出的地图调用预算不退还。取消前已 confirmed-applied 的预算记录不回滚；若其 Observation 尚未成功持久化，后续同槽 replay 必须返回 `operation_result_indeterminate`，不得补发地图或补造证据。

## 18. Artifact 合同

### 18.1 类型与注册

Module 08 精确注册四类 `1.0` Artifact：

| artifact type | 内容 | 不包含 |
|---|---|---|
| `candidate_research.model_action` | Prompt 资源版本/hash、输入摘要、iteration/objective、strict Action 成功输出及语义等值的 Artifact-local invocation metadata snapshot，或安全 Artifact-local LLM error snapshot；成功分支保存完整 query/tag、去重/准入结果 | Prompt、原始模型响应、reasoning、上游 DTO 实例 |
| `candidate_research.model_judgment` | 可见 Observation refs、strict JudgmentPatch、映射后的安全证据 refs及语义等值的 Artifact-local invocation/error snapshot | 任意 rationale、原始响应、上游 DTO 实例 |
| `candidate_research.place_observation` | action ordinal/tool、action/fingerprint hash、语义等值的完整 Artifact-local Search/Details outcome snapshot 或安全失败 snapshot、验证状态 | query/tag、原始 MCP/百度 payload、上游 Place outcome 实例 |
| `candidate_research.candidate_set` | 不可变前驱 ref、运行摘要、最多 128 个候选快照、判断现态、证据 refs、覆盖、gaps、预算、停止状态 | 每次 Search 完整数组、完整 Details、must/avoid 原文 |

每个 Artifact 内容均在写入前计算 canonical bytes 并执行 1 MiB 预检；超限技术失败，不截断。Repository 总体 2048 个 Artifact、64 MiB 的限制仍由 Module 04 执行。

上表中的上游 Module 02/06 DTO 只存在于 service 边界；Artifact 注册类型图始终使用附录 A.8 的本地 snapshot，禁止直接嵌入上游实例。

### 18.2 事实与元数据

每个成功模型调用在服务边界读取 Module 02 `InvocationMetadata`，显式复制为等值的 Artifact-local metadata/usage snapshot。candidate set 精确保存控制器可证明的 semantic consumption、成功结果已公开的 provider attempts 和 usage 合计，并同时保存 `provider_attempts_complete`、`usage_complete`。失败结果未公开 attempts/usage 时不得读取 `ModelOperation` 私有计数、不得猜测或补零；已知合计保持不变，complete 标记置 false。公共 outcome 只保存同一安全预算摘要，不复制逐次明细。

semantic consumption 可由公共结果确定：success 计 1；`schema_unsupported`、`input_too_large`、`quota_exhausted` 在 Module 02 的 claim 之前或未成功 claim，计 0；其他 `ainvoke_structured` failure 计 1。Module 08 在调用前保证不超过 12，因此意外 quota-exhausted 仍是技术失败而不是业务预算耗尽。

每个 Place 调用保存一份完整语义等值的 Artifact-local 规范化 Observation snapshot。供应商 `provenance.verified` 必须为 true；为 false 时不得读取或复制任何 Place 字段，保存安全失败证据并返回 `unverified_result`。经验证的 Module 06 `provenance.retrieved_at` 作为外部事实获取时间保留；ArtifactRef 的 UTC `created_at` 是独立的持久化时间，两者不得互相替代。

### 18.3 聚合与引用

candidate set 保存候选核心快照、状态、research role、兴趣/餐次关联、must/avoid group 结果和 Details 状态。为避免 128 个候选重复复制同一 Ref，它在顶层保存按首次产生顺序去重的 Observation/Action/Judgment Ref 目录，候选与判断记录只保存目录整数索引。完整 Search 数组及 Details 只在 Observation Artifact；Module 09 先读聚合候选集，需要时由索引解析 Ref 后读取完整证据。gap summary 的非空 `evidence_indexes` 也只能索引该 candidate set 顶层 Observation Ref 目录；空 tuple 的合法性必须由 candidate-set state/terminal record 的上下文 validator 按 §17.2 重新证明，不能只依赖分支 DTO 的结构校验。

公共 outcome 不复制候选名称、地址、坐标或“前几个候选”，避免 Workflow State 膨胀、多份事实不一致和隐含排名。

### 18.4 replay 与失败关闭

每次外部调用遵守 Module 04 §12：lookup OperationId；已有 Ref 时校验 type/version/run/hash/内容并 replay；不存在时消费预算、调用外部系统、立即持久化；只有持久化成功才允许后续 await 或状态推进。terminal replay 对每个 gap 重新校验 discriminator、typed target、非空 evidence index 范围及空证据上下文不变量；不能因单个 summary DTO 可解析为空 tuple 就绕过 candidate-set 级验证。

精确 replay 不消费模型、Place 或迭代预算，不创建新 Artifact。相同 OperationId 不同内容、Ref 不匹配、部分结果不确定或已消费但缺 Artifact 均失败关闭。Repository 返回 fresh typed DTO，服务不得缓存或返回可变内部引用。

candidate-set Artifact 的 `record` 是附录 A.8 的严格 terminal/in-progress union。中间轮只保存 in-progress；公共调用的最后一次写入必须使用 terminal successor OperationId 保存 completed、incomplete 或 failed 的完整可重建 payload。no-op supplemental 使用 completed/incomplete record 的 `transition_kind="no_op"`。返回 outcome 必须由刚读回的 fresh terminal Artifact 加其 Ref 构造，不能从另一份内存对象返回。

唯一的最后一跳例外是 **terminal candidate-set 自身写入失败**：不得递归尝试向同一个失败 Repository 再写 terminal Failed。服务直接构造只含安全 `control_failure/persistence_error` 的内存 `CandidateResearchFailed`，按 A.7 固定为 `retryable=false, possible_duplicate=true`；即使 Repository 报告明确拒绝，也采用这一保守公共标记，避免再增加一套末跳错误合同。该 terminal ID 的同进程 singleflight waiter 必须复用同一个内存结果，但此结果不承诺跨调用、跨进程或 `close_run` 后 replay。后续调用若 terminal 仍不存在，且任一计划内预算已消费或已有部分 Artifact，必须返回 `operation_result_indeterminate` 并禁止重发外部调用；只有成功持久化的 terminal Failed 才享有精确关闭后 replay。若 terminal 写失败前已经存在 last-consistent candidate set 或失败证据 Ref，内存 Failed 可安全引用它们，但不得伪造 terminal candidate-set Ref。

## 19. Supplemental 缺口合同

`SupplementalGap` 是以下八分支严格判别 union：

- `candidate_count_shortfall`；
- `interest_coverage_missing`；
- `area_coverage_missing`；
- `meal_coverage_missing`；
- `must_visit_resolution_missing`；
- `avoid_resolution_missing`；
- `candidate_details_missing`；
- `replacement_candidate_needed`。

每个 gap 必须含稳定 gap ID、类型化目标、适用的兴趣/餐次/UID/合法锚点/半径和 1..8 个相关且已验证的 Place Observation evidence Ref；禁止任意自由文本 Prompt 扩大任务。Module 13 验证并构造请求，正常情况下自动执行，无需用户再次确认；Module 08 不主动监控 Module 09。终态 summary 的空 evidence indexes 不满足此输入合同，不能自动转换为 supplemental 请求。

`avoid_resolution_missing` 的 typed target 固定为一个 immutable confirmed avoid `requirement_group_id`、该 group 下 1..100 个唯一且按原 requirement item 顺序排列的 `requirement_item_ids`，以及 1..128 个唯一且按 admission ordinal 排列的 `candidate_uids`。这些 UIDs 精确表示当前仍对该 group 为 ambiguous/unresolved 的已准入候选；不得携带 verdict、自由文本、原始供应商 payload 或不属于该 group 的 item。group/item/candidate 与前驱 state 任一不一致时拒绝整个请求，不静默收缩或扩张 target。

该 gap 当且仅当 target 中每个仍属于当前候选集的 UID 都已针对指定 avoid group 得到非歧义判断时满足：`no_match` 可继续参与 eligible/completion，`confirmed` 必须已经 excluded；任何 ambiguous/unresolved、缺少合法 Judgment evidence、UID 消失/替换未形成新 target，或后续新候选尚未判定时均不满足。target 集合变化后必须生成新的 gap/指纹，不能沿用旧 target 宣称完成。

同一 run 中，Module 13 为 gap 生成语义指纹，同一指纹最多自动 supplemental 一次；一次 supplemental 内仍可使用剩余多个迭代。只有需求、酒店、目标 UID、锚点或半径等实质变化才能产生新指纹。无进展、预算耗尽、容量满或供给不足后该指纹终结；技术失败不转换为新 gap。

replacement 只追加替代候选，不删除原候选，不因当前日程未采用而全局降级。只有 avoid、身份冲突或角色事实冲突改变资格；新结果不得把同一 UID 作为其自身替代。

## 20. 并发、生命周期与资源所有权

- Module 06 全能力共享并发上限为 3；Module 08 自身也固定最多 3，供应商界面显示的并行 10 和 10 QPS 不扩大应用并发。
- 一个 batch 最多 8 个有序 Action，拆为最多 3 个一 wave；下一 wave 必须等待上一 wave 完整收束。
- 服务为每个 run 持有恰好一个 `ModelOperation` 和私有 context；跨 run 不共享模型会话、指纹、预算或候选。
- 同 run 相同 terminal successor OperationId 的并发调用先进入 singleflight；所有 waiter 得到同一精确结果，不同内容争用同 ID 失败关闭。每个 run 另有一个执行锁：不同 terminal ID 的 initial/supplemental 也必须串行，禁止同时读取或推进同一候选链。
- 调用先建立/加入 terminal-ID singleflight，再取得 run 执行锁；取得锁后必须重新检查 terminal replay、predecessor/current-head、remaining OperationId slots、supplemental gap 指纹、预算和 run open/closed 状态，不能复用加锁前快照。释放锁后才完成 singleflight 广播。
- `close_run` 使用同一个 run 执行锁：已有业务调用时等待其完成或取消传播，不得关闭正在执行的 `ModelOperation`；取得锁后同步调用 operation `close()`、清理私有 context 并标记 closed。重复 close 幂等。不得关闭注入的 Module 06 adapter、Artifact Repository、预算管理器、runtime 或 Module 13 所有资源。
- Module 02 或 Module 06 的技术 retry 规则不得由 Module 08 复制。Module 06 工具调用零自动 retry，429 等按 typed error 返回。

## 21. 安全、隐私与可观测性

允许持久化经结构验证的业务事实、完整 Search query/tag、严格模型 DTO、安全 reason code、usage 和 ArtifactRef。query/tag 只存在于 `model_action` Artifact；Observation、candidate set、日志、错误、事件、outcome 和最终报告只放 hash/ref，不复制 query/tag。

禁止保存或记录完整 Prompt、系统指令、模型原始响应、隐藏 reasoning、供应商 payload、MCP content blocks、Secret、Authorization、endpoint、SDK/MCP/session 对象、traceback、任意异常文本、未经验证的自由文本结论。含正文的必要字段设置 `repr=False`。

Module 08 不自造通用 PII/DLP 正则过滤器，也不声称能发现全部敏感信息；安全来自输入上限、strict schema、字段白名单、Prompt 数据隔离、供应商 typed adapter 和最小化持久化。Module 13/04 拥有事件；Module 08 只输出 typed outcome、Artifact 和预算消费。

## 22. 下游消费与保留空间

- Module 09 只能使用 candidate set 中 eligible 候选；不得使用 excluded、withheld 或 conflicted。它负责最终选择、时间、路线、营业时段适配和日程数量，可按 Observation Ref 读取详情。
- Module 09 负责行程提交前的时间、路线、返程、候选资格和事实证据确定性校验；不把 unknown 判为已验证事实。
- Module 10 通过 Module 04 Repository 端口持久化 Module 08 Artifact／evidence，不改变候选资格、判断或 gap。
- Module 11 在恢复研究 run 时读取显式 ArtifactRef、checkpoint 和执行账本，不重置预算、不重复已有确定结果的外部调用。Module 08 永远输出 `budget_fit=not_evaluated`。
- Module 12 消费已完成行程和展示标签，生成结构化结果与中文攻略；不新增事实、不执行重规划、不读取隐藏模型上下文，并将当前费用标记为未核验。
- Module 13 负责 initial/supplemental 编排、OperationId 计划、gap 指纹、Incomplete 是否继续、终态和事件聚合。它只能从含 1..8 个合法 evidence refs 的 typed gap 构造 supplemental；面对 §17.2 的空证据终态 summary，只能保持该业务 Incomplete 或进入另行批准的证据取得流程，不得伪造 Ref、自动重放外部调用或把它改写为技术失败。

这些模块不得要求 Module 08 提前承担排程、路线、价格、饮食、行动限制或最终产品状态。

## 23. 错误优先级与副作用抑制

同一同步检查点出现多个问题时使用全序：取消/进程级传播；replay/OperationId 不确定；输入与前驱完整性；Prompt/模型合同；Artifact 持久化；预算基础设施；地图技术失败；正常业务停止。更高优先级一经成立，不执行任何尚未开始的低优先级副作用。

同一类别也必须确定：涉及 Action 的失败先按 action ordinal 升序，再按封闭 error-code 在对应 Enum 的声明顺序；不带 Action 的控制失败按 `CandidateResearchControlFailureCode` 声明顺序。wave 收束后先尝试保存每个可保存的 Observation；某个 Observation 的持久化失败高于任意 map error，多个持久化失败按 ordinal；否则多个 map error 按 ordinal，再按 `BaiduMapErrorCode` 声明顺序。只由全序第一项成为公共 Failed 的主失败并决定顶层 retryable/possible-duplicate，其他 sibling 失败只保存在各自安全 Observation，不改变主失败。

候选级事实冲突、NoResult、供给不足、容量或业务预算耗尽不是依赖技术故障。反之，模型 strict 输出非法、地图 typed exception、Artifact/预算/重放不确定不能降级成 Incomplete。

## 24. Demo 与 Production 演进边界

Demo 面向单用户，48 次 Place 调用只占供应商 50,000 次/日额度的 0.096%；无需提高并发或修改其他模块执行预算。Module 08 独立参考实现使用进程内 Artifact、执行预算和 run context；完整 Demo 的跨进程恢复由 Module 10/11 持久实现和 Module 13/14 装配提供。

Demo 资格优先证明主演示路径、公共合同、安全边界、Docker 离线回归和 70% coverage，不以穷举极低概率分支为前置。未来若提高 Production coverage 门槛，必须另行设计和授权，不能在当前 Gate 中隐式恢复更高门槛。

Production 可在不改变公共 DTO、结果语义、OperationId、Artifact 类型和预算消费顺序的前提下替换持久化、分布式 singleflight、限流器或供应商实现。若需要变更工具白名单、候选身份键、预算上限、Prompt schema、Artifact schema 或下游所有权，必须新版本设计与迁移，不得静默兼容。

## 25. 版本与迁移

Module 08 初始 schema 全部为 `1.0`。未知 artifact type、未知版本、未知 discriminator、未知 enum、额外字段或宽松可转换值一律拒绝；不提供双读、fallback、隐式迁移或就地覆盖。Prompt 资源版本/hash 与 DTO schema 分别版本化。`MODULE08-DESIGN-ERRATA-01` 发生在 Module 08 closeout、提交和发布之前，因此将 summary evidence 容量修正为 0..8 并加入第八个 gap 分支仍使用 `1.0`，不新增 Artifact type、migration 或 Prompt version；这是发布前冻结设计纠错，不是对已发布 `1.0` 的静默兼容升级。

任何 `1.0 -> 2.0` 变更必须先提供兼容矩阵、Artifact 迁移或明确不迁移政策、重放影响和下游升级顺序，并获得用户批准。当前没有 migration 或删除任务。

## 26. 测试分类与矩阵

未来实施至少覆盖：

| 分类 | 必测合同 |
|---|---|
| public/import | 包根精确导出 `AvoidResolutionMissingGap`、不单独导出各 `*Summary`；禁止私有 Module 02/04/06/07、MCP、SDK、HTTP 和 Module 09+ 导入 |
| DTO | strict/frozen/extra-forbid、字段顺序、required/default/null、tuple/容量、八分支判别 union、summary evidence 0/1/8 与 supplemental evidence 1/8 边界、JSON round-trip、fresh copy |
| Prompt/model | 两资源版本/hash、数据隔离、完整请求字节 Gate、Action/Judgment strict decode、非法输出零 repair、usage 保存 |
| action/policy | objective 优先级、1..8 顺序、白名单、锚点/半径、result_limit=50、query/tag 保存位置、fingerprint 精确规则 |
| map | Search/Details success/no-result、verified=false、typed errors、核心事实冲突、Details unknown |
| budget | 6/48/12、消费时点、最长前缀、issued 不退款、重复/replay 不消费、initial/supplemental 共享 |
| candidate | UID 去重、多角色、状态优先级、128 不回收、overflow 顺序、目标公式、Details ceil |
| must/avoid | 各 100、顺序与重复、稳定 item/group、多个 UID、四态压缩判断、同 UID 冲突、avoid-resolution typed target/satisfied/target-change |
| coverage | 兴趣/午晚餐双门槛、NoResult 不满足、营业时间 unknown、must ordinary Search |
| loop/stop | 迭代状态机、每轮含零 Observation 路径也派生状态/进展/gaps/stops、两轮 no progress、no useful action Gate、Completed 全条件、有序多 stop reasons、无相关 Observation 的业务 Incomplete |
| supplemental | 八 gap union、1..128、no-op successor、部分已满足、fingerprint 一次、avoid target 变化新 fingerprint、空证据 summary 禁止转输入、replacement |
| artifact/replay | 四类型注册、1 MiB 预检、完整 Observation、Search 逐地点容量 disposition、unverified-result 本地证据、aggregate refs、gap evidence 空/非空上下文校验、精确/关闭后 replay、内容冲突、indeterminate、terminal-write last-hop 确定失败/结果不确定/same-process waiter/later retry |
| failure | LLM/map/control 三分支、安全码、policy-rejected 与 action-batch-invalid 分离、优先级、last consistent ref、Incomplete 可继续、Failed 禁止继续 |
| concurrency/cancel | terminal-ID singleflight、同 run 不同 terminal ID 执行锁、同 predecessor 竞态、close_run 竞态、3 并发 wave、有序聚合、sibling 收束、屏障第 N 个 consume 取消、取消传播、无后续副作用 |
| security | 无 Prompt/raw/reasoning/payload/secret/异常文本；query/tag 只在 action Artifact；repr/log/event/outcome 白名单 |

所有测试默认离线，使用注入 fake；不得把 live provider 作为单元/合同 Gate 的前置。Module 08 自有 production line、branch、combined coverage 分别不低于 `70.00%`；必须使用同一单进程采集、`source=app.candidate_research` 和未舍入分子/分母，不得排除源码、降低分母或通过舍入过线。`num_branches == 0` 时 branch 记 100%，combined 按 `(covered_lines + covered_branches)/(statements + branches)` 计算，分母为零是配置错误。

为避免在演示冲刺中重复运行同一重型 Gate，08-5/08-6 只创建并验证 coverage harness、公式、失败关闭与安全合同，不执行完整 Module 08 coverage 采集。完整采集固定在全部代码实现和独立代码审查完成后、MODULE-CLOSEOUT 开始前，针对代码最终版执行一次。MODULE-CLOSEOUT 是最后一步，只同步状态说明和受影响的 Dockerfile，不重复 coverage；closeout 必须证明其没有改变 Module 08 生产代码和测试，才能引用紧邻其前的 coverage 证据。

`D-08-33` 记录 2026-09-02 的一次性历史验收例外及其原始结果。该例外已随代码变化失效；`D-08-34` 生效后，后续资格直接按当前 70% 门槛判断，不再引用该例外。历史百分比不得改写为数值达标。

## 27. Docker、回归与 MODULE-CLOSEOUT

正式实现和验收必须使用仓库 Docker test/runtime 构建路径、read-only 容器、`/tmp` tmpfs、`no-new-privileges` 及既有安全约束。宿主机测试不作为正式验收证据。Module 08 Gate 包含 compileall、unit、contract、单次 coverage 和完整离线回归；真实网络、MCP、百度或模型 live 调用不属于默认 Gate，未授权时必须报告 `NOT_RUN`。

代码、独立审查和单次 70% coverage 通过后，仍不能宣布 Module 08 COMPLETE。最后必须单独执行 `MODULE-CLOSEOUT`：核验并按真实事实同步 README、IMPLEMENTATION_STATUS 和 Dockerfile 影响；从同步后的源码重新构建 runtime/test 镜像，重跑 runtime 默认 CMD、受影响模块 Gate、compileall 和完整离线回归，但不重复 coverage。只有 closeout 证明 Module 08 生产代码与测试相对 coverage 基线逐字节未变，才可引用紧邻其前的 coverage 结果；closeout 独立审查通过后才具备提交申请资格。

## 28. 验收标准

Module 08 只有全部满足才可验收：

1. 公共 API、DTO、四类 Artifact 和版本与本文一致；
2. 直接依赖和工具白名单无越界；
3. 6/48/12、8 Action、并发 3、容量 128 等边界有正反测试；
4. initial、supplemental、Completed、Incomplete、Failed、取消和关闭后 replay 全部可证明；
5. UID、角色、must/avoid（含 `avoid_resolution_missing` typed target）、兴趣/餐次、Details 和完成条件均由确定性控制器正确计算；
6. 任何技术失败不被伪装成业务 Incomplete；
7. Artifact、预算和外部调用顺序满足 Module 04；
8. 安全测试证明禁止内容不会进入 Artifact、repr、日志、事件或 outcome；
9. 空证据 gap 只在 §17.2 严格上下文成立时出现，且不会被自动转换为 supplemental 输入；
10. 独立代码审查通过后、MODULE-CLOSEOUT 前的代码最终版单次 coverage 采集中，line、branch、combined 分别达到 70%；
11. Docker 离线 Gate、完整回归、MODULE-CLOSEOUT 和独立代码审查全部通过。

测试通过只是证据之一；代码审查和验收必须逐条对照本文及 Vibe Coding 执行契约，不能只看测试结果。

## 29. 实施与验收 STOP 条件

以下任一成立必须停止，不得扩大范围、绕过验证或反向修改设计：

- 分支、HEAD、工作区或阶段白名单与授权不符；
- 需要修改已冻结的 Module 01、02、04、06、07 公共合同；
- 需要新增依赖、迁移、删除或配置，但当前阶段未授权；
- 需要 Module 08 直接访问 MCP、HTTP、供应商 SDK、私有 Gateway 或 Module 09+ 实现；
- 不能满足 strict schema、Artifact 1 MiB、预算、OperationId、replay 或取消合同；
- Prompt hash、上游 Artifact、verified provenance 或 run context 无法验证；
- 发现聊天已批准决定之间或与上位真源存在真实冲突；
- 测试、coverage、Docker 或独立审查需要改变公共设计才能通过；
- 白名单外出现变更；
- 用户未授权的 stage、commit、merge、push、PR 或 deploy 将要发生。

小范围且明确的实现缺陷可在当前阶段白名单内直接修复；改变设计、扩大仓库范围或大改必须先取得用户批准。

## 30. 决策记录

### 30.1 上位合同直接继承

- `D-08-01`：直接依赖为 01、02、04、06、07；依赖方向单向。
- `D-08-02`：Module 02 提供 candidate-research Profile、strict decode、12 次 semantic invocation 和安全 metadata/error。
- `D-08-03`：Module 04 提供 candidate-research phase、Artifact/OperationId/预算/replay 基础设施。
- `D-08-04`：Module 06 提供 typed Place DTO、错误、并发 3 和原始 payload 隔离。
- `D-08-05`：Module 07 的 selected hotel Artifact 是唯一酒店事实输入；不可回写。

### 30.2 用户明确确认

- `D-08-06`：Module 08/09 保持独立局部 ReAct，由 Module 13 确定性编排 supplemental 闭环。
- `D-08-07`：工具白名单只含 Search/Details；模型批次最多 8，实际并发 3。
- `D-08-08`：业务预算为 6 个迭代、48 个 Place 调用；所有 pace 统一候选目标。
- `D-08-09`：景点/餐厅目标分别为 `min(6d,60)`、`min(4d,40)`，容量为累计不可回收 128。
- `D-08-10`：Search 固定返回合同上限 50，支持 city/nearby 和 1/3/5/10 km；完整 query/tag 只存 model-action Artifact。
- `D-08-11`：Details 成熟度为三分之一，NoResult 不重试；Search 核心事实足以准入。
- `D-08-12`：UID 唯一、多角色、核心冲突候选级隔离；状态优先级固定。
- `D-08-13`：must/avoid 各继承 100 条、保序保重复、每条可匹配多个 UID，使用四态压缩判断。
- `D-08-14`：兴趣和午晚餐使用 Search + eligible 双门槛；营业时间 unknown 可研究入池但由 Module 09 复核。
- `D-08-15`：pace、价格、预算、饮食和行动限制不评估。
- `D-08-16`：双 Prompt 版本/hash 锁定；Action/Judgment strict union；不保存原始模型内容。
- `D-08-17`：Action 精确指纹、跨 initial/supplemental 去重、重复不消费 Place 且不算进展。
- `D-08-18`：两轮无进展停止；忽略最高目标是消耗迭代但不消耗 Place 的业务策略拒绝；Completed 全条件由控制器判断；Incomplete 可带部分候选继续。
- `D-08-19`：四类 Artifact、调用级 metadata、聚合 usage、含逐地点容量结果的完整 Observation 和 successor 模式。
- `D-08-20`：七类 supplemental gap、同指纹最多自动一次、共享剩余预算和容量（原冻结数量决定；发布前已由 `D-08-31` 扩展为八分支，其余语义保留）。
- `D-08-21`：固定 OperationId 槽位、lookup-first replay、terminal-ID singleflight、run 执行锁和 `close_run`。
- `D-08-22`：失败按 LLM/map/control 三类安全结构表达；取消停止一切新的持久副作用并原样传播。
- `D-08-23`：优先使用标准库及既有成熟依赖，不新增无必要包。

### 30.3 仓库公共合同推导

- `D-08-24`：使用 Module 02 `ainvoke_structured` 两次独立语义调用，不使用原生 tool continuation。
- `D-08-25`：保留 Module 06 `retrieved_at` 作为事实获取时间；Ref `created_at` 仅表示持久化时间。
- `D-08-26`：verified=false 在读取 Place 字段前失败并保存本地安全 Observation；Module 06 typed error 映射而不复制异常。
- `D-08-27`：Artifact 注册由 Module 08 导出 registrations，调用方组合 registry，不修改 Module 04 全局 singleton。
- `D-08-28`：当前依赖已足够；无迁移、数据库、网络或 live API 设计前置。
- `D-08-29`：terminal Artifact 写入自身失败使用唯一 last-hop 内存 Failed，不递归写失败记录、不承诺持久 replay。

### 30.4 用户批准的发布前设计勘误

- `D-08-30`：问题是 controller-only 停止可能在尚无相关 Place Observation 时证明业务 gap，但原 `ResearchGapSummary.evidence_indexes[1..8]` 无法合法表示。决定把公共及 Artifact-local 终态 gap summary 的 evidence 容量改为 0..8；空 tuple 只允许 §17.2 的严格上下文，非空项仍只能索引相关 verified Place Observation。`SupplementalGap.evidence_refs` 和 Judgment 证据保持 1..8。拒绝虚构/错配证据、把 Action/Judgment/Hotel 当 Place evidence、或把该业务状态改写为 Failed。影响是 candidate-set/terminal/replay validator 与测试矩阵需覆盖空/非空上下文；Module 13 不得从空证据 summary 自动构造 supplemental。此为 closeout/提交/发布前纠错，schema/version 仍为 `1.0`。
- `D-08-31`：问题是 `D-08-20` 原七分支 supplemental 合同无法精确表达 avoid group 对指定候选仍未消歧。决定在发布前扩展为八分支，新增公共输入 `AvoidResolutionMissingGap`、公共终态 union 分支 `AvoidResolutionMissingSummary` 和 Artifact-local `ArtifactAvoidResolutionMissingSummary`，统一 discriminator `avoid_resolution_missing`；typed target 固定为 avoid group/item IDs 与按 admission ordinal 排列的 candidate UIDs，satisfied/指纹按 §19 与 B.5。拒绝 generic/free-text gap、复用 must 分支或只凭“执行过 Judgment”宣称完成。公共包根只新增输入类型，终态仍由 `ResearchGapSummary` 导出；四类 Artifact type、Prompt 与 schema/version 均不变。Module 13、public/import、DTO、policy、Artifact、replay 和测试矩阵必须同步。
- `D-08-32`：为尽快形成可演示 Agent，Module 08 production line、branch、combined coverage 门槛统一由 95% 调整为 80%。08-5/08-6 只完成 harness 与合同验证；完整 coverage 固定在全部代码和独立审查完成后、最后的 MODULE-CLOSEOUT 开始前，针对代码最终版执行一次。closeout 不重复 coverage，且只有生产代码和测试未变时才能引用该证据。Demo 独立审查只阻断 P0、P1 和高概率影响主演示的 P2；极低概率、纯防御性或不影响主演示的 P2/P3 可登记为 `DEFERRED_DEMO_NONBLOCKING`，但安全泄露、数据破坏、主演示崩溃和公共合同破坏不得延期。拒绝排除源码、伪造分母、舍入过线或用早期采集替代代码最终版。此决定只改变测试与审查资格节奏，不改变任何公共 DTO、Artifact、Prompt、运行时语义、依赖或 migration。
- `D-08-33`：用户于 2026-09-02 明确授权把唯一的 `MODULE08-FINAL-COVERAGE80-GATE-3` 作为当前演示版 coverage 验收例外。真实结果固定为 317/317 passed、line `83.37430167597765%`、branch `69.78947368421052%`、combined `79.32549019607843%`；这是对当前哈希代码最终版的风险接受，不是把 branch/combined 伪报为 80%，也不修改默认 harness。closeout 必须继续验证生产代码和测试逐字节未变；变更后例外失效，后续重新资格仍按 `D-08-32` 的 80% 默认门槛。
- `D-08-34`：用户于 2026-09-11 明确决定 Module 08 当前 Demo 的 production line、branch、combined coverage 门槛统一为 70%。本条覆盖 `D-08-32` 的当前数值门槛；采集公式、source、单进程、未舍入判断、零 skip/xfail/rerun、Docker 安全要求和最终采集时点保持不变。`D-08-33` 只保留为历史记录，不再作为后续资格例外。

### 30.5 为未来模块保留

- Module 09 决定每日数量、顺序、路线、时间和最终 must 选择；
- Module 10/11 分别持久化 Artifact 和可恢复执行状态，但不改变 Module 08 业务判断；
- 当前 Module 12 只展示费用未核验；可信旅行费用判定等待经批准的价格来源；
- Module 13 决定最终 demo_ready/degraded/failed 和 supplemental 调度；
- Production 分布式限流、隐私生命周期和长期可靠性仍须另行设计；Module 10/11 先实现 Demo 所需的 PostgreSQL 持久化和跨进程恢复。

当前无未决 Module 08 公共合同。

## 附录 A：规范性公共 DTO 字段目录

本附录中的字段顺序即声明顺序。除写出 `=...` 的字段外均 required；只有标出 `| None` 才可空。`BoundedText` 为 strip 后非空 UTF-8 字符串、最长 512 字符；`StableId` 为 strip 后非空、最长 128 字符；`ContentDigest` 和 `ActionFingerprint` 均为 `sha256:` 加 64 位小写十六进制。所有 tuple 上限在表中明确。

### A.1 Operation、输入和锚点

| DTO | 精确字段 |
|---|---|
| `HotelAnchorRef` | `anchor_type: Literal["hotel"]="hotel"` |
| `CandidateAnchorRef` | `anchor_type: Literal["candidate"]="candidate"; uid: BoundedText` |
| `ResearchAnchorRef` | `HotelAnchorRef | CandidateAnchorRef`，以 `anchor_type` 判别 |
| `ResearchIterationOperationIds` | `schema_version="1.0"; invocation_slot_index: int[0..5]; action_operation_id: OperationId; judgment_operation_id: OperationId; candidate_set_operation_id: OperationId; place_observation_operation_ids: tuple[OperationId,...][0..8]` |
| `CandidateResearchOperationPlan` | `schema_version="1.0"; run_id: UUID; iteration_slots: tuple[ResearchIterationOperationIds,...][0..6]; terminal_successor_operation_id: OperationId` |
| `CandidateResearchInitialRequest` | `schema_version="1.0"; confirmed: ConfirmedTravelRequirement; hotel_selection_ref: ArtifactRef; operation_plan: CandidateResearchOperationPlan` |
| `CandidateResearchSupplementalRequest` | `schema_version="1.0"; predecessor_candidate_set_ref: ArtifactRef; gaps: tuple[SupplementalGap,...][1..128]; operation_plan: CandidateResearchOperationPlan` |

Plan validator 要求 invocation slot index 从 0 连续递增；initial 精确 6 个，supplemental 精确等于调用开始时 remaining iteration，零 slot 只允许全部 gaps 已满足的 no-op supplemental。全部 OperationId 同 `run_id`、phase 为 candidate-research、彼此唯一，且每个 Place ordinal 对应 tuple 位置；terminal successor 不得与任何 slot ID 重复。

### A.2 Supplemental gap

每个 gap 先声明 `schema_version="1.0"; gap_type; gap_id: StableId; evidence_refs: tuple[ArtifactRef,...][1..8]`，再按分支追加：

| 分支 | 追加字段 |
|---|---|
| `CandidateCountShortfallGap` | `gap_type="candidate_count_shortfall"; role: ResearchRole; current_count: int>=0; target_count: int>=1`，且 current < target |
| `InterestCoverageMissingGap` | `gap_type="interest_coverage_missing"; interest: InterestTag` |
| `AreaCoverageMissingGap` | `gap_type="area_coverage_missing"; role: ResearchRole; anchor: ResearchAnchorRef; radius_km: Literal[1,3,5,10]` |
| `MealCoverageMissingGap` | `gap_type="meal_coverage_missing"; meal_intent: MealIntent; anchor: ResearchAnchorRef; radius_km: Literal[1,3,5,10]` |
| `MustVisitResolutionMissingGap` | `gap_type="must_visit_resolution_missing"; requirement_group_id: StableId; requirement_item_ids: tuple[StableId,...][1..100]` |
| `AvoidResolutionMissingGap` | `gap_type="avoid_resolution_missing"; requirement_group_id: StableId; requirement_item_ids: tuple[StableId,...][1..100]; candidate_uids: tuple[BoundedText,...][1..128]` |
| `CandidateDetailsMissingGap` | `gap_type="candidate_details_missing"; candidate_uids: tuple[BoundedText,...][1..128]`，UID 唯一 |
| `ReplacementCandidateNeededGap` | `gap_type="replacement_candidate_needed"; rejected_uid: BoundedText; role: ResearchRole; anchor: ResearchAnchorRef | None=None; radius_km: Literal[1,3,5,10] | None=None`，anchor/radius 同时有或同时无 |

`SupplementalGap` 以 `gap_type` 判别上述八分支。每个 gap 的 `evidence_refs` 继续固定为 1..8，且必须是同 run、可从前驱链验证、与 typed target 相关的 `candidate_research.place_observation/1.0` Ref；终态 summary 的空 evidence indexes 不能在此处转换为 Ref。`AvoidResolutionMissingGap.requirement_item_ids` 必须唯一并按原 requirement item 顺序，全部属于指定 immutable confirmed avoid group；`candidate_uids` 必须唯一、属于前驱当前候选集、精确覆盖该 group 下仍 ambiguous/unresolved 的候选并按 admission ordinal 排列。`gap_id` 不进入 semantic fingerprint；其余类型化目标、UID、锚点、半径和前驱 run/digest 进入。任意自由文本说明均不属于 DTO。

### A.3 Action 输出

| DTO | 精确字段 |
|---|---|
| `ProposedSearchAction` | `action_type="search_places"; ordinal: int[0..7]; objective_id: StableId; query: BoundedText; tag: BoundedText | None=None; area_mode: SearchAreaMode; anchor: ResearchAnchorRef | None=None; radius_km: Literal[1,3,5,10] | None=None; interest: InterestTag | None=None; meal_intent: MealIntent | None=None` |
| `ProposedDetailsAction` | `action_type="get_place_details"; ordinal: int[0..7]; objective_id: StableId; uid: BoundedText` |
| `ProposedActionBatch` | `outcome_type="proposed_actions"; effective_interests: tuple[InterestTag,...][2..4] | None=None; actions: tuple[ProposedAction,...][1..8]` |
| `NoUsefulActionProposed` | `outcome_type="no_useful_action"; reason_code: Literal["prior_no_progress","no_legal_action","insufficient_remaining_budget"]` |

`ProposedAction` 以 `action_type` 判别，`ActionModelOutcome` 以 `outcome_type` 判别。ordinals 必须从 0 连续递增且与 tuple 位置相同。city 要求 anchor/radius 均为 null；nearby 要求二者均非 null。`effective_interests` 仅在 initial 且需要模型决定兴趣的第一次 Action 输出出现；其他情况必须 null，控制器随后冻结。

### A.4 Judgment patch

`EvidenceHandle` 字段为 `handle_id: StableId`；它只是在当前 Prompt 中由控制器发放的短句柄，不是 ArtifactRef。reason code 使用按 judgment kind 分开的封闭 Enum，均为 `StableId` 的有限成员，不允许自由文本。

reason-code Enum 精确成员和值如下，声明顺序同时用于确定性排序：

| Enum | 成员值（按顺序） |
|---|---|
| `RoleReasonCode` | `name_semantics`; `provider_category_supports`; `provider_category_contradicts`; `details_support`; `evidence_insufficient`; `evidence_conflicted` |
| `InterestReasonCode` | `targeted_search`; `name_or_category_match`; `details_match`; `evidence_insufficient`; `evidence_conflicted` |
| `MealReasonCode` | `targeted_meal_search`; `business_hours_supports`; `business_hours_conflicts`; `business_hours_unknown`; `evidence_insufficient` |
| `RequirementReasonCode` | `exact_name_match`; `semantic_name_match`; `address_disambiguated`; `details_disambiguated`; `candidate_out_of_scope`; `evidence_insufficient`; `evidence_conflicted` |

| DTO | 精确字段 |
|---|---|
| `RoleJudgmentUpdate` | `candidate_handle: StableId; role: ResearchRole; verdict: RoleVerdict; reason_code: RoleReasonCode; evidence_handles: tuple[EvidenceHandle,...][1..8]` |
| `InterestJudgmentUpdate` | `candidate_handle: StableId; interest: InterestTag; verdict: MatchVerdict; reason_code: InterestReasonCode; evidence_handles: tuple[EvidenceHandle,...][1..8]` |
| `MealJudgmentUpdate` | `candidate_handle: StableId; meal_intent: MealIntent; verdict: MatchVerdict; reason_code: MealReasonCode; evidence_handles: tuple[EvidenceHandle,...][1..8]` |
| `RequirementVerdictException` | `candidate_handle: StableId; verdict: MatchVerdict; reason_code: RequirementReasonCode; evidence_handles: tuple[EvidenceHandle,...][1..8]` |
| `RequirementGroupJudgmentUpdate` | `requirement_group_id: StableId; requirement_kind: Literal["must_visit","avoid"]; evaluated_candidate_handles: tuple[StableId,...][0..128]; default_verdict: MatchVerdict; default_reason_code: RequirementReasonCode; default_evidence_handles: tuple[EvidenceHandle,...][1..8]; exceptions: tuple[RequirementVerdictException,...][0..128]` |
| `JudgmentPatch` | `outcome_type="judgment_patch"; role_updates: tuple[RoleJudgmentUpdate,...][0..256]; interest_updates: tuple[InterestJudgmentUpdate,...][0..512]; meal_updates: tuple[MealJudgmentUpdate,...][0..256]; requirement_group_updates: tuple[RequirementGroupJudgmentUpdate,...][0..200]` |

同一 patch 内相同语义 key 不得重复。default evidence 也必须来自本次 Prompt 可见集合；`evaluated_candidate_handles=()` 只允许表达 group 尚无可评估候选，此时 default 必须 unresolved。

### A.5 候选、匹配和详情现态

本节类型属于 Artifact-local snapshot 图，全部字段显式 required；表内 `Literal[...]` 只表示取值，不表示默认。

| DTO | 精确字段 |
|---|---|
| `ArtifactCoordinateSnapshot` | `coordinate_system: Literal["bd09ll"]; latitude: finite float[-90,90]; longitude: finite float[-180,180]` |
| `CandidateCore` | `uid: BoundedText; name: BoundedText; address: BoundedText; coordinate: ArtifactCoordinateSnapshot; provider_category: BoundedText | None` |
| `RoleAssessment` | `role: ResearchRole; verdict: RoleVerdict; reason_code: RoleReasonCode; evidence_indexes: tuple[int,...][1..48]`，每项索引顶层 observation-ref 目录 |
| `RequirementMatchState` | `requirement_group_id: StableId; verdict: MatchVerdict; reason_code: RequirementReasonCode; evidence_indexes: tuple[int,...][1..48]` |
| `CandidateDetailsState` | `status: Literal["not_requested","success","no_result"]; observation_index: int | None`，只有 success/no-result 才有合法目录索引 |
| `CandidateRecord` | `schema_version: Literal["1.0"]; admission_ordinal: int[0..127]; core: CandidateCore; state: CandidateState; roles: tuple[RoleAssessment,...][2..2]; interests: tuple[InterestTag,...][0..4]; meal_intents: tuple[MealIntent,...][0..2]; must_matches: tuple[RequirementMatchState,...][0..100]; avoid_matches: tuple[RequirementMatchState,...][0..100]; details: CandidateDetailsState; observation_indexes: tuple[int,...][1..48]; dietary_restrictions: Literal["not_evaluated"]; mobility_constraints: Literal["not_evaluated"]; budget_fit: Literal["not_evaluated"]` |

roles 精确按 attraction、restaurant 顺序。interests 和 meal intents 按枚举规定顺序而非模型顺序；match tuple 按原 requirement group 首次索引。顶层 Observation Ref 目录按首次产生顺序且唯一；候选内 indexes 按证据首次关联顺序且唯一。`CandidateState` 必须由内容重新推导并与保存值相等。

### A.6 摘要和公共 outcome

`ResearchGapSummary` 是以 `gap_type` 判别的八分支 Artifact-compatible union。八个分支分别为 `CandidateCountShortfallSummary`、`InterestCoverageMissingSummary`、`AreaCoverageMissingSummary`、`MealCoverageMissingSummary`、`MustVisitResolutionMissingSummary`、`AvoidResolutionMissingSummary`、`CandidateDetailsMissingSummary`、`ReplacementCandidateNeededSummary`。公共字段为 `gap_type; gap_id: StableId; evidence_indexes: tuple[int,...][0..8]`，分支业务字段与 A.2 对应分支完全相同，但不含 `schema_version` 和 `evidence_refs`；indexes 指向其 `candidate_set_ref` 内的 Observation Ref 目录，tuple 内唯一。所有字段 required；空 tuple 的结构解析只有与 candidate-set state 一起满足 §17.2 上下文不变量时才是合法业务记录，单个分支 DTO 可解析不等于可持久化或返回。

| DTO | 精确字段 |
|---|---|
| `KnownLLMUsage` | `prompt_tokens: int>=0; completion_tokens: int>=0; total_tokens: int>=0; reasoning_tokens: int>=0; prompt_cache_hit_tokens: int>=0; prompt_cache_miss_tokens: int>=0`，一致性规则继承 Module 02；全部字段 required，兼容 Artifact 类型图 |
| `ResearchCountSummary` | `attraction_count: int>=0; attraction_target: int[1..60]; restaurant_count: int>=0; restaurant_target: int[1..40]; unique_eligible_count: int[0..128]; admitted_slots: int[0..128]; candidate_capacity: Literal[128]` |
| `ResearchCoverageSummary` | `effective_interests: tuple[InterestTag,...][2..4]; covered_interests: tuple[InterestTag,...][0..4]; covered_meals: tuple[MealIntent,...][0..2]; must_item_count: int[0..100]; resolved_must_item_count: int[0..100]; details_success_count: int[0..128]; details_target: int[0..128]; initial_city_attraction_search_done: bool; initial_hotel_restaurant_nearby_done: bool` |
| `ResearchBudgetSummary` | `iteration_limit: Literal[6]; iterations_consumed: int[0..6]; place_call_limit: Literal[48]; place_calls_consumed: int[0..48]; semantic_invocation_limit: Literal[12]; semantic_invocations_consumed: int[0..12]; known_provider_attempts: int[0..24]; provider_attempts_complete: bool; known_usage: KnownLLMUsage; usage_complete: bool` |
| `CandidateResearchCompleted` | `schema_version="1.0"; outcome_type="completed"; candidate_set_ref: ArtifactRef; counts: ResearchCountSummary; coverage: ResearchCoverageSummary; budgets: ResearchBudgetSummary` |
| `CandidateResearchIncomplete` | `schema_version="1.0"; outcome_type="incomplete"; candidate_set_ref: ArtifactRef; counts; coverage; budgets; gaps: tuple[ResearchGapSummary,...][1..128]; stop_reasons: tuple[CandidateResearchStopReason,...][1..6]` |
| `CandidateResearchFailed` | `schema_version="1.0"; outcome_type="failed"; failure: CandidateResearchFailure; last_consistent_candidate_set_ref: ArtifactRef | None=None; failure_artifact_ref: ArtifactRef | None=None; budgets: ResearchBudgetSummary; retryable: bool; possible_duplicate: bool` |

`ResearchGapSummary` 不含任意解释文本。三个公共 outcome 用 `outcome_type` 判别，所有 candidate-set Ref 必须为同 run 的 `candidate_research.candidate_set/1.0`。当且仅当所有已发生模型调用都公开了对应数据时 complete 标记为 true；零模型调用时 known usage 六字段为 0 且两个 complete 均为 true。已知合计永远不包含未知失败调用的数据。

### A.7 失败分支

`CandidateResearchControlFailureCode` 是完整封闭枚举，声明顺序即同类优先级：

```text
operation_result_indeterminate
replay_mismatch
invalid_input
operation_plan_invalid
prompt_resource_invalid
action_batch_invalid
judgment_patch_invalid
artifact_too_large
artifact_capacity
artifact_error
budget_error
run_context_missing
run_closed
unverified_result
persistence_error
dependency_contract_error
service_usage_error
```

Python member name 是对应 value 的大写下划线形式，例如 `ACTION_BATCH_INVALID = "action_batch_invalid"`；其他 reason-code Enum 同理。

公共 marker exception 只有 `CandidateResearchError`、零参数 `CandidateResearchConfigurationError(code=prompt_resource_invalid)` 和零参数 `CandidateResearchUsageError(code=service_usage_error)`；均使用 `__slots__`、不可变，只暴露 `code`，不携带 message/payload。业务方法内部预期失败仍返回 typed Failed，不抛这些 marker；它们只用于构造/生命周期编程错误。

| DTO | 精确字段 |
|---|---|
| `LLMResearchFailure` | `failure_type="llm_failure"; invocation_kind: ResearchInvocationKind; error: LLMRuntimeError`，error.role 必须 candidate-research |
| `MapResearchFailure` | `failure_type="map_failure"; tool: ResearchTool; action_ordinal: int[0..7]; code: BaiduMapErrorCode; capability: MapCapability; observation_ref: ArtifactRef` |
| `ControlResearchFailure` | `failure_type="control_failure"; code: CandidateResearchControlFailureCode` |

`CandidateResearchFailure` 以 `failure_type` 判别。Map capability 必须与 tool 对应；failure Observation Ref 精确匹配对应 Place slot。control failure 不带 message、异常类名或 payload。

顶层标记映射固定如下：LLM 分支逐字使用 `LLMRuntimeError.retryable/possible_duplicate`；Map 分支中 rate-limit、timeout、transport、session 的 retryable=true，其余=false，只有 timeout/transport 的 possible-duplicate=true；Control 分支全部 retryable=false，只有 operation-result-indeterminate 和 persistence-error 的 possible-duplicate=true。公共主失败由 §23 全序选定，不能对 sibling 标记做 OR。

### A.8 Artifact DTO

以下全部是 `ArtifactSnapshotModel` 子类，所有字段 required、nullable 显式为 null。服务从上游 DTO 逐字段复制并再次交叉校验，不直接嵌入上游 BaseModel。

Artifact-local `ArtifactResearchGapSummarySnapshot` 同样是八分支判别 union。原七个分支与 A.6 对应分支字段语义等值；新增 `ArtifactAvoidResolutionMissingSummary` 的字段顺序固定为 `gap_type: Literal["avoid_resolution_missing"]; gap_id: StableId; evidence_indexes: tuple[int,...][0..8]; requirement_group_id: StableId; requirement_item_ids: tuple[StableId,...][1..100]; candidate_uids: tuple[BoundedText,...][1..128]`。所有 Artifact-local gap 分支的 `evidence_indexes` 均为 0..8、唯一；非空索引必须落在本 candidate set 的 Observation Ref 目录，空 tuple 必须由包含它的 `CandidateSetStateSnapshot`/terminal record 按 §17.2 验证。

| 基础 snapshot | 精确字段 |
|---|---|
| `ArtifactProvenanceSnapshot` | `source: Literal["baidu_maps_mcp"]; capability: MapCapability; retrieved_at: UTC-aware datetime; verified: Literal[True]` |
| `ArtifactInvocationMetadataSnapshot` | `role: Literal[ModelRole.CANDIDATE_RESEARCH]; model_name: Literal["deepseek-v4-flash"]; finish_reason: Literal["stop","tool_calls","length","content_filter","insufficient_system_resource"]; provider_attempts: int[1..2]; possible_duplicate: bool; usage: KnownLLMUsage` |
| `ArtifactLLMErrorSnapshot` | `code: LLMErrorCode; role: Literal[ModelRole.CANDIDATE_RESEARCH]; retryable: bool; possible_duplicate: bool; provider_status_code: int | None; retry_after_seconds: finite float | None; actual_bytes: int | None; max_bytes: int | None` |
| `ArtifactPlaceSummarySnapshot` | `uid; name; address: BoundedText; coordinate: ArtifactCoordinateSnapshot; provider_category: BoundedText | None` |
| `ArtifactPlaceDetailsSnapshot` | `uid; name; address; coordinate; provider_category: BoundedText | None; brand: BoundedText | None; telephone: BoundedText | None; rating: finite float[0,5] | None; business_hours: tuple[BoundedText,...][0..16]` |

`ActionDispositionSnapshot` 字段为 `action_ordinal: int[0..7]; action_fingerprint: ActionFingerprint; disposition: Literal["accepted","duplicate_action","not_admitted_budget_exhausted"]; reused_observation_ref: ArtifactRef | None`；只有 duplicate 有 reused Ref。候选容量是 Search 结果内逐地点准入事实，绝不使用 Action 级 disposition 表达。

| Model Action record | 精确字段 |
|---|---|
| `ModelActionAcceptedRecord` | `record_type="accepted"; output: ActionModelOutcomeSnapshot; metadata: ArtifactInvocationMetadataSnapshot; action_dispositions: tuple[ActionDispositionSnapshot,...][0..8]` |
| `ModelActionPolicyRejectedRecord` | `record_type="policy_rejected"; output: ActionModelOutcomeSnapshot; metadata: ArtifactInvocationMetadataSnapshot; reason_code: Literal["highest_priority_objective_ignored"]` |
| `ModelActionControllerRejectedRecord` | `record_type="controller_rejected"; output: ActionModelOutcomeSnapshot; metadata: ArtifactInvocationMetadataSnapshot; code: Literal[CandidateResearchControlFailureCode.ACTION_BATCH_INVALID]` |
| `ModelActionLLMFailureRecord` | `record_type="llm_failure"; error: ArtifactLLMErrorSnapshot` |

`ActionModelOutcomeSnapshot`、`ProposedActionSnapshot` 与 A.3 字段等值，但使用 ArtifactSnapshotModel、全部字段 required。`ModelActionRecord` 以 `record_type` 判别。

| Model Judgment record | 精确字段 |
|---|---|
| `ModelJudgmentAcceptedRecord` | `record_type="accepted"; patch: JudgmentPatchSnapshot; metadata: ArtifactInvocationMetadataSnapshot` |
| `ModelJudgmentControllerRejectedRecord` | `record_type="controller_rejected"; proposed_patch: JudgmentPatchModelSnapshot; metadata: ArtifactInvocationMetadataSnapshot; code: Literal[CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID]` |
| `ModelJudgmentLLMFailureRecord` | `record_type="llm_failure"; error: ArtifactLLMErrorSnapshot` |

`JudgmentPatchModelSnapshot` 与 A.4 模型输出字段等值；`JudgmentPatchSnapshot` 把每个 evidence handle 替换成 `evidence_indexes: tuple[int,...][1..8]`，索引外层 `evidence_refs`。两者全部 required；`ModelJudgmentRecord` 以 `record_type` 判别。

`PlaceAdmissionDispositionSnapshot` 字段为 `result_ordinal: int[0..49]; uid: BoundedText; disposition: Literal["admitted_new","existing_uid","not_admitted_candidate_capacity"]`。在一个 Search Observation 内，记录数必须与 `places` 长度相等，ordinal 从 0 连续递增，且每项 UID 必须等于同位置 `ArtifactPlaceSummarySnapshot.uid`；同一 wave 横跨多个 Search 时也先按 Action/结果顺序完成全 wave 的容量分配，再持久化 Observation。

| Place Observation record | 精确字段 |
|---|---|
| `PlaceSearchSuccessObservation` | `record_type="search_success"; places: tuple[ArtifactPlaceSummarySnapshot,...][1..50]; admission_dispositions: tuple[PlaceAdmissionDispositionSnapshot,...][1..50]; provenance: ArtifactProvenanceSnapshot` |
| `PlaceSearchNoResultObservation` | `record_type="search_no_result"; provenance: ArtifactProvenanceSnapshot` |
| `PlaceDetailsSuccessObservation` | `record_type="details_success"; details: ArtifactPlaceDetailsSnapshot; provenance: ArtifactProvenanceSnapshot` |
| `PlaceDetailsNoResultObservation` | `record_type="details_no_result"; provenance: ArtifactProvenanceSnapshot` |
| `PlaceMapFailureObservation` | `record_type="map_failure"; code: BaiduMapErrorCode; capability: MapCapability` |
| `PlaceUnverifiedResultObservation` | `record_type="unverified_result"; capability: MapCapability` |
| `PlaceNotIssuedObservation` | `record_type="not_issued"; reason_code: Literal["reservation_barrier_failed"]` |

`PlaceObservationRecord` 以 `record_type` 判别。`unverified_result` 是 Module 08 本地控制失败证据，不伪装成 `BaiduMapErrorCode`，不得携带或复制任何未验证 Place 字段；公共失败映射为 `ControlResearchFailure(unverified_result)`，其 `failure_artifact_ref` 指向该 Observation。not-issued 只允许预算记录已 applied、任何地图调用尚未发出的**非取消**预占屏障失败场景；取消后不得新建 not-issued。

`RequirementVerdictOverrideState` 字段为 `candidate_ordinal: int[0..127]; verdict: MatchVerdict; reason_code: RequirementReasonCode; evidence_indexes: tuple[int,...][1..8]`。`RequirementGroupState` 字段为 `group_id: StableId; requirement_kind: Literal["must_visit","avoid"]; requirement_item_ids: tuple[StableId,...][1..100]; evaluated_candidate_ordinals: tuple[int,...][0..128]; default_verdict: MatchVerdict; default_reason_code: RequirementReasonCode; default_evidence_indexes: tuple[int,...][1..8]; exceptions: tuple[RequirementVerdictOverrideState,...][0..128]`。

`SearchCoverageRecord` 字段为 `objective_id: StableId; objective_kind: ResearchObjectiveKind; action_fingerprint: ActionFingerprint; observation_index: int; role: ResearchRole | None; interest: InterestTag | None; meal_intent: MealIntent | None; eligible_candidate_ordinals: tuple[int,...][0..128]`。`SearchCoverageState` 字段为 `records: tuple[SearchCoverageRecord,...][0..48]; initial_city_attraction_search_done: bool; initial_hotel_restaurant_nearby_done: bool`。重复 Action 不新增 record。

`CandidateSetStateSnapshot` 字段为 `confirmed_requirement_digest: ContentDigest; hotel_selection_ref: ArtifactRef; effective_interests: tuple[InterestTag,...][2..4]; model_action_refs: tuple[ArtifactRef,...][0..6]; model_judgment_refs: tuple[ArtifactRef,...][0..6]; observation_refs: tuple[ArtifactRef,...][0..48]; candidates: tuple[CandidateRecord,...][0..128]; requirement_group_states: tuple[RequirementGroupState,...][0..200]; search_coverage: SearchCoverageState; counts: ResearchCountSummary; coverage: ResearchCoverageSummary; budgets: ResearchBudgetSummary; pending_gaps: tuple[ArtifactResearchGapSummarySnapshot,...][0..128]; iteration_no_progress_streak: int[0..2]`。state validator 必须重新派生每个 `avoid_resolution_missing` 的 group/item/candidate target，并验证所有 gap 的非空 evidence index 或空证据上下文；terminal completed 要求 pending gaps 为空，terminal incomplete 的 `gaps` 与 state.pending_gaps 逐项语义等值。

| Candidate-set record | 精确字段 |
|---|---|
| `InProgressCandidateSetRecord` | `record_type="in_progress"; state: CandidateSetStateSnapshot` |
| `CompletedCandidateSetRecord` | `record_type="completed"; transition_kind: Literal["normal","no_op"]; state: CandidateSetStateSnapshot; counts: ResearchCountSummary; coverage: ResearchCoverageSummary; budgets: ResearchBudgetSummary` |
| `IncompleteCandidateSetRecord` | `record_type="incomplete"; transition_kind: Literal["normal","no_op"]; state: CandidateSetStateSnapshot; counts; coverage; budgets; gaps: tuple[ArtifactResearchGapSummarySnapshot,...][1..128]; stop_reasons: tuple[CandidateResearchStopReason,...][1..6]` |
| `FailedCandidateSetRecord` | `record_type="failed"; state: CandidateSetStateSnapshot | None; failure: CandidateResearchFailureSnapshot; last_consistent_candidate_set_ref: ArtifactRef | None; failure_artifact_ref: ArtifactRef | None; budgets: ResearchBudgetSummary; retryable: bool; possible_duplicate: bool` |

`ArtifactLLMResearchFailureSnapshot` 字段为 `failure_type: Literal["llm_failure"]; invocation_kind: ResearchInvocationKind; error: ArtifactLLMErrorSnapshot`；`ArtifactMapResearchFailureSnapshot` 为 `failure_type: Literal["map_failure"]; tool: ResearchTool; action_ordinal: int[0..7]; code: BaiduMapErrorCode; capability: MapCapability; observation_ref: ArtifactRef`；`ArtifactControlResearchFailureSnapshot` 为 `failure_type: Literal["control_failure"]; code: CandidateResearchControlFailureCode`。`CandidateResearchFailureSnapshot` 以 `failure_type` 判别这三类，所有字段 required。

`CandidateSetRecord` 以 `record_type` 判别。Completed/Incomplete record 不保存尚未知的自 Ref；Repository 返回的 Ref 与 record payload组合后逐字段构造公共 outcome。Failed record 的 failure Artifact 是实际 model-action/model-judgment/place-observation Ref，可空只用于失败发生在能安全持久化该证据之前。

| DTO | 精确字段 |
|---|---|
| `CandidateResearchModelActionArtifact` | `schema_version: Literal["1.0"]; run_id: UUID; mode: ResearchMode; invocation_index: int[0..11]; invocation_slot_index: int[0..5]; iteration_index: int[0..5]; prompt_version: Literal["action-v1"]; prompt_sha256: ContentDigest; input_digest: ContentDigest; result: ModelActionRecord` |
| `CandidateResearchModelJudgmentArtifact` | `schema_version: Literal["1.0"]; run_id; mode; invocation_index: int[0..11]; invocation_slot_index: int[0..5]; iteration_index: int[0..5]; prompt_version: Literal["observation-v1"]; prompt_sha256; evidence_refs: tuple[ArtifactRef,...][1..48]; result: ModelJudgmentRecord` |
| `CandidateResearchPlaceObservationArtifact` | `schema_version: Literal["1.0"]; run_id; mode; invocation_slot_index: int[0..5]; iteration_index: int[0..5]; action_ordinal: int[0..7]; tool: ResearchTool; action_fingerprint: ActionFingerprint; result: PlaceObservationRecord` |
| `CandidateResearchArtifact` | `schema_version: Literal["1.0"]; run_id: UUID; mode: ResearchMode; predecessor_ref: ArtifactRef | None; invocation_request_digest: ContentDigest; record: CandidateSetRecord` |

三个 Ref 目录都按首次产生顺序去重，类型/version/run 必须匹配；所有整数索引必须落在对应目录内且 tuple 内唯一。Search success snapshot 保存 Module 06 返回的全部 1..50 个规范化地点和 retrieved-at；candidate set 不复制数组/details。`CandidateResearchArtifact` 注册 type 精确为 `candidate_research.candidate_set`；Failed record 不得被 Module 09 消费。

## 附录 B：稳定 ID、摘要与指纹算法

### B.1 唯一规范编码

全部摘要使用标准库 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`，再 UTF-8 编码并计算小写 SHA-256。所有 nullable 字段显式保留 null；tuple 变 JSON array 并保持规定顺序；Enum 写 value；UUID/datetime/ArtifactRef 使用其上游规范 JSON 表示。完整摘要格式为 `sha256:<64hex>`，短 ID 只取 digest 前 16 hex，但其完整 digest 必须同时存在于私有 run context 或对应 Artifact 的 digest 字段用于碰撞校验；短 ID 碰撞立即 `dependency_contract_error`，不得合并。

### B.2 requirement item 与 group

requirement item hash tree 精确为：

```json
{"confirmed_requirement_digest":"sha256:...","field":"must_visit|avoid","index":0,"text":"原始未规范化文本"}
```

ID 格式为 `reqitem:<field>:<index>:<digest16>`。group 只合并同 field 且 Python string 精确相等的条目，按首次 index 排序；hash tree 为 `{"confirmed_requirement_digest":...,"field":...,"item_ids":[...],"text":原始文本}`，ID 为 `reqgroup:<field>:<first_index>:<digest16>`。原文只用于内存/hash/Prompt，不进入 candidate-set。

### B.3 Objective

`ResearchObjective` 字段顺序为 `objective_id: StableId; kind: ResearchObjectiveKind; gap_id: StableId | None; requirement_group_id: StableId | None; role: ResearchRole | None; interest: InterestTag | None; meal_intent: MealIntent | None; anchor: ResearchAnchorRef | None; radius_km: Literal[1,3,5,10] | None; candidate_uids: tuple[BoundedText,...][0..128]`，所有字段 required、无关字段显式 null/空 tuple。

objective hash tree 是上述字段去掉 `objective_id` 后的规范 tree，并额外加入 frozen `confirmed_requirement_digest`、`hotel_selection_ref.content_hash` 和当前 predecessor candidate-set content hash（initial 为 null）；ID 为 `objective:<kind>:<digest16>`。控制器按 §10 生成唯一合法 objective tuple，并把完整 tuple放入 Prompt；模型只能回传其中 ID，不得自造。

### B.4 Action 指纹

Search tree 精确为 `{"action_type":"search_places","query":strip(query),"tag":null或strip(tag),"area":{"area_type":"city","region":destination}或{"area_type":"nearby","anchor":规范anchor,"radius_m":1000|3000|5000|10000},"result_limit":50}`。Details tree 精确为 `{"action_type":"get_place_details","uid":uid}`。两者直接输出完整 `sha256:<64hex>`。objective、interest、meal、ordinal 均不进入；除此处明确 strip 外不做任何规范化。

### B.5 Supplemental semantic fingerprint

tree 精确为 `{"confirmed_requirement_digest":...,"hotel_selection_content_hash":...,"gap_type":...,"typed_target":...}`。`typed_target` 按 A.2 分支字段顺序包含 role/count、interest、anchor/radius、meal、requirement group/item IDs、candidate UIDs 或 replacement UID；排除 `gap_id`、`evidence_refs`、predecessor candidate-set Ref 自身和当前计数等非语义证据。`avoid_resolution_missing` 的 `typed_target` 精确为 `{"requirement_group_id":...,"requirement_item_ids":[...],"candidate_uids":[...]}`，item IDs 使用原 requirement 顺序，candidate UIDs 使用 admission ordinal 顺序，不包含当前 verdict；任一 target UID 增删都改变 fingerprint，完全相同 target 则稳定等值。目标、UID、锚点、半径、需求或酒店实质变化会改变 fingerprint；输出完整 ContentDigest。

### B.6 请求摘要与短句柄

`invocation_request_digest` 是 fresh strict 公共 initial/supplemental request 的完整规范 JSON hash，包含 OperationPlan，用于 terminal OperationId replay 内容一致性。candidate handle 固定为 `c:<admission_ordinal>`；当前 Prompt 的 evidence handle 按可见 Ref 目录顺序为 `e:<index>`，objective handle 使用其正式 objective ID。句柄只在当前 Prompt 有效，控制器必须用当前映射表解析；未知、重复或跨 Prompt handle 导致 judgment controller rejection。
## 2026-10-11 批准增量：候选用途判断完整性

用户已授权更新文档、实现和离线测试；本增量覆盖旧的餐次更新语义，依据 M09「§28 用途准入与目标纠错」及 M13 同日接线增量执行，不增加研究、地图或模型额度。

U08-01：研究池与可选用途分离。新 candidate_set/2.0 内 candidate/2.0 使用 `meal_assessments`，每个候选固定按 lunch、dinner 顺序保存两项，字段为 meal_intent、verdict、reason_code、evidence_indexes。引用必须属于本候选，指向同一运行的 observation 目录；目录中的 ArtifactRef/content_hash 是证据版本。未评估或证据不足以 unresolved/evidence_insufficient 记录，不能自动确认为两餐；no_match 表示明确不支持。既有角色判断拥有景点用途，组合用途必须同时满足景点角色、餐厅角色和相应餐次。`meal_intents` 只从 confirmed 判断派生，不能独立写入。

U08-02：每次判断增量覆盖新发现或新证据涉及的餐厅，两餐均须有明确状态；遗漏由控制器保留为待确认，不能误准入，也不能因此立刻终止整个任务。所有新候选从待确认开始；判断调用仍使用既有每轮一次机会。普通更新不重复输出未变化候选的全部结论。

U08-03：定向餐次搜索与餐厅身份支持用途关联；营业证据独立。新增详情没有营业字段、或 verdict 为 ambiguous/unresolved 且原因 business_hours_unknown/evidence_insufficient，不撤销已经确认的关联。只有引用实际非空营业证据的明确 conflicts/no_match 等反证才可撤销对应用途。新增确认必须有本候选的定向搜索或非空营业证据；代码核验来源/字段/归属，不以脆弱文本 parser 声称具体营业时段已核验。M09 仍按原 compatible/conflicts/unknown 规则判断具体时段，unknown 允许待核验和 degraded。

U08-04：必要用途供给缺口优先补研；有足够合格主候选即可规划，不要求研究池全部合格，也不为可选备选补研。全部 restaurant 具有判断条目不等于全部可选。未准入候选仍可作为补研目标，不进入对应普通选择列表。

U08-05：candidate_set/1.0 和 candidate/1.0 历史内容按原 schema 只读，原字节/hash 保持；新运行写 2.0，不得把旧任务静默升级后恢复。现有模型行动、地图 observation、judgment Artifact 不改版本或原始响应。旧已确认 positive 标签可供历史读者投影，不反向补造旧用途证据。数据库表和历史行不迁移。

本次 Prompt 冻结为 observation-v2，资源 `app/candidate_research/prompts/observation_v2.txt`，SHA256=`bbc709dea02558363f8f5a06f33b8cb348a6b09d93b91e86620e575ae587c84b`；observation-v1 原文件保持只读。归属合法的较早定向搜索可以支持后来补确认：控制器按实际引用核验安全来源信号，Prompt仍投影本轮增量。用途资格纯函数由M08拥有，M09消费同一规则，餐次与数量统计都要求对应角色及avoid准入。

为证明跨日唯一地点的必要供给，新 `MealCountShortfallGap/2.0`（gap_type=meal_count_shortfall）携带 meal_intent、current_count、target_count、anchor/radius及evidence_refs；对应新summary额外保存精确 source_candidate_set_ref，不用observation_indexes冒充候选状态证据。新 supplemental request/2.0 的 count、meal-count、must 缺口可用精确前驱 candidate_set/2.0（唯一Ref）作为状态证明；M08在接受前重算合格数/组身份并核验current_count和未满足事实。其余分支仍引用关联Place证据；历史request/1.0只允许Place证据，旧summary/字节不变。新summary只允许出现在candidate_set/2.0，证明Ref必须等于其predecessor，禁止指向successor。

验收逐条对应 U08-01～05：任意来源新餐厅两项完整、遗漏仍待确认、无证据不能确认、unknown 不撤旧关联、真实反证撤对应资格、meal_intents 与判断一致、跨候选证据拒绝、旧字节/hash稳定、补研与重放不重领预算。耗时/token 只报告实际计数，不能预填供应商收益比例。

替换候选缺口必须使用实际补研run的前驱UID基线，并确认存在基线外、不是被拒UID且具有所需角色用途的新候选；历史已有合格点不能冒充新替代点。只有候选快照而没有补研基线时不能宣布该缺口满足；完成检查和终态缺口检查消费同一事实。

## 2026-10-11 批准修订：研究输入、增量状态与有界纠错（R08）

状态：用户已批准完整方案并授权文档、实现与测试，完成等待提交。本节覆盖旧文档冲突条款；不追溯改写历史资格。实施依据见本模块执行规范的 R 阶段；关联 M01 同日 R01、M09 R09、M13 R13 和 M14 R14。

R08-01 输入：明确兴趣允许1～4项且唯一，按枚举顺序冻结；未指定且授权Agent时仍选2～4项（证据不足沿原四项默认）。M01拥有有效性判断，M08调用其纯函数，不自动补兴趣。缺失/冲突在确认之前澄清，非法直接调用在任何外调/预算前拒绝。已确认需求与所有下游入口建立参数化合同测试。

R08-02 需求目录：从同一份已确认需求建立稳定kind/group/item ID与已确认地点名称的对应目录；initial及同进程supplemental保持同一目录。Action/Judgment实际Prompt均包含对应名称，文本仅为不可信需求数据，不是供应商事实或指令。所有group/item必须精确对应；缺目录或错配在外调前失败。不得从哈希猜名称，不在日志/安全摘要写需求原文。无私有run上下文的活动补研继续失败关闭，不靠历史产物补造名称。

R08-03 目标绑定：控制器指定本轮最高且有合法动作的objective；结构化schema将actions首项约束为该目标的主要动作，其余最多7项保持现有白名单。初次明确地点名称搜索由控制器绑定已确认名称；模型仍决定合法查询方式/后续有依据的扩展，不能用泛化查询冒充解析完成。策略拒绝携带稳定target、rule、actual/expected、允许动作及相关证据指纹进入下一次既有Action机会；重复时仅暴露当前目标。全部合同校验后才执行地图，不执行非法批次的合法子集。

R08-04 增量语义：角色按(candidate,role)、兴趣按(candidate,interest)、餐次按(candidate,meal)、要求判断按(group,candidate)合并。未提及保持；明确变更需候选归属证据；重复/冲突键拒绝。组的default只作用于本补丁evaluated集合，不能覆盖历史其他成员；先还原逐候选有效判断再合并并确定性压缩。所有生产及重放路径共用合并函数，保留不相交更新交换等价、相同补丁幂等、未更新条目不变等性质。资格、覆盖、计数、gap从合并后事实重新派生，不由模型直接修改。

R08-05 接受事务：先strict解析/引用/归属/增量范围校验，再在临时状态合并并执行完整不变量检查，通过后才记录accepted和发布状态。失败保留最后一致状态及实际预算/usage。持久化中断按既有OperationId/read-back处理，不承诺跨多个Artifact的数据库事务，不自动重发供应商请求。历史accepted不能绕过完整应用校验。

R08-06 收敛：用稳定目标、方案及相关证据指纹识别进展。解决必需供给/匹配/歧义或取得改变该目标判断的新证据才清除该目标停滞；换ID/措辞/顺序、无关候选增长、相同证据下结论反复不算。首个业务拒绝在余额允许时获得下一既有槽的定向修正；第二个同目标同证据的无效方案停止为action_correction_exhausted，不能冒充地图无结果。有效搜索无进展仍遵循有界no_progress，循环记录在补研中保持，不增加6轮/48地图/现有模型及重试额度。内部状态错误、未知引用和供应商失败不能伪装成可恢复业务缺口。

R08-07 准入：继续沿U08/U09确定性用途函数；新证据重算角色/餐次/must/avoid资格与必要缺口；有足够合格供给不等待整个池合格。未知不是满足，已有缓存不授予资格，饮食/行动适配等延期功能不扩张。

R08-08 版本：新candidate_set=3.0（候选记录仍2.0），允许单兴趣及记录纠错状态；新model_action/model_judgment=2.0；Place Observation保持1.0。旧candidate_set1/2及model_action/judgment1只读，原字节/hash与旧兴趣约束不变。公共summary允许1项；新输出/补研对新版本显式准入，M13 contract_id升级隔离旧活动执行。新Action/Observation Prompt另建版本和hash，旧资源不改。不新增SQL迁移或改M02/供应商/依赖；所有下游Ref/读取接缝同步，不能全局替换版本常量。

R08-09 错误：分别表达input_invalid、context_contract_error、action_correction_exhausted、judgment_patch_invalid、state_invariant_failed、真实replay_mismatch及供应商错误。活动期间异常不能依是否已有run就归replay_mismatch或清零usage；保存最后一致Ref、已知调用消耗及完整性标记。M13/M14只投影安全code与中文说明。

R08-10 验收：输入1/多/自动/空/冲突；actual Prompt的名称/编号及同名/重复/多组；全量→局部、不同默认、组交错、遗漏/撤销、重复应用和生成式更新；反向/无关证据、零地图纠错、循环/补研；accepted之前失败和保存后重放；旧字节/hash及新Ref贯通M13/M09/M12/M14。三城归一化离线材料仅作回归，不调用模型/地图，不修改旧任务。真实验收限新建3城不同要求各1次，无业务自动重跑，实际逻辑调用、attempts、耗时、输入/输出/思考token及地图统计完整核对；只有离线Gate通过后运行，失败保留并报告，不能靠重复刷绿。

R08-08实现边界：旧1/2注册到CandidateResearchLegacyArtifact，新3注册到CandidateResearchArtifact；新纠错目录是必填字段（无纠错为空tuple），包含target/evidence/plan/rule/attempts/active。有效修正关闭active反馈但保留该目标同证据的累计拒绝次数；新候选或新操作编号不隐式清账。旧模型无新字段、不使用序列化器删字段，M12按Ref版本选择精确注册模型。

R08审查落实：最高目标的允许动作及详情UID目录由同一控制器规则投影到实际Prompt、首项schema、整批预检和停止判断。明确名称尚未首查时只允许Search；完成首查后的Details仍限该要求的未解决候选。no_useful的预算理由只接受实际地图余额为0；不能因目标类型未列入判断而停止。同一must/avoid组在initial与supplemental中共用稳定目标身份；有效修正只关闭反馈，不清除拒绝历史。首次实质解决计一次进展，同证据反复改判不重复计功；有效搜索连续两轮无进展由控制器停止，首次业务拒绝的下一既有修正槽仍保留。读回故障保留已知消耗并构造满足strict DTO flags的安全结果。

R08-02/03审查补充：名称首查绑定覆盖批次中的每一个需求组；未获地图预算准入的组不提前记账。零候选前驱尚无候选聚合判断组时，schema2.0精确状态证明的must补研只可通过当前同进程冻结的kind/group/item目录核验，未知组、错条目、错类型、错误前驱及缺上下文不得借此放行。

R08-03生成合同一致性补修：联合类型的判别字段是本地JSON分支解析的必填输入，即使Python DTO允许默认值，向模型投影的每个discriminated union分支仍必须将discriminator.propertyName加入required。规则从schema导出，覆盖顶层/嵌套/数组中的全部分支，不硬编码outcome_type或action_type，不修改原DTO默认值，不猜补模型字段，不降低strict校验；最高目标及首项约束继续保留。本地重复更新键等语义不变量仍按原合同拒绝，不能将本次schema一致性修正解释为额外输出恢复授权。

## 2026-10-11 用户追加批准：共享两次输出恢复（R08-11～15）

本节根据用户随后明确批准覆盖 §13 的“每轮至多一次 Action/Judgment”及 R08-04/06 的直接失败边界；其他严格校验和额度保持。不是将失败数据视为有效，也不是增加 12 次调用上限。

R08-11 共享预算：一个研究 run 的 initial、supplemental、Action、Judgment 共用最多 2 次恢复调用，每次从原 12 次 semantic invocation 扣除。首次调用不占恢复额度；只有准备再次调用且余额存在才占 1 次恢复。仍用同一 ModelOperation；6 次研究迭代、48 次地图、单次 token 上限和供应商技术重试不变。恢复不新建研究轮、不再执行已完成的地图动作；在当前轮和不可变证据上重写完整输出。恢复耗尽或 semantic 余额不足，保留最后明确的输出/判断错误，不能假装成功或无限等待。

R08-12 白名单：M02 明确返回 structured_output_invalid（非法 JSON、schema/重复键约束等）或 output_truncated、通过结构校验但引用其他候选已知证据的补丁，以及遗漏控制器必要判断的输出，允许在共享额度内恢复。未知候选/证据/需求组、目录不一致、内部 state 不变量、存储/依赖错误、供应商/网络/鉴权错误、possible_duplicate 的不确定外部结果、取消均不进入恢复。重复组、角色、兴趣、餐次键仍由原 strict validator 拒绝，禁止自动合并/猜值/丢弃非法部分。Action 原业务目标纠错继续遵循 R08-06 的既有轮次，不借此扩大业务重试。

R08-13 反馈与状态：恢复 Prompt 使用当前完整合法输入及控制器生成的有限安全 code、恢复序号和输出规则，要求重新生成完整对象、唯一更新键、可见且属于候选的证据；不粘贴失败原文、异常文本或 reasoning。每次独立 interaction_call 保留实际请求/响应/验证记录。非法判断不得提交候选变更；只有完整补丁、证据归属、临时完整状态全部通过后才 accepted。恢复后的成功仅提交一次，地图消费不增加。

R08-14 持久化/重放：尚未发布的 model_action/model_judgment 2.0 增加 recovered 结果分支，包裹原最终结果与 1～2 个安全恢复记录（全 run 恢复序号、失败 semantic invocation 索引、白名单 code、可证明的 metadata 或 null）。原 1.0 禁止此分支，原 1.0 字节/hash及普通结果形状不变；candidate_set仍3.0，恢复状态由其模型 Ref 目录中的记录还原，同进程补研不重置。逐次记录及最终 metadata 分别计费；M02 失败未公开 metadata 时，已知合计保持、complete=false，完整真实 usage 仍由现有 M02 交互记录/最终统计核对，禁止补零或读私有计数。重放只使用相同 OperationId/Ref/hash 的已确认记录，不再次外调、不重复计费；未安全落盘的中断仍按既有 indeterminate/私有上下文丢失合同处理，不承诺任意中途恢复。无 SQL 迁移。

R08-15 验收：格式错误→成功、截断→成功、重复各类键→修正、证据归属错误→修正、连续两次修正仍错、Action/Judgment共享、initial/supplemental共享、semantic第12次边界、真实M02+离线adapter、恢复期间取消/保存失败/精确重放、旧版本拒新分支与原字节/hash。先定向、M08 owner coverage、完整离线 Closeout、逐文档双审；通过后另建隔离测试资源，追加上海/西安/成都不同要求各最多1次真实复验，不业务自动重跑，保留本轮前失败记录，逐城报告耗时及输入/输出/思考token。

R08-14补充：v2 controller_rejected中的proposed_patch是结构合法但未通过业务校验的失败审计，可保留不属于当前目录的语法合法句柄；它不是accepted证据，绝不应用到候选状态。v1原拒绝记录及accepted的Ref/归属/索引校验不变。最终失败重放还原最后一次失败的实际semantic成本及complete标志，预算已满不覆盖已落盘的错误。用户另批准消费M02只读semantic_invocations_consumed属性核对取消前后的0/1实际准入差；取消不重试，元数据未知标不完整。

## 2026-10-12 必要资格判断与研究收敛合同（Q08）

状态：用户已批准设计更新、实施、逐阶段双审和真实 API 验证；完成后等待提交。本节是当前运行的资格判断合同，验收同时对照 R08 的输入、严格校验、接受事务、计费和安全要求。

Q08-01 必要清单：控制器在调用前生成候选、判断类别、具体目标和候选自身证据组成的工作单。类别覆盖角色、餐次、must、avoid 和必要兴趣。新候选至少判断查询需要的角色；无确定查询角色时判断两种角色。新发现或证据变化的餐厅明确判断午餐和晚餐。每个 must 组至少需要一个匹配且具有可用用途的候选，普通候选无须匹配全部 must。准备进入普通规划的候选必须明确排除全部 avoid；必要兴趣仅要求承担该兴趣供给的候选判断。明确不适用的角色、已明确排除的候选无须为其补齐无用的餐次和避开判断。

Q08-02 判断覆盖：未判断与明确判断为证据不足分别记录。控制器保存每个已执行判断键及本次实际展示的候选自身证据版本，不从默认 unresolved 或默认证据索引猜测已经判断。明确 ambiguous/unresolved 是合法完成的判断，不强迫模型生成肯定结论；相同判断键在相同证据下不重复补判。按每个判断键的已展示规范事实集合核对新证据，分窗前去重；复制旧观察或重新组合旧窗口不重开该键。搜索已发现的候选没有可执行必要键时，不发起新的Judgment，实际Action和地图步骤仍保存；已有判断产物先精确重放。没有候选的NoResult轮沿原观察判断合同，不隐式改变该调用语义。新证据可以重开该键，营业未知保持既有餐次关联，明确营业反证按 U08-03 撤销。

Q08-03 接受和恢复：完整输出首先通过原严格结构、目录、候选归属检查，然后核对本工作单全部必要键；餐次等条件键按补丁合并后的实际角色核对。缺项为 judgment_required_missing，恢复反馈只包含安全键和规则，不包含失败原文。恢复使用同一工作单、同一证据和同一 ModelOperation，覆盖全研究任务共享两次上限，从十二次模型调用扣除，不增加研究轮或地图请求。完整补丁及临时状态通过后才保存 accepted、提交覆盖记录和候选状态，不能先接受合法子集。possible_duplicate、取消、未知引用、供应商、存储或内部状态错误不恢复。

Q08-04 旧候选补判：增加 judge_existing 研究步骤，只有一次 Judgment 模型调用，没有 Action 模型和地图请求。工作单包含选定旧候选、其可核验的历史事实、现有判断及缺项，不依赖当轮 Observation 是否包含它。每单最多十六个候选、二百五十六个判断键；输出中的必要和额外更新合计也不得超过二百五十六个不同键。每候选每次展示最多八个自身观察；优先最近详情及目标相关搜索，再取较早未消费证据。搜索结果裁剪为本单候选，不把同一观察中的无关地点送给模型。每个步骤最多一个工作单，未选中的候选不隐式计为已判断，剩余工作按后续余额继续。

Q08-05 任务选择：必去匹配已确认但因角色、avoid 或餐次不能准入时，先补现有资格，不再次按地点名搜索。只有尚无可用匹配的必去组享有该优先级；已经由本候选或其他候选满足的组不占用此优先级。其次处理其他必要供给的未判断项；明确证据不足时先用尚未消费的自身相关证据，确实需要新事实时再定向查详情或搜索其他候选；明确不合适时换候选或定向搜索。名称未查且未找到匹配证据时保留名称首查。地图前检查至少留有一次 Judgment 调用额度；剩余模型调用不足以支付 Action 和 Judgment 时优先补判，无法补判则准确停止，不进行无法评估的搜索。

Q08-06 收敛：必要缺项减少、取得可用用途、满足必要 must/兴趣/餐次供给，或取得与当前阻塞目标相关的新事实，才算进展。无关候选数量、句柄、顺序、文案变化不清除停滞。明确证据不足的相同键和证据不反复调用。仍有可执行必要补判且存在余额时，不被全局两轮 no_progress 抢先终止；补研继承覆盖、证据、纠错和共享恢复计数。Action 的同目标同证据两次无效方案上限保持。

Q08-07 准入与停止解释：所有选择和必要供给沿同一用途资格函数。已足够的供给不等待未使用候选全部合格；额外 unknown 不阻塞完成。无供给时区分地图没有候选、必要判断未完成、判断证据不足、已判候选均不适用、恢复耗尽、研究或模型预算耗尽。分类仅用于安全结果说明，不把未知转成 no_match，也不改变严格资格。M09 在任何规划模型或路线请求之前检查必要用途供给；M13/M14 使用明确中文终态说明。

Q08-08 持久化与版本：新 candidate_set/4.0 必填保存 qualification_state（判断覆盖及已完成研究步骤），候选记录仍为2.0；新 model_judgment/3.0 必填保存判断工作单，Prompt 为 observation-v4。模型行动保持2.0、Place Observation 保持1.0。步骤记录实际 search_and_judge 或 judge_existing、迭代序号、可空 Action Ref、实际地图 Ref、可空 Judgment Ref；补判步骤必须 Action Ref 为空且地图目录为空。候选集1/2/3和判断1/2注册原形状只读，原字节/hash不变，不能从旧数据猜补覆盖，也不静默续跑旧活动执行。M13 contract_id 隔离新执行，M12 按精确版本读取。PostgreSQL 沿通用 Artifact 存储，不新增 SQL 迁移。终态和已持久化操作精确重放不再次外调、不重复计费；取消及不确定存储保留原失败关闭规则。

Q08-09 验收：必要角色/两餐/must/avoid/兴趣遗漏、明确 unknown、组 default 的作用域、候选自身证据和跨候选反例、旧候选独立补判、不同证据重开和相同证据停止、已确认 must 的其他资格阻塞、无关增长及反复改判、额外 unknown 不阻塞、十二次模型/六步/四十八地图/共享两恢复边界、准入前后取消、保存失败及精确重放、旧版本原字节和新下游读取均需证明。离线通过及逐文档双审后，以三城不同要求各一次进行正式真实 API 验证，记录耗时、输入/输出/思考 token、模型及地图实际消耗，无自动重跑。思考 token 属于输出 token，不重复计入总量；没有实测证据不承诺提速比例。

Q08 持久化验证：工作单轮次与判断产物一致，证据窗口索引属于该产物的观察目录，accepted 更新仅引用相应候选窗口，并满足无条件和本补丁已激活的必要键。覆盖目标类型、需求组归属和完整容量均在 accepted 保存前验证；终态及前驱重放读取原判断与观察，按历史 accepted 顺序推导当时已支持的角色和排除结论，核对当时工作单的完整条件义务，重新推导显式覆盖并核对实际证据摘要。步骤目录须与实际行动、地图和判断产物的轮次及工作单类型一致；每条地图观察必须对应同轮最终accepted行动的序号、工具和指纹，详情身份也必须一致。duplicate_action和未准入项不提供新地图观察；预算预留后未真正调用的not_issued观察保留其已接受行动来源，不算供应商调用。不能删除行动目录后保留地图记录，也不能仅因候选集哈希正确就信任覆盖或“零行动、零地图”记录。证据摘要只包含候选自身事实与已接受搜索的角色、兴趣、餐次意图，观察编号、时间戳、重复事实和目标标识不制造新证据。

当前 observation-v4 冻结 SHA-256 为 4c00fe74f0e47390e9bddfa3d93251cca732a44de9376b485eb2eb11ec7d54cb；旧 Prompt 资源与哈希保持原值。

候选自身目录须逐观察证明真实身份，不能只检查窗口索引存在于候选声明的目录。成功搜索必须实际包含该候选的UID，且该条结果不是容量拒收；详情成功必须与已接受详情行动及候选UID一致，没有结果、失败或未发出的详情也只能关联该行动指定的候选。候选的首个观察必须是其首次接收的成功搜索，初始核心身份按既有六位坐标精度与该结果一致；后续同UID身份变化沿原冲突规则处理，不覆盖初始身份。新发现但因容量拒收的地点保留观察而不生成候选自身窗口。更改目录、判断、窗口、覆盖记录并重新计算哈希，也不得使其他候选的搜索或详情成为自身证据。

Q08-07 的统计规则：候选集4.0的数量、兴趣、餐次、详情及必去满足统计按实际可用用途派生。景点用途计入景点和兴趣；至少取得午餐或晚餐用途才计入餐馆；具有任一用途才计入唯一可用数、详情成熟度和必去满足数。供给已经满足时，多余候选的未知判断不再生成全局避开补判任务；明确补研目标仍须满足其本身的资格和范围。

新版本状态沿原序列化字段，使用独立类型校验用途统计；候选集1/2/3保留原统计验证和原字节。当前候选必须保存全部声明的避开组，默认未知仍是未准入，不能以省略避开目录绕过门槛。M09同时核对必去和避开组的身份、顺序及项目范围；M12按候选集1/2/3/4精确选择原模型。M08公共入口额外公开旧版本模型、安全供给分类及纯来源核验函数，工作单和内部状态不向网页公开。

来源核验具体入口为 validate_qualification_sources(artifact, sources)：输入当前候选集4.0及按Artifact ID索引的精确ArtifactReadResult，返回None或固定ValueError；不执行存储、模型、地图请求，不改写输入。M08持久化重放与M12格式化共用该算法，核对Ref、精确类型、run/version、规范字节hash/size、实际步骤、候选自身窗口摘要及由accepted判断派生的覆盖。当前4只允许Action2和Judgment3引用，历史版本类保持原门槛。候选最终角色、兴趣、餐次、must和avoid必须逐键由实际来源推导，不能删除判断目录或覆盖记录后继续保留肯定标签。按每轮实际地图事实在前、accepted判断在后的顺序还原；角色和要求按作用域覆盖，兴趣按确认增删，餐次复用既有保留和反证规则，未被判断只允许初始化未知。真实地图身份冲突只能追加冲突原因和对应观察，不能提供肯定标签来源。recovered只消费最终accepted，拒绝记录不提供覆盖；旧1/2/3不调用此入口，不补工作单。此入口落实Q08-08和Q12既有来源核验职责，不新增公共工作单DTO或业务恢复、预算及数据库合同。

零可用供给的分类顺序：候选目录为空为research_no_candidates；存在候选但必要用途均已有明确否定或排除结论为research_candidates_unsuitable；仍有未知资格且因研究、地图或语义调用余额不能继续为research_budget_exhausted；其余情况按显式覆盖区分research_judgments_unfinished与research_evidence_insufficient。额外未知不覆盖明确不适用结论。必要判断经允许修正仍遗漏时使用research_recovery_exhausted，不声称每个判断已单独使用两次恢复。

模型余额停止使用 semantic_budget_insufficient：剩余调用无法支付下一步所需调用，且没有可执行的旧候选补判时返回未完成；Action 恢复用掉最后一次调用后也不得执行地图。候选集4.0保留原接受范围。补判进展记录第一次完成必要判断或取得可用用途与必要供给，同证据反复改判不能重置停滞。

## 31. 研究纠错计数与已有成果交付（C08）

### 31.1 背景、目标与范围

2026-10-19，用户批准独立记录纠错次数、在当前行动中修正错误、额度不足时交付已有核验结果，并授权文档双审、实施、两名新的代码审查者及最后一次真实API测试。当前基线为feature/m09-boundary-day-quality，HEAD b4c382e09695f3bdd71364b9aac1ae5d75ab4325；保留本轮开始时54项已有改动及原D盘工作区的46项改动。

已保存的cb5707fb-d569-448d-a18b-bca78051c0d1记录在离线公共入口重现：73个候选分五批判断，最后一轮8项行动中的4项详情请求超出所绑定目标的允许范围，整批未执行。M08已用6轮、7次模型、8次地图；最后一轮的首次策略拒绝直接形成Failed。本节规定当前写入合同；历史版本继续按原合同只读，不重写旧任务。

目标是使可恢复输出错误在当前研究轮次内获得有限修正机会，并使纠错停止与已有候选是否可用分别判定。保持正常研究6轮、模型总调用12次、地图48次、并发3、供应商每次语义调用技术尝试上限2及每批候选判断上限16。研究与规划职责不变，不自动扩大查询范围，不改变候选用途、证据及规划准入。

### 31.2 计数与调用准入

1. 每个M08 run具有纠错上限2。Action参数策略修正、结构化输出/截断恢复和允许的Judgment必要项/证据修正共享此上限；initial与supplemental共用，切换目标、改措辞或操作编号不重置。
2. 正常输出第一次被拒绝不等于已经执行纠错。仅一次修正调用已获模型运行时语义准入时，corrections_consumed增加1；这次调用也消费12次模型总额。调用发出后超时或取消不退还。schema_unsupported/input_too_large/quota_exhausted等准入前失败沿M02真实消费规则计0，不能伪造成功统计。准入情况无法确认时保持不确定失败，不自动重发。
3. 纠错不再次消费research.iteration。同一正常轮内的原始调用、至多两次修正和最终动作共用该轮；一次轮次只在最终结果上更新一次研究进展/停滞。拒绝次数用于诊断，与实际纠错次数分开。
4. Action修正前至少保留2次模型余额：一次修正、一次必要Judgment；地图动作仍须通过原完整批次校验与48次预算屏障。Judgment修正至少需要1次模型余额。若Action输出恢复过程中余额只够修正却不够后续判断，则不发起修正、不发地图，记录semantic_budget_insufficient。
5. 消耗第2次纠错后若成功，继续其余合法正常工作。仅当再次发生可恢复错误而无修正额度，或第2次修正仍无效时，按31.4收尾；不能看到计数为2就无条件停止。
6. 允许业务修正的拒绝码固定为highest_priority_objective_ignored、objective_parameters、duplicate、details_already_requested、no_useful_action_invalid。输出恢复保持现有可恢复代码及possible_duplicate限制。未知引用、越权/非法结构、内部状态、持久化、预算基础设施、供应商不可用及不确定外调不是普通业务缺口。

### 31.3 当前轮内修正与反馈

Action第一次输出经兴趣冻结、明确名称绑定、整批参数、最高目标和重复校验。业务拒绝时保存结构化拒绝结果，反馈错误行动序号、固定规则、实际绑定目标、允许动作及该目标的详情UID范围。只向模型提供已有候选事实和当前目标，不让控制器替模型更换目标或生成查询。

余额允许时在同一轮重新调用模型，重新执行完整批次校验；最多共享两次修正。整个非法批次不执行地图，不执行其中合法子集。最终接受后才写主行动并执行原地图/判断流程；先前候选、已接受判断和来源目录均保留。Judgment只在临时状态合并及完整校验通过后发布，失败保留先前一致状态。

### 31.4 正常收尾、失败与下游路由

可恢复的Action策略拒绝以及允许的输出/Judgment修正因额度不足而停止时，调用统一收尾逻辑重新派生counts、coverage、gaps和stop_reasons。符合全部完成门槛才Completed；仍有缺口则Incomplete。新增业务停止原因correction_limit_reached，精确表示当前错误确实需要继续修正而共享计数已达2；模型不足使用semantic_budget_insufficient，正常研究余额为0使用iteration_limit_reached，各原因按实际条件可并存。纠错有余额但总模型不足不得记为纠错额度耗尽。旧版本拒绝新增原因。

Incomplete必须交付已有候选、核验状态、真实缺口、实际预算、拒绝与修正记录；不能默认未知为满足、伪造详情或丢弃已接受的判断。零供给的安全分类沿既有优先级，纠错耗尽也是无法继续补证的预算原因；明确不适用仍优先于预算不足。

M13读取已持久化候选集，复用candidate_supply_status：有符合现有用途规则的候选才进M09，零可用供给按真实原因停止；不因Incomplete自动放行。M09继续拥有行程可行性和完成门槛。技术失败仍Failed，不能因为有旧候选就吞掉存储、来源、不确定外调或状态错误。历史Failed终态不原地改写。

### 31.5 操作编号与产物版本

新写入版本：candidate_set/5.0、model_action/3.0、model_judgment/4.0；新增invocation_attempt/1.0。Place Observation保持1.0，候选记录保持2.0，Action/Observation原Prompt资源版本和hash保持；具体错误反馈由控制器附加。

OperationPlan/2.0及每轮操作目录提供action_attempt_operation_ids和judgment_attempt_operation_ids，各固定3个唯一OperationId：序号0为原始调用，1/2为该调用最多两次修正。所有编号由M13在调用前分配，同run/phase且全计划唯一；未使用编号不产生记录或预算。每轮仍只有一份最终主Action和一份最终主Judgment。

每次得到模型结果并完成本次校验后，先保存不可变invocation_attempt，再允许下一次修正或发布最终主记录。Action最终接受的attempt应在去重、预算准入和dispositions确定后、主记录写入及地图请求之前保存；禁止将尚未确定的dispositions作为已接受结果。每份attempt包含run/mode、所属主operation_id、kind(action/judgment)、轮次、attempt_index(0..2)、调用前semantic_index(0..12)、semantic_cost(0或1)、is_correction、correction_number(修正调用的全局1..2，原始为空)、trigger_ref(原始为空，修正引用同轮同kind前一次attempt)、本次固定结果及元数据完整性；不得含原始Prompt、响应或reasoning。只有修正且semantic_cost=1计入共享纠错消耗；全局已准入修正编号连续且唯一。

attempt结果使用独立strict分支：action_result、judgment_result分别复用对应固定模型结果；control_failure保存安全控制码而非伪造已接受行动。取消沿原立即传播规则，不在收到CancelledError之后新增await或补写Artifact；只保留此前已落盘记录及可证明的准入统计。调用消费或结果无法确定时可保存indeterminate安全摘要，semantic_cost允许null且只用于技术Failed，预算/usage完整性不得伪造为true。Failed允许最后未形成主记录或未落盘的中断；核对已证明的消费下界与完整性标记，不将缺失记录默认为零调用。正常Completed/Incomplete必须具备完整可验证链和准确消费。

候选5使用独立预算与纠错状态类型，旧Artifact预算类不加默认字段。新预算强制correction_limit=2和真实corrections_consumed；旧公开预算的两项新投影均为null。旧ActionCorrection.attempts仅是历史停滞等级，不得再驱动新纠错准入；候选5的拒绝诊断须能表达原始拒绝加两次修正的实际次数，最多按总调用12有界，保持旧版本的0..2限制。

主Action3/Judgment4的attempt_refs为本次调用按顺序的1..3个Ref，result为最终固定结果；最终result必须与最后一份attempt一致。主记录是地图和资格判断的唯一来源。拒绝attempt仅供审计，不提供地点资格或覆盖。来源核验需验证完整attempt链的精确类型、run、所属主操作、轮次、顺序、前驱、hash/size、最终结果一致、语义索引及实际修正消费，拒绝遗漏、重复、跨轮/跨kind、替换最终attempt等伪造。

新候选状态及预算增加correction_limit=2和corrections_consumed=0..2，并由attempt来源重新核对；公开预算对旧记录以不可用而非假零表达新计数。候选集5顶层invocation_attempt_refs按执行顺序保存全run的attempt目录，上限14，包含可能未准入的本地拒绝调用；实际语义消费仍不超过12。主记录的attempt_refs只能引用该目录；技术失败没有可发布主记录时，其已确定attempt也留在此目录，并核对固定控制错误和实际语义消费，不伪造accepted。非Failed终态不得出现无主记录归属的attempt。候选集5只引用Action3/Judgment4；候选集4精确保留Action2/Judgment3合同。candidate_set1/2/3/4、Action1/2、Judgment1/2/3按独立旧模型注册与读取，原字节、hash和渲染语义不变。新旧版本显式分派，不用宽松模型吞额外字段。新增版本通过M04通用注册持久化，不新增SQL结构或改M02额度。

同进程replay验证并读取已有attempt，不重发模型/地图，不重复计数；partial chain只能在保有原私有ModelOperation和目录的安全上下文内处理。跨进程私有研究上下文丢失、调用有消费而无确定结果或链中断无法证明安全时，继续失败关闭，不承诺任意中途续跑。M13新执行合同为demo_workflow.web.v9，旧非终态不自动升级；历史终态仍只读。

### 31.6 验收与交付门槛

文档两名独立审查均通过后实施，代码由两名新的独立审查者逐条核对本节。测试覆盖计数0/1/2及成功用满后继续、Action与Judgment共享、initial/supplemental共享、模型余额1/2/3、准入前失败、取消/超时、最后一轮可修正、纠错耗尽但正常预算仍有余额、真实停止原因、零供给、技术故障及持久化链反例。

使用保存的73候选记录进行离线公共服务回放；同时新增不依赖私有记录的确定性公共入口回归，实际调用真实M08/M13控制代码，仅替换模型和地图边界。验证已有合格供给能进M09、无供给正确停止、重复读取和隔离PostgreSQL重新打开后无新外调、旧候选/结果原字节不变。全部M08 unit/contract和生产line/branch/combined coverage分别至少70%，受影响跨模块回归及默认完整离线测试通过。

完成closeout及两名新代码审查者复核后，只新建一次固定合肥三日真实任务：2026-10-24 09:00合肥南站到达、10-26 20:00同站离开，标准节奏，人文/美食/打卡，无必去和避开项，人工酒店步骤在测试中选择实际返回的第一家。复用已授权本机DeepSeek/百度两项凭据，隔离新库/卷/端口，不改现有服务，不增加业务重跑。供应商或规划失败保存结果并停止，不再新建任务刷通过。报告实际模型/纠错/地图、时间和token，并单独说明M08交付与M09最终规划结果。
