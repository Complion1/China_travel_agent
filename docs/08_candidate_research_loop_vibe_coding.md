# Module 08 Vibe Coding 执行契约：受控候选研究循环

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次消费M02专用交互记录，仅增加调用用途/工具结果关联；需求业务语义及M08原6轮/48Place/12逻辑调用不扩大。普通日志/业务Artifact禁正文的约束不变。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

> 状态：已批准并冻结；`MODULE08-DESIGN-ERRATA-01`、`D-08-32` 与 `D-08-34` 修订稿。
>
> 本文规定未来如何实施和验收，不授权当前设计任务创建生产代码、测试、Prompt、依赖、migration 或运行配置。公共语义以 `docs/08_candidate_research_loop.md` 为准；实现冲突时修复实现，不得反向修改设计。设计确需变化时停止并取得用户批准。
>
> 发布前设计勘误基线：本文原 SHA-256 为 `CE0B0846E6564587793AE75740FFA5EDA77F61FDB4AE9135E4125B9B5CD6EFC8`。用户先后授权把权威设计 `D-08-30`、`D-08-31`、coverage 80% 历史门槛 `D-08-32` 与当前 70% 门槛 `D-08-34` 落入实施与验收契约；schema/version、四类 Artifact type 和 Prompt version/hash 不变。修改后 hash 只写入独立复审交接报告。

## 1. 用途、执行模式与授权边界

本文面向实施能力较弱的编码模型，把 Module 08 拆成可核验的小阶段。每次实施提示词必须同时引用：

- `docs/08_candidate_research_loop.md`；
- 本文；
- `docs/SHARED_IMPLEMENTATION_RULES.md`。

不得用本文件替代权威设计，也不得重复粘贴上游文档大段内容。代码审查和验收必须逐条对照两份 Module 08 文档，测试通过不能单独证明完成。

默认一次授权相邻两个阶段：`08-1 + 08-2`、`08-3 + 08-4`、`08-5 + 08-6`，完成两个阶段后统一运行相应 Gate。小范围明确缺陷可在当期白名单内直接修复；修改已确认设计、扩大范围、新增依赖、迁移或大改必须停止询问。

实施、修复和 closeout 模型均不得 `git add`、stage、commit、merge、push、PR 或 deploy。每组阶段完成状态只能是 `READY_FOR_INDEPENDENT_REVIEW`；提交资格为 `NOT AUTHORIZED；等待独立审查`。MODULE-CLOSEOUT 和独立审查完成前不得报告 Module 08 COMPLETE。

## 2. 必读真源

开工前必须完整读取，不得只看摘要或搜索片段：

1. `docs/00_agent_master_design.md` 第 1～11 节；
2. `docs/IMPLEMENTATION_ROADMAP.md` 第 2 节 Module 08、第 3 节 WP4、第 4 节 M3、第 6、8 节；
3. `docs/SHARED_IMPLEMENTATION_RULES.md` 第 1～9 节；
4. `docs/08_candidate_research_loop.md` 全文；
5. 本文全文；
6. `docs/01_requirement_and_dialogue.md` 第 3.3～3.4、5.2～5.3、6.3～6.6、11、13、15～17 节；
7. `docs/02_llm_runtime_and_model_profiles.md` 第 4.2～4.3、5～12、15～18 节；
8. `docs/04_workflow_state_and_artifacts.md` 第 2～3、5～6、8～14、17～21、22～26 节；
9. `docs/06_baidu_map_adapter_and_mcp_gateway.md` 第 3.2、4.3、6～7、10、15、18～22、24～29 节；
10. `docs/07_hotel_candidate_selection.md` 第 3～11、17～27 节；
11. `docs/07_hotel_candidate_selection_vibe_coding.md`，只参考执行组织，不复制酒店语义；
12. `docs/IMPLEMENTATION_STATUS.md` 和 `README.md`，只核验状态；
13. §4 列出的公共代码、合同测试和构建文件。

优先级按权威设计 §1。发现冲突必须给出文件、章节、公共符号和影响，立即停止。

## 3. Git 开始条件

每个阶段提示词必须冻结当次批准的：稳定分支、开始 HEAD、HEAD subject、目标功能分支、clean worktree、staged/untracked 状态和阶段白名单。首次实现预期在用户批准的 `feature/module-08-candidate-research` 上继续；不得根据本文猜测实时 HEAD。

执行前重新检查：

```text
git branch --show-current
git rev-parse HEAD
git log -1 --pretty=%s
git status --short
git diff --name-only
git diff --cached --name-only
```

有漂移时禁止 reset、clean、stash、强制 checkout、覆盖或删除；报告精确差异并 STOP。已有用户变更必须保留。

## 4. 直接依赖公共 API 与 acceptance gate

### 4.1 必读公共代码

完整读取：

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
app/baidu_map/models/places.py
app/baidu_map/models/geocoding.py
app/baidu_map/errors.py
app/hotel_selection/__init__.py
app/hotel_selection/models.py
app/hotel_selection/artifacts.py
app/hotel_selection/protocols.py
```

至少完整读取这些合同测试：

```text
tests/contract/llm_runtime/test_provider_protocol.py
tests/contract/workflow_contracts/test_public_api.py
tests/contract/workflow_contracts/test_artifact_repository_contract.py
tests/contract/workflow_contracts/test_execution_budget_manager_contract.py
tests/contract/baidu_map/test_public_api.py
tests/contract/baidu_map/test_adapter_contract.py
tests/contract/hotel_selection/test_public_api.py
tests/contract/hotel_selection/test_upstream_integration.py
```

只读核验 `pyproject.toml`、`requirements.lock`、`Dockerfile`、`.dockerignore`、`.env.example`、`compose.yaml`。现有 Pydantic、pytest、pytest-asyncio、pytest-cov 和 Hypothesis 足够；不得为 convenience 新增库。

### 4.2 必须成立的资格

- `ModelRole.CANDIDATE_RESEARCH` 和 `CANDIDATE_RESEARCH_PROFILE` 精确存在，semantic limit=12、request bytes=262144、output tokens=131072（[M02 §6](02_llm_runtime_and_model_profiles.md) 2026-10-07获批静态额度；Action/Judgment共用，其他预算不变）；
- `ModelOperation.ainvoke_structured` 在 byte Gate 后消费 semantic invocation，输出 typed success/failure；
- 失败 outcome 不公开 provider-attempt/usage，ModelOperation 累计计数是私有；Module 08 只能按权威设计 §18.2 保存 known totals + complete flags；
- `WorkflowPhase.CANDIDATE_RESEARCH`、Artifact Repository、ExecutionBudget Manager 和相关错误从稳定公共面可用；
- Module 04 预算接线表中 `research.iteration=6`、`research.place_call=48`；
- `BaiduMapAdapter` 公共提供 `search_places`、`get_place_details`，Place Search 最多 50，Module 06 并发为 3、零工具 retry；
- `HotelSelectionArtifact` 为 `hotel.selection/1.0`，selected decision 含可信酒店候选；
- Module 01 的 confirmed requirement 和 must/avoid 各 100 上限未漂移；
- Module 01～07 acceptance/implementation 状态满足路线图前置。

任一不成立不得通过局部 shim、私有导入或复制 DTO 绕过。

## 5. 未来文件树

### 5.1 生产文件

```text
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
app/candidate_research/errors.py
app/candidate_research/protocols.py
app/candidate_research/prompt_loader.py
app/candidate_research/prompting.py
app/candidate_research/fingerprints.py
app/candidate_research/policy.py
app/candidate_research/judgments.py
app/candidate_research/service.py
app/candidate_research/prompts/action_v1.txt
app/candidate_research/prompts/observation_v1.txt
```

实现如能在不降低可读性、覆盖率和白名单精度的前提下合并私有 helper 文件，可以在阶段授权前提出；不得在执行中自行扩张。

### 5.2 测试与 support

```text
tests/support/candidate_research_fakes.py
tests/support/run_module08_coverage.py
tests/unit/candidate_research/test_models.py
tests/unit/candidate_research/test_prompt_loader.py
tests/unit/candidate_research/test_fingerprints.py
tests/unit/candidate_research/test_policy.py
tests/unit/candidate_research/test_judgments.py
tests/unit/candidate_research/test_service_initial.py
tests/unit/candidate_research/test_service_supplemental.py
tests/unit/candidate_research/test_service_failures.py
tests/unit/candidate_research/test_service_concurrency.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_import_boundaries.py
tests/contract/candidate_research/test_artifact_registration.py
tests/contract/candidate_research/test_upstream_integration.py
tests/contract/candidate_research/test_replay_budget_contract.py
tests/contract/candidate_research/test_security_contract.py
tests/contract/candidate_research/test_coverage_gate.py
```

测试文件可按实际职责做小幅合并，但所有 §13 测试项必须有明确归属，不能以减少文件为理由漏测。

## 6. 包根公共 API 精确清单

最终 `__all__` 必须与权威设计 §5 的有序清单逐项相等。为防弱模型漏项，阶段投影固定为：

- 08-1 创建最终清单中从常量到 `CandidateResearchUsageError` 的全部已实现符号，以及 `CandidateResearchPort`；此时明确排除尚不存在的 `CandidateResearchService`，禁止 stub；
- 08-2 不改变包根清单；
- 08-3 创建 Service 后，只在 tuple 尾部追加 `CandidateResearchService`，形成权威设计 §5 的最终清单；之后不得重排或新增。

合同测试对每个阶段使用对应精确 tuple，最终 tuple 必须逐字符为：

```text
CANDIDATE_RESEARCH_SCHEMA_VERSION, CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY,
CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
ResearchRole, RoleVerdict, CandidateState, MatchVerdict, MealIntent,
ResearchInvocationKind, ResearchTool, SearchAreaMode, ResearchMode,
ResearchObjectiveKind, CandidateResearchStopReason, RoleReasonCode,
InterestReasonCode, MealReasonCode, RequirementReasonCode,
CandidateResearchControlFailureCode, HotelAnchorRef, CandidateAnchorRef,
ResearchAnchorRef, ResearchIterationOperationIds, CandidateResearchOperationPlan,
CandidateResearchInitialRequest, CandidateResearchSupplementalRequest,
CandidateCountShortfallGap, InterestCoverageMissingGap, AreaCoverageMissingGap,
MealCoverageMissingGap, MustVisitResolutionMissingGap, AvoidResolutionMissingGap,
CandidateDetailsMissingGap, ReplacementCandidateNeededGap, SupplementalGap, ProposedSearchAction,
ProposedDetailsAction, ProposedAction, ProposedActionBatch, NoUsefulActionProposed,
ActionModelOutcome, EvidenceHandle, RoleJudgmentUpdate, InterestJudgmentUpdate,
MealJudgmentUpdate, RequirementVerdictException, RequirementGroupJudgmentUpdate,
JudgmentPatch, CandidateRecord, ResearchCountSummary, ResearchCoverageSummary,
KnownLLMUsage, ResearchBudgetSummary, ResearchGapSummary, LLMResearchFailure, MapResearchFailure,
ControlResearchFailure, CandidateResearchFailure, CandidateResearchCompleted,
CandidateResearchIncomplete, CandidateResearchFailed, CandidateResearchOutcome,
CandidateResearchModelActionArtifact, CandidateResearchModelJudgmentArtifact,
CandidateResearchPlaceObservationArtifact, CandidateResearchArtifact,
CandidateResearchError, CandidateResearchConfigurationError,
CandidateResearchUsageError, CandidateResearchPort, CandidateResearchService
```

不得导出 prompt 文本、prompt path、hash helper、query normalization、fingerprint builder、state accumulator、private run context、singleflight state 或任意 Module 06 私有符号。各具体 `*Summary`（含 `AvoidResolutionMissingSummary`）不逐一从包根导出，公共终态只导出 `ResearchGapSummary`；本次最终 tuple 只新增 `AvoidResolutionMissingGap`。公共 API 合同测试必须断言精确 `__all__`，不能只断言包含关系。

## 7. 逐文件实现清单

### 7.1 `models.py`

- 实现所有 strict/frozen/extra-forbid Enum/DTO/union；
- 字段顺序、默认、nullable、tuple、容量与权威设计一致；
- `CandidateResearchOperationPlan` 校验 run/phase/唯一 OperationId 和 0..6/0..8 槽位；
- Initial/Supplemental 输入互斥，不在 supplemental 复制 requirement/hotel；
- supplemental gap union 八分支、1..128、有序、稳定 ID；新增 `AvoidResolutionMissingGap` 的 avoid group/item/candidate typed target，summary union 同步八分支；
- supplemental `evidence_refs` 固定 1..8；公共 summary `evidence_indexes` 为 0..8，空 tuple 只能由 candidate-set/terminal 上下文验证，不能由分支模型单独放行；
- Action batch 1..8、ordinal 唯一递增、半径枚举、anchor 约束；
- outcome union 严格区分 Completed/Incomplete/Failed；
- `CandidateResearchFailed` 顶层 retry/duplicate 与分支一致；
- 不在 validator 中修正或排序调用方数据。

### 7.2 `artifacts.py`

- 实现四个 `1.0` Artifact DTO、精确 type 常量和 registrations；
- 所有注册类型图使用权威设计 §6/A.8 的 Artifact-local snapshot，全字段 required，配置精确三键；不得直接嵌入 Module 02/06 BaseModel；
- 只通过 `model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)` 构造 `ArtifactWriteRequest`；
- `model_action` 是 query/tag 唯一持久化位置；
- Observation 保存语义等值的完整 Artifact-local normalized outcome 或安全失败，不直接嵌入上游 DTO，不保存 query/tag/raw payload；Search success 同步保存与 places 一一对应的逐地点容量 disposition，`verified=false` 使用本地 `unverified_result` 分支且不复制 Place 字段；
- candidate-set 只保存严格 record union；state 内使用三个去重 Ref 目录和候选整数索引，不在每个候选重复复制 ArtifactRef；terminal record 必须足以重建 Completed/Incomplete/Failed；
- Artifact-local gap summary 为八分支，新增 `ArtifactAvoidResolutionMissingSummary`；所有 gap evidence index 为 0..8，空证据必须按权威设计 §17.2 在 state/terminal/replay 中重新证明；
- validators 校验 ArtifactRef type/version/run/OperationId 位置关系；
- 不修改 Module 04 registry，不创建全局 singleton。

### 7.3 `errors.py` 与 `protocols.py`

- 精确实现权威设计 A.7 的三个自有异常、完整 control error Enum 和 retry/duplicate 映射；
- `CandidateResearchPort` 精确声明两个业务方法和 `close_run`；
- 在模块内部定义不导出的 `_CandidateResearchRuntimePort` 和 `_CandidateResearchModelOperationPort`：runtime port 只含同步 `start_operation`，operation port 只含 async `ainvoke_structured` 与同步 `close()`；production `DeepSeekRuntime`/`ModelOperation` 分别结构性满足，unit fake 实现该私有消费面；
- 优先标准库 `hashlib`、`json`、`asyncio`，不复制 ProviderAdapter 或 Module 04/06 Protocol。

### 7.4 Prompt 文件、`prompt_loader.py`、`prompting.py`

- 两份 Prompt 是 UTF-8 文本真源，不含运行动态数据；
- loader 有版本常量和固定 SHA-256，启动/首次使用时 bytes 校验；
- 动态 JSON `ensure_ascii=False, sort_keys=True, separators=(",", ":")`，保留 null；
- user/provider 文本明确标记为 data；
- 构造 `PromptEnvelope` 后由 Module 02 对完整请求测量；
- 不记录 Prompt，不将 Prompt 放入 Artifact/异常/repr。

### 7.5 `fingerprints.py`、`policy.py`、`judgments.py`

- query/tag 只做首尾 strip；不做 Unicode、大小写、内部空白或标点归一化；
- requirement/group/objective/action/gap/request 的 ID、摘要和指纹逐字实现权威设计附录 B；
- `avoid_resolution_missing` 指纹精确绑定 avoid group ID、有序 item IDs 和按 admission ordinal 排列的 candidate UIDs，不包含 gap ID、evidence refs 或当前 verdict；
- policy 纯函数计算目标、状态优先级、容量、overflow、Details ceil、完成、进展、stop reasons；
- judgment 验证 handle 可见性，把短 handle 映射为 Ref，按 UID 合并 incremental patch；
- 禁止类别硬编码词典、模糊自动修复、模型排名或随机选择。

### 7.6 `service.py`

- 构造签名逐字为权威设计 §5 的四个 keyword-only 依赖；内部按私有消费 Protocol 使用 runtime，每个新 run 精确调用一次 `start_operation(ModelRole.CANDIDATE_RESEARCH)`；
- 每 run 一个私有 context、一个执行锁和按 terminal ID 建立的 singleflight 表；
- lookup/replay 在 run context 检查之前；
- 严格执行 §8 固定算法及 wave 失败/取消规则；
- 同 wave 的外部调用先完整收束并确定有序结果；随后按 Action 顺序写完 Artifact，之后才允许启动下一 wave、Judgment 或其他新的外部 await；
- `close_run` 取得同一 run 执行锁、等待活动调用结束，再同步 `ModelOperation.close()`；幂等且不关闭注入 runtime/adapter/repository/budget；
- `aclose` 收束残余 run。

## 8. 固定算法顺序

### 8.1 公共入口

```text
research_initial(request):
  1. strict fresh-copy request
  2. 以 terminal_successor_operation_id 作为入口 singleflight key；同 key waiter 加入同一执行
  3. 先 lookup；若 found，验证 request digest/record/ref 链并逐字段重建终态 outcome
  4. 取得 run execution lock；锁内再次 lookup terminal 并重新验证 current head、run 状态、remaining slots 和预算
  5. 验证 requirement、hotel.selection selected、run/phase 和精确 6-slot plan
  6. 创建且仅创建一次 run context + candidate-research ModelOperation
  7. 冻结 effective interests、requirement digest、hotel ref/digest
  8. 进入 controlled_loop(initial)，释放 run lock 后再完成 singleflight 广播

