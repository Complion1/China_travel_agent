# Module 09 Vibe Coding 执行契约：受控行程草案与路线优化循环

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次阶段与预算直接修订本文及原设计§12～15/20/25；代码实施按 [原M09执行规范§29](09_itinerary_planning_loop_vibe_coding.md)。完整采集归 [M02§19](02_llm_runtime_and_model_profiles.md)，专用存储归 [M10§23](10_artifact_persistence.md)，没有新增平行设计规范。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 规范状态：Module 09 已按本文实施并进入本地 `main`，本文保留为实施与验收记录。2026-09-14 下游职责修订以主设计和总路线图最新版为准：Module 09 已拥有确定性提交校验，Module 10/11 改为 PostgreSQL 持久化与恢复；历史阶段白名单不授权在 Module 09 范围内实现数据库。
>
> 本文规定未来如何实施和验收 Module 09，不授权当前文档任务创建生产代码、测试、Prompt、依赖、migration 或运行配置。业务语义和公共合同以 `docs/09_itinerary_planning_loop.md` 为准；实现冲突时修复实现，不得为了迁就代码反向削弱设计。设计确需变化时必须停止并取得用户批准。

```text
module: 09
package: app.itinerary_planning
depends_on: [01, 02, 04, 06, 07, 08]
implementation_pairs: [09-1+09-2, 09-3+09-4, 09-5+09-6]
final_implementation_stage: MODULE-CLOSEOUT
```

## 1. 用途、执行模式与授权边界

本文面向实施能力较弱的编码模型，将 Module 09 拆成有明确前置、文件白名单、允许操作、测试矩阵、停止条件和报告格式的小阶段。

每份实施提示词必须同时引用并要求逐条对照：

- `docs/09_itinerary_planning_loop.md`；
- 本文；
- `docs/SHARED_IMPLEMENTATION_RULES.md`。

不得用本文件替代权威设计，也不得在提示词中重复粘贴设计文档已经写明的大段内容。代码审查与验收必须逐条对照两份 Module 09 文档；“测试通过”不能单独证明实现完成。

默认一次授权相邻两个阶段：`09-1 + 09-2`、`09-3 + 09-4`、`09-5 + 09-6`，两个阶段完成后统一执行对应 Gate。小范围且明确的问题可以在当前阶段白名单内直接修复；改变已确认设计、扩大仓库范围、新增依赖、迁移、删除或大改必须停止询问。

实施、修复和 closeout 模型均不得 `git add`、stage、commit、merge、push、PR 或 deploy；对应阶段提示词必须明确写为未授权，不能省略。只有独立审查者在用户另行明确授权后才可执行指定提交或合并。MODULE-CLOSEOUT 和独立代码审查均完成前不得报告 Module 09 COMPLETE。

### 1.1 本次结构化输入范围修订

业务依据为权威设计 §3.3、§8.2～8.5、§16 和 §20，以及延期记录 DEF-09-010。只调整 Module 09 生产包、Prompt 和模块测试；不得借此扩展 01/03 的需求对象或修改 01～08 接口。

本次纯文档白名单仅为两份 Module 09 规范和延期记录，不授权代码实施。后续实施提示词必须另列精确文件白名单，可在既有阶段中回访受影响文件，不重新实现未受影响模块。代码审查不使用子 agent；只有用户明确授权的文档审查可以委派。

延期文本不作为首版硬/软排程指令；模型只能在结构化输入、默认窗口/时长及路线 Gate 内规划。必须保留完整 must/avoid 组、锁定 pace、业务拒绝与技术失败区分、补研关联、阶段额度/最后提交机会和replay。Completed 只能表示本模块支持范围完成，呈现固定范围说明；实际下游界面接入未实现时单独报告，不能宣称 demo_ready。

现有文本解析回归按批准范围转为“能力未启用且不越权”的公共入口测试，并保留来源测试数据供后续回收。不得通过删除全部反测、skip/xfail 或修改覆盖率 harness 获得通过；纯 DTO 可达分支和 strict 测试仍保留。

## 2. 必读真源

开工前必须完整读取，不得只读摘要或搜索片段：

1. `docs/00_agent_master_design.md` 第 1～11 节；
2. `docs/IMPLEMENTATION_ROADMAP.md` 中 Module 09、对应工作包/里程碑、依赖图和测试门禁；
3. `docs/SHARED_IMPLEMENTATION_RULES.md` 全文；
4. `docs/09_itinerary_planning_loop.md` 全文；
5. 本文全文；
6. `docs/01_requirement_and_dialogue.md` 中 confirmed requirement、节奏、兴趣、must/avoid、抵离信息和作息语义；
7. `docs/02_llm_runtime_and_model_profiles.md` 中 itinerary planning profile、tool calling、operation、失败和生命周期；
8. `docs/04_workflow_state_and_artifacts.md` 中 OperationId、Artifact、预算、Repository、replay、并发和 phase；
9. `docs/06_baidu_map_adapter_and_mcp_gateway.md` 中 Geocoding、Route Matrix、Directions、容量、并发、错误和取消；
10. `docs/07_hotel_candidate_selection.md` 与对应 Vibe 文档中已选酒店公共合同；
11. `docs/08_candidate_research_loop.md` 与对应 Vibe 文档中候选、证据、gap 和 supplemental 公共合同；
12. `docs/IMPLEMENTATION_STATUS.md` 和 `README.md`，只核验实时状态；
13. §4 列出的公共代码、合同测试和构建文件。

优先级按权威设计 §1。发现真源、公共实现和合同测试互相冲突时，必须报告文件、章节、公共符号、实际值和影响并 STOP；禁止私有导入、复制 DTO、兼容 shim 或猜测性降级。

## 3. Git 开始条件

每份阶段提示词必须冻结当次用户批准的稳定分支、开始 HEAD、HEAD subject、目标功能分支、工作树状态、staged 状态、untracked 状态和阶段白名单。本文不保存会过期的实际 HEAD。

执行前逐项核验：

```text
git branch --show-current
git rev-parse HEAD
git log -1 --pretty=%s
git status --short
git diff --name-only
git diff --cached --name-only
```

工作树与批准快照不一致时，禁止 reset、clean、stash、强制 checkout、覆盖或删除；必须保留用户已有变更并精确报告差异。目标分支不存在时，只有当期提示词明确授权创建时才能创建；分支名默认使用 `feature/` 前缀。

## 4. 直接依赖公共 API 与 acceptance gate

### 4.1 必读公共代码

完整读取下列实际存在的公共门面与所需实现，不得从私有实现猜公共合同：

```text
app/requirements/__init__.py
app/requirements/models.py
app/llm_runtime/__init__.py
app/llm_runtime/models.py
app/llm_runtime/profiles.py
app/llm_runtime/protocols.py
app/llm_runtime/runtime.py
app/workflow_contracts/__init__.py
app/workflow_contracts/artifacts/models.py
app/workflow_contracts/artifacts/registry.py
app/workflow_contracts/artifacts/repository.py
app/workflow_contracts/budgets/models.py
app/workflow_contracts/budgets/manager.py
app/workflow_contracts/errors.py
app/baidu_map/__init__.py
app/baidu_map/adapter.py
app/baidu_map/models/geocoding.py
app/baidu_map/models/routes.py
app/baidu_map/errors.py
app/hotel_selection/__init__.py
app/hotel_selection/models.py
app/hotel_selection/artifacts.py
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
```

至少完整读取这些合同测试及同目录相关测试：

```text
tests/contract/llm_runtime/test_provider_protocol.py
tests/contract/workflow_contracts/test_public_api.py
tests/contract/workflow_contracts/test_artifact_repository_contract.py
tests/contract/workflow_contracts/test_execution_budget_manager_contract.py
tests/contract/baidu_map/test_public_api.py
tests/contract/baidu_map/test_adapter_contract.py
tests/contract/hotel_selection/test_public_api.py
tests/contract/hotel_selection/test_upstream_integration.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_upstream_integration.py
tests/contract/candidate_research/test_replay_budget_contract.py
```

只读核验 `pyproject.toml`、`requirements.lock`、`Dockerfile`、`.dockerignore`、`.env.example` 和 `compose.yaml`。现有 Pydantic、pytest、pytest-asyncio、pytest-cov 与 Hypothesis 应足够；禁止为 convenience 新增库。

### 4.2 必须成立的前置资格

- Module 01 从公共面提供 `ConfirmedTravelRequirement`、`TravelPace`、`InterestTag`、`HubType` 和相关严格子树；
- `ModelRole.ITINERARY_PLANNING`、`ITINERARY_PLANNING_PROFILE` 存在，semantic invocation limit=19、max request bytes=2097152、max output tokens=131072、timeout=120 秒；semantic 按已批准 D09 §26，输出按 2026-10-08 批准的 D09 §27 与 M02 §6 同步，其他门槛不变；
- `DeepSeekRuntime.start_operation()` 创建同 run operation，operation 公共能力包含 `ainvoke_tools()`、`acontinue_tools()` 和同步 `close()`；
- `WorkflowPhase.ITINERARY_PLANNING`、Artifact Repository、Execution Budget Manager、OperationId 和公共错误可用；
- 预算接线存在 `planning.model_invocation=17` 与 `planning.direction_call=8*days+2`，或已由获批上游变更提供等价固定入口；
- Module 06 公共提供 Geocoding、Route Matrix、Directions 的 request/outcome、`TravelMode`、`MatrixTravelMode`、`BaiduCoordinate` 和 typed error；Matrix 单请求 `origins×destinations<=100`，地图真实调用并发上限为 3，Directions alternatives 为 1..3；
- Module 07 已选酒店 Artifact 精确为 `hotel.selection/1.0`，selected decision 包含可信酒店候选；
- Module 08 候选集 Artifact 精确为 `candidate_research.candidate_set/1.0`，候选资格、角色、UID、evidence 和 gap 语义未漂移；
- Module 08 公共门面提供 `CandidateResearchOperationPlan`、`CandidateResearchSupplementalRequest` 与所需 `SupplementalGap` 分支；权威设计 §15.1 的原请求规范化只依赖这些公共 DTO 和 `docs/08_candidate_research_loop.md` 附录 B.1/B.6，不以私有 fingerprint 导入补足接口；
- Module 01、02、04、06、07、08 的相应 acceptance/implementation 状态已满足路线图前置。

任一资格不成立必须 STOP。不得通过新增 Module 09 本地枚举、复制上游模型、访问私有字段或放宽测试绕过。

## 5. 未来文件树

### 5.1 生产文件

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/errors.py
app/itinerary_planning/protocols.py
app/itinerary_planning/prompt_loader.py
app/itinerary_planning/prompting.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
app/itinerary_planning/tooling.py
app/itinerary_planning/map_execution.py
app/itinerary_planning/service.py
app/itinerary_planning/prompts/planning_v4.txt
```

`tooling.py` 只负责六个模型工具的私有分派、handle 解析和 Observation 组装；`map_execution.py` 只负责 Module 06 公共调用、预算预占、缓存、并发和有序聚合。两者不得成为新公共层。实现如果需要合并私有 helper，必须在阶段授权前提出；不得执行中自行新增文件。

### 5.2 测试与 support

```text
tests/support/itinerary_planning_fakes.py
tests/support/run_module09_coverage.py
tests/unit/itinerary_planning/test_models.py
tests/unit/itinerary_planning/test_prompt_loader.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/unit/itinerary_planning/test_tooling.py
tests/unit/itinerary_planning/test_map_execution.py
tests/unit/itinerary_planning/test_service_initial.py
tests/unit/itinerary_planning/test_service_resume.py
tests/unit/itinerary_planning/test_service_failures.py
tests/unit/itinerary_planning/test_service_concurrency.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_import_boundaries.py
tests/contract/itinerary_planning/test_artifact_registration.py
tests/contract/itinerary_planning/test_upstream_integration.py
tests/contract/itinerary_planning/test_replay_budget_contract.py
tests/contract/itinerary_planning/test_security_contract.py
tests/contract/itinerary_planning/test_coverage_gate.py
```

测试文件可在不降低白名单精度和可读性的前提下小幅合并，但 §13 的每个验收项必须有明确测试归属，不能以减少文件为理由漏测。

## 6. 包根公共 API 精确清单

最终 `app/itinerary_planning/__init__.py::__all__` 必须与权威设计附录 A 的有序清单逐项相等：

```text
ITINERARY_PLANNING_SCHEMA_VERSION
ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY
ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY
ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
PlanningDayKind
VisitKind
BusinessHoursState
PlanningTool
PlanningStopReason
TransportSemantic
PlanningFailureCode
PlanningInvocationOperationIds
ItineraryPlanningOperationPlan
ItineraryPlanningInitialRequest
ItineraryPlanningResumeRequest
CandidateLocationRef
HotelLocationRef
HubLocationRef
PlanningLocationRef
TimeInterval
RouteTimeInterval
DailyTimeRule
TimeSensitiveMustAnchor
TransportPolicyRule
EffectivePlanningPolicy
ScheduledVisit
RouteAlternativeSummary
RouteLeg
BoundaryTransfer
BackupOption
DayPlan
ItineraryDraft
PlanningGap
PlanningGapSummary
PlanningBudgetSummary
PlanningSummary
LLMPlanningFailure
MapPlanningFailure
ControlPlanningFailure
PlanningFailure
ItineraryPlanningCompleted
ItineraryPlanningIncomplete
ItineraryPlanningCandidateSupplementRequested
ItineraryPlanningFailed
ItineraryPlanningOutcome
GetCandidateEvidenceArgs
GeocodeHubArgs
CalculateRouteMatrixArgs
PlanDirectionsArgs
SubmitItineraryArgs
RequestCandidateSupplementArgs
ItineraryPlanningModelActionArtifact
ItineraryPlanningHubGeocodeObservationArtifact
ItineraryPlanningRouteMatrixObservationArtifact
ItineraryPlanningDirectionsObservationArtifact
ItineraryPlanningArtifact
ItineraryPlanningError
ItineraryPlanningConfigurationError
ItineraryPlanningUsageError
ItineraryPlanningPort
ItineraryPlanningService
```

阶段投影固定为：

- 09-1 创建 §6 最终有序清单中当前实际实现的子序列：移除尚未存在的 `ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS`、五个 Artifact model 和 `ItineraryPlanningService`，其余常量、enum、DTO、六工具参数、错误与 `ItineraryPlanningPort` 全部存在；不得为后续符号建 stub；
- 09-2 在固定位置补入 `ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS` 和五个 Artifact model，其他顺序不变；
- 09-3/09-4 不改变包根清单，不导出新增的私有 helper；
- 09-5 创建 Service 后在 tuple 尾部追加 `ItineraryPlanningService`，形成最终精确清单；
- 09-6 只收紧合同测试，不重排、不新增公共名称。

若阶段投影与 Python import 可用性发生矛盾，应在实施提示词中列出该阶段精确 tuple，不得用 optional import 或 `try/except ImportError` 掩盖。禁止导出 `DirectionLegIntent`、具体 gap 分支、Artifact record/snapshot、handle directory、runtime/operation port、Prompt 路径、canonical/fingerprint helper、锁、run context、state accumulator 或 fake。

## 7. 逐文件实现责任

### 7.1 `models.py`

- 实现权威设计附录 A/B/C 的 enum、strict/frozen/extra-forbid DTO、封闭 discriminator union 和容量；
- Resume 按权威设计 §6.2/B.1 独立声明 `3.0` 和 required 的公共 `CandidateResearchOperationPlan` 字段；基础 schema 常量及其他 DTO/Artifact 维持权威设计 §20.1，不复制上游 DTO、不新增公共导出；
- 保存用户原始时间/供应商秒数，排程字段使用 Asia/Shanghai aware datetime；
- tuple 唯一性、稳定顺序、索引范围、同 run Ref 和交叉字段不变量必须由 validator 重算；
- validator 只拒绝，不偷偷排序、截断、取整、补默认或修复模型输入；
- `PlanningLocationRef` 只允许候选、酒店、枢纽三种 typed ref；模型工具中的 `Handle` 字段只能提交当前上下文 handle，模型生成的 visit/leg/rule/anchor `StableId` 按作用域唯一规则单独校验；
- 不增加 Citywalk 类型、步行专属限制、天气、饮食适配、价格、RouteStep 或任意 provider body 字段。

### 7.2 `artifacts.py`

- 精确注册权威设计 §17.1 的五种 Artifact：model_action/5.0携带planning-v5，itinerary/2.0，三种Observation外层1.0；相应 Ref 按 type/version 对检查，不全局放宽；
- record、snapshot、Ref 目录、索引化 gap 与证据结构保持私有；
- Artifact 只保存安全 Action、typed Observation、合法 itinerary 状态和类型化 failure；
- 不保存原始 Prompt、完整对话、模型自由文本推理、思维链、供应商原 body、异常消息或 secret；
- 每次写入形成不可变版本，保留 parent/predecessor、stable operation、payload hash 与显式 Ref，满足未来 PostgreSQL 迁移；
- 容量必须在写入前计算并拒绝，不能写入后再截断。

### 7.3 `errors.py` 与 `protocols.py`

- 对外仅公开权威设计附录 C 的配置/用法错误和 typed failure DTO；
- `ItineraryPlanningPort` 精确为 `plan_initial`、`plan_resume`、`close_run`；
- 私有 runtime port 只含 `start_operation(ModelRole)`，operation port 只含 `ainvoke_tools`、`acontinue_tools`、同步 `close()`；
- 不要求 fake 实现 Module 02/04/06 的额外私有方法；
- `close_run` 与 `aclose` 不关闭注入依赖。

### 7.4 Prompt、`prompt_loader.py` 与 `prompting.py`

- Prompt 文件只描述规划目标、六工具白名单、handle 规则、受控 ReAct、硬/软约束、完成标准和终止工具；
- Prompt 不含真实用户内容、供应商 key、环境变量、内部文件路径或可执行命令；
- Prompt loader 用 package-relative resource 加载、UTF-8 严格解码、固定 version/hash 和 fail-closed 完整性校验；
- `prompting.py` 只渲染有界 typed snapshot/handle，不拼接完整上游 Artifact、原始用户对话或 provider body；
- 模型可以决定主题、分组、选择、顺序、时间、交通语义、路线模式、工具顺序、权衡、修订和提交；Prompt 不得把这些语义选择硬编码成贪心算法；
- 普通文本、无工具调用或未识别工具不能产生成功。

Prompt 投影只包含权威设计 §3.3 支持内容；不传递延期文本供模型自行解释。当前资源、版本、固定摘要及历史字节保护按权威设计§31 RC-04执行。

### 7.5 `fingerprints.py` 与 `policy.py`

- canonicalization 固定、版本化、字段显式，不以 `repr()`、对象 hash、dict 插入顺序或本地时区为真源；
- 严格区分 B.6 的 Resume planning-resume-fp-v6 与 §15.1 的 Module 08 原请求摘要；前者由固定范围标识和 inline 请求计算，后者 terminal miss 后才重建；Initial/context 的局部版本变化按 §10.3，Module 08 算法不变；
- 为 request context、候选 admission、六类工具参数、路线腿、gap、submit 和 terminal successor 生成稳定 digest；
- deterministic policy 负责所有硬 Gate：依赖准入、时间网格、日期连续性、地点资格、UID 唯一、must、餐次、营业状态、备选、路线匹配、节奏、容量、停止、终态；
- policy 不替模型选择主题、候选、访问顺序、交通偏好或路线方案；只判“该提议是否合法”；
- `effective_pace` 与 `EffectivePlanningPolicy` 的锁定时机按权威设计 §8/§12/§16，不得在拒绝的非终止 Action 上提前锁定；
- full-day attraction/meal、partial day、组合地点、未知营业、备选和 transport 映射必须分别测试；time-sensitive 锚点保留结构测试，并验证首版业务拒绝及默认窗口不可绕过。

### 7.6 `tooling.py` 与 `map_execution.py`

- 只接受六个 `PlanningTool`，按当前 handle 目录解析，模型不能注入 UID、坐标、地址、距离或 ArtifactRef；`DirectionLegIntent.leg_id` 是模型生成的批内/run-draft 唯一 StableId，origin/destination 才必须是已有 handle；Directions 结果 handle 由控制器按 Observation OperationId+leg ordinal 生成并从下一轮起可见；
- `get_candidate_evidence` 只返回已有安全证据切片，不发外部请求；
- `geocode_hub` 只从 confirmed typed endpoint 构造查询，区分 success/ambiguous/no-result/typed failure；
- `calculate_route_matrix` 对 1..10 点构造同集合 N×N，单次 cells≤100，同一迭代同一日期最多一次；
- `plan_directions` 支持批量腿；缓存未命中的每个底层腿独立占一个 direction slot，整批预占成功后才允许发出；
- 地图调用实际并发最多 3，聚合结果按请求 ordinal 稳定排序，不按完成顺序；
- `submit_itinerary` 与 `request_candidate_supplement` 是终止工具，不能和普通工具同批；
- Directions 只保存 route summary 与选择所需索引，不保存 `RouteStep.instruction`。

### 7.7 `service.py`

- 构造签名与权威设计 §7 完全一致，不新增默认创建的网络 client、Repository 或 Budget Manager；
- initial/resume 均先做同步验证、lookup-first、singleflight/run lock，再创建或恢复同一 ModelOperation；
- 准备2/交通5/提交修正5/独立补充5，最多17逻辑调用；首次/成功提交均计修正，批量调用一次，网络重试不重复计；没有额外终态调用；
- 准备/交通按真实进展判断；修正只因连续两次相同reason/field、无业务变更/被采用相关新证据的拒绝而no_progress；无合法Action仍Incomplete；
- 每轮只持久化安全 typed 内容并推进 longest consistent prefix；
- 合法 submit 才可能 Completed；非法 submit 返回 typed observation 并计入当前循环，不保存非法 draft；
- 合法 supplement 请求写 planning Artifact 后返回 typed terminal outcome，由 Module 13 编排 Module 08 后以 resume 继续同一 run；
- 普通文本/无工具调用为 Incomplete；typed 技术失败按权威设计映射 Failed；取消只清理本模块资源后原样传播。

## 8. 固定算法与顺序

### 8.1 Initial 公共入口

严格按以下顺序执行，任一步失败不得越过后续 Gate：

1. 对整个 request 执行 fresh strict JSON 复验；这一步只做类型/schema/容量校验，不读取前驱、不加载 Prompt、不调用模型/地图；
2. 从 fresh request 提取可信的 run、terminal successor、request kind 和 Ref 身份，按权威设计附录 B.6 的精确字段计算 request digest；
3. lookup terminal successor；命中时只用 terminal Artifact 内保存的 request digest、Ref 目录和 record 复验匹配并重建原 public outcome，不重新读取后来可能变化的前驱，不消费预算、不调用模型/地图；
4. terminal absent 时进入该 terminal ID 的 singleflight，锁内再次 lookup；
5. 二次 lookup 仍 absent 才读取 confirmed、酒店选择和候选集公共 Artifact，完整校验 OperationPlan、同 run/phase、允许前驱、摘要和 Ref；
6. 处理 Module 08 状态：eligible=0 返回权威设计 §6.1 的 typed input failure，模型/地图为零；eligible≥1 才继续；
7. 建立 admission snapshot、稳定 candidate/hotel/unresolved-hub handle 目录、初始 policy 候选和安全 Prompt envelope；此步不得主动 geocode；
8. 以 `ModelRole.ITINERARY_PLANNING` 启动一个 operation，进入 §8.3；只有模型调用 `geocode_hub` 后控制器才执行对应 Module 06 请求；
9. terminal Artifact 成功写入后从 fresh read-back 重建并发布 public outcome；写入不确定按 replay 规则处理；
10. public outcome 返回不隐式关闭 operation/context；由 Module 13 在最终 outcome 消费后调用 `close_run`，awaiting supplement 必须保持开放。

### 8.2 Resume 公共入口

1. 对 Resume `3.0` 全请求及两份嵌入计划做 fresh strict JSON 复验；旧 `1.0/2.0`、缺少 `supplemental_operation_plan` 或不合法输入按权威设计 §18.1 处理。按权威设计 B.6 计算只依赖 inline Ref/两份完整计划的 Resume v3 request digest；
2. 按 Initial 第 3～4 步执行 terminal lookup、精确 replay、singleflight 和锁内二次 lookup；命中不读取前驱；
3. miss 后读取并核验同 run 当前 last-consistent awaiting record，与 Module 09 保存的 pending call ID、gap fingerprint 精确匹配，确认原 `ModelOperation` 仍开放；读取保留的 context、policy、预算、缓存、已用迭代、请求史和合法前缀，不修改运行态，不读取 Module 02 私有字段；进程丢失私有 operation 时按权威设计 §15.1/§18 返回 `operation_result_indeterminate`，不得伪造 history；
4. 按权威设计 §15.1 从 awaiting 的最后 candidate-set Ref、原 gaps/证据目录/必要 draft 与实际 `supplemental_operation_plan` 重建公共 Module 08 请求，按其原算法求完整摘要。核验两份计划同 run 且分别满足各自 phase/slot 合同，其中已执行 Module 08 计划仅校验公共 DTO 的静态合同，不按补研后的余额重跑历史动态准入；再核对合法 successor 的原前驱 Ref、terminal OperationId 和 `invocation_request_digest`。错误分类、replay 冲突零写入及副作用边界严格按权威设计 §15.1/§18；
5. 核验后继仍属同一 confirmed/hotel/run 且为 Completed/Incomplete、eligible≥1，保持旧 UID 的 handle/ordinal，并逐个重验最新 state、role、meal、must/avoid、evidence 与坐标；失去资格的访问/备选/合法前缀失效，新 eligible UID 只追加。不得要求目标已全部满足、必须新增 UID，或用结果剩余 `pending_gaps` 代替原请求证明；
6. 以上准入全部通过后才应用候选/草案/缓存失效和 pending 推进；同 fingerprint 最多请求一次，已请求且仍缺失时不得再次循环回 Module 13；
7. 将 supplemented 结果投影成匹配原 pending call ID 的 typed `ToolResult`，由原 operation 调用 `acontinue_tools()`，不得二次 `start_operation()`，不得执行实际补研计划的 Module 08 slot；
8. 从保留预算、缓存、已用迭代、锁定策略和仍合法草案继续 §8.3；关联准入通过不代表 Completed，仍须全部规划 Gate。

### 8.3 单轮受控 ReAct

1. 按权威设计§12.1.1由代码推进阶段、构造有效工具子集；修正剩1仅submit。六个注册工具仍是全集，当次实际provider工具和本地gate必须是阶段子集。
2. 同一M02 operation的首次调用ainvoke_tools，普通后续及resume补研反馈调用acontinue_tools；续调保留原始历史，只更换当次有效tools。先完整byte/quota预检，再单次预算准入回调/semantic claim，再第一次provider attempt；最多两次物理尝试不重复准入。
3. 解析typed outcome。预检拒绝且回调未准入时，不消费总账/分类；已准入的LLM失败或普通文本各计发起阶段一次。错误按§11/权威§18.4停止，不做M09 JSON repair。
4. 对工具批次先验证schema、容量、终止互斥和阶段权限；已准入结构失败计发起阶段一次，不执行任何子集。唯一合法submit归submit_repair、唯一合法supplement归独立申请、其他批次归发起普通阶段，全部只归一次。
5. 最终budget_category确定后写不可变ModelAction/5.0，含真实request_stage、安全摘要和处置；LLM/结构失败返回Failed，普通文本返回Incomplete(no_terminal_submission)。不得先写Action再补计费分类。
6. 之后验证handle、同轮Matrix/date、重复、业务policy和地图额度；合法submit按§8.4完整校验，无论接受/业务拒绝都已计修正一次；supplement按§8.5及阶段门槛处理。
7. admitted非终止工具按固定顺序执行，记录typed Observation、缓存和合法前缀；同batch多工具仍算一次。预算/Artifact不确定仍失败关闭，不把诊断记录当执行许可。
8. 按权威§12.4更新准备/交通真实进展或修正拒绝基线；相关新证据只有被下一次提交采用才参与修正比较。 typed反馈交给下一次模型调用时采用新的有效工具子集。
9. 枢纽可用立即结束准备；首次submit或交通5次耗尽进入submit_repair；补研不重置阶段、四项额度、拒绝基线或私有operation。修正查询留至少一次后续submit，剩1时不允许补充/查询。
10. 成功立刻Completed；准备/修正额度耗尽、对应阶段无进展、无合法动作或typed事实证明不可行时保留真实gaps并停止。交通额度耗尽只切阶段；不增加第18次或旧终态专用调用。

非终止batch动态上限仍为sightseeing_days+11（最多71）：evidence≤8且候选handle唯一，hub≤2且endpoint唯一，Matrix≤days且date唯一，Directions≤1且合并腿。Handle只能引用本轮开始已有目录，不允许sibling dependency；终止工具必须独占batch。其他业务硬Gate保持原合同。

### 8.4 Submit Gate 的必要顺序

控制器必须重新派生并按以下类别全部通过，不能信任模型的声明字段：

1. policy：先锁定合法 trip-wide pace；daily_time_rules/time_sensitive_must/transport_rules 均为空，无访问锚点，duration_source=planning_estimate；非空延期提议业务拒绝，不静默丢弃后接受；
2. date：Asia/Shanghai aware datetime、连续日期、跨午夜占用、开放区间和抵离安全余量一致；
3. location：全部主地点与备选来自准入目录，普通 UID 整趟主路线最多一次；
4. structure：固定枢纽/酒店骨架、抵达活动在入住后、离开活动在最终离店前；
5. density：full-day 景点轻松 1..2/标准 3/紧凑 4，午餐和晚餐各一次，主路线候选≤6；partial/transit 不被强行填满；
6. roles：组合地点最多同时算 attraction 与一顿 meal，不能同时算午餐和晚餐；
7. must/avoid：从 08 完整 requirement_group_states 核验 must 全集及 item 覆盖；每组由 eligible UID 满足，未匹配组不能消失；avoid 和冲突候选不得进入，备选不满足 must；
8. business hours：只接受 compatible/conflicts/unknown；conflicts 阻止，unknown 标记核验；
9. route：每个相邻主地点有 origin/destination/mode/observation/alternative 完全匹配的 Directions success；
10. pace：full-day 原始 Directions 秒数总和和所有单腿上限均通过，不使用 向上取整的排程值替代；
11. backup：每个有对应角色的非纯交通日，可不提供备选；已提供备选须为当天实际角色的有序子集，合格且不重复，并有 3000 米内 walking Matrix 证据；
12. provenance：不得宣称天气、饮食适配、价格、预约、实时交通、酒店寄存已核验或逐转向导航；
13. artifact capacity、安全字段、Ref 目录、summary 和 completeness claim 全部重新派生；
14. 只有首版上述 Gate 全部通过才写 Completed；延期文本不单独阻断，也不能声称已满足；非法提议返回业务拒绝供修订，无进展时按既有规则 Incomplete。

### 8.5 Supplement Gate

只允许把权威设计 §15 可映射为 Module 08 输入的候选供给 gap 转换为请求。控制器必须重新派生 gap、稳定排序、生成 fingerprint，核验同 fingerprint 未请求、run 内已接受 supplement 少于 5 次且返回后仍返回后仍有合法普通阶段调用，写入保留 draft/policy/budget/cache/阶段分类计数/拒绝基线/pending call ID 的 planning Artifact，然后终止返回 `candidate_supplement_requested`。补充申请独立最多5；普通2+5+5，加补充5，合计最多17。补研保持阶段/拒绝基线，修正剩1只能提交。

Module 09 不直接调用 Module 08。控制器必须逐分支执行权威设计 §15 映射表，原样传递 gap ID/evidence，并只做 hotel/candidate anchor 类型转换；不得由 Module 13 猜分支、半径、current/target、role、meal、requirement IDs 或 rejected UID。枢纽歧义、作息澄清、地图技术失败、兴趣偏好、天气、饮食、价格或实时导航问题不得伪装成候选 supplement。Directions NoResult 本身不是 Module 08 evidence；只有它进一步导致了可按 Module 08 公共合同表达、且已有 1..8 个合法 candidate-research evidence Ref 的候选供给/替换缺口时，才允许请求 supplement。

## 9. 时间、路线与策略的固定实现规则

### 9.1 时间与排程

- 规划时间使用整数分钟，分钟可以为00–59；用户原始时间与供应商 `duration_s` 原样保存；
- 每条 `RouteLeg` 保存 `RouteTimeInterval`，区间分钟数精确等于 `(duration_s+59)//60`；`duration_s=0` 时允许 start=end，正值两端使用整数分钟，禁止发明最低交通占用；
- 活动时间标记为 planning_estimate，按整数分钟排程；结构化抵离时刻保留原值；文本活动锚点不启用；
- 默认普通窗口 07:30–22:00；午餐开始 11:30–13:30，晚餐开始 17:30–19:30；延期文本不覆盖默认；
- 默认时长：景点 1–4 小时、餐次 1–2 小时、组合地点 1.5–4 小时；用户文本时长延期；
- 机场抵达处理 60 分钟/离开提前 120 分钟，铁路 30/60 分钟，近似抵离时间再增加 30 分钟；不从文本增减安全余量；
- 全部时间使用 `Asia/Shanghai` aware datetime 连续计算；允许跨午夜，跨日占用必须真实压缩相邻日，不能按日期边界截断；
- daily window 是可安排边界，不是填满目标；空闲时间不满足景点、餐次或 must；
- 文本活动和锚点授权延期，不突破默认窗口；user_provided 标签或营业时间证据不能启用能力；普通 POI 访问不得冒充活动完成。

### 9.2 每日密度和节奏

- full-day 景点数固定：轻松 1..2、标准 3、紧凑 4；午餐/晚餐分开计数；
- full-day 主路线候选地点最多 6；酒店/枢纽不计候选数；
- full-day 路线原始 `duration_s` 总和上限：轻松 120、标准 180、紧凑 240 分钟；抵达/离开影响日不套用总量上限；
- 所有日期、所有路线模式的单腿硬上限：轻松 60、标准 90、紧凑 120 分钟；
- 自定义交通时长及文本硬/软限制不启用，保留已冻结节奏上限；
- must 或周边游导致上限无法满足时重排；仍无解返回 Incomplete，不自动放宽；
- `agent_decides_pace=true` 时模型从 `TravelPace.RELAXED/NORMAL/INTENSE` 选择一个整趟 `effective_pace`，在首次结构合法的 submit 或 supplement 终止尝试时锁定，之后不得改变；
- 儿童/老人仅是软提示，不能凭年龄自动创造数值上限；文本行动限制延期。

### 9.3 交通与路线

- 同时保存模型选择的交通展示语义和 Module 06 TravelMode，按权威设计 §10.1 固定映射；不声称执行了用户文本限制；
- 模型在首版合法模式和节奏限制内选择，不使用延期文本重新生成硬/软规则；
- 非法模式/映射按业务拒绝，不因用户文本未支持而单独阻断；不得声称具体服务、价格或可用车辆；
- Matrix 只用于多点比较与备选距离，不能作为 Completed 的逐腿证据；
- 每个相邻主地点必须有同 origin、destination、route mode 的 Directions success；允许从供应商原序 0..2 选择方案；
- Directions 无出发时刻，只能描述为供应商路线估算；不能证明公交班次或硬性精确时刻；
- “逐段路线”仅输出相邻地点静态摘要，不输出 `RouteStep.instruction`，也不构成实时导航；
- Citywalk、步行总量、连续步行和步行专属距离/时间限制均不存在；walking 只是普通路线模式。

### 9.4 备选

- 只为实际存在相应核心角色的非 `transit_only` 日期创建备选；
- 核心景点、午餐、晚餐每类至多一个，备选必须 eligible、角色匹配、营业不冲突、未在主路线、与其他备选不重复；
- “附近”只由同一规划上下文中的 Route Matrix 路网距离 `<=3000m` 证明，并保存 Matrix observation/index/mode；
- 备选不进入主路线、不请求 Directions、不计主路线候选数、不满足或替换 must；
- 备选只是信息提示，不能在运行时自动替换。替换必须重新规划或由未来 Module 16 发起。

## 10. Artifact、预算、缓存与 replay 安全顺序

### 10.1 Artifact 写入

五种 Artifact 固定为权威设计 §17.1 的 type/version。每次写入前依次执行：

1. 构造 canonical typed payload；
2. 校验安全字段和容量；
3. 校验 run、parent/predecessor 与 evidence Ref；
4. 校验 OperationId 对应的 successor 类型；
5. lookup 同 OperationId；若已存在则比对 hash 并 replay；
6. 写入不可变新版本；
7. 读取并核对已写结果；
8. 才向调用方发布 outcome。

写入超时或结果不确定时不得盲重试产生重复版本；必须 lookup，能证明等价才 replay，不能证明则 `operation_result_indeterminate` Failed 并标记 `possible_duplicate`。

### 10.2 预算

- planning.model_invocation固定上限17，完整预检后/首次provider前消费一次，所有准入调用按设计§12.2唯一归类；
- `planning.direction_call` 上限固定为 `8 * confirmed.time_window.sightseeing_days + 2`；这里的 `sightseeing_days` 就是上位预算公式中的 `days`，不得另造 `trip_days` 或按抵离自然日重算；每个未命中缓存的底层 Directions 腿消费 1；
- Directions 批次先计算所有 cache miss 并做全批剩余额度预检；额度足够后按模型腿序和各自 OperationId 逐槽确定性 `consume(amount=1)`，只有全部槽均 confirmed-applied 才派发任何地图请求；不存在多槽事务 API，也不得退款；
- 预检额度不足时零 consume、零地图；逐槽 reservation barrier 中途基础设施失败时零地图、已 applied 槽不退款并按权威设计写可证明的 not-issued 后 Failed；取消时停止后续 consume/write/call，不补写 not-issued；
- Route Matrix、Geocoding 不消费 direction 预算，但受 Module 06 容量/并发/错误合同和 Module 09 operation slots 约束；
- cache hit 与精确 replay 不重复消费；
- 修正剩1时当次出站tools及本地Gate仅submit，不允许地图/证据/补充，不另加调用；
- Artifact 中只保存已知 usage 与 complete flag，不能猜 provider 重试或 token。

### 10.3 缓存与失效

缓存 key 和 hash tree 必须逐字段实现权威设计附录 B.6，不得增删字段或另造 `repr()` key。缓存只复用 typed success 或权威设计明确允许的稳定 NoResult；技术失败不缓存为业务结论。

`supplemental_operation_plan` 的变化必须改变 Resume 请求摘要，但不得单独改变 planning context、gap、候选 handle、Geocoding/Matrix/Directions 指纹。不能全局升级 `planning-fp-v1` 来实现局部 Resume v2。

confirmed、酒店、hub 用户修正或影响 provider 请求语义的端点/坐标/mode/alternatives 变化会使对应指纹变化。candidate-set 后继只使更新/新增候选的 evidence 以及实际包含变化坐标的 Matrix/Directions 失效；未受影响候选 handle 和精确同请求的地图事实可复用。首次锁定 effective policy 不使此前同 run、同端点/坐标/mode 的 provider 事实失效，但草案仍必须重新通过全部 policy Gate。预算按权威设计的同 run 继承规则处理。

本次业务范围固定为 module09-phase-budget-v1。Initial request/context 使用 planning-fp-v5，Resume request 使用 planning-resume-fp-v6，fields 精确遵循权威设计 B.6。仅这些本地指纹加入范围标识，不能污染 Module 08 重建请求或其摘要。升级前的会话不恢复、不迁移；新范围内 replay/缓存/预算规则不变。补充旧 digest 不可 replay 为新范围结果、同范围 exact replay 零新增副作用的测试。

### 10.4 OperationPlan 与并发

- Module 09 operation slot 的容量、唯一性、run/phase 和消费用途按权威设计 §14 与附录 B；Resume 的实际 Module 08 计划按权威设计 §15.1 验证公共 DTO 静态合同并用于关联，不重跑原调用动态预算准入，不查询/消费/预占其 slot 或预算，也不当作新规划计划；
- initial/resume、同请求并发与重复提交必须 lookup-first + 同 run singleflight + 锁内二次 lookup；
- 不同 run 可并发；同 run 状态推进串行；地图子调用在单轮内最多 3 个并发；
- 完成顺序不能影响 Artifact 顺序、fingerprint、summary 或 outcome；
- `close_run` 幂等；关闭后新调用返回 typed failure，不 resurrect context；
- 取消时停止派发新工具，取消并 await 自己创建的任务，关闭 operation，释放锁并原样抛出取消。

## 11. 错误映射与优先级

构造 `ItineraryPlanningService` 时一次性校验 Prompt version/hash；失败只抛 `ItineraryPlanningConfigurationError(prompt_resource_invalid)`。每个 public call 第一动作是整个 request 的 fresh strict JSON 复验；对象类型错误、复验失败或 service 已 `aclose` 只抛 `ItineraryPlanningUsageError(service_usage_error)`。这两类 marker 均不写 Artifact，也不消费预算。

fresh request 合法并可安全取得 terminal ID 后，检查和副作用顺序必须与权威设计 §18 一致：先 terminal lookup/replay 和同 ID digest 冲突；terminal miss 后才读取/校验 input、predecessor、run、OperationPlan；随后才建立/恢复 context，调用模型、写中间 Artifact、消费预算和调用地图。若同一检查点有多个已显现 operational 问题，稳定优先级精确为：取消/进程退出 → replay/OperationId/result indeterminate → input/predecessor/OperationPlan → Prompt/模型/tool schema → Artifact → budget infrastructure → map typed failure → 正常业务 gap/NoResult/无进展/预算耗尽。不得使用本文件另造第二套排序。

关键场景的实现映射固定为：

| 场景 | 结果 | iteration |
|---|---|---:|
| run 已显式 `close_run`，且不是 exact terminal replay | `ItineraryPlanningFailed(ControlPlanningFailure(run_context_closed))` | 不消费 |
| Module 02 返回 `LLMInvocationFailure`，含 unknown tool/arguments invalid | `ItineraryPlanningFailed(LLMPlanningFailure)` | 不消费 |
| 空 `ToolCallsReady` | `ItineraryPlanningFailed(ControlPlanningFailure(model_contract_invalid))` | 不消费 |
| terminal 工具与其他调用混批、容量或 sibling dependency 等纯结构错误 | `ItineraryPlanningFailed(ControlPlanningFailure(action_batch_invalid))` | 不消费 |
| `ToolTurnCompleted` 普通文本 | `ItineraryPlanningIncomplete` + `no_terminal_submission` | 不消费 |
| 结构合法的非终止 batch 被业务 policy 拒绝 | typed rejection Observation，可继续 | 消费一次 |
| 结构合法但业务非法 submit | typed rejection Observation，可继续 | 消费一次 |
| Direction 剩余额度不足、geocode/Matrix/Directions NoResult | 业务 Observation/gap，最终可 Incomplete | 已开始非终止 cycle 时消费一次 |
| Artifact/预算基础设施、Module 06 typed exception、已发结果不确定 | 对应 Control/Map Failed | 按 cycle 是否已开始决定，不伪装 Incomplete |
| accepted submit / accepted supplement | Completed / CandidateSupplementRequested | 不消费 |