research_supplemental(request):
  1. strict fresh-copy request
  2. 以 terminal_successor_operation_id 作为入口 singleflight key；同 key waiter 加入同一执行
  3. 先 lookup；若 found，即使 run 已 close 也验证 request digest/record/ref 链并 replay
  4. 取得 run execution lock；锁内再次 lookup terminal 并重新读取 current head/predecessor
  5. 验证 run context digest/open 状态；不存在且非 replay -> Failed(run_context_missing)
  6. 锁内重新校验八分支 gap order/uniqueness/semantic fingerprint、1..8 Place evidence refs、remaining iteration/预算和 slot 数；avoid 分支还要逐项匹配前驱的 group/items/ambiguous-or-unresolved candidate target
  7. 全部已满足且 slots=0 -> 先从前驱现态重新计算完整终态，再用 terminal ID 写 transition_kind=no_op；空证据终态 summary 不得被合成为 supplemental 输入；否则 controlled_loop(supplemental)
  8. 释放 run lock 后再完成 singleflight 广播
```

### 8.2 单轮

```text
run_iteration(slot):
  check cancellation
  run_iteration_index = iteration budget consumed before this call
  objectives = deterministic_objectives(state)
  use action_operation_id to consume research.iteration before Action invocation
  action_prompt = build_action_prompt(...)
  action_result = model_operation.ainvoke_structured(action_prompt, ActionOutcome)
  if strict success:
      validate schema/tools/parameters and highest-priority policy; classify duplicates/budget prefix
      persist accepted, policy_rejected or controller_rejected model-action record
  else:
      persist llm_failure model-action record
  controller_rejected/llm_failure -> build and persist terminal Failed record; return from that record
  policy_rejected -> Place=0, progress=false; compute completion/stops; continue next round if legal
  for each admitted Place observation_operation_id in Action order:
      consume amount=1; do not issue any map call yet
  if non-cancel reservation barrier fails:
      persist not-issued records for already applied reservations
      persist terminal Failed; return
  if cancellation during reservation barrier:
      stop later consumes/Artifacts/maps immediately; re-raise; later consumed-without-Artifact replay is indeterminate
  for each non-overlapping wave of at most 3:
      issue typed Search/Details calls
      on cancellation: cancel siblings, gather resource cleanup only, write no new Artifact, re-raise
      on other failure: gather issued siblings; persist each safe observation
      after whole wave settles, determine every Search place admission disposition in Action/result order
      persist each success/no-result/map-failure/unverified-result observation in Action order before later wave
      if any failure: select primary by total order; persist terminal Failed; return
  order observations by Action ordinal
  if any new observation:
      build Judgment prompt; byte gate occurs now
      if too large: keep observations; no judgment semantic consume; persist judgment llm-failure and terminal Failed
      invoke strict JudgmentPatch
      validate handles/business invariants
      persist accepted or controller_rejected model-judgment record
      rejected/failure -> persist terminal Failed; return
  next_state = deterministic_merge(previous_state, observations, patch)
  compute progress, streak, completion, all eight typed gaps and ordered stops before writing, even when this iteration produced no Observation or Judgment
  validate every non-empty gap evidence index against relevant verified Place Observations; allow empty only under authority design §17.2
  if another iteration is legal:
      persist in-progress candidate set with slot candidate_set_operation_id
  else:
      persist completed/incomplete candidate set with terminal_successor_operation_id
      return public outcome reconstructed from fresh terminal Artifact + returned Ref

persist_terminal(record):
  try repository.put(terminal successor)
  except terminal-write failure:
      do not recursively write Failed
      return safe in-memory Failed(persistence_error); same-process singleflight waiters reuse it
      no durable replay promise; later terminal-absent plus any consumed/partial state -> operation_result_indeterminate
```

### 8.3 Search 与 Details

Search 由控制器构造 typed request：destination/anchor 坐标来自可信输入，radius km 转 m，`result_limit=50`。模型永远不能写坐标或 limit。Details 只接受已准入 UID；同 UID NoResult 后本 run 不重试。每次已发出调用均消耗一个 Place 预算。

### 8.4 聚合和判断

先验证所有 Module 06 `provenance.verified`，再读取 Place 字段。false 时不读 Place 字段，写本地 `PlaceUnverifiedResultObservation` 并以 `control_failure/unverified_result` 失败关闭。对 verified Search，先按 Action ordinal、供应商原序和 UID 首次发现顺序计算每个地点的 `admitted_new | existing_uid | not_admitted_candidate_capacity`，并与 places 一一对齐写入 Observation，再按同一顺序合并。核心冲突不覆盖旧事实。判断 patch 只能更新语义 verdict/association；控制器重新推导总体状态、数量、覆盖、Details threshold、八类 pending gaps 和 completion。额外 conflicted 候选隔离后不得计入任何完成目标，也不得仅因其存在阻止 Completed；它导致既有目标不足时只派生该目标对应的现有 typed gap，禁止新增 gap 分支或转成 `artifact_error`。avoid-resolution target 只含指定 group 下仍 ambiguous/unresolved 的当前候选，按 admission ordinal 排列；新候选或 verdict 变化必须重新派生 target。

## 9. Artifact、预算与 replay 安全顺序

每个有外部副作用的槽严格执行 lookup-first。Action ID 同时对应 iteration consume + action Artifact；每个 Place ID 对应 amount=1 consume + Observation；Judgment 只写 Artifact；中间/终态 candidate-set 分别使用 slot/terminal ID。顺序为：

```text
lookup(operation_id)
  found -> 验证 ref/type/version/run/hash/内容 -> replay
  absent -> 校验全部本地条件
          -> 若该槽有业务预算，consume 对应 key/amount
          -> 发出一次外部调用
          -> repository.put 对应 Artifact
          -> 才允许下一副作用