`policy_contract_invalid` 只表示控制器自身锁定 policy/snapshot 合同损坏，不用于拒绝模型提出但可用 typed Observation 表达的业务冲突。Failed 可以携带 `last_consistent_itinerary_ref`，但不能把非法草案保存成“最后一致”。resume terminal miss 且 predecessor 不可读、无法恢复 sightseeing days/历史预算时，Failed 的 `budgets=None`；不得为满足 DTO 猜造 limit。公共错误不得包含异常正文。

Map/Control/LLM failure 的 `retryable` 与 `possible_duplicate` 必须逐枚举按权威设计附录 C 固定派生：地图异常不读取不存在的 flags；terminal write 的唯一例外和 budget consume indeterminate 升级规则不得合并。测试必须参数化覆盖全部 `BaiduMapErrorCode`、全部 `PlanningFailureCode`、terminal-write 例外，并断言 public outcome、record 与 failure 三层 flags 相等。

禁止 `except Exception: continue`、generic `unknown_error`、异常字符串外泄、自由 detail dict 或将技术失败转换为模型可自行解释的事实。

## 12. 测试实现原则

- 默认测试使用 strict fake runtime、fake operation、fake map adapter、fake Artifact Repository/Budget Manager；不得联网、不得读取 `.env`、不得需要 secret；
- 时间、UUID、OperationId、完成顺序和 fake outcome 必须可注入、可复现；
- 每个成功测试都同时断言调用次数、预算、OperationId、Artifact、Ref、排序和无额外副作用；
- 每个失败测试都断言后续模型/地图/写入没有发生；
- property tests 用于网格、日期连续性、N×N cells、route matching、fingerprint、稳定排序、唯一性和边界；
- security tests 扫描 Artifact 和日志，确保无 Prompt、CoT、完整对话、provider body、secret、异常文本与 RouteStep；
- coverage source 精确为 Module 09 生产包，line、branch、combined 三项分别 `>=70%`；报告 statements、branches、missing lines/branches、三项百分比和退出码；
- 测试通过后仍必须按权威设计逐项人工审查。

## 13. 完整测试矩阵

### 13.1 公共 API、DTO 与导入边界

- 最终 `__all__` 与 §6 精确有序相等，无私有泄露；
- Resume `3.0` 字段顺序、required 实际补研计划、两份计划分别 strict 验证；显式旧 `1.0`、缺字段、额外字段和错误 phase 失败。Initial/规划OperationPlan/预算/Outcome2.0，Resume3.0，ModelAction5.0/planning-v5，itinerary2.0；其他未变DTO/Observation按设计§20.1；另验权威设计 §20/B.6 的新范围指纹隔离；不允许将 Module 08 ID 误判为规划 phase；
- 权威设计附录 A/B/C 的枚举值/顺序、strict/frozen/extra-forbid、discriminator、tuple 容量、唯一性、索引、aware datetime、Asia/Shanghai、schema/version；所有 public request/outcome、具体 gap 分支、五类 Artifact、五种 itinerary record 和 snapshot nested DTO 都执行 `model_dump_json() → 对应 model_validate_json()` fresh round-trip，断言值相等但对象非同一；
- 五个 registration 必须共同实例化 Module 04 真实 `ArtifactSchemaRegistry`，逐个执行 repository prepare/decode round-trip；对 Artifact 完整可达图逐节点断言精确 config、全部字段 required、无 default/alias/serializer/computed field，禁止只比较 registration descriptor；
- public/upstream DTO 到 Artifact-local snapshot 的显式双向转换必须逐分支保留字段语义、UTC/Asia-Shanghai 转换与全部交叉 validator；Module 09 public DTO、Module 06 Map DTO 和 `LLMRuntimeError` 不得直接进入 Artifact 图；
- 服务边界对已构造对象仍重新序列化并 fresh strict 解析；`model_construct`/forged instance、宽松字符串转 int/bool、额外字段和错误 subclass 不能绕过；
- ModelCallSummary 按权威设计 B.5 精确重算 handle_count/digest/前16项预览；最大合法 Directions/submit/supplement 可落盘，超过6000个 Handle 出现项在任何外部副作用前失败且不截断；
- request/planning-context/submit fingerprint 纳入 Module 04 ArtifactRef 全部七字段；只改 `size_bytes` 也必须 request mismatch，不能误判 exact replay；
- initial/resume 互斥字段、两份 OperationPlan 同 run 且各自满足 slot 唯一/phase/capacity；
- Candidate UID 精确继承 Module 08 的 1..512 域；129/512 字符合法，513 非法，不能误用 128 字符 Handle/StableId 上限；
- production 包只依赖 Module 01、02、04、06、07、08 的公共门面；
- 无 MCP、HTTP、SDK、数据库或 Module 10/11/12/13/16 私有反向依赖；Module 10/11 以后只能从 Module 04 公共端口和 Module 09 公共 Artifact／outcome 接入。

### 13.2 时间、日期、密度与作息

- 分钟排程和最早时间向上取整到分钟；用户/供应商原始精度不丢失；
- RouteLeg interval 与 scheduled minutes 精确一致；日内腿按访问顺序不重叠；零耗时普通腿和 BoundaryTransfer 可用 start=end 表示；
- 默认 07:30–22:00、午餐/晚餐开始窗口和默认时长；文本不覆盖；
- 机场/铁路固定抵离余量、近似时间 +30；拒绝模型从文本改变处理值；
- arrival/departure `BoundaryTransfer` 与正式游览 `DayPlan` 分离；抵达可早于 sightseeing start、离开可晚于 sightseeing end，均不伪造额外 DayPlan；同日抵离和跨午夜压缩；
- full/partial 至少一个 open interval；完全被抵离/跨日压缩的 transit-only 可用空 tuple，禁止伪造窗口；
- full-day 轻松 1..2、标准 3、紧凑 4；恰好午餐+晚餐；主候选≤6；
- partial 日的 lunch/dinner 各允许 0..1，由模型按真实可用窗口、用户要求和节奏决定；不设餐次硬配额，不越过不可移动边界、不凑数；
- daily window 不要求填满，空闲不算访问；
- 保留锚点 DTO 原有结构反测；公共入口证明锚点能力停用、默认窗口不被突破，POI 访问不冒充活动；
- 三类延期规则必须为空；非空提议被业务拒绝，合法 pace 已先锁定；不得靠解析原文或 source_digest 声称语义正确；
- agent-decides pace 的首次结构合法终止尝试锁定与后续不可变。

### 13.3 候选、must、餐次、营业与备选

- 只有 eligible admitted candidate 可用，avoid/conflicting 不可用；
- 普通 UID 整趟主路线最多一次，酒店可重复；
- 完整 must 组一 UID/组、等价组可共用 UID、容量不足 Incomplete；覆盖零匹配组和未解决 predecessor gap，备选不满足 must；
- interest 只影响偏好，不设覆盖 Gate；
- attraction+一顿 meal 可组合，只占一地点；不能同时 lunch+dinner；
- compatible/conflicts/unknown 三态，unknown 待核验，conflicts 阻止；不得 arbitrary parser 提升；
- 每个适用日期的 attraction/lunch/dinner 备选资格、唯一性、未上主线、Matrix≤3000m、无 Directions；
- 备选缺失不生成阻塞缺口、已提供备选仍校验且无自动替换；
- Module 08 Incomplete+eligible≥1 可尝试；eligible=0 按输入前置违反失败且不启动模型；Completed 保留 upstream gaps 且所有当前硬条件满足。

### 13.4 工具、Matrix 与 Directions

- 工具白名单精确六个；未知工具、模型伪造 UID/坐标/Ref、过期 handle 均拒绝；
- evidence 仅取已有数据；hub geocode 四类 outcome 与用户修正失效；
- Matrix N×N、1..10、cells≤100、每迭代每日期最多一次、多日 batch 的真实调用并发≤3；
- Directions 多腿 batch、每腿独立 operation/budget、并发≤3、稳定 ordinal；
- Directions 批次预算不足 all-or-none；cache hit/replay 不消费；
- 每个相邻主腿 exact origin/destination/mode/observation/alternative 匹配；Matrix 不能替代；
- alternatives 1..3 与供应商原索引；NoResult 可重排，耗尽后 Incomplete；typed 技术/不确定失败为 Failed；
- 不传 departure time、不宣称公交班次；不保存/展示 RouteStep instruction；
- 交通展示语义与 route mode 双保存；文本限制延期而非强制 Incomplete；非法模式仍拒绝，无价格/服务可用性声明。

### 13.5 节奏、硬软约束与非目标

- full-day 总路线 raw seconds 上限 120/180/240；抵离日排除；
- 全模式单腿上限 60/90/120；恰好边界通过，超一秒拒绝；
- 既有节奏上限保持，must/daytrip 冲突重排后 Incomplete；自定义数值上限文本不执行；
- mobility/交通/备注文本不进入规则生成，不能成为隐藏的软提示；
- 儿童/老人不自动生成数值限制；
- 不存在 Citywalk、步行总量/连续步行/专属距离时间 Gate；
- 完全不读取或判断天气；不评估饮食适配；酒店寄存只是排程默认且不作为核验声明；
- 不计算路线价格，不进行实时导航、预约或库存；时间、路线、返程和事实证据校验已经属于 Module 09 自身提交 Gate，不调用下游模块重复验证。

### 13.5.1 范围收敛的公共入口回归

- 同一合法结构化输入，分别增加“十二点后出门”、指定 20:15 餐次/时长、交通方式限制及自定义交通时长文本；在相同 fake 模型动作下应仍按默认规则判定，延期文本不自动产生阻塞 gap，不要求真实模型两次随机输出相同。
- 断言给模型的支持范围投影不包含延期文本作为指令；源 confirmed 不被修改，完整请求摘要仍绑定源对象，不能删原文来制造同请求 replay。
- 模型强行提交非空 daily/time-sensitive/transport 规则或 user_explicit 时长：拒绝该提交，无非法 draft/ref，pace 已锁定；下一次合法提交可继续，不能改 pace。
- 固定范围说明与 Completed 定义一致；下游展示不在本轮实现范围时报告 NOT_RUN，不假称已展示，也不阻塞本模块离线算法验证。
- 一条 must 已匹配而另一条零匹配、未解决 avoid、默认窗口冲突、Directions 缺失各有独立反测；范围延期不能让它们 Completed。
- 原 R 系列文本解析测试按 DEF-09-010 记录替代关系；保留真实技术失败、写入不确定、业务拒绝、补研和 exact replay 测试，不把删测试或降低覆盖率当作范围收敛。

### 13.6 ReAct、迭代、停止与 terminal

- 首轮 `ainvoke_tools`、后续 `acontinue_tools`，同 run 一个 operation；
- 结构合法的非终止批次计一轮，批量仍为一轮且 policy/controller rejection 也计；结构合法但业务非法的 submit 计一轮；accepted terminal、普通文本、模型合同失败和纯结构非法批次不误计；
- 准备2/交通5/修正5/补充5，总17；交通提前submit不双计，修正最后仅submit；
- 准备/交通真实连续无进展、修正连续两次完全重复拒绝按设计§12.4；无合法Action仍Incomplete；
- 普通文本→Incomplete；空工具调用、terminal 混批、超容量和 sibling dependency→对应技术 Failed 且不消费；合法 submit 是 Completed 唯一路径；
- 无独立terminal_action；17个统一invocation slots各有action/state，最后修正使用其正常slot且实际仅submit；非法工具继续结构失败，普通文本/业务拒绝按设计保留真实未完成；
- rejected submit 返回 typed observation 且不保存非法 draft；
- longest consistent prefix 只随可证明合法状态推进；
- Completed 必要且充分条件逐项重算；Incomplete gap/stop order 稳定；Failed 优先级正确。
- full/partial/transit 的非法景点构成使用 `DayCompositionGap`；只有过程停止时使用匹配 stop reason 的 `PlanningProcessGap`，不得伪造候选/路线 gap，且所有 Incomplete gaps 非空。

### 13.7 Supplemental、Artifact、replay 与数据库预留

- supplement 只含可映射候选供给 gap；Module 09 不直接调用 Module 08；
- `CandidateSupplyGap` 七种 target 与 attraction/meal backup 映射逐字段生成 Module 08 对应分支，覆盖 hotel/candidate anchor、replacement 可空 anchor、backup 的 date/core visit 定位；原 gap/证据/UID tuple 顺序和全部标量不变。兴趣无映射，非法 target/evidence 零 Module 08 调用；
- 使用真实公共 `CandidateResearchService` 配合离线 fake 依赖生成带真实 `invocation_request_digest` 的补研 Artifact，再经 Module 09 公共 `plan_resume` 验证原请求关联；手工填入任意“匹配”摘要的夹具不能作为这条跨模块合同的唯一证据。其余组合可用公共 DTO fake，不改 Module 08 生产或测试文件；
- 关联正测覆盖合法 Incomplete、没有新增 UID、仅旧 UID 合法更新且 eligible≥1；仍有原 gap 不阻止准入，也不得直接提升为规划 Completed；
- 原 Module 08 计划经历业务轮和中间 successor 后仍能关联最终 Artifact；其 `predecessor_ref` 对应原调用起点而非最后中间 Ref。原计划 slots 不必等于补研完成后的余额，合法零 slot/no-op 结果同样准入，Module 09 不查询 Module 08 预算或重跑其历史动态 Gate；
- 关联反测覆盖错误原前驱 Ref、实际计划 terminal ID、完整摘要、awaiting/pending 不匹配及不可还原来源；另构造 gap 顺序、证据顺序、typed target 或非 terminal 计划 ID 不同但上游摘要合法的其他补研请求，证明 Module 09 比较自己的 pending 请求。按权威设计 §15.1/§18 断言错误分类、运行态不变、无模型/地图/预算调用，`request_mismatch` 零新 Artifact；
- 改变实际 Module 08 计划必须改变 Resume v2 摘要并阻止同 terminal ID 的错误 replay；exact replay 在前驱后来缺失/损坏时仍不读取它。相同输入的 Initial、context、gap、handle 与地图指纹保持旧值，Module 08 原请求 hash 不带 Module 09 envelope；
- 同 gap fingerprint 整趟最多自动请求一次、整个 run 最多接受 5 次且必须保留至少一轮；2+5+5+5=17边界通过，第6次申请必须拒绝；resume同run保留预算/缓存/阶段分类/拒绝基线/policy/draft；
- accepted supplement 不关闭 operation，保存 pending call ID；resume 以匹配 `ToolResult` 调同一 operation 的 `acontinue_tools`，不得第二次 start；
- 尚无合法访问/路线前缀时 supplement 可提交 `draft=None`；一旦存在合法前缀必须携带匹配 draft，反向组合均拒绝；
- 新候选 admission 只追加，不改变旧 handle/ordinal；既有候选更新后逐项重验 state/role/meal/must/avoid/evidence/坐标，并移除已失效访问、备选和前缀；
- 五类 Artifact type/version/registration 精确；Ref 同 run、允许 type、index 与证据匹配；
- immutable version chain、parent/predecessor、stable operation、payload hash、无 scan lookup；
- terminal write timeout 的 lookup/replay/indeterminate；同请求并发 singleflight；
- resume predecessor 缺失/损坏时不读取低优先级预算副作用、不猜 direction limit，Failed `budgets=None`；有可信 state 时必须返回带 complete flags 的摘要；
- payload 可直接映射未来 PostgreSQL 主键/唯一约束/JSON version，但首版无数据库调用与 migration。

### 13.8 安全、取消、并发与生命周期

- Prompt hash fail-closed；超长/恶意候选文本按有界安全投影，不成为工具指令；
- Artifact/日志不含原 Prompt、完整对话、CoT、provider body、secret、异常消息、RouteStep；
- 不同 run 并发、同 run 串行、地图最多 3，并发完成顺序不影响结果；
- 调用方取消原样传播，任务全部收敛，无 terminal Artifact、无继续派发；
- close_run/aclose 幂等，只关闭本模块创建的 operation/context，不关闭注入依赖；
- capacity boundary、失败后无额外副作用和资源泄漏。

## 14. 阶段 09-1：公共 DTO、错误与协议

### 14.1 目标与前置

目标：建立不依赖 Service 的严格公共模型、枚举、错误和协议骨架。前置：§2～§4 gate 全部通过，分支/HEAD 与批准快照一致。

### 14.2 文件白名单

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/errors.py
app/itinerary_planning/protocols.py
tests/unit/itinerary_planning/test_models.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_import_boundaries.py
```

只允许创建/修改这些文件。禁止触碰 Artifact、Prompt、Service、地图执行、上游代码和配置。

### 14.3 验收

- 权威设计附录 A/B/C 的本阶段 DTO、enum、union、容量和错误全部实现；
- 每个 public/proposal/tool DTO 均有冻结 `schema_version` 第一字段；strict/frozen/extra-forbid、时区、索引、唯一性和 operation plan 测试通过；
- 本阶段 `__all__` 使用提示词冻结的精确投影，不含 Service/stub/私有端口；
- import boundary 只允许标准库、Pydantic 与六个上游公共门面；
- 对照权威设计 §6～§8、§16、附录 A～C 人工检查。

### 14.4 本阶段执行合同

- 允许操作：只读审计；仅创建/修改 §14.2 文件；运行只覆盖 §14.2 的格式、静态导入和 pytest；在同一白名单内修复本阶段明确缺陷。
- 禁止操作：Artifact/Prompt/Service/地图调用；白名单外文件；上游改动；新依赖、migration、网络、secret；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计 §1、§4、§6～§8、§16、§18、§20、附录 A～C，以及本文 §3～§7。
- 高收益正测：合法 Initial/Resume/OperationPlan、三类 LocationRef、时区区间、全部 outcome discriminator、Protocol 签名、阶段精确 `__all__`，以及 public DTO 的 fresh JSON round-trip 与对象非同一性。
- 高收益反测：bool 冒充 int、naive/非上海 datetime、额外字段、宽松 coercion、`model_construct`/forged instance、伪造 enum/ref/version、重复/跨 run slot、导出私有端口；服务边界必须再次拒绝 forged 对象。
- Docker Gate：09-1 单独完成时不宣告成组 PASS；完成 09-2 后按 §15.5 从当前源码构建 test target 并统一运行 09-1+09-2 测试。
- PASS：目标、白名单、正反测试和人工对照均满足后，仅可报告 `READY_FOR_INDEPENDENT_REVIEW`。
- STOP：§4 前置不成立、需要改 Artifact/Prompt/Service/上游/配置、白名单不足或设计不自洽。
- 报告：完整使用 §24 模板，阶段写 `09-1`，未到联合 Gate 的测试明确标 `NOT_RUN — 等待相邻阶段 09-2 统一执行`。

## 15. 阶段 09-2：Artifact、指纹与确定性 policy 基础

### 15.1 目标与前置

目标：实现五类 Artifact、稳定 canonical/fingerprint、硬 Gate 的纯函数基础。前置：09-1 内容仍在批准白名单内且没有未解释漂移。

### 15.2 文件白名单

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
tests/unit/itinerary_planning/test_models.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_artifact_registration.py
tests/contract/itinerary_planning/test_upstream_integration.py
```

### 15.3 验收

- Artifact type/version、record/snapshot 私有性、Ref 目录的精确 type/version/同 run/首次顺序唯一性和安全容量正确；
- 五类 Artifact 及其每个 record/observation 分支均通过真实 `ArtifactSchemaRegistry` 注册、repository prepare/decode、fresh JSON round-trip、对象非同一性、forged/extra/coercion 反测；所有可达 Artifact-local model 均满足 Module 04 精确 config 与全字段 required 合同；
- public/upstream DTO 与 Artifact-local snapshot 双向转换逐分支同构，转换后重验公共与 snapshot 不变量；不兼容的 Module 09/06/02 DTO 没有直接嵌入 Artifact 图；
- canonical/fingerprint 跨 dict 顺序、并发顺序和进程稳定；
- 分钟排程、连续日期、跨午夜、抵离余量、密度、meal、UID、must/avoid、营业、备选、节奏与交通硬 Gate 有纯函数测试；
- policy 只判合法性，不替模型做语义选择；
- 无数据库、migration、Prompt 或外部调用。

### 15.4 本阶段执行合同

- 允许操作：只读审计；仅创建/修改 §15.2 文件；实现纯 DTO/Artifact/canonical/policy；运行 §15.2 测试；在同一白名单内修复明确缺陷。
- 禁止操作：Prompt、Service、模型/地图调用、数据库或 migration；白名单外文件、上游改动、新依赖、secret；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计 §8～§9、§11、§14、§16～§20、附录 A～D，以及本文 §6～§10。
- 高收益正测：五类注册、合法不可变版本链、稳定 fingerprint、full/partial/transit 日、组合餐次、等价 must、unknown 营业、合法备选和三档节奏边界。
- 高收益反测：同 OperationId 异 payload、超 1 MiB、非法 Ref/index、午夜截断、兴趣硬覆盖、备选替 must、walking 专属 Gate、天气/饮食字段、超过交通上限。
- Docker Gate：本阶段完成后必须立即执行 §15.5 的 09-1+09-2 联合 Gate，不得只运行新增测试。
- PASS：联合 Gate、白名单和逐条设计对照都通过后，仅可报告 `READY_FOR_INDEPENDENT_REVIEW`。
- STOP：需要数据库/migration/Prompt/Service、修改上游 Artifact 合同、增加业务规则或扩大白名单。
- 报告：完整使用 §24 模板，阶段写 `09-1 + 09-2`，逐条列出两个阶段的文件、测试和偏差。