```

`BudgetExceeded` 不调用外部系统。`BudgetConsumed(disposition="replayed")` 只能与既有 Artifact 精确对应；已消费但 Artifact absent 是 `OperationResultIndeterminateError`，禁止重试。候选集只在整轮成功后推进，单个 Observation 可先持久化但不代表 aggregate 已提交。

Place 前缀使用 N 个独立 amount=1 consume 形成外部调用预占屏障；全部完成前零地图调用。Module 04 不提供多 ID 事务，不得伪造“账本原子提交”。仅非取消屏障异常为已经 applied 的记录写 not-issued Observation 后失败关闭；`CancelledError` 立即停止后续 consume/Artifact/map 并原样传播，已经 consumed 但 absent 的槽在未来 replay 返回 indeterminate。terminal record 的 lookup/write 规则和 partial replay 顺序逐字遵守权威设计 §14、§15、§18.4、A.8；terminal put 自身失败使用且只使用 §18.4 last-hop exception，不递归写失败 Artifact。

Artifact 内容写入前必须使用与 Module 04 一致的 canonical JSON 计算 bytes，超过 1 MiB 不调用 `put`、不截断。相同 OperationId 不同 type/version/hash/bytes 是冲突。

## 10. 错误映射与优先级

| 来源 | 输入 | Module 08 输出 |
|---|---|---|
| Module 02 | `LLMInvocationFailure.error` | `llm_failure`，保留安全 code/kind/retry/duplicate/bytes |
| Module 06 | `BaiduMapError` | `map_failure`，保留 code/capability/tool/ordinal/ref |
| Module 06 | `provenance.verified=false` | 本地 `unverified_result` Observation + `control_failure`；零 Place 字段复制 |
| Module 04 Artifact | Repository marker exception | 对应 `control_failure`，不复制 message |
| Module 04 budget/replay | Budget/Replay marker exception | `budget_error`、`replay_mismatch` 或 `operation_result_indeterminate` |
| Module 07 input | selection missing/declined/stale | `invalid_input` 或 `artifact_error`，零外部调用 |
| 未知依赖行为 | 非公共异常或非法类型 | `dependency_contract_error`，失败关闭 |

优先级：取消/进程级传播；replay 不确定；输入/前驱/OperationPlan；Prompt/模型；Artifact/预算；地图；业务 Incomplete。更高项成立后禁止执行更低副作用。

完整全序逐字使用权威设计 §23：先类别，再 action ordinal，再对应 Enum 声明顺序。地图 wave 的普通技术失败不丢弃 sibling 成功 Observation，但 aggregate 保持上一版本；Observation 持久化失败高于 map failure。主失败独自决定顶层 retry/duplicate，不对 sibling 做 OR。候选事实冲突和 NoResult 是业务证据，不映射技术失败。

测试必须形成相邻碰撞矩阵：replay vs input、input vs LLM、LLM vs Artifact、Artifact vs budget、budget vs map、map vs business；再覆盖多个 map ordinal、map vs sibling persistence、多个 persistence ordinal 和同 ordinal code 顺序。

## 11. 安全、取消、并发、容量与缓存

- Prompt、raw model response、reasoning、MCP/百度 payload、Secret、endpoint、traceback、异常文本不得进入 DTO、Artifact、repr、日志、事件或报告；
- 完整 query/tag 只在 model-action Artifact，所有其他位置使用 hash/ref；
- 不写通用 PII/DLP 正则，不声明完全检测敏感信息；
- 单 batch 最多 8，wave 最多 3，wave 不重叠，返回顺序不影响聚合顺序；
- 同 run 相同 terminal ID singleflight；每 run 执行锁串行不同 terminal ID；锁内重查 replay/head/remaining/gap/run state；不同内容冲突，跨 run 完全隔离；
- 128 槽位累计不回收，overflow 按固定顺序处理并在 Search Observation 保存逐地点 disposition；
- Action 指纹缓存贯穿 initial/supplemental，新的 run 清空；
- `CancelledError` 取消 sibling 后只做无持久副作用的资源清理并原样传播，已发出预算不退款；预占期取消也不得补写 not-issued；
- `close_run` 取得 run 锁等待活动调用，调用自有 ModelOperation 的同步 `close()`；`aclose` 幂等收束各 run，二者均不关闭注入资源。

## 12. 测试实现原则

- 默认全离线；所有模型、地图、Artifact、预算、Clock、UUID/Hasher 使用严格 fake；
- fake 必须能脚本化 success/no-result/typed failure/cancel/并发阻塞/重复/不确定结果；
- 不 mock 私有 Module 06 Gateway 或供应商 payload；fake 实现公共 Protocol；
- 不使用真实 DeepSeek、百度、MCP、DNS、数据库或 `.env`；
- 禁止 skip、xfail、rerun、降低断言、捕获过宽异常或通过 sleep 猜并发顺序；
- 属性测试只用于纯指纹/排序/容量不变量，不替代明确边界样例；
- 任何安全断言同时扫描 outcome、Artifact JSON、repr 和 fake sink 记录。

## 13. 完整测试矩阵

### 13.1 公共与 DTO

- 精确 `__all__`、Protocol signatures、Artifact registrations；
- 禁止私有/供应商/Module 09+ import；
- 每个 DTO strict/frozen/extra、字段顺序、unknown/null/default、tuple 和最大最小容量；
- 所有 discriminator 正向与未知分支负向；supplemental/summary/Artifact-local gap union 精确八分支；
- summary evidence indexes 覆盖 0/1/8、9 拒绝及上下文相关/无关证据；supplemental evidence refs 覆盖 1/8、0/9 拒绝，证明两者不得混用；
- Python strict 与 JSON round-trip；`model_construct`/mutation 注入被拒绝；fresh read 不共享嵌套引用。

### 13.2 Prompt、模型与 Action

- 两 Prompt bytes/hash 正向，改一 byte 失败；不在代码重复 Prompt；
- canonical dynamic JSON、null 保留、不可信数据隔离；
- 完整 request 恰好 262144 通过、262145 失败；
- Action/Judgment success/failure、provider attempts、usage 累计；
- semantic failure code 到消费计数、known attempts/usage 与两个 complete 标记；不得读 ModelOperation 私有计数；
- 1 和 8 Action 边界，0/9 失败；ordinal、objective、tool、anchor、radius、UID、坐标/limit 越权；
- strict invalid、unknown tool、schema invalid 零 JSON repair、零 Place 调用并技术 Failed；合同合法但忽略最高优先级目标写 policy-rejected、Place=0、progress=false，可继续且两轮触发 no-progress Incomplete。

### 13.3 Search、Details、UID 与判断

- city/nearby、hotel/candidate anchor、1/3/5/10 km、固定 limit 50；
- query/tag 仅首尾 strip，Unicode/case/内部空白不同指纹；
- objective/meal 不进指纹；重复 Action 不调用/不预算/不进展；
- requirement item/group、objective、gap semantic、request digest 的 canonical tree、digest16 碰撞和句柄跨 Prompt 负向；avoid gap 的 group/item/candidate target 稳定指纹、target 增删变化和 verdict/evidence 不入指纹；
- Search success/no-result、Details success/no-result、verified false 本地 Observation/零字段复制/控制失败、typed map errors；
- 同 UID 去重、多 role、多 interest/meal/must；核心字段相同和各字段冲突；
- RoleVerdict 和总体状态四级优先；provider category 反驳、category null、名称可支持；
- Details `ceil(n/3)` 边界、相同 UID 只计一次、NoResult 不重试。

### 13.4 must/avoid、覆盖与容量

- 0/1/100/101 条输入边界由 Module 01/08 正确处理；保持重复与索引；
- 完全同原文合组、大小写/空白不同不合组、must/avoid 不跨组；
- 四态 default + exceptions + evaluated scope + evidence handle 验证；
- 一条多个 UID、同 UID must/avoid 冲突、未解决 must 不阻普通 eligible；avoid-resolution group/item/candidate target 精确、candidate admission 顺序、no-match/confirmed 满足与 ambiguous/unresolved 未满足、新候选触发新 target；
- 每兴趣及 lunch/dinner 双门槛；NoResult 只计尝试；business hours unknown 可入研究覆盖；
- 127/128/overflow、不回收、准入固定顺序、容量满仍可 Details；单 Search 与同 wave 多 Search 跨越 128 时，places 与逐地点 disposition 等长、ordinal/UID 对齐且顺序确定。

### 13.5 预算、状态机与停止

- iteration 0..6、Place 0..48、semantic 0..12；initial/supplemental 共享；
- iteration 在 Action 前、semantic 在 Module 02 byte Gate 后、Place 在 map 前；
- prefix 的逐 Place `amount=1` 预占屏障、非取消屏障失败 not-issued、取消恰在第 N 个 consume 时零后续 consume/Artifact/map 且未来 replay indeterminate、issued 失败/取消不退款、duplicate/replay 不消费；
- 每轮最多 Action+Judgment，no Observation 不 Judgment；
- progress 每个正项及所有非进展项；连续两轮 no progress；
- NoUsefulAction 三个允许条件及非法建议；
- Completed 每个必要条件单独缺失；avoid unresolved 必须阻止 Completed；额外且未参与任何目标的 conflicted 候选被隔离后仍可 Completed，冲突导致既有目标不足时派生对应 typed gap；多 stop reasons 顺序；Incomplete eligible>0/0 下游 Gate；零 Observation 的 policy/no-useful/no-legal-action 路径仍派生业务 gap 与 `Incomplete`，相关 Observation 不存在时 summary evidence 为空且不转 Failed。

### 13.6 Artifact、replay 与 supplemental

- 四注册、type/version、Ref run/OperationId、1 MiB 前后边界；
- action 唯一保存 query/tag，Observation 完整结果，candidate set 不复制数组/details，并校验顶层 Ref 目录及全部整数索引；
- exact replay、关闭后 replay、absent、same-id different content、consumed/no artifact、partial iteration；
- terminal successor 作为入口/singleflight key，in-progress 与 completed/incomplete/failed record 全分支逐字段 replay；terminal put 确定失败/结果不确定、same-process waiter 复用内存 Failed、later terminal-absent retry indeterminate；调用内 slot 与 run iteration index 不混淆；
- Observation 已写但 judgment/candidate-set 失败时 aggregate 不推进；
- 八 gap union、1/128/129、顺序、semantic duplicate、全部已满足 no-op successor、部分剩余；
- same fingerprint 一次、实质变化新 fingerprint、avoid candidate target 变化新 fingerprint、replacement 不返回自身；
- public/Artifact summary 的空 evidence 只在无相关 verified Observation 时允许；有相关 Observation 必须 1..8；terminal replay 重验上下文；空证据 summary 不能构造 supplemental request。

### 13.7 并发、失败、安全与生命周期

- 8 Action 按 3/3/2 wave；最大在途 3；wave 不重叠；乱序返回仍按 ordinal；
- wave 一个失败时 sibling success 被持久化、无下一 wave/judgment/aggregate；
- cancellation 取消 sibling、无低优先级持久副作用、原异常传播；
- singleflight 多 waiter 一次执行；different payload conflict；同 run 不同 terminal ID 同 predecessor 必须串行且第二个锁内重查失败关闭；跨 run 独立；
- LLM/map/control 每个 code 映射、retry/duplicate 一致、未知依赖失败关闭；
- 同 wave 多失败全序、ordinal/code 声明顺序及 persistence-over-map 碰撞；
- 正向断言完整 query/tag 存在且仅存在于 model-action Artifact；分别对 Observation、candidate-set、outcome、repr、日志、事件和报告做 query/tag 负向扫描；Prompt/raw/reasoning/payload/Secret 在所有持久化与可观测位置均做负向扫描；
- close_run 两次、close 与活动调用竞态必须等待、close 后 replay、非 replay run missing、operation 同步 close、aclose、不得关闭注入 runtime/adapter/repository/budget。

## 14. 阶段 08-1：公共 DTO、错误与 Artifact

### 14.1 目标

建立无外部调用的公共合同层：所有 Enum/DTO/union、OperationPlan、gap、错误、Protocol、四类 Artifact、registrations 和精确包根导出。

### 14.2 文件白名单

```text
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
app/candidate_research/errors.py
app/candidate_research/protocols.py
tests/unit/candidate_research/test_models.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_artifact_registration.py
```

### 14.3 验收

- §6 公共面精确；
- 所有 strict/frozen/extra、字段顺序、capacity、discriminator 和 validators 正反覆盖；
- OperationId run/phase/唯一性和槽位边界；
- Artifact-local snapshot 与 Module 04 Registry 类型图逐条兼容；禁止直接嵌入 Module 02/06 BaseModel；
- Artifact 内容白名单、Ref 关系、四类 registrations、terminal record 全分支、JSON round-trip 和 fresh copy；
- supplemental/summary/Artifact-local gap union 精确八分支；summary evidence 0..8 的结构与上下文校验、supplemental evidence 1..8 的输入边界分别覆盖；
- 不导入 service、Prompt、地图私有面或 Module 09+。

## 15. 阶段 08-2：Prompt、指纹与确定性 policy

### 15.1 目标

实现两个 hash 锁定 Prompt、规范动态 JSON、Action/Judgment schema 的纯校验，以及 objective、指纹、候选状态、容量、完成、进展和停止纯函数。

### 15.2 文件白名单

```text
app/candidate_research/prompts/action_v1.txt
app/candidate_research/prompts/observation_v1.txt
app/candidate_research/prompt_loader.py
app/candidate_research/prompting.py
app/candidate_research/fingerprints.py
app/candidate_research/policy.py
app/candidate_research/judgments.py
tests/support/candidate_research_fakes.py
tests/unit/candidate_research/test_prompt_loader.py
tests/unit/candidate_research/test_fingerprints.py
tests/unit/candidate_research/test_policy.py
tests/unit/candidate_research/test_judgments.py
tests/contract/candidate_research/test_import_boundaries.py
```

如 08-2 揭示 08-1 的小型 schema 缺陷，只有在联合授权明确列出 §14.2 对应生产文件时才能修复；公共语义变化必须停止。

### 15.3 验收

- Prompt 改一 byte 即失败；动态 JSON 规范且 null 不丢；不可信数据边界清楚；
- Action 1..8 与 JudgmentPatch strict 判别合同；
- Search/Details 指纹逐字段正反测试；
- avoid-resolution semantic fingerprint 绑定 group/item/candidate target，target 变化必须产生新 fingerprint；
- objective 固定优先级、状态四级优先、128 overflow、目标公式、Details ceil；
- must/avoid 压缩组、兴趣/餐次双门槛、进展和多停止原因；
- 无模型、地图、Artifact Repository、预算或网络调用。

### 15.4 `08-1 + 08-2` 联合 Gate

前置条件：正式提示词写明用户批准的两份文档 SHA-256、当前分支/开始 HEAD/subject，工作树除该组批准文件外干净、staged 为空，上游 acceptance gate 通过。联合白名单精确为：

```text
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
app/candidate_research/errors.py
app/candidate_research/protocols.py
app/candidate_research/prompts/action_v1.txt
app/candidate_research/prompts/observation_v1.txt
app/candidate_research/prompt_loader.py
app/candidate_research/prompting.py
app/candidate_research/fingerprints.py
app/candidate_research/policy.py
app/candidate_research/judgments.py
tests/support/candidate_research_fakes.py
tests/unit/candidate_research/test_models.py
tests/unit/candidate_research/test_prompt_loader.py
tests/unit/candidate_research/test_fingerprints.py
tests/unit/candidate_research/test_policy.py
tests/unit/candidate_research/test_judgments.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_artifact_registration.py
tests/contract/candidate_research/test_import_boundaries.py
```

允许：按 08-1 后 08-2 实现、白名单内小修、从当前源码 build test target、在隔离容器执行本节 Gate。禁止：service、上游、文档、依赖、配置、网络/live、stage/commit/merge/push/PR/deploy。最后统一执行；下列 `python` 行全部是安全 Docker 容器内命令，禁止在宿主机把结果当正式证据：

```text
python -m pytest -q -p no:cacheprovider tests/unit/candidate_research/test_models.py tests/unit/candidate_research/test_prompt_loader.py tests/unit/candidate_research/test_fingerprints.py tests/unit/candidate_research/test_policy.py tests/unit/candidate_research/test_judgments.py tests/contract/candidate_research/test_public_api.py tests/contract/candidate_research/test_artifact_registration.py tests/contract/candidate_research/test_import_boundaries.py
python -m pytest -q -p no:cacheprovider tests/contract/llm_runtime/test_provider_protocol.py tests/contract/workflow_contracts/test_public_api.py tests/contract/baidu_map/test_public_api.py tests/contract/hotel_selection/test_public_api.py
python -m compileall -q app/candidate_research tests/unit/candidate_research tests/contract/candidate_research
```

正式提示词把三行分别放进以下同安全参数的 `docker run`，镜像从当前源码新建：

```powershell
$module08DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module08-phase12-docker"
$module08TestImage = "china-travel-agent:module08-phase12-test"
docker --config $module08DockerConfig buildx build --load --progress plain --tag $module08TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/unit/candidate_research/test_models.py tests/unit/candidate_research/test_prompt_loader.py tests/unit/candidate_research/test_fingerprints.py tests/unit/candidate_research/test_policy.py tests/unit/candidate_research/test_judgments.py tests/contract/candidate_research/test_public_api.py tests/contract/candidate_research/test_artifact_registration.py tests/contract/candidate_research/test_import_boundaries.py
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/contract/llm_runtime/test_provider_protocol.py tests/contract/workflow_contracts/test_public_api.py tests/contract/baidu_map/test_public_api.py tests/contract/hotel_selection/test_public_api.py
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m compileall -q app/candidate_research tests/unit/candidate_research tests/contract/candidate_research
```

该组不实现 service，不调用 live，不修改上游。PASS 状态：

```text
MODULE08_PHASES_01_02_COMPLETE_AWAITING_03_04_AUTHORIZATION
```

## 16. 阶段 08-3：Initial 受控循环与地图执行

### 16.1 目标

实现 initial 前置验证、酒店 Artifact 绑定、run context、Action 模型调用、批次校验、Place 预算、3 并发 wave、Observation 持久化、Judgment 调用和 candidate-set 提交。

### 16.2 文件白名单

```text
app/candidate_research/service.py
app/candidate_research/__init__.py
tests/support/candidate_research_fakes.py
tests/unit/candidate_research/test_service_initial.py
tests/unit/candidate_research/test_service_failures.py
tests/unit/candidate_research/test_service_concurrency.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_upstream_integration.py
tests/contract/candidate_research/test_replay_budget_contract.py
```

### 16.3 Fake 要求

support fake 对 Module 04/06 实现公共 Protocol；模型侧实现 Module 08 的私有最小 runtime/operation 消费 Protocol，或用真实 `DeepSeekRuntime + ProviderAdapter fake` 做上游集成。两种方式都必须返回真实 Module 02 typed outcome，精确记录调用顺序、OperationId、budget、最大在途数和取消；不得用 `Any`、认识 MCP payload、供应商 SDK 或私有 adapter。

### 16.4 验收

- requirement/hotel/ref/plan 任一非法时零模型、地图和预算副作用；
- Action byte Gate、iteration/semantic/Place 消费顺序；
- Search/Details typed request 精确，limit/anchor/radius 由控制器注入；
- 1、3、4、8 Action wave 和乱序返回；
- 同 wave 全部结果收束后按固定顺序计算 Search 逐地点容量 disposition，并在启动下一 wave 前按 Action 顺序持久化；
- verified=false 使用本地 Observation 且不复制任何 Place 字段；
- wave 普通失败收束 sibling；预占和 wave 取消都不产生取消后的新持久副作用并原样传播；
- Action 合同无效技术 Failed；最高目标 policy-rejected 进入业务进展/停止计算；
- Judgment 只在有新 Observation 时执行；aggregate 只在整轮成功后推进；
- Completed/Incomplete/Failed 公共结果不复制候选表。

## 17. 阶段 08-4：Supplemental、replay、singleflight 与生命周期

### 17.1 目标

补全 supplemental 八 gap、共享预算/context、no-op successor、replacement、快速/部分 replay、OperationId 不确定、terminal-ID singleflight、run 执行锁、terminal-write last-hop、`close_run` 和 `aclose`。

### 17.2 文件白名单

```text
app/candidate_research/service.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
tests/support/candidate_research_fakes.py
tests/unit/candidate_research/test_service_supplemental.py
tests/unit/candidate_research/test_service_failures.py
tests/unit/candidate_research/test_service_concurrency.py
tests/contract/candidate_research/test_replay_budget_contract.py
tests/contract/candidate_research/test_upstream_integration.py
```

`models.py`、`artifacts.py` 只允许修复已冻结合同的小型实现问题；需要改变字段或版本立即停止。

### 17.3 验收

- supplemental 不重复 requirement/hotel；前驱和 run digest 验证；
- gaps 八分支、有序、1..128、semantic unique；已满足 no-op、部分剩余、同指纹一次；avoid target 精确并随候选范围变化；
- initial/supplemental 共享 6/48/12、指纹和 128 槽位；
- replay lookup 先于 run context，close 后终态/失败 replay；
- consumed absent、partial iteration、same ID different content 全部 fail-closed；
- singleflight 同内容只执行一次，不同内容冲突；同 run 不同 terminal ID 串行且锁内重校验；
- terminal put 失败不递归写入；same-process waiter 一致，later retry 不重发外部调用；
- replacement 追加且不返回自身；close_run 等待活动执行并同步 close operation；aclose 资源所有权正确。
- 无相关 Place Observation 的业务 gap 可写 0 evidence summary，但不能转 supplemental；存在相关 Observation 时必须保存 1..8 indexes，replay 重新校验。

### 17.4 `08-3 + 08-4` 联合 Gate

前置状态必须精确为 `MODULE08_PHASES_01_02_COMPLETE_AWAITING_03_04_AUTHORIZATION`，08-1+08-2 Gate 已通过；正式提示词还必须写明批准文档 hash、分支/开始 HEAD/subject、白名单内既有 diff 和 staged 为空。联合白名单精确为：

```text
app/candidate_research/service.py
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
tests/support/candidate_research_fakes.py
tests/unit/candidate_research/test_service_initial.py
tests/unit/candidate_research/test_service_supplemental.py
tests/unit/candidate_research/test_service_failures.py
tests/unit/candidate_research/test_service_concurrency.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_upstream_integration.py
tests/contract/candidate_research/test_replay_budget_contract.py
```

允许：实现 initial/supplemental/service、白名单内小修、build test target、隔离容器 Gate。禁止：改 Prompt/policy/judgment 公共语义、上游、文档、依赖、配置、网络/live 或 Git 外部操作。下列 `python` 行全是容器内命令：

统一执行：

```text
python -m pytest -q -p no:cacheprovider tests/unit/candidate_research tests/contract/candidate_research/test_artifact_registration.py tests/contract/candidate_research/test_public_api.py tests/contract/candidate_research/test_import_boundaries.py tests/contract/candidate_research/test_upstream_integration.py tests/contract/candidate_research/test_replay_budget_contract.py
python -m pytest -q -p no:cacheprovider tests/contract/llm_runtime/test_provider_protocol.py tests/contract/workflow_contracts/test_artifact_repository_contract.py tests/contract/workflow_contracts/test_execution_budget_manager_contract.py tests/contract/baidu_map/test_adapter_contract.py tests/contract/hotel_selection/test_upstream_integration.py
python -m compileall -q app/candidate_research tests/unit/candidate_research tests/contract/candidate_research
```

正式提示词使用：

```powershell
$module08DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module08-phase34-docker"
$module08TestImage = "china-travel-agent:module08-phase34-test"
docker --config $module08DockerConfig buildx build --load --progress plain --tag $module08TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/unit/candidate_research tests/contract/candidate_research/test_artifact_registration.py tests/contract/candidate_research/test_public_api.py tests/contract/candidate_research/test_import_boundaries.py tests/contract/candidate_research/test_upstream_integration.py tests/contract/candidate_research/test_replay_budget_contract.py
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/contract/llm_runtime/test_provider_protocol.py tests/contract/workflow_contracts/test_artifact_repository_contract.py tests/contract/workflow_contracts/test_execution_budget_manager_contract.py tests/contract/baidu_map/test_adapter_contract.py tests/contract/hotel_selection/test_upstream_integration.py
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m compileall -q app/candidate_research tests/unit/candidate_research tests/contract/candidate_research
```

PASS 状态：

```text
MODULE08_PHASES_03_04_COMPLETE_AWAITING_05_06_AUTHORIZATION
```

### 17.5 发布前设计勘误回填与 Phase34 修正门禁

`MODULE08-DESIGN-ERRATA-01` 在 08-3+08-4 独立审查后改变了尚未发布的冻结合同。进入 08-5 前必须另行取得修复授权，按修订后两份文档的新 SHA-256 生成完整 `MODULE08-PHASE34-INDEPENDENT-REVIEW-FIX-2` 提示词；不得把旧 Phase34 Gate 当作新合同已经通过。修复提示词至少覆盖以下勘误直接影响文件，只有独立审查的既有 P0/P1/P2 证据能支持增加其他文件：

```text
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
app/candidate_research/fingerprints.py
app/candidate_research/policy.py
app/candidate_research/service.py
tests/support/candidate_research_fakes.py
tests/unit/candidate_research/test_models.py
tests/unit/candidate_research/test_fingerprints.py
tests/unit/candidate_research/test_policy.py
tests/unit/candidate_research/test_service_initial.py
tests/unit/candidate_research/test_service_supplemental.py
tests/unit/candidate_research/test_service_failures.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_artifact_registration.py
tests/contract/candidate_research/test_upstream_integration.py
tests/contract/candidate_research/test_replay_budget_contract.py
```

该修正必须：

- 在公共、Artifact-local、policy、service 和 replay 全链路落实 summary evidence 0..8 的上下文规则，同时保持 supplemental/Judgment evidence 1..8；
- 实现 `AvoidResolutionMissingGap`、两个 summary 分支、八分支 union、公共 API 投影、Artifact validator、指纹、objective、satisfied/no-op/completion 语义；
- 为零 Observation 的 policy-rejected、NoUsefulAction、no-legal-action、no-progress/预算或迭代停止路径补充 fresh JSON 正反测试；
- 为 avoid group/item/candidate target、一致性、顺序、target 变化、证据和 replay 补充 fresh JSON 正反测试；
- 保持 schema/version、四类 Artifact type 和 Prompt version/hash 不变，不创建 migration；
- 运行修复定向测试、完整 Module 08 Gate、上游 acceptance Gate 和 compileall；完整 coverage 按 `D-08-32` 延至全部代码实现和独立代码审查完成后执行；
- 完成后再次独立复审，P0/P1/P2 清零前不得进入 08-5/08-6。

修复实现报告通过时只可使用：

```text
MODULE08_PHASE34_REVIEW_FIX_2_COMPLETE_AWAITING_INDEPENDENT_REREVIEW
```

独立复审逐条确认修订后设计且 P0/P1/P2 清零后，才可形成 08-5/08-6 的前置状态：

```text
MODULE08_PHASE34_REREVIEW_PASSED_AWAITING_05_06_AUTHORIZATION
```

## 18. 阶段 08-5：安全、边界与 coverage harness

### 18.1 目标

补齐安全泄露、错误相邻碰撞、Artifact 容量、取消、import boundary、八分支/空证据边界和 line/branch/combined 单进程 coverage harness。

### 18.2 文件白名单

```text
tests/support/run_module08_coverage.py
tests/contract/candidate_research/test_security_contract.py
tests/contract/candidate_research/test_import_boundaries.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_artifact_registration.py
tests/unit/candidate_research/test_service_failures.py
tests/unit/candidate_research/test_service_concurrency.py
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
app/candidate_research/errors.py
app/candidate_research/protocols.py
app/candidate_research/prompt_loader.py
app/candidate_research/prompting.py
app/candidate_research/fingerprints.py
app/candidate_research/policy.py
app/candidate_research/judgments.py
app/candidate_research/service.py
```

生产文件只允许修复本阶段测试揭示且完全位于权威设计内的小问题。

### 18.3 coverage harness

`tests/support/run_module08_coverage.py` 必须在一个 Python 进程内：

1. 使用 coverage API，`branch=True`，source 精确为 `app.candidate_research`；
2. 通过 pytest API 一次执行 `tests/unit/candidate_research` 和 `tests/contract/candidate_research`；
3. JSON 写到容器 `/tmp/module08-coverage.json`；
4. 用未舍入分子/分母分别判断 line、branch、combined 均不低于 70.00%；
5. `num_statements=0` 失败；`num_branches=0` 时 branch=100%；combined 分母 0 失败；
6. pytest 非零、无测试收集、skip/xfail/rerun 或任一 coverage 不足均非零退出；
7. 不重跑、不改源码、不联网、不吞参数。

08-5/08-6 只通过 `test_coverage_gate.py` 验证上述纯函数、70% 门槛、单次 pytest 编排和失败关闭，不执行完整 coverage 采集。完整采集必须等全部代码实现和独立代码审查完成后、最后的 MODULE-CLOSEOUT 开始前，使用代码最终版和新镜像执行一次；closeout 只能在生产代码和测试哈希保持不变时引用该结果。

公式：

```text
line = 100 * covered_lines / num_statements
branch = 100 * covered_branches / num_branches
combined = 100 * (covered_lines + covered_branches)
           / (num_statements + num_branches)