### 15.5 `09-1 + 09-2` 联合 Gate

联合文件白名单精确为：

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/errors.py
app/itinerary_planning/protocols.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
tests/unit/itinerary_planning/test_models.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_import_boundaries.py
tests/contract/itinerary_planning/test_artifact_registration.py
tests/contract/itinerary_planning/test_upstream_integration.py
```

当次实施提示词必须给出从仓库根执行的精确安全容器命令，至少运行：

```text
tests/unit/itinerary_planning/test_models.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_import_boundaries.py
tests/contract/itinerary_planning/test_artifact_registration.py
tests/contract/itinerary_planning/test_upstream_integration.py
```

固定外层 PowerShell 命令为：

```powershell
$module09DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module09-phase12-docker"
$module09TestImage = "china-travel-agent:module09-phase12-test"
$module09Container = "china-travel-agent-module09-phase12-tests"
function Assert-Module09NativeSuccess { param([string]$Step) $code = $LASTEXITCODE; Write-Output "$Step exit_code=$code"; if ($code -ne 0) { throw "$Step failed with exit code $code" } }
docker --config $module09DockerConfig buildx build --load --progress plain --tag $module09TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
Assert-Module09NativeSuccess "phase12-build-test"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/unit/itinerary_planning/test_models.py tests/unit/itinerary_planning/test_fingerprints.py tests/unit/itinerary_planning/test_policy.py tests/contract/itinerary_planning/test_public_api.py tests/contract/itinerary_planning/test_import_boundaries.py tests/contract/itinerary_planning/test_artifact_registration.py tests/contract/itinerary_planning/test_upstream_integration.py
Assert-Module09NativeSuccess "phase12-module09-tests"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/contract/requirements tests/contract/llm_runtime/test_provider_protocol.py tests/contract/workflow_contracts/test_public_api.py tests/contract/baidu_map/test_public_api.py tests/contract/hotel_selection/test_public_api.py tests/contract/candidate_research/test_public_api.py
Assert-Module09NativeSuccess "phase12-upstream-contracts"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --env PYTHONPYCACHEPREFIX=/tmp/pycache $module09TestImage python -m compileall -q app/itinerary_planning tests/unit/itinerary_planning tests/contract/itinerary_planning
Assert-Module09NativeSuccess "phase12-compileall"
$module09Residual = @(docker --config $module09DockerConfig ps -a --filter "name=$module09Container" --format "{{.ID}} {{.Names}} {{.Status}}")
Assert-Module09NativeSuccess "phase12-residual-query"
if ($module09Residual.Count -ne 0) { $module09Residual | Write-Output; throw "phase12 residual container detected" }
```

最后一行输出必须为空；任一命令非零立即 STOP。不得在宿主机直接执行 Python 命令并作为正式证据。

并对照权威设计 D-09-01～D-09-10、D-09-15～D-09-18、D-09-23～D-09-28、D-09-34～D-09-50 做逐项人工核对。报告状态只能是 `READY_FOR_INDEPENDENT_REVIEW` 或 STOP。

## 16. 阶段 09-3：Prompt、六工具分派与每日策略

### 16.1 目标与前置

目标：实现新范围 Prompt 完整性、安全投影、六工具白名单、handle 目录及首版 pace policy 锁定/延期规则拒绝。前置：09-1+09-2 已通过独立审查或当次授权明确允许继续。

### 16.2 文件白名单

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/prompt_loader.py
app/itinerary_planning/prompting.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
app/itinerary_planning/tooling.py
app/itinerary_planning/prompts/planning_v4.txt
tests/support/itinerary_planning_fakes.py
tests/unit/itinerary_planning/test_prompt_loader.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/unit/itinerary_planning/test_tooling.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_security_contract.py
```

### 16.3 验收

- Prompt version/hash fail-closed，渲染有界且不含敏感字段；
- 工具清单精确为 `get_candidate_evidence`、`geocode_hub`、`calculate_route_matrix`、`plan_directions`、`submit_itinerary`、`request_candidate_supplement`；
- 未知工具、终止混批、越界 batch、伪造/过期 handle 全部在外部调用前拒绝；
- 模型保有权威设计 §12.1 的规划决策权，控制器只执行 Gate；
- 支持范围投影、延期规则业务拒绝、默认时间窗/时长、合法路线映射与 effective pace 锁定测试完整；
- 普通文本/空调用不能形成成功。

### 16.4 本阶段执行合同

- 允许操作：只读审计；仅创建/修改 §16.2 文件；生成固定 Prompt/hash；实现安全投影、handle 目录和六工具结构分派；运行本阶段测试；在白名单内修复明确缺陷。
- 禁止操作：真实模型/地图/HTTP/MCP、Service 主循环、数据库、依赖或配置；自由查询工具；白名单外文件；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计 §3、§7～§12、§15～§20、附录 A～D，以及本文 §6～§12。
- 高收益正测：新版 Prompt hash、六 ToolContract、合法 handle、三空文本规则、trip-wide pace、终止工具独占、合法结构化输入不受延期文本阻断。
- 高收益反测：Prompt 篡改、候选文本注入、伪造 UID/坐标/Ref、未知/近似工具名、终止混批、超容量、普通文本、偷改已锁策略。
- Docker Gate：09-3 单独完成时不宣告成组 PASS；完成 09-4 后按 §17.5 构建 test target 并统一运行 09-3+09-4 测试。
- PASS：目标、白名单、正反测试和人工对照均满足后，仅可报告 `READY_FOR_INDEPENDENT_REVIEW`。
- STOP：需要第七个工具、真实外部能力、修改 Module 02/06、放宽 Prompt 完整性或扩大白名单。
- 报告：完整使用 §24 模板，阶段写 `09-3`，联合 Gate 未到时注明等待 09-4，不把局部绿误报为成组完成。

## 17. 阶段 09-4：地图执行、预算、缓存与逐腿证据

### 17.1 目标与前置

目标：实现 Hub Geocoding、N×N Matrix、多腿 Directions、批次预占、缓存和有序并发。前置：09-3 工具合同稳定，Module 06 acceptance gate 未漂移。

### 17.2 文件白名单

```text
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
app/itinerary_planning/tooling.py
app/itinerary_planning/map_execution.py
tests/support/itinerary_planning_fakes.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/unit/itinerary_planning/test_tooling.py
tests/unit/itinerary_planning/test_map_execution.py
tests/contract/itinerary_planning/test_upstream_integration.py
tests/contract/itinerary_planning/test_replay_budget_contract.py
tests/contract/itinerary_planning/test_security_contract.py
```

### 17.3 验收

- Hub 四类 outcome、用户修正后缓存失效；
- Matrix 1..10/N×N/cells≤100、同轮同日限制、备选 3000m 证据；
- Directions multi-leg、alternatives 1..3、每腿 operation/budget、全批预检、逐槽 consume 与 all-reserved-before-dispatch barrier；
- 缓存 key/失效、cache hit/replay 不消费预算；
- 真调用并发≤3、稳定排序、取消收敛；
- Directions exact matching 是 Completed 前置，Matrix 永不替代；
- 无 departure time、RouteStep、价格、实时导航或步行专属 Gate。

### 17.4 本阶段执行合同

- 允许操作：只读审计；仅创建/修改 §17.2 文件；用 fake 实现/验证 Module 06 公共调用、预算、缓存、并发和取消；在白名单内修复明确缺陷。
- 禁止操作：live API、MCP/HTTP/SDK 私有层、`.env`/secret、Service terminal 主循环、上游代码、依赖/配置、白名单外文件；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计 §9.6、§10～§14、§18～§20、D-09-13～D-09-14、D-09-24～D-09-25、D-09-28～D-09-33、D-09-37～D-09-39、D-09-49～D-09-50，以及本文 §8～§13。
- 高收益正测：10 点/100 cells N×N、跨日期 Matrix batch、3 并发 wave、多腿 Directions、1..3 方案、exact match、cache/replay 和预算边界。
- 高收益反测：101 cells、transit Matrix、同轮同日第二 Matrix、预算少 1 的整批零调用、wave 技术失败、取消、结果乱序、过期 cache、Matrix 冒充 Directions、RouteStep 泄露。
- Docker Gate：本阶段完成后必须立即执行 §17.5 的联合 Gate；容器必须无网络、只读且不传 secret。
- PASS：联合 Gate 和人工对照通过后，仅可报告 `READY_FOR_INDEPENDENT_REVIEW`。
- STOP：Module 06 公共合同不足、需要私有 gateway/live provider、无法实现全批预检+逐槽 consume+全预留后派发屏障、取消无法收敛或白名单不足。
- 报告：完整使用 §24 模板，阶段写 `09-3 + 09-4`，报告每条地图调用计数、预算、并发峰值和容器退出码。

### 17.5 `09-3 + 09-4` 联合 Gate

联合文件白名单精确为：

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/prompt_loader.py
app/itinerary_planning/prompting.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
app/itinerary_planning/tooling.py
app/itinerary_planning/map_execution.py
app/itinerary_planning/prompts/planning_v4.txt
tests/support/itinerary_planning_fakes.py
tests/unit/itinerary_planning/test_prompt_loader.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/unit/itinerary_planning/test_tooling.py
tests/unit/itinerary_planning/test_map_execution.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_upstream_integration.py
tests/contract/itinerary_planning/test_replay_budget_contract.py
tests/contract/itinerary_planning/test_security_contract.py
```

至少运行：

```text
tests/unit/itinerary_planning/test_prompt_loader.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/unit/itinerary_planning/test_tooling.py
tests/unit/itinerary_planning/test_map_execution.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_upstream_integration.py
tests/contract/itinerary_planning/test_replay_budget_contract.py
tests/contract/itinerary_planning/test_security_contract.py
```

固定外层 PowerShell 命令为：

```powershell
$module09DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module09-phase34-docker"
$module09TestImage = "china-travel-agent:module09-phase34-test"
$module09Container = "china-travel-agent-module09-phase34-tests"
function Assert-Module09NativeSuccess { param([string]$Step) $code = $LASTEXITCODE; Write-Output "$Step exit_code=$code"; if ($code -ne 0) { throw "$Step failed with exit code $code" } }
docker --config $module09DockerConfig buildx build --load --progress plain --tag $module09TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
Assert-Module09NativeSuccess "phase34-build-test"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/unit/itinerary_planning/test_prompt_loader.py tests/unit/itinerary_planning/test_fingerprints.py tests/unit/itinerary_planning/test_policy.py tests/unit/itinerary_planning/test_tooling.py tests/unit/itinerary_planning/test_map_execution.py tests/contract/itinerary_planning/test_public_api.py tests/contract/itinerary_planning/test_upstream_integration.py tests/contract/itinerary_planning/test_replay_budget_contract.py tests/contract/itinerary_planning/test_security_contract.py
Assert-Module09NativeSuccess "phase34-module09-tests"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/contract/llm_runtime/test_provider_protocol.py tests/contract/workflow_contracts/test_artifact_repository_contract.py tests/contract/workflow_contracts/test_execution_budget_manager_contract.py tests/contract/baidu_map/test_adapter_contract.py tests/contract/hotel_selection/test_upstream_integration.py tests/contract/candidate_research/test_upstream_integration.py tests/contract/candidate_research/test_replay_budget_contract.py
Assert-Module09NativeSuccess "phase34-upstream-contracts"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --env PYTHONPYCACHEPREFIX=/tmp/pycache $module09TestImage python -m compileall -q app/itinerary_planning tests/unit/itinerary_planning tests/contract/itinerary_planning
Assert-Module09NativeSuccess "phase34-compileall"
$module09Residual = @(docker --config $module09DockerConfig ps -a --filter "name=$module09Container" --format "{{.ID}} {{.Names}} {{.Status}}")
Assert-Module09NativeSuccess "phase34-residual-query"
if ($module09Residual.Count -ne 0) { $module09Residual | Write-Output; throw "phase34 residual container detected" }
```

最后一行输出必须为空；任一命令非零立即 STOP。严禁把 live vertical 或宿主机结果计入 Gate。

并逐项对照权威设计 §9～§14、§18～§20、D-09-11～D-09-15、D-09-21～D-09-33、D-09-35～D-09-42、D-09-49～D-09-50。不得以 fake 只测“两点路线”替代 10 点 Matrix、跨日 batch 和多腿 Directions。

## 18. 阶段 09-5：完整 ReAct、Initial/Resume 与终态

### 18.1 目标与前置

目标：实现端到端受控 ReAct Service、最长合法前缀、initial/resume、supplemental、所有公共 outcome。前置：09-1～09-4 合同通过，用户明确授权本阶段。

### 18.2 文件白名单

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/errors.py
app/itinerary_planning/protocols.py
app/itinerary_planning/prompting.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
app/itinerary_planning/tooling.py
app/itinerary_planning/map_execution.py
app/itinerary_planning/service.py
tests/support/itinerary_planning_fakes.py
tests/unit/itinerary_planning/test_service_initial.py
tests/unit/itinerary_planning/test_service_resume.py
tests/unit/itinerary_planning/test_service_failures.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_artifact_registration.py
tests/contract/itinerary_planning/test_upstream_integration.py
tests/contract/itinerary_planning/test_replay_budget_contract.py
```

### 18.3 验收

- Service 构造/Port/close 签名精确；一个 run 使用一个 Module 02 operation；
- Initial准入、Hub提前完成、2/5/5/5、最后实际submit-only、阶段无进展和无合法Action 顺序正确；
- submit 全 Gate 重算，合法 submit 是唯一 Completed 路径；非法 submit 不保存并可在剩余轮次修订；
- longest consistent draft 与 typed gaps 正确；
- Module 08 Incomplete/eligible 分支正确；Resume `3.0` 按权威设计 §15.1 完成实际计划、原请求摘要和 successor 关联验证，合法无新增/仅旧 UID 更新可恢复；
- supplement fingerprint 一次性、Module 13 边界、resume 同 run 预算/缓存/迭代/policy/handle 连续；
- ordinary text→Incomplete，typed 模型/地图/控制/Artifact failure→Failed；
- terminal successor lookup-first/replay，结果不确定不盲重试。

### 18.4 本阶段执行合同

- 允许操作：只读审计；仅创建/修改 §18.2 文件；实现 Service 编排、initial/resume、terminal outcomes、最长合法前缀和 supplemental；运行本阶段 fake 测试；在白名单内修复明确缺陷。
- 禁止操作：真实模型/地图、Module 13 编排实现、数据库/migration、下游 Module 10～14 代码、依赖/配置、白名单外文件；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计 §2～§20、附录 A～D，重点 §6、§12～§18；以及本文 §7～§13。
- 高收益正测：完整合法 submit、partial 合法前缀、17次分类边界、不同阶段无进展、accepted supplement、同 run resume、exact terminal replay。
- 高收益反测：普通文本冒充成功、非法 submit 落盘、预算/迭代重置、重复 fingerprint、Module 09 直接调 Module 08、terminal 写不确定盲重试、关闭注入依赖。
- Docker Gate：09-5 单独完成时不宣告成组 PASS；完成 09-6 后按 §19.5 构建测试镜像并运行完整 09-5+09-6 Gate。
- PASS：目标、白名单、正反测试和逐条人工审查均满足后，仅可报告 `READY_FOR_INDEPENDENT_REVIEW`。
- STOP：需要改变终态/工具/预算/Artifact、接管 Module 13/10/11/12/14、引入网络或扩大白名单。
- 报告：完整使用 §24 模板，阶段写 `09-5`，未执行的完整覆盖和独立审查必须写原因。

## 19. 阶段 09-6：安全、并发、覆盖率与模块 Gate

### 19.1 目标与前置

目标：补齐恶意输入、并发、取消、生命周期、容量、全路径覆盖和最终模块 Gate。前置：09-5 功能面完整；仅允许补测试和当前白名单内的小范围缺陷。

### 19.2 文件白名单

```text
app/itinerary_planning/__init__.py
app/itinerary_planning/models.py
app/itinerary_planning/artifacts.py
app/itinerary_planning/errors.py
app/itinerary_planning/protocols.py
app/itinerary_planning/prompt_loader.py
app/itinerary_planning/prompting.py
app/itinerary_planning/fingerprints.py
app/itinerary_planning/policy.py
app/itinerary_planning/tooling.py
app/itinerary_planning/map_execution.py
app/itinerary_planning/service.py
app/itinerary_planning/prompts/planning_v4.txt
tests/support/itinerary_planning_fakes.py
tests/support/run_module09_coverage.py
tests/unit/itinerary_planning/test_models.py
tests/unit/itinerary_planning/test_prompt_loader.py
tests/unit/itinerary_planning/test_fingerprints.py
tests/unit/itinerary_planning/test_policy.py
tests/unit/itinerary_planning/test_tooling.py
tests/unit/itinerary_planning/test_map_execution.py
tests/unit/itinerary_planning/test_service_initial.py
tests/unit/itinerary_planning/test_service_resume.py
tests/unit/itinerary_planning/test_service_failures.py
tests/unit/itinerary_planning/test_service_concurrency.py
tests/contract/itinerary_planning/test_public_api.py
tests/contract/itinerary_planning/test_import_boundaries.py
tests/contract/itinerary_planning/test_artifact_registration.py
tests/contract/itinerary_planning/test_upstream_integration.py
tests/contract/itinerary_planning/test_replay_budget_contract.py
tests/contract/itinerary_planning/test_security_contract.py
tests/contract/itinerary_planning/test_coverage_gate.py
```

### 19.3 验收

`tests/support/run_module09_coverage.py` 的合同固定为：在一个 pytest 进程中仅采集 `app.itinerary_planning`；测试集合精确为全部 Module 09 unit/contract；零 test、skip、xfail、rerun、采集错误或非零 pytest 均失败；使用 coverage JSON 的未舍入分子/分母计算：

```text
line = 100 * covered_lines / statements
branch = 100 * covered_branches / branches
combined = 100 * (covered_lines + covered_branches) / (statements + branches)
```

`statements=0` 或 combined 分母为 0 必须失败；`branches=0` 时 branch 记 100%，但不能改变 combined 的真实分母。三项分别以未舍入值比较 70.00%，并打印 statements、covered_lines、branches、covered_branches、line、branch、combined、missing lines/branches 与退出码。禁止 coverage combine、多进程数据拼接、扩大/缩小 source 或排除难测生产文件。

- §13 全矩阵有测试定位，无 `skip/xfail`、真实网络、secret 或 sleep 型竞态；
- 权威设计 D-09-67/68 的局部版本和补研关联测试通过；旧 Resume 拒绝，精确 replay 顺序与未升级 DTO/Artifact 格式不回归；指纹变更另按 §10.3 核验；
- 取消、并发、singleflight、close、terminal indeterminate、容量边界和安全扫描通过；
- line、branch、combined coverage 分别 `>=70%`，coverage harness 失败码和报告可审计；
- 全部 Module 09 测试通过，受影响的 Module 01/02/04/06/07/08 合同回归通过；
- 在 `PYTHONPYCACHEPREFIX=/tmp/pycache` 下执行 `python -m compileall -q app tests` 通过；
- 独立代码审查前不跑全仓覆盖率，不修改 closeout 文档/构建文件。

### 19.4 本阶段执行合同

- 允许操作：只读审计；仅创建/修改 §19.2 文件；补齐安全、并发、取消、容量、coverage harness 和合同测试；只在同一白名单内修复已复现的 Module 09 缺陷。
- 禁止操作：closeout 文件、上游/下游模块、依赖、migration、live API、secret、白名单外文件；删除/降级测试；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计全文，尤其 §6.2、§15.1、§16～§23、D-09-51～D-09-68；本文 §3～§13 和 §20～§23。
- 高收益正测：端到端 fake happy path、同 run singleflight、不同 run 并发、取消收敛、terminal replay、五 Artifact 重建、三项 coverage 门槛。
- 高收益反测：canary 泄露、close 竞态、部分写/预算不确定、超容量、并发乱序、全仓 source 稀释、skip/xfail/零测试、残留容器与网络尝试。
- Docker Gate：本阶段完成后必须执行 §19.5 全序 Gate；任一步失败即 STOP，不得跳到独立审查。
- PASS：§19.5 全部通过、diff 白名单正确、逐条人工对照完成后，仅可报告 `READY_FOR_INDEPENDENT_REVIEW`。
- STOP：三项 coverage 任一不足、测试/compileall/安全失败、P0/P1 已知未修、需要 closeout/上游/配置改动或白名单不足。
- 报告：完整使用 §24 模板，阶段写 `09-5 + 09-6`，列出 line/branch/combined、missing lines/branches、容器和全部异常文件。

### 19.5 `09-5 + 09-6` 联合 Gate

顺序固定：

1. 构建测试镜像；
2. 安全容器运行 Module 09 unit/contract 测试；
3. 运行受影响上游合同回归；
4. 运行 compileall；
5. 运行 Module 09 coverage harness 并验证 line、branch、combined 三项分别≥70%；
6. 检查 git diff、异常文件、staged、依赖和 secret；
7. 按两份 Module 09 文档逐项人工审查；
8. 输出 `READY_FOR_INDEPENDENT_REVIEW`，不得自称 COMPLETE。

固定外层 PowerShell 命令为：