```

## 19. 阶段 08-6：模块 Gate 与独立审查准备

### 19.1 目标

在不执行 MODULE-CLOSEOUT 和完整 coverage 采集的情况下，完成 coverage/security 合同、Module 08 行为 Gate、互补完整回归和独立代码审查输入。最终 70% coverage 结果留到独立代码审查通过后、MODULE-CLOSEOUT 开始前的代码最终版单次采集。

### 19.2 文件白名单

原则上无新增生产语义。08-5+08-6 联合白名单精确为：

```text
app/candidate_research/__init__.py
app/candidate_research/models.py
app/candidate_research/artifacts.py
app/candidate_research/errors.py
app/candidate_research/protocols.py
app/candidate_research/prompt_loader.py
app/candidate_research/prompting.py
app/candidate_research/fingerprints.py
app/candidate_research/policy.py
app/candidate_research/judgments.py
app/candidate_research/service.py
tests/support/candidate_research_fakes.py
tests/support/run_module08_coverage.py
tests/unit/candidate_research/test_models.py
tests/unit/candidate_research/test_prompt_loader.py
tests/unit/candidate_research/test_fingerprints.py
tests/unit/candidate_research/test_policy.py
tests/unit/candidate_research/test_judgments.py
tests/unit/candidate_research/test_service_initial.py
tests/unit/candidate_research/test_service_supplemental.py
tests/unit/candidate_research/test_service_failures.py
tests/unit/candidate_research/test_service_concurrency.py
tests/contract/candidate_research/test_public_api.py
tests/contract/candidate_research/test_import_boundaries.py
tests/contract/candidate_research/test_artifact_registration.py
tests/contract/candidate_research/test_upstream_integration.py
tests/contract/candidate_research/test_replay_budget_contract.py
tests/contract/candidate_research/test_security_contract.py
tests/contract/candidate_research/test_coverage_gate.py
```

生产文件只允许按已冻结合同修复本组测试揭示的小问题；禁止 Prompt 文本、README、IMPLEMENTATION_STATUS、Dockerfile、上游、依赖和配置。需要改这些文件时 STOP。

### 19.3 `08-5 + 08-6` 联合 Gate

前置状态必须为 `MODULE08_PHASE34_REREVIEW_PASSED_AWAITING_05_06_AUTHORIZATION`；正式提示词写明勘误后两份批准文档的新 hash、分支/开始 HEAD/subject、现有白名单 diff、staged 为空、前两组 Gate 及 Phase34 Fix-2 独立复审证据。允许操作只有 §19.2 白名单小修、coverage/security 测试、当前源码 Docker build 和本节离线 Gate；禁止 closeout、网络/live、stage/commit/merge/push/PR/deploy。

从当前源码构建 test target，在新容器内使用 `--rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true`，不传 env file 或 Secret。固定执行一次：

```text
python -m pytest -q -p no:cacheprovider tests/unit/candidate_research tests/contract/candidate_research
python -m pytest -q -p no:cacheprovider tests/unit tests/contract --ignore=tests/unit/candidate_research --ignore=tests/contract/candidate_research
python -m compileall -q app tests
```

正式提示词使用以下完整外层命令，不得以宿主机执行替代：

```powershell
$module08DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module08-phase56-docker"
$module08TestImage = "china-travel-agent:module08-phase56-test"
docker --config $module08DockerConfig buildx build --load --progress plain --tag $module08TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/unit/candidate_research tests/contract/candidate_research
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/unit tests/contract --ignore=tests/unit/candidate_research --ignore=tests/contract/candidate_research
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m compileall -q app tests
```

前两项收集集合的并集必须精确等于项目完整 unit/contract，交集为空；collect 均非零。再检查 diff、whitespace、pycache、白名单、staged、异常文件和容器残留。

本组行为、安全和 harness 合同 PASS 后进入独立只读代码审查；coverage 必须报告 `NOT_RUN — 按 D-08-32 延至独立代码审查通过后、MODULE-CLOSEOUT 前的代码最终版单次采集`。状态为：

```text
MODULE08_IMPLEMENTATION_GATES_PASSED_AWAITING_INDEPENDENT_REVIEW
```

P0、P1 和高概率影响主演示的 P2 清零后才可请求 Demo closeout 授权。极低概率、纯防御性或不影响主演示的 P2/P3 可登记为 `DEFERRED_DEMO_NONBLOCKING`，必须写明概率、影响、延期理由和发布加固回收位置；安全泄露、数据破坏、主演示崩溃和公共合同破坏不得延期。

## 20. Docker 构建与安全容器

未来实施提示词必须按当时批准的 Docker 工作流展开完整命令，使用任务专属临时 Docker config，不复用或改写 `HOME`。等价 PowerShell 骨架：

```powershell
$module08DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module08-docker"
$module08TestImage = "china-travel-agent:module08-test"
docker --config $module08DockerConfig buildx build --load --progress plain --tag $module08TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
docker --config $module08DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module08TestImage python -m pytest -q -p no:cacheprovider tests/unit/candidate_research tests/contract/candidate_research
```

互补回归和 compileall 必须在从同一最终源码构建的新 test image 中运行。不得注入 `DEEPSEEK_API_KEY`、`BAIDU_MAPS_API_KEY` 或其他 Secret；不得读取 `.env`、启动 MCP/DNS/PostgreSQL/live、复用偶然容器状态或把宿主机测试冒充正式 Gate。

镜像和命令名称可由对应阶段提示词按实际基线锁定，但安全选项、网络隔离和测试集合语义不得降低。

## 21. 独立代码审查

模块 Gate 通过后，必须由与实施者不同的只读 Reviewer 逐条检查：

- 两份 Module 08 文档，而不只是测试结果；
- 公共 API、strict DTO、四类 Artifact 和版本；
- 01/02/04/06/07 边界与 import boundary；
- 受控 Action/Judgment、工具白名单和 Prompt hash；
- 6/48/12、3 并发、8 Action、128 容量；
- UID、多角色、must/avoid（含 avoid-resolution typed target）、兴趣/餐次、Details 和停止条件；
- 八分支 supplemental/summary union、空 evidence summary 的严格上下文及禁止转 supplemental；
- Artifact/预算/OperationId/replay/partial iteration；
- initial/supplemental/Incomplete 下游合同；
- LLM/map/control failure、错误优先级和取消；
- query/tag 保存位置及所有禁止内容；
- coverage harness 算法、70% 门槛、最终单次采集时点、Docker 证据和文件白名单。

审查格式：

```text
文件：
章节或行号：
等级：P0 / P1 / P2 / P3
对应设计条款：
问题：
后果：
最小修复建议：
```

Reviewer 只读，不改文件。Demo 审查阻断 P0、P1 和高概率影响主演示的 P2；低概率且不涉及安全泄露、数据破坏、主演示崩溃或公共合同破坏的 P2/P3 可按 `D-08-32` 登记为 `DEFERRED_DEMO_NONBLOCKING`。阻断项未清零不得进入 closeout；修复后由原 Reviewer 复核。

## 22. 修复规则

- 当前阶段白名单内的小范围明确缺陷可直接修复；
- 修复后运行失败项、直接受影响项和冻结 Gate，不用无意义重复全部命令；
- 不得通过弱化断言、skip、xfail、rerun、宽泛捕获、排除源码、伪造分母、增加重试或扩大容差来“修复”；coverage 门槛固定按 `D-08-34` 的 70%；
- 改公共 API、schema/version、预算、工具白名单、候选资格、依赖或仓库范围时立即停止并请求用户决定；
- 设计勘误后的实现修复必须引用两份文档的新 hash；旧 hash、旧七分支 Gate 或旧 `evidence_indexes[1..8]` 结果不得作为 08-5 前置证据；
- Reviewer 不修改代码；实施者修复，原 Reviewer 复核。

## 23. PASS、STOP 与 NOT_RUN

### 23.1 阶段 PASS

只有下列全部成立才可报告一组阶段完成：

- 两个相邻阶段目标和前置条件全部满足；
- 变更精确位于联合白名单，staged 为空；
- 对应定向、合同、上游和阶段 Gate 通过；
- 无 skip/xfail/rerun；
- 逐条给出权威设计证据，而非只说测试通过；
- 无 commit、merge、push、PR、deploy；
- 报告含开始/结束 HEAD、全部修改文件、migration、异常文件和下一状态。

### 23.2 STOP

出现任一项立即停止：

- baseline/hash/worktree 或 Prompt hash 漂移；
- 上游合同与权威设计冲突；
- 需要修改 Module 01、02、04、06、07；
- 需要新增依赖、migration、配置、MCP/HTTP/SDK 私有访问或 Module 09+ 依赖；
- 文件白名单不足或出现异常文件；
- Artifact replay 会重复调用、OperationId 结果不确定或 aggregate 原子语义无法保证；
- MODULE-CLOSEOUT 前的代码最终版单次采集无法达到 70% line/branch/combined；08-5/08-6 不得因尚未执行最终采集而误报此 STOP；
- Demo 审查的 P0、P1 或高概率主演示 P2 未清零；
- staged 非空或将要进行未授权 Git/外部操作；
- 当前工作需要改变已确认设计或大改。

固定状态按事实使用：

```text
BLOCKED_BASELINE_DRIFT
BLOCKED_UPSTREAM_CONTRACT_CONFLICT
BLOCKED_SCOPE_EXPANSION_REQUIRED
BLOCKED_REPLAY_SAFETY
BLOCKED_TEST_GATE
BLOCKED_REVIEW_FINDINGS
AWAITING_NEXT_STAGE_AUTHORIZATION
AWAITING_CLOSEOUT_AUTHORIZATION
AWAITING_COMMIT_AUTHORIZATION
```

未执行的非适用或未授权动作必须写 `NOT_RUN — 原因`，不能写 PASS。

## 24. 实施阶段报告模板

每次阶段报告至少包含：

```text
任务：MODULE08-IMPLEMENTATION
阶段：
最终状态：