```powershell
$module09DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module09-phase56-docker"
$module09TestImage = "china-travel-agent:module09-phase56-test"
$module09Container = "china-travel-agent-module09-phase56-tests"
function Assert-Module09NativeSuccess { param([string]$Step) $code = $LASTEXITCODE; Write-Output "$Step exit_code=$code"; if ($code -ne 0) { throw "$Step failed with exit code $code" } }
docker --config $module09DockerConfig buildx build --load --progress plain --tag $module09TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
Assert-Module09NativeSuccess "phase56-build-test"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/unit/itinerary_planning tests/contract/itinerary_planning
Assert-Module09NativeSuccess "phase56-module09-tests"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/unit tests/contract --ignore=tests/unit/itinerary_planning --ignore=tests/contract/itinerary_planning
Assert-Module09NativeSuccess "phase56-complement-tests"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --env PYTHONPYCACHEPREFIX=/tmp/pycache $module09TestImage python -m compileall -q app tests
Assert-Module09NativeSuccess "phase56-compileall"
docker --config $module09DockerConfig run --name $module09Container --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python tests/support/run_module09_coverage.py
Assert-Module09NativeSuccess "phase56-coverage"
$module09Residual = @(docker --config $module09DockerConfig ps -a --filter "name=$module09Container" --format "{{.ID}} {{.Names}} {{.Status}}")
Assert-Module09NativeSuccess "phase56-residual-query"
if ($module09Residual.Count -ne 0) { $module09Residual | Write-Output; throw "phase56 residual container detected" }
```

前两条 pytest 的收集集合并集必须精确覆盖全部 unit/contract，交集为空且两边 collect 均非零。coverage harness 必须在单一 Python/pytest 进程中采集 `app.itinerary_planning`，分别计算 line、branch、combined 并对每项执行 70% 门槛；不得合并多个 `.coverage` 文件、改 source 或用全仓代码稀释。残留容器检查输出必须为空，任一命令非零立即 STOP。

## 20. Docker 构建与安全容器

每个阶段提示词必须根据仓库实时 Dockerfile/compose 生成精确命令，不得盲复制本文。通用安全要求：

- 从当前工作树源码重建测试镜像，不使用宿主虚拟环境证明验收；
- Docker 配置目录使用项目专用临时目录，不复用或覆盖用户全局配置；
- 容器使用 `--rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true`；
- 所有 read-only 容器内的 `compileall` 命令额外使用 `--env PYTHONPYCACHEPREFIX=/tmp/pycache`，把字节码缓存写入 `/tmp` tmpfs；不得向宿主工作树生成 `__pycache__`；
- 不传真实 API key、不挂载 `.env`、不开放端口、不运行真实百度/DeepSeek；
- 测试必须使用 fake 并能在无网络容器中完成；
- 构建/测试命令、镜像 ID、退出码和测试统计写入报告；
- 构建失败不可改镜像源、依赖锁或 Docker 配置绕过，除非在白名单内且用户批准。

允许的命令形态示例仅用于提示词生成参考：

```text
docker --config <project-temp-docker-config> buildx build --load --progress plain --tag china-travel-agent:test --target test .
docker --config <project-temp-docker-config> run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true china-travel-agent:test <pytest-command>
docker --config <project-temp-docker-config> run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --env PYTHONPYCACHEPREFIX=/tmp/pycache china-travel-agent:test <compileall-command>
```

实际 Python/pytest 参数必须由当期阶段白名单和实时构建文件决定。

## 21. 独立代码审查

阶段 `09-5 + 09-6` Gate 通过后，由未参与实施的独立主审直接完成代码审查；代码审查禁止使用子 agent。主审逐项覆盖以下两个维度，不以测试全绿替代文档对照：

1. 架构/逻辑/跨模块审查：依赖方向、模型与控制器边界、状态机、预算、Artifact/replay、Module 08/10/11/12/13/16 边界；
2. 弱模型可执行性/测试审查：阶段白名单、算法顺序、批量地图、错误路径、并发取消、安全、覆盖率和验收是否足以阻止假实现。

主审必须完整读取两份 Module 09 文档、实际 diff、相关上游公共面和测试；审查过程只读，不修改文件。文档交叉审查可按单独获批的文档任务使用子 agent，不得据此委派代码审查。每条发现使用：

```text
严重度：P0/P1/P2/P3
位置：文件 + 行号/符号
违反：设计章节/决定 ID + 执行契约章节
证据：可复现事实
影响：为什么会破坏合同
最小修复：不扩大设计的建议
```

P0/P1 必须修复后由原审查者复核；P2 需修复或给出逐条证据并取得批准；P3 可记录但不能掩盖。修改设计或扩仓必须 STOP 询问。独立审查不能由更多测试替代。

## 22. 修复规则

- 只在最近获批阶段白名单内修复；
- 小范围明确缺陷可直接修复并运行受影响测试；
- 修复必须新增或收紧能复现问题的测试，除非纯文档/注释；
- 不得删除测试、降低断言、扩大容差、跳过分支或用 catch-all 掩盖；
- 需要改已确认设计、上游模块、依赖、migration、构建配置或进行大改时 STOP；
- 修复后先运行受影响测试，再运行对应联合 Gate；P0/P1 由同一原审查者复核。

## 23. PASS、STOP 与 NOT_RUN

### 23.1 阶段 PASS

只有同时满足以下条件才可 PASS：

- 前置和 Git 快照一致；
- diff 精确落在白名单；
- 本阶段目标与两份文档逐项匹配；
- 对应测试/Gate 全通过；
- 无未解释异常文件、staged、secret、依赖或网络访问；
- 报告完整且状态不夸大。

阶段 PASS 的最终状态只能是 `READY_FOR_INDEPENDENT_REVIEW`。未获授权的 commit/push/PR/deploy 必须分别报告 `NOT AUTHORIZED / NOT RUN`。

### 23.2 必须 STOP

遇到任一情况停止，不做猜测性修复：

- branch/HEAD/worktree 与批准快照漂移；
- 需要修改白名单外文件；
- 上游公共 API、Artifact type/version、预算或 profile 不满足 §4；
- 两份 Module 09 文档彼此或与更高权威决定冲突；
- 需要改变已确认的 分钟排程、六工具、已确认阶段额度、Directions 逐腿证据、节奏上限、备选或结构化输入范围，或重新启用延期功能；
- 需要引入天气、Citywalk/步行专属限制、饮食适配、路线价格、实时导航或数据库；
- 需要 migration、删除、真实外部调用、新依赖、push、PR 或 deploy；
- 无法证明 Artifact/replay 幂等、预算 all-or-none、取消收敛或 terminal write 结果；
- P0/P1 未修复，P2 未处置；
- 测试、coverage、compileall 或安全扫描失败。

### 23.3 NOT_RUN 写法

没有授权或阶段不适用时必须写明原因，例如：

```text
Migration：NOT_RUN — Module 09 首版明确使用进程内 Artifact，未授权数据库迁移。
真实外部调用：NOT_RUN — 验收使用离线 fake，禁止读取 secret 或联网。
Commit：NOT AUTHORIZED — 当期提示词未明确授权。
Push/PR/Deploy：NOT AUTHORIZED — 用户未明确授权。
```

不得用“无需”省略状态字段。

## 24. 实施阶段报告模板

每组阶段结束必须按以下字段报告，不能只报测试绿：

```text
Module：09
阶段：09-x + 09-y
最终状态：READY_FOR_INDEPENDENT_REVIEW | STOP

Git：
- 分支：
- 开始 HEAD：
- 开始 HEAD subject：
- 结束 HEAD：
- 结束 HEAD subject：
- HEAD 是否变化：
- staged：
- merge main：NOT AUTHORIZED / NOT_RUN / <获批结果>
- push：NOT AUTHORIZED / NOT_RUN / <获批结果>
- PR：NOT AUTHORIZED / NOT_RUN / <获批结果>

修改文件：
- 新增：
- 修改：
- 删除：
- 白名单外文件：NONE | <逐项>
- 异常文件：NONE | <逐项来源与处置>

设计对照：
- 权威设计章节/决定 ID：
- 执行契约章节：
- 已满足：
- 偏差/未决：NONE | <逐项>

迁移与依赖：
- Migration：NOT_RUN — <原因> | <获批结果>
- 新依赖：NONE | <逐项>
- 配置变更：NONE | <逐项>

测试：
- 镜像/构建命令与结果：
- 测试命令与结果：
- passed/failed/skipped/xfail：
- line coverage：
- branch coverage：
- combined coverage：
- missing lines：
- missing branches：
- compileall：
- 真实外部调用：NOT_RUN — <原因>

审查与异常：
- 自审：
- 独立审查：NOT_RUN — 尚未进入独立审查 | <结果>
- P0/P1/P2/P3：
- 已知风险：

提交：
- commit：NOT AUTHORIZED | <hash + subject>
- commit 内容：NONE | <文件>
- merge main：NOT AUTHORIZED
- push：NOT AUTHORIZED
- PR：NOT AUTHORIZED
- deploy：NOT AUTHORIZED

下一步：
- 等待独立审查 / STOP 等待用户决定 / 已获准进入下一组阶段
```

## 25. MODULE-CLOSEOUT

MODULE-CLOSEOUT 是独立代码审查通过并获得用户单独授权后的最终独立阶段，不与 09-6 合并，不用于修复生产缺陷。

### 25.1 前置与文件白名单

前置：§21 独立主审完成两个维度的审查与修复复核；P0/P1=0，P2 已修复或获批；Module 09 Gate 通过；用户明确授权 closeout。

白名单：

```text
README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile
compose.yaml
.env.example
```

只修改经实际影响核验确需同步的文件。若 `.dockerignore` 或其他使用说明确需修改，先报告并取得扩白名单授权。禁止在 closeout 修改 `app/`、`tests/`、两份 Module 09 设计文档或新增 migration。

### 25.2 内容

- README 同步 Module 09 真实能力、静态路线性质、离线测试方法和非目标；
- IMPLEMENTATION_STATUS 只写实际完成状态、证据和准确测试统计；
- Dockerfile/compose/.env.example 仅在实际运行入口或配置需要时同步；没有影响则保持不动；
- 不宣传天气、饮食适配、价格、实时导航、自动备选切换或数据库已实现；
- Module 09 只拥有规划提交前的确定性校验；不接管 Module 10 Artifact 持久化、Module 11 checkpoint／恢复或 Module 12 成稿职责，也不生成旅行费用估算。

### 25.3 同步后重建与回归

closeout 修改后必须从当前源码重新构建 test/runtime 镜像，并在无网络、只读、安全容器中运行：

1. Module 09 全部 unit/contract tests；
2. Module 01、02、04、06、07、08 受影响合同测试；
3. 全仓离线回归；
4. 以 `PYTHONPYCACHEPREFIX=/tmp/pycache` 执行 `python -m compileall -q app tests`；
5. Module 09 line、branch、combined coverage gate 分别 `>=70%`；
6. runtime 镜像 import/startup smoke，不读取 secret、不访问网络；
7. Git 白名单、staged、依赖、secret 和异常文件检查。

固定外层 PowerShell 命令为：

```powershell
$module09DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module09-closeout-docker"
$module09RuntimeImage = "china-travel-agent:module09-closeout-runtime"
$module09TestImage = "china-travel-agent:module09-closeout-test"
$module09RuntimeContainer = "china-travel-agent-module09-closeout-runtime"
$module09TestContainer = "china-travel-agent-module09-closeout-tests"
function Assert-Module09NativeSuccess { param([string]$Step) $code = $LASTEXITCODE; Write-Output "$Step exit_code=$code"; if ($code -ne 0) { throw "$Step failed with exit code $code" } }
docker --config $module09DockerConfig buildx build --load --progress plain --tag $module09RuntimeImage --target runtime --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
Assert-Module09NativeSuccess "closeout-build-runtime"
docker --config $module09DockerConfig buildx build --load --progress plain --tag $module09TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
Assert-Module09NativeSuccess "closeout-build-test"
docker --config $module09DockerConfig run --name $module09RuntimeContainer --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09RuntimeImage
Assert-Module09NativeSuccess "closeout-runtime-smoke"
docker --config $module09DockerConfig run --name $module09TestContainer --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/unit/itinerary_planning tests/contract/itinerary_planning
Assert-Module09NativeSuccess "closeout-module09-tests"
docker --config $module09DockerConfig run --name $module09TestContainer --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/unit tests/contract
Assert-Module09NativeSuccess "closeout-full-unit-contract"
docker --config $module09DockerConfig run --name $module09TestContainer --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python -m pytest -q -p no:cacheprovider tests/integration/baidu_map/test_m2_offline_vertical.py
Assert-Module09NativeSuccess "closeout-offline-vertical"
docker --config $module09DockerConfig run --name $module09TestContainer --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true --env PYTHONPYCACHEPREFIX=/tmp/pycache $module09TestImage python -m compileall -q app tests
Assert-Module09NativeSuccess "closeout-compileall"
docker --config $module09DockerConfig run --name $module09TestContainer --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module09TestImage python tests/support/run_module09_coverage.py
Assert-Module09NativeSuccess "closeout-coverage"
$module09Residual = @(docker --config $module09DockerConfig ps -a --filter "name=china-travel-agent-module09-closeout" --format "{{.ID}} {{.Names}} {{.Status}}")
Assert-Module09NativeSuccess "closeout-residual-query"
if ($module09Residual.Count -ne 0) { $module09Residual | Write-Output; throw "closeout residual container detected" }
```

runtime 默认 CMD 在 closeout 后必须诚实包含 Module 09 import；不得用另写的临时 `python -c` 绕过。全量 unit/contract collect 必须非零且不得出现 skip/xfail/rerun；离线 vertical 不得替换为 live vertical。最后一行输出必须为空，任一命令非零立即 STOP。

任一失败返回相应实施阶段修复，不能在 closeout 越界修生产代码。

### 25.4 closeout 报告

最终报告除 §24 全部字段外，至少包含：

- 分支、开始/结束 HEAD 及各自 HEAD subject；
- 所有修改/新增/删除文件和异常文件；
- migration 状态；
- test/runtime 镜像 ID；
- 每条测试命令、退出码、passed/failed/skipped/xfail；
- line/branch/combined coverage 和 missing lines/branches；
- §21 独立主审的两个审查维度及最终 P0/P1/P2/P3；
- README/状态页/构建配置的实际同步项；
- commit hash/subject/files 或 `NOT AUTHORIZED`；
- merge main、push、PR、deploy 状态；
- 最终状态 `MODULE09_IMPLEMENTATION_COMPLETE` 或 STOP。

### 25.5 closeout 执行合同

- 阶段地位：MODULE-CLOSEOUT 是 Module 09 最后一个、独立且不可与 09-6 合并的实施阶段；其后只允许经单独授权的独立提交/合并，不再有功能实施阶段。
- 允许操作：完整只读影响审计；只修改 §25.1 白名单中被真实影响的文件；从当前源码重建 test/runtime target；执行 §25.3 全部离线验收；生成完整报告。
- 禁止操作：修改 `app/**`、`tests/**`、Module 09 设计文档、上游冻结文档或未授权说明；新增依赖/migration；live API/secret；stage、commit、merge、push、PR、deploy。
- 强制设计对照：权威设计 §3～§4、§6.2、§15.1、§17～§23、D-09-59～D-09-68；本文 §20～§28；主设计/路线图中的 Module 10～14 边界。
- 高收益正测：test/runtime 镜像均从结束工作树重建；Module 09 全测、受影响上游合同、全仓离线回归、compileall、三项 coverage 与 runtime smoke 全部通过。
- 高收益反测：文档夸大天气/价格/饮食/实时导航/数据库、runtime 读取 secret/联网、残留容器、closeout 越界修代码、状态页测试数字与实际不符。
- Docker Gate：严格执行 §25.3；必须报告两个镜像 ID、安全参数、全部退出码和项目命名空间残留容器检查。
- PASS：独立审查清零、白名单同步准确、§25.3 全通过后才可报告 `MODULE09_IMPLEMENTATION_COMPLETE`；仍不包含 commit/push/PR/deploy 授权。
- STOP：发现生产缺陷、文档需改变设计、任何测试/coverage/runtime smoke 失败、需要扩白名单或出现异常 dirty/staged 文件；返回对应阶段或请求用户决定。
- 报告：同时满足 §24 与 §25.4，不得省略 migration、异常文件、commit、push、PR、deploy 和最终状态。

## 26. commit、merge、push、PR 与 deploy

- 默认全部未授权；
- 09-1～09-6 的实施/修复模型和 MODULE-CLOSEOUT 模型不得 stage 或 commit，即使测试通过也不例外；
- 只有独立审查者在用户单独明确授权的提交/合并阶段才能执行 commit 或 merge；授权必须冻结分支、HEAD、staged 白名单和 commit subject；
- 获准 commit 前必须展示 staged 精确文件；禁止夹带用户文件；
- 不得 amend、rebase、squash、merge 或 force 操作，除非用户单独授权；
- 未经用户明确允许，永远禁止 push、PR 和 deploy；
- 设计文档批准不等于实施、commit、push、PR 或 deploy 授权。

## 27. 后续实施提示词硬要求

每份提示词必须保留而不能过度精简：

- 当前阶段目标与不做什么；
- 必读权威设计、本文和共享执行规范；
- 冻结分支、前后 HEAD 和工作树前置；
- 当前相邻两阶段与精确文件白名单；
- 允许操作与禁止操作；
- 要实现的公共合同、算法顺序和相应设计章节/决定 ID；
- 完整测试矩阵、Docker 安全参数、coverage/compileall Gate；
- 小修允许范围与需要停止征询的设计/扩仓/大改条件；
- 独立代码审查要求；
- 实施/修复/closeout 的 commit 明确写为未授权；如生成独立提交/合并提示词，则写明用户授权、staged 白名单和精确操作；push/PR/deploy 默认禁止；
- §24 报告格式和唯一可报状态。

每份正式实施、修复、独立审查、MODULE-CLOSEOUT、提交或合并提示词的完整正文必须全部放在同一个 Markdown 代码框中；不得输出依赖旧提示词才能理解的补丁。不得重复粘贴设计文档大段内容，但必须给出精确章节、决定 ID、公共符号、边界和验收定位，使弱模型不靠猜测工作。

## 28. 最终自检

实施者、审查者和 closeout 执行者结束前逐项回答：

- 是否完整读取两份 Module 09 文档和共享规范？
- 是否只使用 01、02、04、06、07、08 公共 API？
- 是否让模型做语义规划、控制器做硬 Gate，而非退化成固定规则排程？
- 是否精确六工具、2/5/5/5、最后实际submit-only与阶段无进展规则？
- 是否批量 Matrix、多腿 Directions，且每个相邻主腿有匹配 Directions？
- 是否正确执行 分钟排程、跨午夜、抵离余量和 full/partial day，并拒绝延期的 time-sensitive 锚点、避免冒充定时活动完成？
- 是否正确执行餐次、must、UID 唯一、营业三态、备选和 3000 米 Matrix？
- 是否执行 120/180/240 总量与 60/90/120 单腿上限？
- 是否没有 Citywalk/步行专属限制、天气、饮食适配、价格和实时导航？
- 是否保留 longest consistent draft、typed gaps、supplement once 和同 run resume？
- 是否 lookup-first、预算 all-or-none、singleflight、有序并发、取消收敛和 future-DB-safe version chain？
- 是否 Artifact/日志无 Prompt、CoT、完整对话、provider body、secret、异常字符串和 RouteStep？
- 是否逐条对照文档而不只看测试？
- 是否 diff 精确在白名单、staged 和异常文件已报告？
- 是否未越权 commit、push、PR、deploy 或 migration？
- 最终报告是否包含分支、前后 HEAD、修改文件、迁移、测试、异常文件、commit、push、PR 和最终状态？

任一答案不是可证明的“是”，不得宣布阶段完成。


## 2026-09-28 备用点可选验收修订

对照 D §9.6、§16、§20 和 D-09-69：无/部分备选的完整主规划应 Completed；提供非法备选仍拒绝，主规划非法仍 Incomplete。保留前缀不自动产生备用缺口，备用专用补研不得准入。本次版本按设计§20.1：planning-v5/ModelAction5.0/itinerary2.0，同步精确消费方；旧范围不恢复，不重写历史。新增无备选、部分备选、非法已有备选、主行程缺口、重放隔离及 M12 三日读写矩阵。本文旧测试若依赖缺备选制造 Incomplete，改用真实主行程缺口继续验证原目的，不删除或跳过原安全门槛。

## 公交乘车步骤增量执行规范（2026-09-28）

用户已批准独立分支实现静态公交乘车顺序。以同模块设计文档末节“公交乘车步骤扩展”和 [实施记录](VALIDATION.md) 为本增量权威范围；旧文禁止保存/展示全部instruction的要求仅对非公交步骤及未批准字段继续生效。持久模型继续全部必填、strict/frozen/extra-forbid。必须核验旧数据兼容、选中方案绑定、步骤原序、缓存与落库后读取、危险文本转义及工具结果不含步骤。不新增模型调用，不自动提交、合并或发布。
## 2026-09-29 四项改进增量修订（用户已批准）

以 [M09四项改进设计及验收](VALIDATION.md) 为本次增量依据：M09供应商工具使用beta严格schema，原本地完整校验保留；非法工具调用整批拒绝；Directions给出代码推导时长，路线end_at可空并由代码计算（显式旧值须一致）；Prompt减少格式/计算重复；提交反馈包含稳定reason/field。仅修改模型输入端，不改变历史持久行程schema、预算、硬门槛或数据库。本文此前涉及统一非严格工具输出、路线end_at必填和泛化反馈的条款由本节限定覆盖，其余条款继续有效。实施范围、前置条件、测试矩阵及Git限制见上述文档。