Git：
- 分支：
- 开始 HEAD：
- 结束 HEAD：
- staged：
- tracked：
- untracked：

前置条件：
- 权威设计及 hash：
- Vibe 执行契约及 hash：
- 共享实施规则：
- 上游合同：
- 基线漂移：

修改：
- 生产文件：
- Prompt 文件：
- 测试文件：
- 白名单外文件：
- 依赖：
- migration：

逐条设计对照：
- 当前阶段目标：
- 公共合同：
- ReAct/工具白名单：
- 候选/UID/must/avoid：
- Artifact/预算/replay：
- supplemental/下游：
- 错误/取消/并发：
- 安全：

测试：
- 定向测试：
- 合同测试：
- 上游回归：
- 完整离线回归：
- line：
- branch：
- combined：
- skip/xfail/rerun：
- Docker：
- compileall：

异常：
- 失败与修复：
- 异常文件：
- 未决问题：
- STOP 条件：

外部操作：
- commit：
- merge：
- push：
- PR：
- deploy：

下一步：
```

Module 08 无数据库 migration 时必须写 `NOT_RUN — Module 08 无数据库迁移`。未授权操作写 `NOT_AUTHORIZED / NOT_RUN`。

## 25. MODULE-CLOSEOUT

MODULE-CLOSEOUT 是全部代码、测试和独立代码审查通过后的独立阶段，必须取得新的明确授权。它不是 08-6 的一部分。

### 25.1 closeout 白名单

```text
README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile
```

如果真实影响需要其他文件，STOP 并申请扩大；不得在 closeout 顺手改生产代码或测试，发现代码缺陷返回相应阶段。

### 25.2 内容

1. README 只按真实事实同步 Module 08 状态与架构，不复制易变测试数到冻结设计；
2. IMPLEMENTATION_STATUS 记录实施、测试、审查和 Git 事实；
3. 检查 Dockerfile `COPY app ./app` 是否覆盖新包，并把 runtime 默认 import 自检诚实扩展到 Module 08；
4. test target 继续包含全部 tests/support；
5. 无新增依赖时不得修改 pyproject/lock；
6. 不新增 live、Secret、Provider Discovery、数据库或 MCP 配置。

### 25.3 同步后重建与回归

必须从 closeout 最终源码重新构建 runtime/test 镜像，并重新执行：

1. runtime 默认 CMD；
2. 核验紧邻 closeout 前的代码最终版 coverage 命令、镜像 ID、生产/测试哈希、70% Gate 及延期项状态；不得再次执行 coverage；
3. 排除 Module 08 目录的互补完整 unit/contract；
4. 两集合并集完整、交集为空；
5. `python -m compileall -q app tests`；
6. 证明 Module 08 生产代码和测试相对 coverage 基线逐字节未变；
7. diff、whitespace、pycache、白名单、staged 和容器残留检查；
8. closeout 独立只读审查。

closeout 前的旧镜像和普通测试不能替代同步后的重建证据；唯一例外是按 `D-08-32` 紧邻 closeout 前执行的代码最终版 coverage，前提是 closeout 未修改任何 Module 08 生产代码或测试。

### 25.4 closeout 报告

```text
分支：
开始 HEAD：
结束 HEAD：
修改文件：
migration：
依赖：
README：修改内容或基于实际行的无需修改证据
IMPLEMENTATION_STATUS：写入的模块状态、测试与审查快照
Dockerfile COPY：实际行及影响判断
Dockerfile runtime CMD：实际行及 Module 08 import 结果
Dockerfile test target：实际行及 tests/support 覆盖判断
runtime image：
test image：
同步后重新构建：是/否；build 完整命令、exit code、结果
runtime 默认 CMD：完整命令、exit code、结果
Module 08 Gate：
上游回归：
完整 unit/contract：
closeout 前代码最终版 coverage 完整命令、镜像 ID、exit code、结果（本阶段不重跑）：
互补回归完整命令、exit code、结果：
compileall 完整命令、exit code、结果：
line：
branch：
combined：
skip/xfail/rerun：
白名单外文件：
异常文件：
staged：
commit：
merge：
push：
PR：
deploy：
最终状态：
```

只有 closeout 重建回归与 Demo 独立审查通过，且 P0/P1/高概率主演示 P2 为 0、所有延期项已登记，才能报告：

```text
MODULE08_READY_FOR_COMMIT_AUTHORIZATION
```

这仍不表示已经 commit 或 merge。

## 26. commit、merge、push、PR 与 deploy

- 实施、修复和 closeout 模型一律不得 stage 或 commit；
- 只有独立审查通过并取得新的明确 commit 授权后才能提交；
- commit 授权不等于 merge；merge 也需独立授权和合并后验证；
- 未经用户明确允许，永远禁止 push、PR 和 deploy；
- 禁止 reset、clean、stash、强制 checkout 或删除未知文件；
- 最终报告逐项写明 commit、merge、push、PR、deploy 实际状态。

## 27. 后续实施提示词硬要求

文档批准后生成的每份实施、修复、审查、closeout 或提交提示词必须：

- 引用权威设计、本文和共享执行规范；
- 不重复粘贴文档已有的大段内容，但保留当前阶段目标、前置条件、精确分支/HEAD、逐行文件白名单、允许操作、测试矩阵、停止条件和报告格式；
- 原则上一次授权两个相邻阶段并统一测试；迁移或删除安全门可单独成阶段；
- 明确小修范围与设计变更 STOP；
- 明确 commit 是否授权；默认不授权；
- 明确 push、PR、deploy 未授权；
- 要求代码审查和验收逐条对照文档；
- 最终报告至少含分支、前后 HEAD、修改文件、migration、测试、异常文件、commit、push、PR 和最终状态。

所有这类提示词必须完整放在一个 Markdown 代码框内，不得输出依赖旧提示词的补丁或“其余沿用上一版”。

## 28. 最终自检

实施者在报告 READY 前逐项回答：

```text
[ ] 当前授权覆盖两个相邻阶段或明确的独立安全阶段
[ ] 分支、开始 HEAD、工作树与白名单精确
[ ] 完整读取两份 Module 08 文档和直接依赖
[ ] 公共 API 和 Artifact registrations 精确
[ ] 没有私有/供应商/Module 09+ 导入
[ ] Prompt 双资源版本/hash 锁定且无内容泄露
[ ] 受控 Action/Judgment 和 controller completion 无越权
[ ] 6/48/12、8、3、50、128、三分之一全部有边界证据
[ ] UID、多角色、must/avoid、兴趣/餐次与状态优先级正确
[ ] supplemental/summary/Artifact-local gap union 精确八分支，avoid typed target/指纹/满足条件正确
[ ] summary evidence 0..8 只在严格上下文为空，supplemental/Judgment evidence 仍为 1..8
[ ] initial/supplemental/replay/singleflight/close_run 正确
[ ] LLM/map/control error、wave failure 和取消正确
[ ] 四类 Artifact、1 MiB、fresh copy、query/tag 唯一位置正确
[ ] 08-5/08-6 已验证 coverage harness 70% 合同且未提前完整采集；独立代码审查通过后、MODULE-CLOSEOUT 前的代码最终版将或已经恰好采集一次
[ ] Docker 安全 Gate 和互补完整回归符合当前阶段
[ ] staged 为空，无异常文件
[ ] commit/merge/push/PR/deploy 均按授权报告
[ ] MODULE-CLOSEOUT 未完成前没有宣称 Module 08 COMPLETE
```

## 29. 发布前设计勘误记录

`MODULE08-DESIGN-ERRATA-01` 以本文原 SHA-256 `CE0B0846E6564587793AE75740FFA5EDA77F61FDB4AE9135E4125B9B5CD6EFC8` 和权威设计原 SHA-256 `C43586BAE1B14A9DA2A721055B3F5A965CE655FE99EF84398301850BCA602281` 为基线，经用户授权作两项发布前纠错：

1. 落实权威设计 `D-08-30`：终态 gap summary 的 evidence indexes 为 0..8，空 tuple 只允许无相关 verified Place Observation 且 controller 可证明业务 gap 的上下文；supplemental/Judgment evidence 仍为 1..8，禁止伪造或错配证据。
2. 落实权威设计 `D-08-31`：新增 `avoid_resolution_missing` 第八分支、typed group/item/candidate target、稳定指纹、满足条件及公共/Artifact-local 投影。

本勘误不修改代码、测试、Prompt、四类 Artifact type、schema/version、依赖或 migration。旧 Phase34 实现与测试不能因为旧 Gate 通过而视为符合新设计；必须先完成 §17.5 的 Fix-2 和独立复审，再申请 08-5/08-6 授权。

`MODULE08-COVERAGE-THRESHOLD-01` 经用户另行明确授权落实权威设计 `D-08-32`：Module 08 production line、branch、combined coverage 门槛统一为 80%。harness 保持同一 source、分母、单进程采集、零 skip/xfail/rerun 和 Docker 安全 Gate。08-5/08-6 只验证 harness 与安全合同；完整采集延至全部代码和独立审查完成后、最后的 MODULE-CLOSEOUT 开始前，对代码最终版执行一次。closeout 不重复 coverage，并须证明生产代码和测试哈希未变。Demo 审查只阻断 P0、P1 和高概率主演示 P2；合规延期项使用 `DEFERRED_DEMO_NONBLOCKING`。

`MODULE08-COVERAGE-DEMO-EXCEPTION-01` 经用户于 2026-09-02 明确授权落实权威设计 `D-08-33`：`MODULE08-FINAL-COVERAGE80-GATE-3` 的真实结果为 317/317 passed、line `83.37430167597765%`、branch `69.78947368421052%`、combined `79.32549019607843%`，其中 branch/combined 未达到默认 80%。当前演示版以该精确结果视为 coverage 验收通过；不得修改 harness、排除源码、重跑 coverage 或把真实百分比写成 80%。例外只绑定 Gate-3 报告锁定的 production/test 哈希；closeout 若发现任一哈希变化必须 STOP，后续代码变化重新按 80% 默认门槛资格。

`MODULE08-COVERAGE-THRESHOLD-02` 经用户于 2026-09-11 明确授权落实权威设计 `D-08-34`：当前 Module 08 production line、branch、combined coverage 门槛统一为 70%。采集公式、source、分母、单进程、零 skip/xfail/rerun、Docker 安全 Gate 和最终采集时点保持不变；`D-08-33` 仅为历史记录，不再作为后续资格例外。
## 2026-10-11 用途修复实施契约（U-A1＋U-A2）

前置：用户本轮授权修订与实施；先完成前端18文件提交及 main 合并，现分支 feature/module-09-structured-output-fix，起点237b243f44555a5a941b07148a5d6778d60c5bd6。必读本设计同日 U08-01～05、[M09 §28](09_itinerary_planning_loop.md)、[M09执行契约同日增量](09_itinerary_planning_loop_vibe_coding.md)、[共享规则](SHARED_IMPLEMENTATION_RULES.md)、00 §4/5、路线图08 depends_on=01/02/04/06/07的当前公共合同；本轮不改这些上游。

目标：A1判断记录/证据更新，A2用途准入/必要供给补研，相邻完成后统一测试。代码白名单 app/candidate_research/ 下既有 models.py、artifacts.py、service.py、judgments.py、policy.py、prompt_loader.py、prompts/*.txt、__init__.py；允许新增 purpose.py、prompts/observation_v2.txt；app/itinerary_planning/ 下既有 models.py、artifacts.py、policy.py、service.py、prompt_loader.py、prompts/*.txt、__init__.py，允许新增 admission.py 和 prompts/planning_v7.txt；app/demo_workflow/{nodes,supplement,graph,artifacts}.py；app/result_formatting/{projection,service,artifacts,models}.py。只修改该问题必要版本、资格和接线，不更改预算/供应商适配/M02历史。

测试白名单：tests/unit/及tests/contract/下 candidate_research、itinerary_planning、demo_workflow、result_formatting、workflow_contracts、web_application 的既有文件及这些目录下新增用途/准入/纠错 test_*.py；tests/integration/下 artifact_persistence、durable_execution、demo_workflow、result_formatting、web_application 的既有文件及用途回放测试；tests/support/{candidate_research_fakes,itinerary_planning_fakes,demo_workflow_fakes,demo_workflow_worker,module14_browser_worker}.py；fixture只同步合法新合同，保留专门旧版本测试及完整Gate断言。

文档白名单：08/09/12/13的设计及对应_vibe_coding.md；closeout仅README.md、docs/IMPLEMENTATION_STATUS.md。不新增诊断文档。Dockerfile/Compose/锁文件只读。允许当前白名单读写、Docker内离线/隔离PG测试、精确清理本轮测试资源、两个独立只读子agent文档对照审查。原本机/云端运行资源不重启；真实API、stage/commit、push/PR/部署不授权（本轮先前UI提交及合并单独已授权完成）。

A1：新增2.0用途记录及旧读取注册，遗漏待确认、正标签派生、来源字段/归属验证、unknown保持和反证撤销；更新Prompt版本/hash。A2：唯一用途评估、必要数量/must供给检查、选择列表过滤、M13补研及M12版本读取，所有调用/消费保原账。

新入口数量缺口遵循设计同日增量的 supplemental request/2.0、MealCountShortfallGap/2.0 与精确前驱状态证明；不得把候选Ref写进Place观察目录。测试必须穿过真实M08控制器与M13转换，覆盖跨完整日的唯一UID数量、角色不符不计餐厅、伪current_count/前驱拒绝，以及归属合法的较早餐次搜索在后来详情轮次中支持补确认。

replacement补研另验真实run基线与相应角色用途：前驱旧点不满足新替代要求，新UID资格满足后移除已解决缺口；只给state不得误判完成，公开补研终态及精确回放不得异常或新增调用。

统一测试矩阵：U08五条＋U09-01及06；任意搜索源、两餐判断遗漏、空营业、明确冲突、跨候选引用、主备/组合资格、避免点/未确认must、必要供给不足零规划模型/地图、余额补研/耗尽具体gap、旧JSON字节/hash与新schema闭合。Docker断网定向unit/contract，随后M08/M09完整Gate及各自line/branch/combined≥70，受影响M12/M13合同。失败在白名单小修后重跑受影响项，不能skip/xfail/放宽门槛。

STOP：越白名单或新依赖；新行为与以上已批准条款冲突；预算增长；旧数据静默改写/续跑；源码泄漏；外部资源误操作；测试零收集或失败须先修复并保留失败记录。每组结束两个子agent逐条核查设计和本契约，不仅看绿灯。报告：分支、前后HEAD、文件、迁移、定向/模块/覆盖率/PG及异常、文档对照、双审、commit/push/PR/部署、最终状态。

## 2026-10-11 批准执行契约：R1～R6 研究合同修复

授权：用户同意完整R方案，更新文档并实施；每两个阶段后两个独立只读子agent逐条对照设计，完成全部测试等待提交。分支feature/module-09-structured-output-fix，开始HEAD=237b243f44555a5a941b07148a5d6778d60c5bd6。保留原57份未暂存用途修复及../m13-live-evidence/；baseline位于<EVIDENCE_DIR>/baseline.json，禁止覆盖/回滚已有变更。

必读：M08设计R08-01～10、§4/9～17/21～23及本规范；M01 §6.5/9～11及R01；M02 §19公共结构化调用/记录、M04 Artifact/预算、M06 Search/Details typed DTO、M07 selected-hotel合同（08依赖01/02/04/06/07，来源IMPLEMENTATION_ROADMAP）；M09 §6/12～15/20/28及R09；M12产物读取/R12；M13 §节点/终态/恢复及R13；M14 §交互/统计/安全投影及R14；00 §4/5、SHARED_IMPLEMENTATION_RULES。只修改上述已批准边界，不改未消费依赖。

R1 输入贯通：M01纯有效性函数、collect/confirm统一、M08单兴趣、公共摘要及候选版本接缝；R2 需求目录：不可变名称/ID、实际Action/Judgment投影、缺失外调前拒绝。两阶段完成统一unit/contract定向、旧/新版本回归，两个审查者按R01/R08-01/02/08审查。

R3 增量合并：逐键合并、作用域默认、撤销/冲突键、派生资格/统计；R4 接受/持久化/重放：完整临时状态检查先于accepted、失败保持一致状态及消耗、同一reducer和操作精确重放。统一生成式/故障注入/三城离线回归，两个审查者按R08-04/05/07/08审查。

R5 目标与纠错：首项绑定、明确名称首查、typed反馈、目标/证据进展与循环；R6 端到端错误/终态统计、M13/M14投影、下游完整Gate与closeout。两阶段统一测试后双审R08-03/06/09/10、R09/R12/R13/R14及全部先前条款。小缺陷可在白名单内直接修复，变更设计/增加预算/依赖/SQL迁移需停止请求批准。

生产白名单：app/requirements/{__init__,models,merge,service,dialogue}.py及新增interest_policy.py；app/candidate_research/{__init__,models,artifacts,service,judgments,policy,prompt_loader,fingerprints,purpose}.py及新增context.py、correction.py、prompts/action_v2.txt、prompts/observation_v3.txt；app/itinerary_planning/{__init__,models,artifacts,service,policy,admission}.py；app/demo_workflow/{artifacts,graph,nodes,supplement}.py；app/result_formatting/{models,artifacts,projection,service}.py；app/web_application/{models,presentation,service,summaries}.py；已只读确认实际统计文件为app/web_application/summary.py、终态中文映射为app/web_application/static/app.js，精确补列这两个同职责文件，仅改R14的安全错误投影/统计，不改变设计范围。

测试白名单：tests/unit及tests/contract下requirements/candidate_research/itinerary_planning/demo_workflow/result_formatting/web_application及workflow_contracts既有测试和新增test_research_contract*.py/test_incremental*.py/test_research_correction*.py；tests/integration下artifact_persistence/durable_execution/demo_workflow/result_formatting/web_application既有版本/主链测试及新增研究合同测试；tests/support/{candidate_research_fakes,itinerary_planning_fakes,demo_workflow_fakes,demo_workflow_worker,module14_browser_worker}.py。只能按新合法DTO更新外部边界fixture，不能替换真实控制器、删除反例或放宽验收。

文档白名单：01/08/09/12/13/14设计及对应执行规范；README.md和docs/IMPLEMENTATION_STATUS.md仅记录实收。Dockerfile/Compose/lock/scripts只读（现有COPY app/scripts/tests覆盖新增源）；没有新诊断文档。旧Prompt只读。允许Docker构建、断网离线测试、独立scratch PG初始化既有迁移、资源身份核验与精确清理。原18080/8080/云/其他项目不变。禁止stage/commit/merge/push/PR/部署。

验证命令：从当前源码docker build --target test/runtime；定向python -m pytest -q -p no:cacheprovider对应上述模块（全部在Docker）；M08/M09 tests/support/run_module08_coverage.py及run_module09_coverage.py，line/branch/combined沿原≥70；M01完整unit/contract；隔离PG经scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix独立前缀 -EvidenceDir全新目录运行完整unit/contract、M14 PG/覆盖率、上游M10/M11/M12/M13、实际重启/容量/18Chromium/SSE关闭。精确重放安全归一化输入，不读供应商原文/reasoning。真实复验另用全新隔离本机测试资源与端口，3次各一次，不修改现有运行服务。

停止条件：白名单外实质改动/冻结设计冲突、版本错配、秘密输出、未知外部操作结果、新依赖或预算增长、无测试收集、skip/xfail/rerun/覆盖率失败、资源身份不明。失败保留并在白名单修复后重跑受影响项，后续Gate不能提前宣称通过。最终报告：分支/前后HEAD、修改文件及异常、迁移、各组测试/双审/离线重放/真实API时间token、closeout、staged/commit/push/PR/部署及最终状态。完成后等待提交。

2026-10-11用户明确补充授权：本轮测试白名单增加tests/browser/web_application/test_demo_pages.py，仅同步workflow_provider_failed中文终态文案，并新增workflow_model_output_invalid显示验证。保留不显示部分攻略和完整样例入口等原断言。其余范围、预算、既有服务保护、逐文档双审及不提交/推送/部署约束不变。

R5/R6真实验收后的白名单补修：Closeout-c离线通过后完成新隔离三城各一次，上海暴露实际发送的JSON Schema允许省略union判别字段、而本地解析拒绝的合同不一致。在既有correction.py及test_research_correction.py内按R08-03统一投影required，不改公共DTO默认、不补值、不增调用或额度；测试覆盖实际调用schema、顶层/首项/后续项/无动作分支，以及任意命名嵌套union和原schema不变。定向33项已通过；两位子agent再次逐文档复审后，最终代码须重新完成M08 owner coverage及当前完整Closeout。此前三城结果保留为补修前实收，不冒充新源码真实资格；新增真实调用未授权，不自动重跑。分支/HEAD、白名单、其他停止条件及禁止提交/推送/部署约束均沿本节。

## 追加批准执行契约：R7/R8 共享输出恢复

用户已随后批准 M08 共享2次恢复及离线通过后三城各1次复验，覆盖上一段“新增真实调用未授权”。必读设计 R08-03/04/05/06/08/11～15 与本文 R1～R6、共享规则；直接依赖沿上述精确章节，不改 M02 公共 DTO/重试、M04 预算或供应商实现。

前置：schema 小修 r56h 36项定向通过、两位审查逐条复审；原 Closeout-c 和三城实测仅是旧源码快照。分支与开始 HEAD 不变。生产白名单限既有 app/candidate_research/{service,artifacts,judgments,correction,models}.py，测试为原 candidate_research unit/contract 和 tests/support/candidate_research_fakes.py（如需）；文档本设计/执行、README/IMPLEMENTATION_STATUS。不新建诊断文档，不改旧 Prompt。其他原白名单仅必要下游合法 fixture 对齐。

R7：完成通用 schema 必填判别字段补修，以及 Action/Judgment 格式/截断共享恢复；在调用前检查全run2次及semantic12次余额，使用原输入和有限安全反馈，复用同一ModelOperation。R8：可修正判断归属错误显式分类、recovered安全记录、总用量/complete保真、模型Ref还原共享计数及精确重放。未知引用、内部状态/存储/供应商失败和取消不重试。两阶段实现后统一测试，再用两个只读子agent对照设计逐条审查，不在调查阶段使用子agent。

允许：白名单内编辑、Docker构建/断网定向和owner coverage、隔离PG既有迁移及完整Closeout、身份确认后清理自有资源。三城真实测试只能在最终源码全部离线PASS及双审通过后启动，另建全新项目/PG卷/端口，各一次无业务自动重跑；记录耗时和token，失败保留。保护原18080/8080/其他项目及旧卷。禁止stage/commit/merge/push/PR/部署。

测试矩阵及算法顺序按 R08-11～15：先读回/核验既有操作记录；新调用先核预算，再独立interaction_call；记录实际semantic和已知metadata；只有白名单错误才准备安全反馈并消费恢复机会；最终记录包裹失败摘要和原结果；完整临时状态验证/accepted写入成功后提交状态。离线必须覆盖上述所有成功/失败/边界、共享、旧版本、重放和取消情况，不能只看定向绿灯。最终重跑M08 owner三指标≥70及完整Closeout所有现有Gate；仅在源码冻结不变时复用未影响的M09 owner证据。

停止条件：超出白名单/改变未批准设计、预算增大、未知外部结果、安全信息泄漏、旧版本字节变化、测试未收集/skip/xfail/rerun、资格资源不明。最终报告含分支/前后HEAD、修改文件/异常、迁移、所有测试和逐文档双审、三城时间token/cleanup、commit/push/PR/部署及最终状态。等待新的提交授权。

2026-10-11用户追加最小M02白名单：app/llm_runtime/runtime.py、tests/unit/llm_runtime/test_structured_runtime.py及D/E02，仅保留结构化失败的既有possible_duplicate标志；精确执行与门禁见E02末尾维护入口。覆盖本节M02只读约束的这一处实现缺陷，不增加重试/调用/token额度。

取消统计使用用户追加批准的M02公共只读计数属性（D/E02末尾）；白名单补列已有tests/support/candidate_research_fakes.py消费合同。R8补充矩阵：第12次accepted/failed中间操作重放、已知纠错metadata后最终有成本/无成本LLM失败的complete一致性、未知evidence拒绝审计可持久化且精确终态重放、取消在准入前/后不双计。

## 2026-10-12 执行契约：Q1～Q6 必要资格与收敛修复

用户已批准完整修复及必要重构，完成全部测试后等待提交。权威来源为 [M08设计 Q08-01～09](08_candidate_research_loop.md)、[M09用途准入](09_itinerary_planning_loop.md)、[M12读取](12_result_formatting.md)、[M13路由](13_demo_workflow.md)、[M14安全展示](14_web_application.md)及[共享规则](SHARED_IMPLEMENTATION_RULES.md)。直接依赖依路线图：08依赖01/02/04/06/07，09额外消费08，12/13/14只同步本轮实际读取和投影；依赖章节沿本文R阶段精确必读范围，不改其未消费合同。

开始分支 feature/module-09-structured-output-fix，HEAD=237b243f44555a5a941b07148a5d6778d60c5bd6；暂存为空，保留已有90份工作区变更及白名单外 ../m13-live-evidence/。每组前核验状态，禁止覆盖既有修复。当前原18080/8080和其他项目资源保持运行。

生产白名单：app/candidate_research/{__init__,models,artifacts,service,judgments,policy,prompt_loader,prompting,fingerprints,correction,purpose}.py；新增qualification.py、prompts/observation_v4.txt。app/itinerary_planning/{models,artifacts,service,admission,policy}.py；app/demo_workflow/{nodes,supplement,artifacts,graph,terminal,models}.py；app/result_formatting/{artifacts,projection,service}.py；app/web_application/{models,presentation,summary}.py、static/app.js。只允许资格、版本、预算记账和安全终态接线的必要变更，不改供应商、技术重试、M02预算或 SQL。

测试白名单：tests/unit和tests/contract下candidate_research、itinerary_planning、demo_workflow、result_formatting、web_application的既有及新增test_qualification*.py；tests/integration下artifact_persistence、durable_execution、demo_workflow、result_formatting、web_application的版本、主链和重放测试；tests/browser/web_application/test_demo_pages.py；tests/support/{candidate_research_fakes,itinerary_planning_fakes,demo_workflow_fakes,demo_workflow_worker,module14_browser_worker}.py。旧版本专用测试继续使用原模型，普通合法fixture同步当前合同。合法输出场景可以显式构造工作单对应的测试补丁；运行时、仓库或模型替身不得暗中补全输出，遗漏反例必须保留原补丁。

文档白名单：08/09/12/13/14设计及各执行规范；README.md、docs/IMPLEMENTATION_STATUS.md仅在Closeout记实收。Dockerfile、Compose、锁文件、验证脚本只读；其COPY app/scripts/tests覆盖新增模块。允许本白名单内重构、Docker构建、断网测试、全新隔离PostgreSQL及资源精确清理。禁止stage、commit、merge、push、PR、部署和更新既有网页。不得写新的诊断报告。

每组实施提示词如下；前置测试失败先在白名单修复，不能直接进入后续Gate。

```text
实施Q1＋Q2。必须引用D08的Q08-01/02/03/08/09、E08本节、共享规则及上述直接依赖章节。
目标：控制器生成必要判断工作单，显式记录覆盖和实际证据；完整判断缺项进入共享两次恢复，接受前整体校验。
前置：用户已批准；分支/HEAD/既有变更核验与本文一致；只使用上述M08生产和测试白名单及08设计/执行文档，必要共享下游fixture按本节测试白名单同步当前合法版本。
允许：白名单编辑及必要重构、更新Prompt版本/hash、Docker断网定向/模块测试；不提交、不外调、不操作原服务。
矩阵：所有判断类别遗漏、明确unknown、条件餐次、组default/exception覆盖、候选证据归属、缺项恢复成功/耗尽/共享/第12次边界、无部分accepted、旧字节及精确重放。
STOP：未经批准的设计变化/依赖/额度/SQL/秘密暴露、未知资源、无法安全保留既有代码；测试失败保留结果并修复，不能skip/xfail/rerun。
报告：分支和前后HEAD、文件/异常、无SQL迁移、测试及Q08逐条证据、两位子agent逐条独立审查、提交状态；通过后进入Q3＋Q4。
```

```text
实施Q3＋Q4。必须引用D08的Q08-02/04/05/06/08/09、E08本节、共享规则和上述直接依赖章节。
目标：旧候选自身历史证据工作单、独立judge_existing步骤、实际阻塞目标优先和有界收敛。
前置：Q1/Q2统一测试及两个审查通过；分支/HEAD保持；仅上述M08白名单及其测试/08文档。
允许：必要重构、Docker断网定向/模块测试；不提交、不外调，不操作原服务。
矩阵：已确认must被其他资格阻塞、历史详情/搜索证据、同证据不重复与新证据重开、批次容量及未选候选、零Action/零地图、仍有补判不被no_progress停止、补研不重领额度、六步/十二调用边界、取消及保存失败/重放。
余额边界还须验证：仅剩一次调用且有旧工作时只补判；没有旧工作时以semantic_budget_insufficient准确停止；Action输出恢复用完余额后零地图。旧候选窗口须证明最近详情及目标事实优先，较早未消费事实可进入下一有限工作单，全部窗口消费后不再重开相同键。还须验证追加重复观察不重开，新增一条事实消费后其余旧窗口组合不重开；公共搜索入口跳过已发现候选的空必要判断并保存真实步骤，全新服务终态及既有空工作单精确重放；NoResult空候选沿原调用语义；十六候选容量内优先尚未准入的必去匹配，已由其他候选满足的同组匹配不占此优先级。
STOP同Q1/Q2；新事实不足返回准确未完成，不能猜肯定结论。
报告字段同Q1/Q2，两个子agent逐条审查Q08-01～06/08/09；通过后进入Q5＋Q6。
```

```text
实施Q5＋Q6。必须引用D08全部Q08条款、D/E09/12/13/14本次资格接线、E08本节和共享规则。
目标：M09地图前统一准入、M13/M14安全原因、历史版本只读、完整测试及MODULE-CLOSEOUT。
前置：前两组测试和四次独立审查通过；保持分支/HEAD及原资源；使用本节全部精确白名单。
允许：白名单内编辑/重构、同步合法fixture及README/状态实收；Docker离线、隔离PG完整Gate；两个子agent逐条审查全部设计与执行条款。
矩阵：未使用unknown不阻塞足够供给、零候选/缺项/证据不足/不适用/恢复与预算耗尽区分；M09零调用准入失败；全部新旧Ref贯通；M08/M09三覆盖指标≥70、受影响上游回归、完整unit/contract、PG/容量/重启/浏览器/SSE；最终说明同步后重新构建runtime/test/browser并完成当前Closeout，不能沿用旧源码资格。
真实验证：只有全部离线PASS及双审通过后，在全新隔离项目/PG卷/端口以三个城市不同must/avoid/兴趣/节奏需求各最多1次正式运行，无业务自动重跑；只用现有私有配置传入凭据，不输出原文/响应/reasoning。逐城记录秒数、输入/输出/思考/总token、调用和地图消耗，失败保留；不更新原18080/8080，不恢复旧任务。
STOP：额度/余额/鉴权/网络阻断正式资格则如实报告，不循环刷绿；其他停止条件同前组。
报告：分支、前后HEAD、修改文件、迁移、各组测试/六次审查/Closeout/真实API与时间token、异常文件、staged/commit/push/PR/部署、最终状态。全部完成后等待用户提交授权。
```

Q5/Q6补充矩阵：当前用途统计必须排除只有餐厅角色而无餐次资格的候选，必去匹配但用途未知不得计为满足；已有足够供给时保留额外未知候选并完成。旧1/2/3状态原字段和统计校验保持，新4类型不能反向修改历史内容。当前4候选的避开组必须完整，M09核对确认需求的必去和避开目录；四版本M12读取须核对原字节及Ref/hash。安全分类按D08的明确优先级逐项测试，M13实际路由和M14六项固定中文文案均须证明，不以网页模拟替代真实主链验收。

自身归属矩阵覆盖：跨候选成功搜索和详情，详情NoResult/失败/not_issued的行动UID绑定，首次接收来源和初始核心身份，容量拒收保留观察但不成为候选来源，以及后续真实同UID冲突。至少一个公共来源核验与M12反例须同步重算合法判断、窗口、覆盖和Ref/hash后仍拒绝伪造归属；合法自身详情、未知和冲突后接受、旧版本读取继续通过，不通过删除摘要校验或放宽类型来修复fixture。

来源核验在同一白名单内复用validate_qualification_sources纯函数，研究重放与M12共同核验实际步骤、完整目录、规范字节和候选自身窗口、accepted覆盖及最终标签；不能通过两份业务规则维持一致。当前4严格拒绝Action1和Judgment1/2，原历史类不改。地图观察逐条核对同轮最终accepted行动的序号、工具、指纹及详情身份；保留合法零地图轮和not_issued审计，不把重复行动当新地图来源。最终标签从初始化未知开始，逐轮先处理实际地图身份冲突，再应用最终accepted补丁；要求组default仅作用于evaluated范围，兴趣按实际增删，餐次调用已有apply_meal_assessment。无判断来源不能保留肯定标签，冲突追加须来自真实自身观察。补缺失Judgment、错步骤、错摘要、伪造覆盖、整体删除判断或行动目录、地图与行动不一致、各类肯定标签缺来源及恢复最终记录反例。旧1/2/3不补新资格字段；PostgreSQL重新打开后验证四版原字节。主链证明使用真实M08服务、M13图及隔离PostgreSQL，仅替换外部边界，核对零供给、证据不足、不适用、预算及恢复耗尽的真实终态和实际消耗；必要判断未完成的安全防御分类另保留节点与页面反例，不把合成结果称为真实控制器产物。

## 30. C1～C6 研究纠错与成果交付执行规范

### 30.1 目标、依据与前置

实施真源为[D08 §31 C08](08_candidate_research_loop.md#31-研究纠错计数与已有成果交付c08)和本节，及SHARED_IMPLEMENTATION_RULES §1～9。路线图M08的直接依赖01/02/04/06/07已核对；必读[01_requirement_and_dialogue.md §6、§11～12](01_requirement_and_dialogue.md)的确认需求/兴趣公共合同、[02_llm_runtime_and_model_profiles.md §5.4～5.6、§9～11、§19](02_llm_runtime_and_model_profiles.md)的语义消费/失败/恢复及完整采集、[04_workflow_state_and_artifacts.md §6.4、§8～13](04_workflow_state_and_artifacts.md)的Artifact/预算/OperationId、[06_baidu_map_adapter_and_mcp_gateway.md §7、§10、§18～19](06_baidu_map_adapter_and_mcp_gateway.md)的Place结果/错误/技术重试合同、[07_hotel_candidate_selection.md §8～10](07_hotel_candidate_selection.md)的已选酒店Artifact/坐标/引用合同。M09 §30、M12当前读取合同、M13“研究纠错接线合同”只消费必要版本和路由，不改规划、展示或工具职责。

开始分支feature/m09-boundary-day-quality，HEAD b4c382e09695f3bdd71364b9aac1ae5d75ab4325。继续当前尚未合并任务，不切分支、不stash、不重置、不建新工作区。基线54项已有改动完整保留；原D盘feature/m09-minute-scheduling的46项改动和现有18080/18081/8080服务保持。先在仓库外保存文件摘要、dirty列表和服务身份。D08/E08及必要接线文档经两名独立文档审查者逐条PASS后冻结摘要；文档冻结后代码不得反向修改规范。

### 30.2 精确白名单与允许操作

生产：app/candidate_research/{__init__,models,artifacts,service,policy,correction,qualification,purpose}.py及新增recovery.py；app/demo_workflow/{plans,nodes,graph,artifacts,supplement}.py；app/itinerary_planning/{models,artifacts,admission,service,policy}.py；app/result_formatting/{models,artifacts,projection,service}.py；app/web_application/{models,presentation,summary,service}.py；app/frontend_experience/{models,projection}.py仅必要版本识别/预算字段接线。跨模块生产只允许新增候选5/Action3/Judgment4的显式读取、注册和安全终态接线，不改原规划排程、来源校验或UI风格。

测试：tests/unit及tests/contract下candidate_research、itinerary_planning、demo_workflow、result_formatting、web_application、frontend_experience；tests/integration下demo_workflow、result_formatting、artifact_persistence、web_application、durable_execution；tests/browser/web_application仅验证既有流程/断言必要新版本；tests/support/{candidate_research_fakes,itinerary_planning_fakes,demo_workflow_fakes,demo_workflow_worker,module14_browser_worker,frontend_experience_worker}.py。只对公共合同有实际影响的fixture做精确对齐，旧版本专用反例保留，不批量替换所有版本数字。

文档：docs/08_candidate_research_loop.md及其_vibe_coding.md、docs/13_demo_workflow.md及其_vibe_coding.md；docs/09_itinerary_planning_loop.md及其_vibe_coding.md、docs/12_result_formatting.md及其_vibe_coding.md只同步候选5的显式读取合同。README.md和docs/IMPLEMENTATION_STATUS.md仅C5事实同步。Dockerfile、依赖和锁文件只读，当前COPY app/scripts及test target已包含本次范围。仓库外专属证据和隔离Docker资源允许创建。

未经本轮明确授权不stage、commit、merge、push、PR或部署；本轮仅C6授权一次真实任务。不得读出或打印密钥、模型原始思考或完整供应商响应。

### 30.3 C1＋C2：版本、操作计划、计数及当前轮纠错

C1实现D08 §31.2/31.5的strict版本和计数合同。新候选5、Action3、Judgment4及invocation_attempt1分别注册；旧类型保留原字节和精确版本。每轮原始和最多两次修正的Action/Judgment attempt IDs由M13预分配，最终主记录唯一。新增两个纠错预算字段，旧公共摘要以不可用表达；新状态强制limit=2及实际consumed。只增加识别字段和记录，不增加模型12/地图48/研究6或供应商技术尝试。

C2将参数策略修正移入当前逻辑行动；共享已有输出/Judgment修正计数，第一次拒绝不计纠错。每次调用返回后记录真实准入消耗，再保存attempt，随后才可能下一调用。修正前检查Action至少剩2、Judgment至少剩1；技术/不确定错误禁止进入修正。整批重检后接受才执行地图，未知或越权引用保持失败。明确指出错误序号和允许范围，模型决定重新提交内容。正常轮只消费一次研究预算和最终进展。

C1＋C2后统一跑新增DTO/版本/操作计划/计数/修正余额和现有恢复定向测试；此阶段暂不运行真实网络，不要求所有下游旧fixture立即通过。不能以删除旧反例消除失败。

### 30.4 C3＋C4：结果交付、来源重放与下游

C3依D08 §31.4实现统一收尾：可恢复错误修正无余额用Incomplete及准确停止原因，correction_limit_reached仅实际达到2且当前仍需修正；最后一轮本身不再决定Failed。Completed门槛原样，未确认项和已接受判断均保留；技术失败保持原错误。补判修正失败保留本轮地图事实但不得授予未经接受的资格，关联失败attempt及完整账本。

C4验证每个主记录和完整attempt链、语义索引/消费、共享纠错数量、最终来源和拒绝记录无资格贡献。相同私有run上下文内重复调用不重发；跨进程上下文缺失及消费无确定结果继续失败关闭。兼容读旧候选1/2/3/4及原最终结果，不改旧bytes。M13升web.v9并预分配OperationPlan2，候选Incomplete有合格供给才进入M09，零供给准确停止；M09/M12/M14显式按Ref分派新旧版本，不放宽资格。

C3＋C4统一执行矩阵：

| 组 | 必须证明 |
|---|---|
| 计数 | 首错0；修正1/2；成功用满后继续正常研究；第三次需求不调用；Action/格式/Judgment/补研共享；前置失败0、准入后超时/取消实计且不伪造usage |
| 余额 | Action余额1不修正不地图；余额2恰能修正及判断；Judgment余额1可修正；研究第6轮仍可修正；没有新事实的空判断不浪费调用 |
| 交付 | 真实73候选离线回放；公开合成同类回归；纠错不足但普通预算有余仍合法Incomplete；原候选/未知/gaps保留；同一轮进展只算一次 |
| 失败 | 未知UID/目标/锚点、错run/越权引用及状态/存储/预算/外调不确定仍Failed；已知合法取值与目标不匹配走objective_parameters；schema错误沿原恢复白名单；非法批次合法子集不执行 |
| 来源 | attempt链遗漏、重复、错序、错kind/run/主操作、伪造cost/纠错号、最终结果与末attempt不一致、拒绝结果授予资格均拒绝 |
| 下游 | 真实M08与M13节点/图、隔离PG，仅fake外部边界；合格供给进M09、零供给按原因停；读取与终态重放零新调用；新旧混配拒绝 |
| 兼容 | 四版旧候选、旧Action/Judgment、既有M12结果重新打开bytes/hash和展示不变；旧活动执行不自动升web.v9 |

### 30.5 C5：模块Gate、closeout和两名新审查者

先定向再M08完整unit/contract及单进程source=app.candidate_research的line/branch/combined各70%覆盖，不排除源码、不舍入过线；然后受影响模块默认unit/contract及新隔离PG主链/来源读取/重新打开验证。默认完整离线pytest仍执行，不能用只绿子集替代。浏览器不涉及样式改动，至少真实网页主链及来源详情/统计读取的相关回归。

完成真实状态同步README/IMPLEMENTATION_STATUS后重新构建runtime/test及适用browser镜像，执行runtime默认CMD（缺少必须配置时应与项目既有自检行为一致）、compileall、M08 Gate和默认完整离线。覆盖证据只有M08生产与测试摘要未变才复用；变化则重新采集。Docker COPY/CMD和资源身份写入报告。

由两名不同于文档审查者的新agent分别核对D08 §31、本执行规范和跨模块读取/恢复；P0/P1及高概率影响主演示P2清零后才能C6。修复白名单内明确问题后，受影响测试重跑并交回两位复核。不得仅以测试通过宣称审查通过。

### 30.6 C6、STOP和最终报告

C6严格D08 §31.6：在新namespace/新PG库和私有目录中，只创建一次固定合肥真实任务。复用已授权两项现有凭据并安全传输，保留暂停Key；外部边界真实，酒店只选本次实际第一家。记录当前源码/镜像摘要、run_id、阶段与纠错/模型/地图用量、耗时、输入/输出/思考/缓存token、网页结果和安全诊断。只读诊断/刷新同一任务允许，不新建替代任务。异常报告失败并保留数据，不循环重跑。

STOP：设计冲突、白名单不足、改动丢失、额度增加、门槛降低、私密信息暴露、现有服务身份变化、离线或审查未通过、正式调用的凭据/余额/网络阻断。先报告具体证据与最小范围；不得擅自扩大任务。用户本轮已授权本节必要版本、计数和跨模块接线，不为原合同例外重复请求授权。

最终报告必须包含分支/前后HEAD、基线与新增文件、迁移/版本、文档及两轮各两人审查、定向/模块/覆盖/全量/PG/browser/closeout镜像及CMD、唯一真实任务结果、异常文件、隐私扫描、staged/commit/push/PR/部署和最终完成或限制状态。阶段提示词必须引用D08 §31及本节，保留目标、前置、分支、白名单、允许操作、测试矩阵、STOP及报告格式；C1＋C2、C3＋C4成对实施，C5/C6独立执行门槛。