2026-09-29修复重跑补充：依据用户“修复问题然后重跑，有三次重跑机会”授权，strict wire完全展开无递归本地引用；被供应商限制移除的数量/长度及日期格式保留为说明，本地验证不变。M09终结提交拒绝在返回通用状态前记录代码内reason/field与terminal_only，不输出参数或异常正文，不改公开错误/持久化合同。三次真实结果、白名单及验收详见[m09_four_improvements.md](VALIDATION.md)；最后业务提交仍未通过，不据此扩大重跑或预算。

2026-09-29轻量错误定位增量：按用户授权实现[错误定位日志](error_location_logging.md)。在JSON解析、本地工具参数校验和业务拒绝转换为通用结果之前记录脱敏字段/规则/源码位置；最后一次提交同样记录，真实测试单独保存error-locations.jsonl。仅复用日志与run_id，不增加数据库表、迁移、错误结果字段、回放系统或调用额度。详见该文档的范围、测试矩阵及限制。


## 29. 阶段预算与完整记录的本次实施（2026-09-30）

本节用于本次实际代码增减；用户已批准设计并授权 W00～W03、两个新的 GPT-6.1-sol/max 只读代码审查及最多 5 次真实 API 尝试，成功即停。当前无 commit、merge、push、PR 或部署授权；现场执行与验收状态见本次报告。引用原模块设计：M09 §12～15、20、25，M02 §19，M10 §23，以及本节执行边界。原09-1～09-6是模块初建流程，本次维护只执行W00～W03，不重跑初建阶段或复建已有模块。文档审查/批准状态见 [报告](VALIDATION.md)。

### 29.1. 开工条件与分支

1. 用户批准本次设计和本规范，并明确授权当次阶段后才能实施；纯文档写作授权不能当成代码授权。
2. 每次提示词现场核对分支、前后 HEAD/subject、staged、dirty、目标文件/符号和真实测试入口。使用当前已授权功能分支，分支/HEAD放当次提示词及报告，不固化到设计。
3. 当次提示词固定分支：同一未合并功能继续时复用其已授权feature分支；独立新功能按通用规则从最新本地main创建feature/<name>。本设计不虚构用户已批准分支例外。先记录dirty清单/hash并保留既有改动，不reset/clean/stash/回滚，不因工作区不干净删除公交/错误定位代码。
4. 允许在阶段白名单内精简旧六轮/额外终态逻辑和旧版本测试；不建旧结果兼容层。改已确认设计、扩大目录或明显大改必须先征求用户同意。
5. 无本次 commit 授权。push/PR/deploy 禁止；不得创建下一阶段提示词来自动扩大授权。

### 29.2. 必读真源

| 用途 | 精确章节 |
|---|---|
| 本次业务与接口 | 09_itinerary_planning_loop.md §12～15、20、25；02_llm_runtime_and_model_profiles.md §19；10_artifact_persistence.md §23；AC-01～12。。 |
| 规划硬规则 | 09_itinerary_planning_loop.md §6～11、14～20、附录A～D；阶段/预算按§12～15，版本按§20.1。。 |
| 模型边界 | 02_llm_runtime_and_model_profiles.md §5～11及本次增量；consumer不读私有history、不自造重试。 |
| 预算/Artifact | 04_workflow_state_and_artifacts.md §8～13、17～21；M09设计§12.2/20.1；M10设计§7～11及诊断增量。 |
| 数据库/恢复 | 10_artifact_persistence.md §8～11；11_durable_execution_and_recovery.md §7～13、22及本次增量。 |
| 消费端 | 03_requirement_extraction_adapter.md §5/8～12；08_candidate_research_loop.md §13/17～19；12_result_formatting.md §4～8；13_demo_workflow.md §4～11及本次增量。 |
| 通用执行/证据 | SHARED_IMPLEMENTATION_RULES.md §1～8；IMPLEMENTATION_ROADMAP.md §2；IMPLEMENTATION_STATUS.md；本规范§29.3～29.7。 |

依赖直接读取真实代码与公共包根，不猜接口。新文件必须标“允许新增”，不能作为尚不存在的必读引用。所有新公共导出、DTO/Ref/版本、准入顺序必须在实施报告中逐项对照真源；测试全绿不能代替合同审查。

### 29.3. 阶段与文件白名单

W00 是只读预检，不是另一次代码实施。W01 涉及迁移单独执行；普通代码 W02+W03 原则上一次授权两个相邻阶段，全部完成后统一执行相应测试。不能为凑两个阶段跳过数据库 Gate。

#### W00：只读预检

目标：确认设计已批准、当前dirty归属、M02字节/语义claim与M09预算消费接缝、M10迁移head/池生命周期、M12/M13旧版本绑定及真实测试命令。输出差异映射和拟改文件；未知阻断不得凭空实现。不得改文件/数据库或调用API。

#### W01：诊断 DTO、专用表和存储

目标：先完成不会执行模型的记录类型/端口、m10_0002、M10追加/读取/容量/短等待及本地兜底，M02 no-op/in-memory基础。

生产白名单：

- app/llm_runtime/interaction_recording.py（允许新增：固定DTO、端口和上下文，不做HTTP/数据库/文件I/O）；app/llm_runtime/__init__.py。
- app/artifact_persistence/interaction_records.py（允许新增）；app/artifact_persistence/runtime.py、__init__.py。
- app/artifact_persistence/migrations/versions/m10_0002_interaction_records.py（允许新增）；不改m10_0001历史迁移。

测试白名单：tests/unit/llm_runtime/test_interaction_recording.py、tests/unit/artifact_persistence/test_interaction_records.py、tests/integration/artifact_persistence/test_interaction_records.py（均允许新增）；现有tests/unit/artifact_persistence/test_runtime.py、tests/integration/artifact_persistence/test_migrations.py和conftest.py、tests/contract/artifact_persistence/test_public_api.py和test_import_boundaries.py、tests/integration/artifact_persistence/test_security.py，仅对应新head/公共端口。

必须落实M02设计§19和M10设计§23：一张专用表、相同ID同内容可重放、异内容冲突、没有UPDATE/DELETE；诊断依赖/容量错误不改业务结果；borrowed pool不另建或关闭；2秒整体写等待；取消不是日志错误。新诊断DTO供M10只读导入，M02不反向导入M10。

W01 PASS：AC-09/10的数据库/存储部分通过，且m10_0001→m10_0002真实PostgreSQL迁移、业务记录未改、新表重启读取、诊断容量独立和幂等冲突可证明。尚未采集真实模型，不能报告完整记录功能完成。

#### W02：阶段循环与版本接线

目标：实现M09设计§12～15/20.1，复用完整Gate；去掉旧六轮/额外终态分支，同步M02/M13预算和M12版本。

生产白名单：

- app/llm_runtime/runtime.py、profiles.py、protocols.py、models.py、__init__.py（只增加实际工具子集、单次准入回调、安全关联及规划17；既有错误/其他角色不改）。
- app/itinerary_planning/models.py、artifacts.py、service.py、tooling.py、protocols.py、fingerprints.py、prompting.py、prompt_loader.py、__init__.py；app/itinerary_planning/prompts/planning_v5.txt（允许新增）。旧Prompt只读。
- app/demo_workflow/plans.py、nodes.py、graph.py、models.py、__init__.py、artifacts.py、publication.py（预算/slots/contract及control2.0的Ref校验、M12请求版本接线）。
- app/result_formatting/artifacts.py、models.py、projection.py、service.py、__init__.py（新版来源/结果；app/result_formatting/renderer.py仅允许必要版本引用，不重写展示）。

测试白名单：tests/unit/{llm_runtime,itinerary_planning,demo_workflow,result_formatting}/ 下既有test_*.py、fakes.py、conftest.py；tests/contract/{llm_runtime,itinerary_planning,demo_workflow,result_formatting}/ 下既有test_*.py；允许新增tests/unit/itinerary_planning/test_phase_budget.py、tests/contract/itinerary_planning/test_phase_budget_contract.py、tests/unit/llm_runtime/test_effective_tools.py。tests/integration/{demo_workflow,result_formatting}/ 下既有test_*.py、conftest.py和restart_worker.py可同步新版本，不修改地图业务规则。tests/support/demo_workflow_worker.py仅允许同步M12读取请求2.0及对应版本断言，保留新进程、零模型/地图调用和结果一致性验证；tests/support/itinerary_planning_fakes.py、tests/support/demo_workflow_fakes.py仅允许同步预算key/17、新版DTO/Ref及M02工具子集/准入callback，不能跳过真实Gate。

固定顺序：

1. 定义新版预算/阶段snapshot、slot/Ref与导出，删除当前运行旧key，不改变M04预算Protocol。
2. M02先完整byte/quota预检，再一次准入回调/semantic claim，再provider最多两尝试；continuation只替换当前tool definitions，保留供应商要求的原始历史。
3. M09按阶段生成工具子集；计数从真实准入开始，结果唯一归类，业务终态也计；技术失败按发起阶段计。
4. 准备成功立即切交通；提前submit计修正；交通耗尽进入首次提交；修正查询留一次；最后出站与本地均仅submit。
5. 复用_submit校验与typed反馈；落实相同拒绝业务指纹/相关证据的双次停止，不能用“有模型文本变化”当进展。
6. supplement独立计数；M13/M08额度另计；恢复保留阶段、额度、缓存和私有Operation，检查两个计划与前驱绑定。
7. 同步Artifact/Prompt/指纹/M12/M13新版本，拒绝旧测试结果/运行，无自动转换；当前Directions事实1.0/1.1保留。

W02 PASS：AC-01～06/12的离线部分可在W03完成后统一验证。单阶段若被中断，记录未执行Gate；不得声称因为尚未W03而主链已完成。

#### W03：完整采集、消费端关联和导出

目标：实现M02设计§19和M10设计§23，覆盖完整Demo并能按run_id查阅实际Token来源。

生产白名单：

- app/llm_runtime/provider.py、runtime.py、protocols.py、interaction_recording.py、__init__.py；diagnostics.py仅安全错误关联，不写正文到旧logger。
- app/requirement_extraction/session.py、adapter.py（extract/repair用途标记，既有领域及修复语义不改）。
- app/candidate_research/service.py（action/judgment用途与工具结果关联，既有研究预算/资格不改）。
- app/itinerary_planning/service.py、protocols.py（关联模型call与反馈/typedObservation）；不得改map adapter。
- app/demo_workflow/diagnostics.py（允许新增）；graph.py、nodes.py、models.py、__init__.py；生产装配注入共享recorder/run上下文。
- scripts/export_interactions.py（允许新增：只读导出）；M10 interaction_records.py仅导出读取接缝。

测试白名单：tests/unit/{llm_runtime,requirement_extraction,candidate_research,itinerary_planning,demo_workflow}/ 下既有test_*.py、fakes.py、conftest.py；tests/contract/{llm_runtime,requirement_extraction,candidate_research,itinerary_planning,demo_workflow}/ 下既有test_*.py；tests/integration/{artifact_persistence,demo_workflow,result_formatting}/ 下既有test_*.py、conftest.py和restart_worker.py；允许新增tests/unit/llm_runtime/test_raw_interaction_capture.py、tests/integration/demo_workflow/test_interaction_recording.py、tests/unit/demo_workflow/test_interaction_export.py；tests/live/travel_pipeline/test_live_requirements_to_result.py仅允许启用生产记录/归档，同步17总账、新invocation slots/预算/DTO/Ref/M12请求2.0并移除旧terminal_action构造；保持测试题、调用次数及重试限制，不在本次文档或离线Gate执行真实API。

固定顺序：HTTP钩子捕获实际body→保存每attempt→SDK/Runtime仍严格解码→补处理结果→控制器关联工具/提交反馈→终态摘要→只读导出。capture异常用专用失败标志/本地兜底，不改SDK原异常；不依赖事后DTO找回已丢原文。没有根run上下文的独立M02探针允许no-op，不伪造run_id；正式Demo与live harness必须显式注入recorder，缺失注入报告diagnostics不完整。

W03 PASS：AC-07～12全部离线/真实PG检查通过；具备完整的请求、响应、处理结果/工具关联和可对账导出。真实API未执行时标NOT_RUN，不借历史five4报告新代码通过。

### 29.4. 统一测试矩阵

| Gate | 最小有意义覆盖 | 执行时机 |
|---|---|---|
| G-DB | 增量迁移/新head、旧业务表未改、同ID幂等/冲突、重启读取、容量独立、超时/不可用/兜底/取消。真实PostgreSQL。 | W01独立；W02+03后按新改变定向回归。 |
| G-PHASE | early hub success、准备2失败、交通提前submit/5耗尽、修正第5实际schema仅submit、混合batch失败计一次、最多17、网络重试不重复claim、byte/quota失败零外调、补研不重置。 | W02+03完成后统一。 |
| G-REPAIR | 两次完全重复拒绝停止；错误位置变化/业务字段修改/被采用新证据可继续；纯主题/无关查询不算进展；查询+resubmit各计一次。 | 同上。 |
| G-CAPTURE | HTTP fixture真实raw body保持、SDK解析失败/length/未知usage/2attempt无响应；M03两用途/M08两用途/M09工具与校验关联；secret sentinel不落库/文件/repr/日志。 | 同上，不访问互联网。 |
| G-EXPORT | PG/本地合并同ID去重、冲突可见、summary缺失标志、usage正确/思考不双计、完全重复标记不伪称精确片段Token。 | 同上。 |
| G-DEMO | 真实M01～M13和PG；M02/SDK用受控HTTP fixture，地图用typed fake；完整多日主链、候选补充、准备提前结束、提交修正、发布新版结果和公交线路/换乘步骤、需求/酒店安全暂停、模型节点未知结果失败关闭。 | W02+03完成且前Gate通过。 |

先新增定向测试，再运行改动包相关unit/contract和真实PGintegration；M11按预算/journal/恢复受影响部分回归，不为测试数量扩大修复。Docker运行必须使用包含当前源码的独立本项目镜像/资源、断网测试或只允许隔离PG网络；不能在宿主机跑Python或以collection替代执行。测试路径和命令在当次预检验证后写入提示词，不虚构当前目录不存在的入口。

代码覆盖率line/branch/combined分别报告，M09沿用70%门槛，其他改动包沿各自已有门槛；新增纯DTO不靠镜像实现的测试凑数。已有外部资格不自动废弃或自动重复调用；确需重资格时单独申请具体场景/上限。

### 29.5. 真实资格与收尾

真实API只在用户另行授权后执行，当次提示词写明场景、模块起点、最大尝试次数和成功停止规则。必须启用生产诊断，输出run_id/每次调用usage/失败原因/攻略及文件位置；真实失败先凭完整记录定位，不额外发模型查询“分析原因”。

完成代码后同步IMPLEMENTATION_STATUS.md与本次审查报告；必要实际验证记录写13_live_pipeline_acceptance.md。未执行事项显式NOT_RUN。只针对实际代码增减生成实施提示词，文档修订和审查由主协调者直接完成。

### 29.6. STOP与审查策略

- 超出白名单、改变已确认阶段/预算/硬规则、引入第二条推理路径、新数据库/日志平台/通用记忆/跨进程模型恢复：STOP，报告具体冲突及最小范围。
- 迁移必须改旧业务表、删除日常库/卷/测试证据或回填旧状态才能运行：STOP，不自行清理。
- 总账与分类冲突、旧版本静默兼容、预算重复消费、最后仍可查询、凭证泄露、诊断故障掩盖业务失败：阻断。
- 测试前置/实际执行失败、不明确当前源码/镜像、0 collected、coverage失败或必须放宽完整Gate才通过：FAIL，不自动重跑凑绿。
- 仅P0/P1及高概率影响演示的P2阻断；其余P2/P3记录统一延期清单并给重新处理条件，不为理论完备反复改动。

两个只读子Agent分别审查架构/上下游冲突与弱模型可执行性；主协调者合并意见修文档，两个Agent第二轮复核，主协调者最终总审。子Agent不改文件、不实施、不运行外部API。审查逐条映射设计AC与代码/证据，不只看pytest。

### 29.7. 实施报告最低字段

任务及授权阶段；分支；前后HEAD/subject；修改文件与新增文件；与预检dirty的区分；新迁移及实际目标/revision；设计§/AC逐项实现及验证证据；测试命令/结果/line/branch/combined；异常文件与缺失记录；真实API逻辑/物理/Token及NOT_RUN；延期问题；commit/push/PR/deploy；最终状态READY_FOR_INDEPENDENT_REVIEW、FAIL或STOP。

引用设计和执行规范的精确章节/阶段/AC；不要再粘贴本文件已写明的大段内容。每份实施提示词保留目标、前置、现场分支、白名单、允许操作、测试、停止条件和报告栏目，栏目可用精确引用；动态现场事实与新增授权才展开。

白名单中的 {a,b}/ 表示仅列出的目录集合，文件模式只允许已存在匹配项；列出的新增路径必须明确标允许新增。tests/contract/workflow_contracts/test_import_boundaries.py仅在新增M10→M02依赖需要同步时允许；M11定向回归只读运行，不列为修复白名单。Dockerfile/Compose/依赖锁/README不在本次白名单，新依赖需求先STOP。M02诊断记录关联scope/公共端口按M02§19.4～19.5落实，不能由消费方访问私有history。

## 30. 通用参数修复实施入口（2026-10-06，用户已批准）

仅实施 M02 §20、M09 §26 的批准增量；旧§29的阶段预算与版本按该增量替代。完整实施提示词如下，禁止 stage/commit，完成后等待用户授权提交。

```text
任务：M09-GENERIC-ARGUMENT-RECOVERY；始终中文。
目标：完成通用工具参数拒绝与最多两次格式修正；普通17 + 同run共享格式2，总19；旧记录可读；原严格schema/业务/地图门槛保留。
分支：feature/module-09-structured-output-fix；开始HEAD=b3438ca13e3c88e4669c942d18018e6b133b7857。沿现checkout；预检dirty仅仓库外既有../m13-live-evidence/，不得读取正文、stage、修改或删除。
前置：先读00§1/4，SHARED_IMPLEMENTATION_RULES§1/3/4/5/6/7/9，IMPLEMENTATION_STATUS现状态；设计 M02§8/10/11/19/20 和 E02§11/13/15/21及本次入口；设计 M09§12～21/B/26 与 E09§29/30。
依赖真源：IMPLEMENTATION_ROADMAP§2。M02 depends_on 无；M09全部直接依赖：M01=01_requirement_and_dialogue.md§6/11（已确认需求）；M02上述章节；M04=04_workflow_state_and_artifacts.md§8～13/21（Artifact/registry/hash/预算）；M06=06_baidu_map_adapter_and_mcp_gateway.md§7/11/12/20/22（typed地图/重试/执行边界）；M07=07_hotel_candidate_selection.md§8～11/21（选择Ref/预算）；M08=08_candidate_research_loop.md§13/18/19/25（补研/Artifact关联）。逐个核验引用，不能仅按编号大小猜依赖。
M13依赖01、03、04、05、06、07、08、09、10、11、12：本轮消费边界读取以上各合同及03_requirement_extraction_adapter.md§4/5/9/10（公共Session与错误映射）、05_requirement_subgraph.md§4.1/6/8（interrupt/resume）、10_artifact_persistence.md§6/10/11/23、11_durable_execution_and_recovery.md§5.4/5.5/5.6/10/11/12（合同、codec、预算与未知执行结果）、12_result_formatting.md§3/6/7及本次消费增量；M13设计§4.3/6/8/9/11及“M09共享格式储备装配增量”，E13末节“M09参数修复装配执行入口”。M14 depends_on13，读取M13上述和M14§6/9及“M09参数修复展示维护”、E14末节“M09参数修复展示执行入口”。不得按章节标题近似猜依赖。

生产白名单（仅必要增量，禁止新增依赖或通用平台）：
- app/llm_runtime/models.py、protocols.py、provider.py、runtime.py、diagnostics.py、interaction_recording.py、profiles.py、__init__.py。
- app/itinerary_planning/models.py、artifacts.py、fingerprints.py、prompt_loader.py、prompting.py、protocols.py、service.py；允许新增app/itinerary_planning/prompts/planning_v6.txt，旧prompt资源不改。
- app/demo_workflow/plans.py、nodes.py、artifacts.py、graph.py、commands.py；app/result_formatting/service.py、projection.py、artifacts.py：仅新旧M09版本读取/装配接缝及新运行合同/codec revision隔离，历史预算行不改。
- app/web_application/static/app.js：仅失败文案。
测试白名单：tests/unit/{llm_runtime,itinerary_planning,demo_workflow,result_formatting,web_application}/下既有test_*.py、fakes.py、conftest.py；tests/contract/{llm_runtime,itinerary_planning,demo_workflow,result_formatting,web_application}/下既有test_*.py；tests/integration/{artifact_persistence,durable_execution,demo_workflow,result_formatting,web_application}/下既有test_*.py、conftest.py、restart_worker.py；tests/browser/web_application/test_demo_pages.py；tests/support/itinerary_planning_fakes.py、demo_workflow_fakes.py、demo_workflow_worker.py、module14_browser_worker.py。允许新增tests/unit/llm_runtime/test_tool_argument_recovery.py、tests/unit/itinerary_planning/test_tool_argument_recovery.py、tests/contract/itinerary_planning/test_recovery_version_compatibility.py。现有与17预算/旧版本直接相关fixture同步，但保留专门旧版本读取fixture；不删/skip/xfail测试。
文档白名单：docs/02_llm_runtime_and_model_profiles.md及_vibe_coding.md、09_itinerary_planning_loop.md及_vibe_coding.md、12_result_formatting.md及_vibe_coding.md、13_demo_workflow.md及_vibe_coding.md、14_web_application.md及_vibe_coding.md；仅追加批准增量，M14冻结段不改；closeout允许README.md、docs/IMPLEMENTATION_STATUS.md，Dockerfile只读核验（确有必要修改先STOP）。其余规则/路线图/设计正文不改。
允许：以上白名单内读写、现有Docker镜像/隔离资源、定向和完整离线测试、独立PG/browser验证、外部证据归档；所有Python仅在Docker。禁止真实API新调用、旧服务/卷删除或重启、宿主Python、stage/commit/merge/push/PR/部署。

R01+R02（相邻两阶段完成统一测试）：
R01：按M02§20实现原始完整批次、先协议后参数、整批拒绝/安全issues、显式sync/async repair、私有pending互斥、close/cancel、真实SDK wire保留和诊断分类。
R02：按M09§26实现opt-in编排、normal/format账本、阶段耗尽时pending优先、19slots/38attempts、新版本/Prompt/fingerprint和旧byte/hash读取；M13新预算/装配，M12双版本消费。每repair独立一次语义调用，不在M02隐藏循环。
统一测试矩阵：两toy合同、六参数族；bad→good、bad→bad→good、bad3止；换ID/字段不重置；全批mixed拒绝/后置unknown/dup优先；normal耗尽/pending储备/normal禁止占储备；byte/quota零外调；schema成功再业务Gate；补研/resume不重领；网络2attempt一次消费；raw SDK实际wire/order/reasoning；诊断非fatal/usage完整性；sync/async/defaultconsumer；cancel/close/并发；新19与旧17闭合DTO/Ref/bytes/hash。
R01+R02通过：Docker中定向unit+contract及M02完整Gate（python -m pytest -q tests/unit/llm_runtime tests/contract/llm_runtime --cov=app.llm_runtime --cov-branch --cov-report=term-missing --cov-fail-under=95）；M09 run_module09_coverage.py三项各≥70；受影响M12/M13 unit/contract。随后原两子agent各按R-02-01～06/R-09-01～09逐条独立核查，有阻断在同白名单修复并重跑受影响项。

R03+R04（相邻两阶段完成统一测试）：
R03：M14中性失败文案；离线整链通过后执行隔离真实PG的主图/持久读取/补研/失败/恢复，browser完整Demo网页Gate；不把受控HTTP fixture说成真实供应商资格。
R04 MODULE-CLOSEOUT：README/IMPLEMENTATION_STATUS只同步实际事实；逐项核查Dockerfile COPY app/scripts、runtime默认CMD、test/browser target，保存具体证据。从最终源码重新构建runtime/test/browser适用镜像；运行默认CMD、M02/M09 owner Gate、M12/M13/M14受影响Gate及完整离线回归。旧兼容必须独立验证；没有SQL迁移时明确无迁移和理由，不触碰历史行。
R03+R04通过后：两原子agent按全部批准AC+closeout逐条审查；完成等待提交。真实API=NOT_RUN（本轮未分配新预算），最终不得宣称供应商可靠率或外部资格已重新通过。
STOP：超白名单/必要新依赖/改变其他已确认设计；删nullable必填/本地补值；普通占储备/账本不等/私有history重建；旧bytes/hash漂移/新字段静默回填；原有库或卷删除；任何测试零收集/非零/覆盖率未达；secret/raw泄漏。小范围明确问题直接在本阶段白名单修复，不能以“等用户确认”拖延已批准工作。
报告至少：阶段和授权、分支、前后HEAD、修改/新增文件、迁移/revision、设计AC逐条证据、定向/模块/全量/coverage/PG/browser/默认CMD结果、异常文件、真实API NOT_RUN与旧诊断区分、secret检查、staged/commit/push/PR/deploy均状态、最终READY_FOR_COMMIT（等待提交）或FAIL/STOP。
```

## 31. M09 单次生成额度维护（2026-10-08，用户已批准）

```text
任务：M09-OUTPUT-128K；始终中文；模式 maintenance，qualification_scope=offline。
目标：按 D02 §6 与 D09 §27 的 O-09-01～07，将 M09 单次生成静态额度从48K改为128K，并完成离线验收；不重复 D09 已冻结的算法。执行规范引用 E02“M09生成额度维护入口”、本文§31及 SHARED_IMPLEMENTATION_RULES §3～7；用户授权优先。
分支：feature/module-09-structured-output-fix；前置HEAD=b3438ca13e3c88e4669c942d18018e6b133b7857；复用当前checkout，保留全部既有dirty和4份已暂存文件。修改前核验HEAD/逻辑索引/白名单外内容摘要与既有Docker身份，真实.env及诊断正文不读取或输出。
前置：确认当前Profile/SDK统一使用静态 max_tokens；读 D02 §6/20、D09 §26/27、E02维护规则及本文§31。原运行服务、数据库和其他项目资源只读。所有Python仅在Docker，离线运行不挂载真实.env、不开放端口、不访问模型/地图。
生产白名单：app/llm_runtime/profiles.py，仅固定ITINERARY_PLANNING_PROFILE.max_output_tokens。
测试白名单：tests/unit/llm_runtime/test_models_and_profiles.py、test_provider_adapter.py、test_runtime_contract_edges.py；tests/contract/itinerary_planning/test_upstream_integration.py，仅更新既有受影响断言，不增加镜像实现的测试、不删skip/xfail。
文档白名单：docs/02_llm_runtime_and_model_profiles.md及_vibe_coding.md、docs/09_itinerary_planning_loop.md及_vibe_coding.md；MODULE-CLOSEOUT仅README.md、docs/IMPLEMENTATION_STATUS.md。本次共11份文件，历史诊断文档只读；Dockerfile/.dockerignore/锁文件只读核验。
允许操作：白名单内小范围明确修改、现有Docker缓存镜像内离线测试、从当前源码构建本项目命名空间的runtime/test镜像、只清理本次确认身份的无端口容器、两次独立只读子agent代码审查；不新增诊断文档。
禁止：改变任何其他生产设置或算法、迁移或改运行库、真实API、新任务/恢复/重试、启动或重启现有网页、宿主Python、stage/commit/merge/push/PR/部署。
C01：同步获批Profile/设计/消费方执行规范及4处现有测试断言；SDK首调/续调/修正共用128K，其他值逐条核对 O-09-02～05。
C02 MODULE-CLOSEOUT：C01完成后统一运行定向与owner Gate；根据实收同步README/状态快照，然后从同步后的源码重建runtime/test并运行默认CMD、两个owner Gate和完整unit/contract；两子agent按O-09-01～07分别审查，发现白名单内明确问题直接修复并重跑受影响门禁。
测试矩阵：
- 定向：Docker内pytest -q -p no:cacheprovider，四份白名单测试；验证四角色32,768/131,072/131,072/32,768、默认16,384、SDK enabled/high/max_tokens/retries=0、同步和异步续调完整字节门以及超限零provider。
- M02：Docker内pytest -q -p no:cacheprovider tests/unit/llm_runtime tests/contract/llm_runtime --cov=app.llm_runtime --cov-branch --cov-report=term-missing --cov-fail-under=95；coverage仅写容器/tmp。
- M09：Docker内python tests/support/run_module09_coverage.py；原line/branch/combined各≥70%，保留业务/参数修正和回放合同。
- 完整离线：新test镜像默认CMD的unit/contract；原需要持久化的合同若适用，独立临时PG按既有迁移初始化，禁止读写现有运行库；无skip/xfail/自动rerun。
- Docker：核验COPY app/scripts、test COPY tests、runtime默认python -m app.web_application；无网络/凭据/端口时缺配置须安全exit=2，另做生产Profile import自检，不将其称作可用网页启动资格。
- 本次无数据库/网页行为变更，不重复PG/browser资格；正式live和现有18080更新NOT_RUN。按E02既有维护规则供应商资格待正式probe，不把离线结果宣称为live通过。
停止条件：需扩大白名单或改变其他已确认设计；字节/次数/思考/超时/strict漂移；索引/原有dirty/运行资源改变；凭证或原始正文泄漏；测试零收集/失败/覆盖率未达；遇到这些情况保留已验证结果并报告，不放宽门槛。
报告：直接在聊天列分支、前后HEAD、11份增量文件、迁移、定向/M02/M09/全量/覆盖率/Docker实收、逐条设计核查和双审、白名单外异常、索引/dirty保留、真实API与网页更新NOT_RUN、commit/push/PR/部署状态；实现和离线验收可通过，供应商资格按原M02规则写task_result=FAIL/live_qualified=not_run（未分配本次真实调用），不能冒称完整供应商资格。
```
## 2026-10-11 完整用途与纠错实施契约（U-B1＋U-B2＋CLOSEOUT）

使用 [M09 §28 U09-01～07](09_itinerary_planning_loop.md)、[M08用途增量](08_candidate_research_loop.md)、[A1/A2执行契约及完整白名单](08_candidate_research_loop_vibe_coding.md)、[M13接线增量](13_demo_workflow.md)、[共享规则§7](SHARED_IMPLEMENTATION_RULES.md)。M09直接依赖01/02/04/06/07/08的当前公共合同、M12读取合同与M13装配合同须核对，不复制规则。前置分支与HEAD、允许操作、STOP和报告格式继承A契约；当前用户授权覆盖本增量，后续修复不commit。

B1目标：Matrix/Directions明确用途意图的整批预检、共享准入与定位反馈；B2目标：稳定问题目标、两次无目标进展、同证据循环、结构化局部补丁及路线复用。相邻完成后统一测试。生产白名单继承A，并增加 app/itinerary_planning/tooling.py、fingerprints.py、handles.py、map_execution.py（仅资格前置/缓存绑定接缝，供应商并发不变）；测试白名单继承A。`visit_intents`与`patch`为本次新DTO，旧记录按旧DTO读，新的新工具合同写model_action7.0。

顺序：先收集全部可独立资格问题，再整批拒绝且零地图；执行模型调用如实计账；合法查询返回后完整时序/事实Gate不变。定位目标不用visit_id；第一次允许修正，同问题移动/交换不算，删除必需项仍有missing目标，合法换点出现新路线gap继续。保存相关证据指纹，补研不清空；补丁只应用模型明确列项，不自行删点，随后完整校验；新规划Prompt/hash与版本接线一起完成。

必要回归还包括：真实submit主循环中的午晚餐错误轮转；同must组及营业义务换餐次、营业未验证/冲突轮转、只改unknown原因码；相同首工具但不同后续查询的全批指纹；Matrix省略区间而Directions补充区间的合法同次访问，以及区间冲突、无证据声明和同工具重复反例。M13无草案incomplete通过真实terminal/4.0 Ref收束，不能用旧3.0夹具替代新运行。

统一测试矩阵：U09-02～07及U08回归；餐次/角色/组合/备选/avoid/must/重复无效批次地图/预算0；多个独立错误一次定位；schema格式储备与业务额度分开；换ID/交换/移动/删必需项不进展；合法换点新路线不误停；局部补丁完整校验、未改日期/路线复用；补研、resume、回放不重复扣额；新/旧版本字节/hash；M12来源和M13终态保持。定向后M08/M09完整coverage Gate、受影响M12/M13/M14合同，失败保留并在白名单修复；两个只读子agent分别逐条合同与执行核查。

MODULE-CLOSEOUT：仅在阶段测试/双审通过后同步README、IMPLEMENTATION_STATUS的实收与限制；Dockerfile逐项核对COPY app/scripts、test COPY tests、runtime默认CMD及静态UI保留。同步后重建runtime/test适用镜像，默认无凭据CMD安全exit2，执行完整离线回归、M08/M09覆盖率、真实隔离PG主链/持久与旧读取Gate；浏览器沿M14适用Gate验证已合并UI不漂移。性能只声称离线可证明的调用/无效请求减少；真实供应商速度与token测量NOT_RUN，不能预填百分比。无SQL表变更明确无迁移；历史安全恢复限制保留。报告字段完整沿A契约，完成后保持未暂存修复等待用户提交授权，不触碰现有18080服务。


## 2026-10-11 批准执行入口：研究合同修复

执行本设计同日R条款及 [M08 R1～R6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约拥有阶段目标、前置/分支、联合文件白名单、允许操作、测试矩阵、停止条件和最终报告。R1/R2、R3/R4、R5/R6每组结束两个子agent逐条对照文档审查。未经新的提交授权保持未暂存；禁止push/PR/部署。

## 2026-10-12 必要资格判断执行入口

执行本设计“必要资格判断接线”和 [M08 Q08合同](08_candidate_research_loop.md)、[Q1～Q6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约明确分支/HEAD、既有变更、每组目标/前置、生产及测试白名单、允许操作、统一测试、停止条件和报告字段。每两阶段统一测试后两个子agent逐条对照文档；全部离线通过后隔离三城各一次真实API，记时间及token。不得提交、推送、PR、部署或更新既有服务。

## 2026-10-13 分钟排程实施契约（T1～T4，用户已批准）

权威依据：[M09设计§8/§11/§12/§29](09_itinerary_planning_loop.md)、本执行规范及[共享规则§3/§4/§7](SHARED_IMPLEMENTATION_RULES.md)。直接依赖按[路线图](IMPLEMENTATION_ROADMAP.md)为01/02/04/06/07/08；只核对M01结构化抵离与候选用途输入、M02六工具与计数、M04 Artifact/预算、M06原始路线秒数、M07酒店引用、M08资格证据。相关权威文件为01_requirement_and_dialogue.md、02_llm_runtime_and_model_profiles.md、04_workflow_state_and_artifacts.md、06_baidu_map_adapter_and_mcp_gateway.md、07_hotel_candidate_selection.md、08_candidate_research_loop.md；按实际仓库文件名核对后引用，名称缺失不得猜测。消费方依据[12结果整理](12_result_formatting.md)和[13工作流](13_demo_workflow.md)，页面依据[14网页应用](14_web_application.md)。

前置条件：用户批准四点和双审；基线 main/c623272ede4cc7e1630001f3c38d9ed71b0f9d06；本轮分支 feature/m09-minute-scheduling。已有未跟踪 ../m13-live-evidence/ 原样保留。先核验当前HEAD、dirty、目标资源和文档引用；不读取凭据或原始思考。

### 文件白名单与允许操作

生产：app/itinerary_planning/{models,policy,artifacts,service,tooling,prompt_loader,admission}.py，新增内部 timing.py 仅用于既有提交校验；prompts/planning_v8.txt新增，v1～v7只读；app/result_formatting/{artifacts,projection,service}.py仅M09版本和新时间读取；app/demo_workflow/{artifacts,nodes,plans,supplement,terminal_nodes}.py仅同一接缝。
测试：tests/unit/itinerary_planning/、tests/contract/itinerary_planning/；tests/unit/及tests/contract/{result_formatting,demo_workflow,web_application}/仅受影响的M09夹具和消费断言；tests/integration/{result_formatting,demo_workflow}/；tests/browser/web_application/test_demo_pages.py；tests/support/{itinerary_planning_fakes,demo_workflow_fakes,demo_workflow_worker,module14_browser_worker}.py仅相同M09合同的离线主链夹具与验证接线；必要的tests/unit/llm_runtime/test_strict_planning_tools.py仅既有M09工具schema断言。
文档：本D/E09、docs/12_result_formatting.md及其执行规范、docs/13_demo_workflow.md及其执行规范仅新M09读取版本；closeout只含README.md、docs/IMPLEMENTATION_STATUS.md。

2026-10-14用户批准最小测试扩展：补列tests/unit/requirements/test_live_checkpoints.py，仅将M09写入封装版本取自scenario.itinerary.schema_version，保留读取持久化结果不创建API运行环境的全部断言；不修改M01生产代码或其他检查点合同。

同日用户批准补列tests/integration/web_application/test_workflow_api.py，仅将M09行程版本断言更新为5.0，保留格式修正、实际预算、主图及只读重新打开的全部断言，并重跑原网页/PostgreSQL集成检查。M14生产逻辑不在本次扩展范围内。

同日用户另批准该文件默认样例source_kind断言同步为validated_result。基线main和当前manifest均为该类型，样例文件不修改；其他样例及业务检查保持。

同日用户批准补列tests/integration/artifact_persistence/test_capacity_snapshot.py，仅同步M09样本：保存当前5.0行程和非整点分钟行程；模型校验和仓储准备都拒绝旧2.0/3.0/4.0行程，不写入数据库。保留M08历史版本、模型动作事实、规范字节/hash、幂等和PostgreSQL重新打开的全部检查，不修改M10生产代码、迁移或预算。

Dockerfile、锁文件、既有测试脚本只读核验；本轮具名隔离runtime/test/browser镜像、无外网测试容器、独立临时PostgreSQL及离线浏览器服务允许创建。运行既有迁移初始化隔离库，无新迁移；不访问或更新现有18080应用及其数据库，不执行真实模型/地图调用。测试输出写仓库外隔离目录，仅保存测试事实和页面截图，不新增诊断报告。

### T1＋T2：统一规则、版本和时间反馈

T1：实施T09-01～04/08～09，统一公共、工具、持久公式及分钟精度；写入新记录版本和新Prompt，精确更新读取方。保留六工具、原始秒数、上下界和所有预算。
T2：实施T09-05～07，在已有提交流程定位和汇总可独立确定的时间问题，保持原拒绝及纠错机制；不新增第二套排程器或自动修改草案。针对性测试同时覆盖合法非整点、真实冲突、结构/引用失败和实际消除问题的进展。
两阶段完成后统一运行定向及M09模块Gate；两子agent分别逐条审查时间/记录合同和执行/纠错/额度合同。发现白名单内明确问题直接修复，重跑受影响测试并复核。

### T3＋T4：存储、浏览器与closeout

前置：T1＋T2测试和双审通过。
T3：隔离PostgreSQL用既有迁移验证新记录规范字节/hash、完整来源、保存后读取及进程重启；执行受影响M12/M13合同，旧规则拒绝与新来源闭合必须有证据。
T4：隔离离线网页跑真实生产规划及渲染链，展示非整点活动和交通占用，验证刷新、复制和下载；不得用手写HTML或替换业务结果冒充主链。同步README/状态实收，核对Docker COPY/CMD；从同步源码重建runtime/test并运行默认无配置CMD、M09覆盖率和完整离线回归，按共享规则处理必需隔离PG依赖。
两阶段结束后两个子agent逐条对照T09-01～11及本契约验收。若发现问题，修复并重新验证对应项。

### 测试矩阵、停止条件与报告

定向：minute/ceil/interval/duration、route feedback、重叠/窗口/抵离/跨日、unknown营业与既有资格、no-progress/循环/patch、record版本/hash/读取；模块：Docker内tests/support/run_module09_coverage.py，line/branch/combined各≥70%；受影响M12/M13/M14合同；存储和浏览器按T3/T4执行。完整离线默认test CMD，不得删测试、skip/xfail或降低Gate。
停止条件：超出白名单或改变四点以外设计；新增工具或额度；旧资源/索引被改变；数据泄漏；测试零收集、失败或Gate不足。可定位的小范围问题在白名单内修复，不能带失败进入下一组阶段。无真实API授权，不启动live。
最终直接在聊天报告：分支、前后HEAD、修改文件、迁移、定向/模块/覆盖率/存储/浏览器/完整回归结果、逐条双审、异常文件、真实API NOT_RUN、stage/commit/push/PR/部署状态及READY_FOR_COMMIT或未完成原因。完成后等待提交，禁止stage/commit/merge/push/PR/部署。

### 2026-10-14 用户授权的本机空库与网页换版

依据上述已通过的T1～T4、M09设计§29、M14执行规范LS01/LS02及共享规则。用户明确授权新建Demo专用空库、保留旧库并更新127.0.0.1:18080，优先于上一阶段禁止本机换版的限制；不授权Git写入、云端更新或真实规划请求。沿用feature/m09-minute-scheduling与c623272ede4cc7e1630001f3c38d9ed71b0f9d06，保留既有未暂存修改。

白名单为本执行规范、README.md、IMPLEMENTATION_STATUS.md，以及忽略目录.publication/m09-minute-local-20261014a/中的换版脚本、无秘密的Compose覆盖配置、镜像验证与安全收据。生产代码、Compose主文件及已有凭据配置不修改。

L1为单独的数据安全门：核验cta-m14-live-20261006a的Web/PostgreSQL标签、原数据卷及18080映射；只读核对旧库无活动租约、待分发命令或待接纳请求。确认新库名不存在后，在既有PostgreSQL中创建m14_demo_minute_20261014a，仅在新库运行既有M10/M11/M14迁移，验证业务数据为空。原m14_demo库、数据卷、密钥及其他项目不修改。

L2在L1通过后换版：从当前源码构建runtime，核对应用文件、planning-v8、行程5.0及分钟公式；复用原Compose和env-file链，秘密仅在内存中处理，只将该Web的POSTGRES_DB和镜像切换为新值。再次核验旧库无活动执行后正常关闭Web，仅重建m14-web，保持原PostgreSQL及其他8080资源。失败回退必须恢复原镜像和原数据库，保留新旧两库。

验证矩阵为runtime默认入口、生产文件一致性、新库三个迁移与空业务计数、旧库前后安全计数、配置仅两项变化、健康接口、四页及静态资源、公开样例、浏览器会话及历史列表。复用已通过的3278项离线回归、488项M09、59项数据库集成、6项独立存储和33项浏览器资格，不重复真实模型或地图调用。换版脚本由两位既有只读审查者核对目标、旧库保持及回退；不使用子agent调查运行故障。

停止条件为数据库重名、活动执行、资源或配置不符、新库初始化失败、旧库计数变化、关闭异常或健康/源码校验失败。报告分支、前后HEAD、文件、新旧数据库、迁移及目标、验证、异常文件、Git操作、本机/云端状态和访问地址；提交仍等待用户授权。

### 2026-10-14 交通预留按10分钟取整（T5＋T6）

用户直接要求43分钟预留50分钟，授权修改交通占用规则。设计依据为[M09 §29 T09-01～11](09_itinerary_planning_loop.md)，消费方依据[M12分钟规划读取合同](12_result_formatting.md)与[M13分钟规划读取合同](13_demo_workflow.md)。沿feature/m09-minute-scheduling，前置HEAD为c623272ede4cc7e1630001f3c38d9ed71b0f9d06，保留已验收且未提交的分钟排程、既有测试证据和仓库外文件。

白名单沿T1～T4，允许新增prompts/planning_v9.txt，v1～v8字节保持；之前批准的检查点、网页集成及容量快照测试仅同步本次M09版本和相关样本。生产修改限交通预留、记录版本、提示词及既有读取接缝，不增加工具、调用、token额度、供应商并发或重试。文档限D/E09、D/E12、D/E13、README和IMPLEMENTATION_STATUS。允许Docker离线测试、独立临时PostgreSQL和隔离浏览器；不修改现有18080、两份运行库或数据卷，不执行真实API、迁移运行库、stage、commit、merge、push、PR或云端更新。

T5目标：公共计算、工具结果、路线结束推导、持久化及读取使用10分钟交通预留；活动和抵离最早边界仍按一分钟表达，原始秒数及所有安全检查保持。写入6.0行程与3.0路线，planning-v9/action9及directory5绑定完整；拒绝旧行程和混合版本，保持旧模型动作事实。不得将交通函数用于活动时长。

T6目标：定向覆盖取整边界、43→50、非整点出发及真实冲突；验证真实工具反馈到提交、规范字节/hash、PostgreSQL重新打开和网页刷新、复制、下载一致。相邻两阶段完成后统一执行M09完整单元/合同及原三项70%覆盖率门槛、M12/M13受影响单元合同、针对性PostgreSQL与浏览器检查；不重复未受影响的全模块门禁。构建最终runtime/test，核验Docker COPY/CMD和Prompt资源。两个原只读子agent逐条对照T09-01～11及T5/T6审查，小范围问题在白名单内修复并重跑对应项。

停止条件：需要修改无关业务、扩大白名单、新增额度或工具、运行资源或索引改变、凭据/正文泄漏、零测试收集或验证失败；不得放宽安全检查以通过。最终直接报告分支、前后HEAD、修改文件、迁移、测试与双审、异常文件、真实API、本机版本和commit/push/PR状态，完成后等待提交。

## 32. 每日实际可用时间统一规划执行合同

本轮已由用户授权更新六份文档、双文档审查、实施、两名新代码审查者复核及一次新真实API任务。执行必须引用[D09 §30 UD-01～07](09_itinerary_planning_loop.md)、本节及[共享实施规则](SHARED_IMPLEMENTATION_RULES.md)。此授权不包含stage、commit、merge、push、PR、部署或替换既有网页服务。

### 32.1 前置条件与分支

继续未合并分支feature/m09-boundary-day-quality，开始HEAD为b4c382e09695f3bdd71364b9aac1ae5d75ab4325。工作区为现有m24-frontend-experience管理工作区中的china_travel_agent，不另建或切换工作区。已有39项改动含历史实现及尚未通过的P1回归，全部保留并纳入本轮对照；不能以之前的测试通过代替当前资格。D盘feature/m09-minute-scheduling的46项改动和既有服务必须核对前后hash／容器身份，不修改、stash或清理。

路线图M09直接依赖01、02、04、06、07、08。实施提示词必须先列出并读取：D00 §1～4；D01 §6/8/11；D02 §5/6/19/20；D04 §8～10/12/13/21；D06 §7/11/12/22；D07 §8/9/17；D08 §7/8/18/19及Q08；D12 §5～10及“每日规划质量结果合同”；D13 §5～11及“每日规划接线合同”。同时读取路线图、共享规则和当前状态。仅消费既有上游合同，不修改上游事实或预算。

新证据根为<EVIDENCE_DIR>，私有目录使用独立同名前缀；不得覆盖既有boundary及P1证据。创建基线记录分支、HEAD、dirty文件hash、原工作区hash和既有服务身份。六文档双审通过后保存document-freeze.json，之后实施不得为迁就代码修改冻结设计。

### 32.2 精确文件白名单

生产：
- app/itinerary_planning/{__init__,models,artifacts,service,policy,admission,quality,prompt_loader,prompting,tooling,fingerprints,timing,map_execution}.py及prompts/planning_v11.txt；旧prompt资源只读。
- app/result_formatting/{__init__,artifacts,models,projection,renderer,service}.py。
- app/demo_workflow/{artifacts,graph,nodes,plans,supplement,publication}.py。
- Web生产代码只读，现有接口继续消费M12结果，不增加模板专用接缝。

测试：tests/unit/itinerary_planning/、tests/contract/itinerary_planning/；tests/unit/llm_runtime/test_strict_planning_tools.py仅新工具参数合同；tests/unit及contract下result_formatting、demo_workflow的相关测试；tests/integration/result_formatting/、demo_workflow/、artifact_persistence/test_capacity_snapshot.py、durable_execution/、web_application/test_workflow_api.py；tests/browser/web_application/内受影响流程／结果测试；tests/support/内既有fake装配及跨进程worker仅本次版本和场景接缝。不得修改覆盖率脚本、删除测试、skip、xfail、自动重试或降低安全断言。

设计阶段仅改D09/E09、D12/E12、D13/E13六文件；实施冻结后只读。closeout阶段仅README.md及docs/IMPLEMENTATION_STATUS.md。Dockerfile、依赖和lock只读，镜像构建与外部测试驱动写入仓库外证据目录；若发现必须变更其他文件或上游合同，停止并报告最小扩展，不自行扩大。

### 32.3 相邻阶段与允许操作

| 阶段 | 目标及前置 | 允许操作与完成证明 |
|---|---|---|
| UD1＋UD2 | 文档双审PASS、hash冻结；统一窗口与规则、前置整份安排 | 实现D09 UD-01～04；新DTO严格验证、首次全日期／后续局部替换、整批先验证后接纳、边界先查、统一景点上限／交通上限、最终重验；跑定向及M09完整Gate |
| UD3＋UD4 | 前组通过；P1容量、版本及下游 | 实现UD-05～06；业务容量反馈与技术故障分开，真实账本和槽位分开，复用需求清单；新state/action/source/template合同、旧只读；跑定向、模块、PG保存重启及浏览器链路、全项目离线 |
| UD5 | 离线Gate全部通过 | 同步closeout事实；从同步后源码重建runtime/test/浏览器适用镜像，运行runtime默认CMD、受影响Gate、完整默认离线CMD；记录源码清单与镜像身份；两名新的代码agent分别审查规划及交付，不得复用本轮文档审查者 |
| UD6 | 两新代码审查PASS、当前源码与最终镜像一致、冻结未变 | 独立资源运行D09 UD-07固定输入一次新任务，保存诊断及安全指标，终态后正常关闭仅本轮临时资源；不新建重试任务或修后再跑第二次 |

每份实施／复核提示词必须完整放入一个代码块，保留本轮目标、前置、分支、文件白名单、允许操作、矩阵、STOP及报告字段，引用设计与执行规范，不粘贴大段正文。小范围明确问题在对应白名单内直接修正，并重跑必要测试；不能改冻结语义。

### 32.4 实施顺序

1. 定义DailyPlanOutline及PlanDirectionsArgs3.0；Artifact本地outline快照；state8.0、itinerary8.0、directory7.0、action11.0精确版本映射及旧版本保留。生产Prompt资源从本轮证据根的planning_v11.txt按冻结hash复制，loader和packaging核对。
2. 保留统一窗口计算，移除依赖day_kind的数量／餐次／城内交通豁免；供给预检不按完整日数强行拒绝，must保持。质量纯函数同时供初步安排和最终提交调用，按UD-04确定检查终点，严禁普通晚上强制填满。
3. _validate_batch完整验证兄弟、类型和归属后核验查询用途，再检查边界证据与合并后的全日期outline；只在整批准入成功后更新run中outline。拒绝返回稳定target/rule/field及允许动作。不得在ToolDispatcher内自动补访问、移动时间或多发请求。
4. 在模型调用前读实际Direction账本；失败按BUDGET_ERROR收敛。planning_state分别显示账本剩余、新操作槽位、相邻路线需求与既有句柄。任何新增缓存判断复用fingerprint和context_digest，不复制另一套宽松键。
5. 容量不足作为专用业务分支记录并持久化安全反馈；部分不足允许缩小，完全无容量时重验基线或Incomplete。MapExecutor原有整批只读准入继续计新指纹、执行前预留和并发控制，不降低取消、持久化及副作用屏障。非法hub/matrix计划、句柄、工具、预算读取失败不得降级为业务容量。
6. submit/patch及结束回退均做全日期硬校验和质量派生，长空日不能被保留为可交付基线。补研引起证据变化时重新验证，不使用已失效基线。接线M12 v4、M13当前web.v9及恢复限制。
7. 按矩阵统一测试、closeout、最终构建、独立代码审查，最后唯一一次真实任务。失败后记录失败和NOT_RUN项，不继续外部循环。

### 32.5 测试矩阵

| ID | 证明要求 |
|---|---|
| UT-WINDOW | 铁路／机场、近似、秒取整、同日、跨午夜、空／短窗口、未查路线；同窗口同证据的日期仅标签不同，规则一致 |
| UT-OUTLINE | 首次缺日、漏晚离开日、整日自由活动、全程重复／用途／营业／must错误在日内地图前拦截；边界纯查询合法；拒绝不污染；补丁不重排其他日期；未知交通不伪造 |
| UT-QUALITY | 所有日期检查；活动间和白天大空档、累计空档、累计休息、餐次边缘、组合餐；普通合理晚间收尾不强制22:00；长空日不是完整基线 |
| UT-P1 | 公共服务入口：账本为零、仅槽位为零、批次超过部分容量、缓存直接复用；无基线／无效基线为Incomplete；混合非法hub/matrix计划、无效句柄、未知工具仍失败；反馈持久化失败、账本读取失败及取消维持错误语义；拒绝零地图副作用 |
| UT-VERSION | 新DTO／状态／父链／directory/action/prompt精确版本；新旧混用拒绝、旧6/7非终态拒绝恢复；旧6/7行程、旧结果规范bytes/hash/Markdown不变 |
| PG-RESULT | 新来源→日块／索引→保存→另进程重读一致；幂等／冲突安全、补研来源重验、既有容量；使用全新测试数据库，旧固定UUID证据库不复用 |
| WEB-RESULT | 实际装配或明确标注的结果展示夹具，证明质量提醒、degraded、完整读取及导出；长空日Incomplete没有伪攻略；不得把展示夹具冒称完整主图 |
| OFFLINE | M09既有Docker coverage Gate（原阈值不变）、受影响M12/M13及Web、相关PG／browser；当前默认test镜像全量unit/contract；compileall、diff、白名单、隐私检查 |
| CLOSEOUT | README/STATUS事实同步后重建；runtime默认CMD缺配置预期退出2，Prompt正确打包；受影响模块Gate与完整默认离线重跑；记录最终源码与镜像 |
| LIVE-ONE | 固定输入显式填写并断言、仅一次创建，确认／酒店操作不含交易；记录耗时、模型次数、token／缓存、每日窗口／安排／质量及终态，完整私有记录导出；失败不重跑 |

没有SQL结构迁移，只在独立数据库执行既有迁移。无需新增真实供应商单元测试。任何未完成的Gate不得写PASS。旧真实失败保留，本轮唯一新任务与历史任务分开统计。

### 32.6 停止条件与最终报告

设计冲突、白名单不足、上游合同扩展、预算增加、降低安全断言、异常dirty冲突、文档审查或离线／代码审查未通过、资源身份不符、真实服务鉴权／余额／网络失败即停止相关后续动作。真实一次任务失败后保存证据，不再调用真实API。不能把技术故障、记录缺失、整日未安排或版本不一致写为完成。

报告至少列分支、前后HEAD、修改文件与异常、迁移、六文档冻结、两文档及两新代码审查、定向／模块／全量／覆盖率／PG／browser、closeout后镜像和默认CMD、正式真实任务与诊断边界、耗时及token、隐私、原工作区及既有服务核验、staged／commit／push／PR／部署状态、最终完成或失败及限制。

## 33. 多日期路线合并调用执行合同

### 33.1 目标、前置与工作区

执行[D09§31 RC-01～06](09_itinerary_planning_loop.md)，保留D09§30 UD-01～05和现有研究来源核验。本轮阶段、当前Prompt版本、文件白名单、测试和授权以本节为准；历史阶段不构成本轮修改授权。继续feature/m09-boundary-day-quality，不切换或新建工作区；开始HEAD为b4c382e09695f3bdd71364b9aac1ae5d75ab4325，工作区现有88项未提交改动、暂存区为空。现有改动全部保留，只核对本轮增量。原D盘M09工作区46项改动、既有服务及云端保持。

必读：D09§10.3、12.3、20.2、30、31及本节；SHARED_IMPLEMENTATION_RULES.md§1～7；00_agent_master_design.md§1～4和IMPLEMENTATION_ROADMAP.md的M09依赖行。上游精确引用为01_requirement_and_dialogue.md§6/8/11、02_llm_runtime_and_model_profiles.md§5/6/19/20、04_workflow_state_and_artifacts.md§8～10/12/13/21、06_baidu_map_adapter_and_mcp_gateway.md§7/11/12/22、07_hotel_candidate_selection.md§8/9/17、08_candidate_research_loop.md§7/8/18/19/31。下游12_result_formatting.md§5～10及“每日规划质量结果合同”、13_demo_workflow.md§5～11及“研究纠错接线合同”，仅核对既有动作读取、规划版本和职责边界。以上路径均相对于docs目录，先核实存在，不增加上游修改。

证据目录<EVIDENCE_DIR>，私有记录目录<PRIVATE_DIR>。开工保存全项目文件摘要、分支/HEAD/dirty/staged、原工作区摘要及既有服务身份。文档阶段只修改D09和E09；两个文档审查者均无P0/P1/P2并复核通过后，保存这两份文档的document-freeze.json。实施期间不得改冻结文档迁就代码。

### 33.2 精确文件白名单

生产只允许：
- 新建app/itinerary_planning/prompts/planning_v12.txt；不存在但允许创建的目标，文本来自本轮证据目录受固定摘要保护的同名草稿。
- app/itinerary_planning/prompt_loader.py：切换受保护资源、版本和摘要。
- app/itinerary_planning/tooling.py：仅plan_directions的工具说明，不改验证、分派或执行逻辑。
- app/itinerary_planning/artifacts.py：仅11.0动作对planning-v12的严格识别及保留11.0/v11；其余模型、版本和行为不改。

测试只允许：tests/unit/itinerary_planning/test_prompt_loader.py、test_tooling.py、test_daily_planning.py、test_service_initial.py，以及tests/contract/itinerary_planning/test_artifact_registration.py。允许新建tests/unit/itinerary_planning/test_route_call_merging.py，作为真实模型请求及合并调用合同测试。不得重写共用供应商替身掩盖模型输入变化，不删弱原断言，不改覆盖率脚本。

MODULE-CLOSEOUT只允许README.md与docs/IMPLEMENTATION_STATUS.md。Dockerfile、依赖/锁、旧提示词、service.py、模型参数、预算、错误恢复、上游/下游代码、迁移与网页均只读。测试驱动、诊断和提示词草稿只写仓库外证据目录；不得把私有交互或凭据放入仓库。

### 33.3 相邻阶段与允许操作

| 阶段 | 前置及目标 | 完成证明 |
|---|---|---|
| RC1＋RC2 | 文档双审及摘要冻结；切换Prompt、明确工具说明和必要版本识别，并补回归 | 逐条实现RC-01～05，按33.4统一运行定向测试、M09完整模块及覆盖率；保留原错误语义 |
| RC3 | 前组通过；MODULE-CLOSEOUT与两个新的代码审查者 | 同步README/STATUS；从当前源码重建runtime/test；核对打包、默认CMD和源码摘要；完成适用离线测试；两名代码审查者逐条对照冻结文档并复核通过 |
| RC4 | 两个新的代码审查者均通过，最终源码和镜像一致 | 按33.5执行唯一一次真实任务，保存完整私有记录、安全汇总和收尾证明 |

不运行Git写操作，不stage、commit、合并、push、创建PR或部署。小范围明确问题仅在当前白名单内修正并补必要验证；扩大设计或范围须先取得用户同意。后续生成的实施、修复或审查提示词必须各自放在一个代码块中，保留目标、前置、分支、白名单、允许操作、测试、STOP和报告栏目，按精确章节引用真源，不复制大段规则。

### 33.4 测试矩阵

| ID | 验证内容及入口 |
|---|---|
| RC-PROMPT | test_prompt_loader.py及test_route_call_merging.py：v12资源/摘要、v11原字节、合并与先接驳后日内要求；捕获生产M02装配的实际请求，核对system文本、工具description及既有daily_outline/legs/visit_intents参数，不能仅检查常量 |
| RC-PUBLIC | test_daily_planning.py或test_service_initial.py：通过公开plan_initial入口，先取得两条接驳，再用一个路线调用提交至少两个日期的路线及完整初步安排；实际地图次数/顺序、模型次数、动作版本/Prompt摘要、整份安排接纳与已有路线保留可观察；不以私有run状态代替公共结果和保存记录 |
| RC-BOUNDARY | test_tooling.py及既有daily_planning测试：一个路线调用携带多腿合法；同轮多个路线调用仍拒绝且零新地图副作用；漏日、长空日、边界未核验仍拦截。明确不测试或宣称新增批次纠错 |
| RC-ARTIFACT | test_artifact_registration.py：真实registry保存读取11.0/v11、11.0/v12；其他schema/Prompt配对和非法额外字段拒绝；旧记录规范字节与历史资源保持 |
| RC-MODULE | 在当前源码新建的test镜像、新容器、network none、只读文件系统与/tmp临时目录中运行M09全部unit/contract；按tests/support/run_module09_coverage.py现有方式，line/branch/combined分别不低于70%。不改阈值或测试数量来追求绿灯 |
| RC-CLOSEOUT | README/STATUS同步后重建runtime/test，Prompt打包与完整源码核对；compileall仅写/tmp；runtime默认命令缺配置按预期退出2；test镜像默认离线命令通过。无数据库结构/界面变化，不新增离线PG重启矩阵或全套浏览器回归；唯一真实任务继续走公开网页/接口链 |
| RC-LIVE | 33.5唯一任务；实际请求及逐轮路线组合核验、终态与结果读取、窗口/安排/质量/统计和记录完整性；真实模型、百度地图，不能用固定回答代替 |

设计文档阶段不运行pytest、Docker构建、coverage或compileall。离线测试只使用替身且无真实凭据；源码重建前后的历史结果不得充当本轮通过证明。测试量与本次提示词及元数据改动匹配，不扩展到其他问题修复。

### 33.5 一次真实任务与停止条件

唯一真实输入、确认/酒店操作、隔离数据库和私有凭据规则按D09 UD-07；只复用此前明确授权的本机DeepSeek和百度地图两项凭据，经进程环境传入本轮临时服务。使用新资源前缀cta-m09-route-merge-live-261019a和临时端口18084，先确认未占用；数据库仅应用现有迁移，不读取或改写既有业务库。

运行前持久化资格、最终文件清单与“一次创建意图”。创建后不得再新建任务或业务重跑；需求确认、酒店选择、GET状态/结果及已保存诊断读取不算新的规划测试。终态收集每轮路线调用数和腿数，检查生产请求包含v12与工具说明；记录每日窗口、安排与空白、最终标记、模型/地图消耗、耗时、token、供应商缓存命中及记录完整性。完整原始交互只存权限受限目录，不展示reasoning或密钥。终态后停止本轮服务并保留证据与数据库卷，其他服务身份不变。

文档/代码审查未通过、白名单不足、冻结变化、异常dirty、来源/记录/版本不一致、离线失败或资源身份冲突时停止相关后续动作。真实任务失败后仅收集证据，不修复或重复调用。只完成了部分流程、没有实际日内路线查询或没有攻略，必须分别报告，不以“仅一个路线调用”冒称业务成功。

### 33.6 报告

至少包含分支、前后HEAD、基线与本轮修改文件、异常文件、迁移/依赖、两份文档冻结摘要、两文档审查及两新代码审查、定向/模块/默认离线及覆盖率、runtime/test镜像与打包核对、真实任务次数/编号/调用组织/业务结果/指标、隐私、原工作区与既有服务核验、MODULE-CLOSEOUT、staged/commit/merge/push/PR/部署和最终状态。保留“批次错误仍直接拒绝”的范围限制；没有运行或没有通过的项目明确标记。

## 研究纠错来源读取执行合同

本次仅按[D09“研究纠错来源读取合同”](09_itinerary_planning_loop.md)和[D08 §31/E08 §30](08_candidate_research_loop_vibe_coding.md#30-c1c6-研究纠错与成果交付执行规范)的C1～C6执行候选5接线。分支、前置、白名单、允许操作、矩阵、STOP和报告均归E08；只改必要类型/Ref分派及来源核验入口，不修改现有每日规划规则。定向证明candidate5合法来源、混版和拒绝来源反例、Incomplete候选的真实准入及旧候选只读，不扩充本轮真实调用次数。
