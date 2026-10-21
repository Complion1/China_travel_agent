# Module 07 Vibe Coding 执行契约：酒店候选、UID 去重与人工选择

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

文档状态：设计候选，待用户批准

## 1. 用途与执行模式

本文把 <code>docs/07_hotel_candidate_selection.md</code> 转换成弱模型可执行的分阶段合同。它不是当前实施授权，也不允许仅凭本文开始编码、测试、Docker、commit 或 merge。

任何未来实施、修复、独立审查、续执行、重新资格、MODULE-CLOSEOUT 或下一阶段提示词必须：

- 完整放在同一个 Markdown 代码框中；
- 引用本文、权威设计和共享执行规范；
- 不写“其余沿用上一版”；
- 保留当前阶段目标、前置条件、分支、文件白名单、允许操作、测试矩阵、停止条件和报告格式；
- 原则上一次授权相邻两个阶段，并在两个阶段完成后统一执行对应测试；
- 逐条对照文档验收，不得只看测试是否通过；
- 未经明确授权不得 stage、commit、merge、push、PR 或 deploy。

## 2. 必读真源

开始任一实施阶段前必须核验并读取：

1. <code>docs/00_agent_master_design.md</code>
   - §3.2 主流程；
   - §5 人工确认和恢复；
   - §6 Artifact 与重放；
   - §7 受控模型循环与运行预算；
   - §8 百度地图边界；
   - §9～§11 可信、安全和交付。
2. <code>docs/IMPLEMENTATION_ROADMAP.md</code>
   - Module 07 行；
   - WP4 顺序；
   - 07–09 规划闭环 Gate、10–12 持久执行与输出 Gate；
   - 依赖和完成定义。
3. <code>docs/SHARED_IMPLEMENTATION_RULES.md</code>
   - 全文。
4. <code>docs/07_hotel_candidate_selection.md</code>
   - 全文，唯一 Module 07 业务真源。
5. <code>docs/01_requirement_and_dialogue.md</code>
   - §3.1～§3.4；
   - §5.2～§5.3；
   - §6.1～§6.6；
   - §8.4；
   - §11；
   - §16～§17。
6. <code>docs/04_workflow_state_and_artifacts.md</code>
   - §1～§3；
   - §5～§6；
   - §8～§13；
   - §14～§20；
   - §21～§26。
7. <code>docs/06_baidu_map_adapter_and_mcp_gateway.md</code>
   - §4～§7；
   - §10；
   - §15；
   - §18～§19；
   - §21～§23；
   - §27～§29。

Vibe 结构可参考 Module 04/06 已批准 Vibe，但不得复制其阶段号、MCP/live/Secret 任务或文件树。

## 3. 开始条件与前置证据

每个未来提示词必须冻结实际可核验的：

- 工作目录；
- Git 根目录；
- 开始分支；
- 开始 HEAD 与 subject；
- clean/staged/tracked/untracked；
- 已批准两份 Module 07 文档的内容 hash；
- Module 01/04/06 已在目标基线；
- 目标阶段文件是否存在及是否与预期一致；
- 上一阶段报告与测试证据；
- 当前允许的 Docker image/tag 和容器残留状态。

Module 07 实施与修复目标分支固定为
<code>feature/module-07-hotel-selection</code>。提示词必须核验该分支建立在已批准设计基线，
不得在 <code>main</code> 直接实施，也不得自行创建其他分支或 worktree。

未满足任一前置条件时报告 <code>BLOCKED_BASELINE_DRIFT</code>，不得 reset、clean、stash、checkout 覆盖或接管未知文件。

设计批准、实施授权、修复授权、closeout 授权、commit 授权和 merge 授权相互独立。

## 4. 直接依赖核验

生产 import 只允许：

<pre><code>from app.requirements import ConfirmedTravelRequirement

from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactConflictError,
    ArtifactFound,
    ArtifactRef,
    ArtifactRepository,
    ArtifactSchemaRegistration,
    ArtifactWriteRequest,
    BudgetConsumeRequest,
    BudgetConsumed,
    BudgetExceeded,
    ContentHash,
    ExecutionBudgetKey,
    ExecutionBudgetManager,
    ExecutionBudgetScope,
    OperationId,
    OperationResultIndeterminateError,
    UtcClock,
    WorkflowPhase,
)

from app.baidu_map import (
    BaiduMapAdapter,
    BaiduMapError,
    BaiduMapErrorCode,
    CitySearchArea,
    PlaceSearchAreaType,
    PlaceSearchNoResult,
    PlaceSearchRequest,
    PlaceSearchSuccess,
    PlaceSummary,
)</code></pre>

以及 Python 标准库和项目已经锁定的 Pydantic。

禁止：

<pre><code>app.llm_runtime
app.requirement_subgraph
app.baidu_map.models.*
app.baidu_map._*
langgraph
langchain*
mcp
langchain_mcp_adapters
Module 08+ 实现
任意 HTTP client 或 provider SDK</code></pre>

实现前必须用包根公共 API 和合同测试核验所需符号真实存在。不得修改上游合同补齐假设。

## 5. 未来生产文件树

精确目标：

<pre><code>app/hotel_selection/
├── __init__.py
├── artifacts.py
├── errors.py
├── models.py
├── protocols.py
├── query_policy.py
└── service.py</code></pre>

不得新增：

- <code>agent.py</code>；
- <code>prompts/</code>；
- <code>graph.py</code>；
- <code>mcp_gateway.py</code>；
- <code>settings.py</code>；
- <code>clients.py</code>；
- 兼容 shim、legacy alias 或 generic JSON fallback。

如实现确需新增生产文件，必须停止并取得扩大白名单授权。

## 6. 未来测试文件树

精确目标：

<pre><code>tests/support/hotel_selection_fakes.py

tests/unit/hotel_selection/
├── test_artifacts.py
├── test_errors.py
├── test_models.py
├── test_query_policy.py
├── test_qualification.py
├── test_service_discovery.py
├── test_service_selection.py
└── test_serialization.py

tests/contract/hotel_selection/
├── test_artifact_registration.py
├── test_import_boundaries.py
├── test_public_api.py
├── test_replay_budget_contract.py
├── test_selection_contract.py
├── test_upstream_integration.py
└── test_security_contract.py

tests/support/run_module07_coverage.py</code></pre>

不得新增 Module 07 live、integration/live、网络 probe、Secret fixture 或 sleep-based concurrency test。

## 7. 包根公共 API 清单

<code>app/hotel_selection/__init__.py</code> 必须显式 import 并以 tuple/string 精确导出权威设计 §6 的符号。至少包括具体 union 分支，禁止只导出无法做 <code>isinstance</code> 的 alias：

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

<code>__all__</code> 不得包含 Module 01/04/06 重导出、私有 helper、内部 token 常量或测试 fake。

## 8. DTO 实现清单

### 8.1 models.py

实现并逐字段对照权威设计：

- <code>HOTEL_SELECTION_SCHEMA_VERSION</code>；
- <code>HOTEL_PLACE_CALL_BUDGET_KEY</code>；
- <code>HotelMapFailureCode</code>；
- <code>HotelCoordinate</code>；
- <code>HotelPlaceEvidence</code>；
- <code>HotelCandidate</code>；
- <code>HotelCandidateOperationIds</code>；
- <code>HotelSelectCommand</code>；
- <code>HotelDeclineCommand</code>；
- <code>HotelSelectionCommand</code>；
- <code>HotelSelectionRequest</code>；
- discovery outcome 三分支与 union；
- selection outcome 三分支与 union；
- <code>HotelCandidateDiscoveryFailureCode</code> 和
  <code>HotelSelectionRejectionCode</code>。

所有 DTO：

<pre><code>model_config = ConfigDict(
    strict=True,
    frozen=True,
    extra="forbid",
)</code></pre>

字段顺序、required、nullable、tuple 和 discriminator 必须与权威设计一致。禁止 mutable default、list、dict input shortcut、alias 和宽松转换。

默认值不得自行猜测：Artifact 可达模型全部字段无默认；非 Artifact request 的 schema version、command/outcome discriminator 和 ready disclaimer 只按权威设计 §7 的固定默认实现，其余字段 required。

<code>models.py</code> 的精确字段顺序：

<pre><code>HotelCoordinate(
    schema_version required,
    coordinate_system required,
    latitude required,
    longitude required,
)
HotelPlaceEvidence(
    schema_version required,
    uid required,
    name required,
    address required,
    coordinate required,
    category required nullable,
)
HotelCandidate(
    schema_version required,
    uid required,
    name required,
    address required,
    coordinate required,
    category required non-null,
)
HotelCandidateOperationIds(
    schema_version="1.0",
    search_operation_ids required length=3,
    candidate_set_operation_id required,
)
HotelSelectCommand(
    schema_version="1.0",
    action="select",
    selected_uid required,
)
HotelDeclineCommand(
    schema_version="1.0",
    action="decline",
)
HotelSelectionRequest(
    schema_version="1.0",
    selection_operation_id required,
    candidate_set_ref required,
    command required,
)
HotelCandidatesReady(
    schema_version="1.0",
    outcome_type="ready",
    candidate_set_ref required,
    candidates required length=3..5,
    disclaimer_code="map_identity_only",
)
HotelCandidatesInsufficient(
    schema_version="1.0",
    outcome_type="insufficient",
    candidate_set_ref required,
    qualified_candidates required length=0..2,
    searches_attempted required Literal[3],
)
HotelCandidateDiscoveryFailed(
    schema_version="1.0",
    outcome_type="failed",
    reason required,
    failed_search_ordinal required,
    qualified_count required,
    failure_search_ref required nullable,
)
HotelSelected(
    schema_version="1.0",
    outcome_type="selected",
    selection_ref required,
    selected_candidate required,
)
HotelSelectionDeclined(
    schema_version="1.0",
    outcome_type="declined",
    selection_ref required,
)
HotelSelectionRejected(
    schema_version="1.0",
    outcome_type="rejected",
    reason required,
)</code></pre>

字段类型、长度、数值范围、Enum member/value 和 cross-field validator 继续逐项服从权威设计
§7～§10、§16～§18；上表不得被解释为允许省略约束。

Enum 成员和值精确为：

<pre><code>HotelMapFailureCode:
    REQUEST_ERROR="request_error"
    CONFIGURATION_ERROR="configuration_error"
    AUTHENTICATION_ERROR="authentication_error"
    RATE_LIMIT_ERROR="rate_limit_error"
    TIMEOUT_ERROR="timeout_error"
    TRANSPORT_ERROR="transport_error"
    SESSION_ERROR="session_error"
    TOOL_UNAVAILABLE="tool_unavailable"
    TOOL_SCHEMA_ERROR="tool_schema_error"
    PROTOCOL_ERROR="protocol_error"
    RESPONSE_ERROR="response_error"
    CONCURRENCY_ERROR="concurrency_error"
    UNVERIFIED_RESULT="unverified_result"
HotelCandidateDiscoveryFailureCode:
    BUDGET_EXHAUSTED="budget_exhausted"
    MAP_CALL_FAILED="map_call_failed"
HotelSelectionRejectionCode:
    CANDIDATE_UID_NOT_FOUND="candidate_uid_not_found"
    CANDIDATE_SET_NOT_READY="candidate_set_not_ready"
    STALE_CANDIDATE_SET="stale_candidate_set"
    DECISION_CONFLICT="decision_conflict"</code></pre>

### 8.2 artifacts.py

实现：

- <code>HotelSearchSuccessEvidence</code>；
- <code>HotelSearchNoResultEvidence</code>；
- <code>HotelSearchFailedEvidence</code>；
- <code>HotelSearchEvidence</code>；
- <code>HotelPlaceSearchArtifact</code>；
- <code>HotelCandidatesReadyRecord</code>；
- <code>HotelCandidatesInsufficientRecord</code>；
- <code>HotelDiscoveryResult</code>；
- <code>HotelDiscoveryArtifact</code>；
- <code>HotelSelectedDecision</code>；
- <code>HotelDeclinedDecision</code>；
- <code>HotelSelectionDecision</code>；
- <code>HotelSelectionArtifact</code>；
- <code>HOTEL_ARTIFACT_REGISTRATIONS</code>。

Artifact 可达类型的精确字段顺序：

<pre><code>HotelSearchSuccessEvidence(
    schema_version required,
    outcome_type required Literal["success"],
    places required length=1..10,
)
HotelSearchNoResultEvidence(
    schema_version required,
    outcome_type required Literal["no_result"],
)
HotelSearchFailedEvidence(
    schema_version required,
    outcome_type required Literal["failed"],
    failure_code required,
)
HotelPlaceSearchArtifact(
    schema_version required,
    search_ordinal required,
    request_hash required,
    source required,
    verified required,
    retrieved_at required,
    evidence required,
)
HotelCandidatesReadyRecord(
    schema_version required,
    result_type required Literal["ready"],
    candidates required length=3..5,
)
HotelCandidatesInsufficientRecord(
    schema_version required,
    result_type required Literal["insufficient"],
    candidates required length=0..2,
)
HotelDiscoveryArtifact(
    schema_version required,
    confirmation_summary_digest required,
    query_plan_hash required,
    search_operation_ids required length=3,
    search_refs required length=1..3,
    result required,
)
HotelSelectedDecision(
    schema_version required,
    decision_type required Literal["selected"],
    selected_candidate required,
)
HotelDeclinedDecision(
    schema_version required,
    decision_type required Literal["declined"],
)
HotelSelectionArtifact(
    schema_version required,
    candidate_set_ref required,
    decision required,
)</code></pre>

注册顺序固定：

1. <code>hotel.place_search/1.0</code>；
2. <code>hotel.candidate_set/1.0</code>；
3. <code>hotel.selection/1.0</code>。

不得注册 Module 06 DTO；必须逐字段投影成 Module 07 Artifact DTO。

### 8.3 errors.py

实现固定 code：

<pre><code>invalid_input
artifact_replay_mismatch
candidate_identity_conflict</code></pre>

异常不得保存 UID、query、destination、坐标、Artifact content、底层异常或 traceback 副本。原生 <code>__traceback__</code> 不进入业务序列化。
三个公开子类构造函数无参数，code 为 class-level 固定值，<code>args == (code,)</code>。

### 8.4 protocols.py

<code>HotelCandidateSelectionPort</code> 只声明两个 async 方法：

<pre><code>async def discover_candidates(
    self,
    confirmed: ConfirmedTravelRequirement,
    operation_ids: HotelCandidateOperationIds,
) -> HotelCandidateDiscoveryOutcome: ...

async def record_selection(
    self,
    request: HotelSelectionRequest,
    *,
    active_candidate_set_ref: ArtifactRef,
) -> HotelSelectionOutcome: ...</code></pre>

不得声明 Agent、LLM、Graph、interrupt、close 或 context-manager 方法。

## 9. service 构造合同

<code>HotelCandidateSelectionService</code> 构造参数必须显式注入：

<pre><code>adapter: BaiduMapAdapter
repository: ArtifactRepository
budget_manager: ExecutionBudgetManager
utc_clock: UtcClock</code></pre>

构造函数：

- exact/Protocol capability 检查必须与项目现有风格一致；
- 不读取环境；
- 不创建 adapter/repository/manager；
- 不启动网络或 event loop；
- 不保存全局 singleton；
- 不拥有依赖的 close；
- 不接受任意 dict settings；
- 不保留调用方可变 DTO 引用。

<code>HOTEL_PLACE_CALL_BUDGET_KEY</code> 唯一位于 <code>models.py</code>，从包根导出；
<code>service.py</code> 只能导入使用，不得复制构造。不得修改 Module 04。

## 10. query_policy 算法

### 10.1 查询构造伪代码

<pre><code>def build_queries(confirmed):
    require exact ConfirmedTravelRequirement
    destination = confirmed.requirement.destination.strip()
    preference = confirmed.requirement.preferences.hotel_preferences
    if preference is not None:
        preference = preference.strip()
    reject blank destination or blank non-null preference

    raw = []
    if preference is not None:
        raw.append(preference + " 酒店")
    raw.extend(("酒店", "宾馆", "住宿"))

    typed_requests = []
    for raw_query in raw:
        request = construct strict PlaceSearchRequest(raw_query, destination)
        append request only when request.query differs from every earlier request.query
    requests = tuple(typed_requests[:3])
    require len(requests) == 3
    return requests</code></pre>

每条 <code>PlaceSearchRequest</code>：

- schema 1.0；
- query 为上表；
- tag=None；
- city region 为 destination；
- result_limit=10。

不得从 room type、mobility、预算或日期生成额外关键词；这些信息没有百度事实资格。

### 10.2 hash

私有 helper 必须先取得 typed request 的
<code>model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)</code>
结果，再对该 JSON-safe tree 做 UTF-8 canonical encoding 和 SHA-256。测试必须证明：

- 同输入相同 hash；
- 顺序变化 hash 改变；
- query 变化 hash 改变；
- hash 字符串符合 <code>ContentHash</code>；
- Artifact 和 error 中没有独立 query/destination 请求字段或其输入副本；测试按结构断言，
  不得因百度候选 address 事实自然包含目的地文字而误报泄漏。

### 10.3 category 资格

精确使用 <code>re.split(r"[;；,，/|>]+", category)</code>；批准分隔符只有
U+003B、U+FF1B、U+002C、U+FF0C、U+002F、U+007C、U+003E。全角斜线、反斜线和其他
字符不切分。随后实现权威设计 §13.1 的 token 集：

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

只允许 exact token；不得使用 <code>in category</code> substring、模糊搜索、名称兜底或自增 allowlist。
切分结果必须逐项执行 <code>token.strip()</code>，丢弃 strip 后的空字符串，再对余下 token
做 exact allowlist 匹配。不得因分隔符两侧空白或连续分隔符误拒有效住宿 token。

### 10.4 UID 处理

<pre><code>seen_identity_by_uid = {}
candidates = []

for every returned place in provider order:
    identity = (
        place.name,
        place.address,
        place.coordinate.latitude,
        place.coordinate.longitude,
    )

    if uid already seen:
        if seen identity differs:
            raise HotelCandidateIdentityConflictError
    else:
        seen_identity_by_uid[uid] = identity

    if not qualified_category(place.category):
        continue
    if uid already emitted as candidate:
        continue
    if len(candidates) < 5:
        candidates.append(project_candidate(place))</code></pre>

必须处理本次 success 的全部最多 10 条，以检测已见 UID 冲突；不能在第三个或第五个候选处提前跳出当前响应。

## 11. Artifact、预算、adapter 调用顺序

三类 Artifact 只允许通过下列唯一私有 helper 构造写请求；search、candidate-set 和
selection 路径不得内联或复制 <code>model_dump</code> 逻辑：

<pre><code>def _artifact_write_request(
    *,
    operation_id: OperationId,
    artifact_type: Literal[
        "hotel.place_search",
        "hotel.candidate_set",
        "hotel.selection",
    ],
    artifact: (
        HotelPlaceSearchArtifact
        | HotelDiscoveryArtifact
        | HotelSelectionArtifact
    ),
) -> ArtifactWriteRequest:
    content = artifact.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    if type(content) is not dict:
        raise HotelInputError()
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type=artifact_type,
        schema_version="1.0",
        content=content,
    )</code></pre>

该 helper 必须保持私有，不进入包根 <code>__all__</code>。合同测试必须对三个 artifact type
逐一断言它们经过该 helper、content 为 exact <code>dict</code>，且 prepare/put/get/canonical
round-trip 通过。

### 11.1 candidate-set 快速重放

<pre><code>validate and fresh-copy inputs
build requests and query_plan_hash
candidate_lookup = await repository.lookup(
    operation_id=operation_ids.candidate_set_operation_id,
)

if isinstance(candidate_lookup, ArtifactFound):
    candidate_read = await repository.get(
        candidate_lookup.ref,
        expected_model=HotelDiscoveryArtifact,
    )
    compare confirmation digest
    compare query_plan_hash
    compare all three search_operation_ids in order
    compare candidate_read.ref.operation_id with candidate-set operation
    any mismatch -> HotelReplayError
    return fresh typed outcome from candidate_read.content.result

if not isinstance(candidate_lookup, ArtifactAbsent):
    raise HotelReplayError
run search loop</code></pre>

### 11.2 每个搜索槽

<pre><code>search_lookup = await repository.lookup(
    operation_id=search_operation_id,
)

if isinstance(search_lookup, ArtifactFound):
    search_read = await repository.get(
        search_lookup.ref,
        expected_model=HotelPlaceSearchArtifact,
    )
    compare search_read.ref.operation_id with search_operation_id
    compare search_read.content.search_ordinal with ordinal
    compare search_read.content.request_hash with request_hash
    any mismatch -> HotelReplayError
    search_ref = search_read.ref
    evidence = search_read.content.evidence

elif isinstance(search_lookup, ArtifactAbsent):
    budget_outcome = await budget_manager.consume(
        BudgetConsumeRequest(
            operation_id=search_operation_id,
            key=HOTEL_PLACE_CALL_BUDGET_KEY,
            amount=1,
        )
    )

    if isinstance(budget_outcome, BudgetExceeded):
        do not call adapter
        return HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
            failed_search_ordinal=ordinal,
            qualified_count=len(candidates),
            failure_search_ref=None,
        )

    if not isinstance(budget_outcome, BudgetConsumed):
        raise HotelReplayError()
    if budget_outcome.disposition == "replayed":
        raise OperationResultIndeterminateError()

    try:
        map_outcome = await adapter.search_places(request)
    except BaiduMapError as error:
        project error.code to HotelMapFailureCode
        build failed HotelPlaceSearchArtifact with verified=False
    else:
        if map_outcome.provenance.verified is not True:
            build failed artifact with UNVERIFIED_RESULT and verified=False
        else:
            project success/no_result and exact provenance

    search_write = _artifact_write_request(
        operation_id=search_operation_id,
        artifact_type="hotel.place_search",
        artifact=search_artifact,
    )
    search_ref = await repository.put(search_write)
    evidence = search_artifact.evidence

else:
    raise HotelReplayError

continue or return map_call_failed only after search_ref and evidence both exist</code></pre>

构造细节不得留给实现者猜测：

- success/no-result：父 Artifact 的 source、verified、retrieved_at 逐字段复制
  <code>map_outcome.provenance</code>，evidence 使用对应 required schema/discriminator；
- unverified result：source 和 retrieved_at 复制 provenance，verified=false，
  failure code=<code>UNVERIFIED_RESULT</code>；
- typed exception：source=<code>"baidu_maps_mcp"</code>、verified=false、
  retrieved_at=<code>utc_clock.now()</code>，failure code 由
  <code>error.code</code> 穷尽映射；
- 三者都显式传 schema_version、search_ordinal、request_hash 和 evidence 全部字段；
- <code>utc_clock.now()</code> 必须 UTC aware，否则 <code>HotelInputError</code>。

### 11.3 搜索推进

- no-result：继续下一 query；
- success 但零合格候选：继续；
- success 后累计 1～2：继续；
- success 后累计 3～5：完成当前响应处理后停止；
- failed evidence：立即返回 <code>map_call_failed</code>；
- 三槽完成仍 0～2：put insufficient discovery artifact；
- ready/insufficient candidate-set put 完成后才允许进入人工 await。

ready 或 insufficient 的统一写入必须保存完整三个
<code>operation_ids.search_operation_ids</code>，实际 <code>search_refs</code> 只能是其前缀；
只能调用 §11 唯一私有 helper 构造
<code>candidate_write = _artifact_write_request(
operation_id=operation_ids.candidate_set_operation_id,
artifact_type="hotel.candidate_set", artifact=discovery_artifact)</code>，并执行
<code>candidate_ref = await repository.put(candidate_write)</code>。

### 11.4 不确定状态

如果预算 replayed 但 Artifact absent：

- 必须抛 Module 04 <code>OperationResultIndeterminateError</code>；
- 不得再次调用 adapter；
- 不得消费新 OperationId 规避；
- 不得转成 no-result 或 insufficient。

## 12. 人工选择算法

<pre><code>fresh-copy request and active_candidate_set_ref
if request.candidate_set_ref != active_candidate_set_ref:
    return rejected(STALE_CANDIDATE_SET)

candidate_read = await repository.get(
    active_candidate_set_ref,
    expected_model=HotelDiscoveryArtifact,
)
validate selection operation phase/run
require selection operation differs from candidate_read.ref.operation_id
require selection operation differs from every
        candidate_read.content.search_operation_ids item
if candidate_read.content.result is not ready:
    return rejected(CANDIDATE_SET_NOT_READY)

selection_lookup = await repository.lookup(
    operation_id=request.selection_operation_id,
)
if isinstance(selection_lookup, ArtifactFound):
    selection_read = await repository.get(
        selection_lookup.ref,
        expected_model=HotelSelectionArtifact,
    )
    if same candidate ref and equivalent command:
        return selected/declined replay
    return rejected(DECISION_CONFLICT)
if not isinstance(selection_lookup, ArtifactAbsent):
    raise HotelReplayError

if command is select:
    find exact uid in candidate_read.content.result.candidates
    not found -> rejected(CANDIDATE_UID_NOT_FOUND)
    decision = HotelSelectedDecision(
        schema_version="1.0",
        decision_type="selected",
        selected_candidate=fresh exact candidate,
    )
else:
    decision = HotelDeclinedDecision(
        schema_version="1.0",
        decision_type="declined",
    )

selection_artifact = HotelSelectionArtifact(
    schema_version="1.0",
    candidate_set_ref=active_candidate_set_ref,
    decision=decision,
)
selection_write = _artifact_write_request(
    operation_id=request.selection_operation_id,
    artifact_type="hotel.selection",
    artifact=selection_artifact,
)
try:
    selection_ref = await repository.put(selection_write)
except ArtifactConflictError:
    race_lookup = await repository.lookup(
        operation_id=request.selection_operation_id,
    )
    if not isinstance(race_lookup, ArtifactFound):
        raise HotelReplayError
    race_read = await repository.get(
        race_lookup.ref,
        expected_model=HotelSelectionArtifact,
    )
    if same candidate ref and equivalent command:
        return selected/declined replay
    return rejected(DECISION_CONFLICT)
return selected/declined with selection_ref</code></pre>

禁止：

- 接受名称、序号或自由文本替代 UID；
- 自动选择第一家；
- decline 后继续搜索；
- 覆盖旧 selection；
- 选择 insufficient candidate set；
- 由 Module 07 实现 interrupt/resume。

## 13. typed outcome 与异常对照

| 场景 | 固定结果 |
|---|---|
| 3～5 个候选 | <code>HotelCandidatesReady</code> |
| 三次后 0～2 | <code>HotelCandidatesInsufficient</code> |
| Place 预算不足 | <code>HotelCandidateDiscoveryFailed(budget_exhausted)</code> |
| Module 06 typed error | 先持久化 failed search evidence，再返回带该 ref 的 <code>map_call_failed</code> |
| 同 UID 身份冲突 | <code>HotelCandidateIdentityConflictError</code> |
| Artifact request hash/digest 不同 | <code>HotelReplayError</code> |
| budget replayed + artifact absent | Module 04 <code>OperationResultIndeterminateError</code> |
| Artifact conflict/integrity/registry 错误 | 保留 Module 04 typed error |
| 候选内 UID | <code>HotelSelected</code> |
| 用户拒绝全部 | <code>HotelSelectionDeclined</code> |
| 候选外 UID | <code>HotelSelectionRejected(candidate_uid_not_found)</code> |
| insufficient candidate set | <code>HotelSelectionRejected(candidate_set_not_ready)</code> |
| 旧 candidate ref | <code>HotelSelectionRejected(stale_candidate_set)</code> |
| 同 selection operation 不同决定 | <code>HotelSelectionRejected(decision_conflict)</code> |
| cancellation | 原样传播，不写猜测 Artifact |

## 14. 阶段 07-1：公共 DTO、Error 与 Artifact

### 14.1 目标

实现无副作用的公共模型、三类 Artifact 注册和安全异常。

### 14.2 文件白名单

<pre><code>app/hotel_selection/__init__.py
app/hotel_selection/models.py
app/hotel_selection/artifacts.py
app/hotel_selection/errors.py
tests/unit/hotel_selection/test_models.py
tests/unit/hotel_selection/test_artifacts.py
tests/unit/hotel_selection/test_errors.py
tests/unit/hotel_selection/test_serialization.py
tests/contract/hotel_selection/test_public_api.py
tests/contract/hotel_selection/test_artifact_registration.py
tests/contract/hotel_selection/test_import_boundaries.py</code></pre>

### 14.3 验收

- 07-1 与 07-2 的阶段性 <code>__all__</code> 精确等于 §7 最终清单删除
  <code>HotelCandidateSelectionPort</code> 和
  <code>HotelCandidateSelectionService</code> 后的有序投影；
- 不得为这两个尚未实施的符号创建 stub、占位类或延迟 import；
- <code>test_public_api.py</code> 在 07-1 明确断言该阶段投影；
- 字段、顺序、strict/frozen/extra、required/nullable/tuple 精确；
- union discriminator 可 strict JSON round-trip；
- Registry 构造成功；
- 三类 Artifact 都只能调用 §11 唯一私有 helper；helper 使用
  <code>model_dump(mode="json", exclude_none=False, by_alias=False,
  exclude_computed_fields=True)</code>，断言 content 的 exact type 为 <code>dict</code>，再构造
  <code>ArtifactWriteRequest</code>；三类路径均通过 prepare/put/get/decode fresh-copy；
- 三个 type/version 精确；
- error/repr 安全；
- 包根没有越权导出。

## 15. 阶段 07-2：确定性查询和资格纯函数

### 15.1 目标

实现 query plan、hash、category 资格、投影和 UID 去重纯函数。

### 15.2 文件白名单

<pre><code>app/hotel_selection/query_policy.py
app/hotel_selection/__init__.py
tests/unit/hotel_selection/test_query_policy.py
tests/unit/hotel_selection/test_qualification.py
tests/contract/hotel_selection/test_security_contract.py</code></pre>

### 15.3 验收

- preference + 酒店/宾馆/住宿稳定去重取三；
- request result_limit=10、tag=None、city area；
- exact category token，无名称兜底；
- 七个批准分隔符逐项正测，分隔符两侧空白、连续分隔符、仅空白 token 清理测试，
  全角斜线、反斜线和未批准字符负测；
- UID 冲突矩阵完整；
- 最多 5 候选并保持供应商顺序；
- hash 不泄漏原文；
- 无 I/O、环境或网络。

### 15.4 07-1 + 07-2 统一测试

相邻两阶段完成后统一执行一次：

<pre><code>tests/unit/hotel_selection/test_models.py
tests/unit/hotel_selection/test_artifacts.py
tests/unit/hotel_selection/test_errors.py
tests/unit/hotel_selection/test_serialization.py
tests/unit/hotel_selection/test_query_policy.py
tests/unit/hotel_selection/test_qualification.py
tests/contract/hotel_selection/test_public_api.py
tests/contract/hotel_selection/test_artifact_registration.py
tests/contract/hotel_selection/test_import_boundaries.py
tests/contract/hotel_selection/test_security_contract.py</code></pre>

失败后只重跑失败项和直接受影响项，不重复跑已通过集合获取偶然绿灯。

### 15.5 07-1 + 07-2 联合授权合同

前置条件：

- 分支精确为 <code>feature/module-07-hotel-selection</code>；
- 两份 Module 07 文档已批准且 hash 与提示词一致；
- 工作区除提示词明确列出的已批准设计文件外无漂移，staged 为空；
- Module 01/04/06 前置完成证据有效；
- §14.2 与 §15.2 目标实现/测试文件在开始前不存在，或状态与提示词逐项声明一致。

联合文件白名单精确为 §14.2 和 §15.2 两个代码块的并集。正式提示词必须把并集逐行展开，
不得只写章节引用。

允许操作：读取真源、使用 <code>apply_patch</code> 创建/修改联合白名单文件、只读 Git 检查、
按 §20 构建 test image，并在 network-none 安全容器中执行一次：

<pre><code>python -m pytest -q -p no:cacheprovider tests/unit/hotel_selection/test_models.py tests/unit/hotel_selection/test_artifacts.py tests/unit/hotel_selection/test_errors.py tests/unit/hotel_selection/test_serialization.py tests/unit/hotel_selection/test_query_policy.py tests/unit/hotel_selection/test_qualification.py tests/contract/hotel_selection/test_public_api.py tests/contract/hotel_selection/test_artifact_registration.py tests/contract/hotel_selection/test_import_boundaries.py tests/contract/hotel_selection/test_security_contract.py</code></pre>

PASS 必须逐条满足 §14.3、§15.3 和本测试集合，唯一下一状态：
<code>MODULE07_PHASES_01_02_COMPLETE_AWAITING_03_04_AUTHORIZATION</code>。
任一前置、白名单、合同或测试失败按 §23 STOP；报告使用 §24。

## 16. 阶段 07-3：候选发现与 replay/budget

### 16.1 目标

实现 Protocol、service 构造、candidate-set replay 和三槽外部调用编排。

### 16.2 文件白名单

<pre><code>app/hotel_selection/protocols.py
app/hotel_selection/service.py
app/hotel_selection/__init__.py
tests/support/hotel_selection_fakes.py
tests/unit/hotel_selection/test_service_discovery.py
tests/contract/hotel_selection/test_public_api.py
tests/contract/hotel_selection/test_replay_budget_contract.py
tests/contract/hotel_selection/test_upstream_integration.py</code></pre>

### 16.3 fake 要求

fake adapter：

- 实现 <code>BaiduMapAdapter</code> 所需 async 方法签名；
- Module 07 测试只调用 <code>search_places</code>；
- 可脚本化 success/no-result/每类 typed exception/cancellation；
- 可合成 verified=true 的生产合同响应和 verified=false 的拒绝路径；所有数据明确为测试合成，不得报告为 live 证据；
- 记录 fresh typed request 和调用次数；
- 不 sleep、不联网、不读取环境；
- <code>geocode</code>、<code>reverse_geocode</code>、<code>get_place_details</code>、
  <code>calculate_route_matrix</code>、<code>plan_directions</code>、<code>get_weather</code>
  六个非 search 方法全部记录调用并立即失败；
- 合同测试逐项断言上述六个调用次数均为 0。

### 16.4 验收

- 07-3 增加 Protocol 和 concrete service 后，<code>__all__</code> 一次收口为 §7
  最终精确清单，<code>test_public_api.py</code> 改为最终断言；
- 完整 lookup/get/consume/call/put 顺序；
- budget limit 3；
- found replay 零调用；
- success、no-result、failed 三种已存在 search Artifact 分别重放，均断言
  consume=0、adapter call=0，且继续使用原 <code>search_read.ref</code>；
- no-result 与过滤不足推进；
- ready 早停；
- 第三次搜索才达到 3 时生成 ready，并含三个 search refs；
- candidate-set 快速重放比较完整三个 search OperationId；
- map failure Artifact；
- verified=false 固定映射为 unverified_result，不能形成候选；
- budget failure 的 failure_search_ref=None，map failure 携带精确 failed search ref；
- indeterminate；
- cancel；
- candidate-set 最终 Artifact 在返回前存在。

## 17. 阶段 07-4：人工选择

### 17.1 目标

实现 selected、declined、rejected、stale、conflict 和 selection Artifact 幂等。

### 17.2 文件白名单

<pre><code>app/hotel_selection/service.py
app/hotel_selection/models.py
app/hotel_selection/artifacts.py
app/hotel_selection/__init__.py
tests/unit/hotel_selection/test_service_selection.py
tests/contract/hotel_selection/test_selection_contract.py</code></pre>

### 17.3 验收

- select 只接受 exact candidate UID；
- decline 不生成 selected candidate；
- active ref 七字段比较；
- exact replay 返回同 ref；
- 同 OperationId 不同决定 rejection；
- barrier 并发同决定幂等、不同决定稳定 rejection；
- selection OperationId 与 candidate-set/三个 search OperationId 全部不同；
- 不覆盖 artifact；
- 不调用 adapter、不消费预算；
- insufficient 不可选。

### 17.4 07-3 + 07-4 统一测试

统一执行一次：

<pre><code>tests/unit/hotel_selection/test_service_discovery.py
tests/unit/hotel_selection/test_service_selection.py
tests/contract/hotel_selection/test_replay_budget_contract.py
tests/contract/hotel_selection/test_selection_contract.py
tests/contract/hotel_selection/test_upstream_integration.py</code></pre>

并重跑 07-1/07-2 中直接受 public API 或 Artifact 变化影响的项目；不得无原因扩大。

### 17.5 07-3 + 07-4 联合授权合同

前置条件：

- 分支、设计 hash、staged 和漂移规则同 §15.5；
- 07-1+07-2 状态精确为
  <code>MODULE07_PHASES_01_02_COMPLETE_AWAITING_03_04_AUTHORIZATION</code>；
- §15.4 测试已通过，且无未授权文件；
- §16.2 与 §17.2 文件状态在提示词中逐项列明。

联合文件白名单精确为 §16.2 和 §17.2 两个代码块的并集，正式提示词必须逐行展开。
允许操作与 §15.5 相同，只能在联合白名单内写入。两个阶段完成后在 §20 安全容器中执行一次：

<pre><code>python -m pytest -q -p no:cacheprovider tests/unit/hotel_selection/test_service_discovery.py tests/unit/hotel_selection/test_service_selection.py tests/contract/hotel_selection/test_public_api.py tests/contract/hotel_selection/test_replay_budget_contract.py tests/contract/hotel_selection/test_selection_contract.py tests/contract/hotel_selection/test_upstream_integration.py</code></pre>

公共 API/Artifact 直接受影响时，把 §15.4 中精确受影响文件追加到同一次 pytest 命令；
必须在运行前列明原因，不能先重复跑再解释。PASS 唯一下一状态：
<code>MODULE07_PHASES_03_04_COMPLETE_AWAITING_05_06_AUTHORIZATION</code>。
STOP 和报告分别使用 §23、§24。

## 18. 阶段 07-5：安全、边界与覆盖率 harness

### 18.1 目标

补齐安全、容量、取消、import boundary 和三指标 coverage 的单进程 harness。

### 18.2 文件白名单

<pre><code>tests/support/run_module07_coverage.py
tests/contract/hotel_selection/test_security_contract.py
tests/contract/hotel_selection/test_import_boundaries.py
tests/contract/hotel_selection/test_public_api.py
tests/unit/hotel_selection/test_service_discovery.py
tests/unit/hotel_selection/test_service_selection.py
app/hotel_selection/__init__.py
app/hotel_selection/artifacts.py
app/hotel_selection/errors.py
app/hotel_selection/models.py
app/hotel_selection/protocols.py
app/hotel_selection/query_policy.py
app/hotel_selection/service.py</code></pre>

上述七个生产文件只允许修复本阶段测试揭示的、权威设计范围内的小问题；若需重构公共合同或扩大仓库范围必须停止。

### 18.3 coverage harness

<code>tests/support/run_module07_coverage.py</code> 必须：

1. 在一个 Python 进程内以 coverage API 启动；
2. <code>branch=True</code>；
3. source 精确为 <code>app.hotel_selection</code>；
4. 通过 pytest API 一次执行 <code>tests/unit/hotel_selection</code> 和 <code>tests/contract/hotel_selection</code>；
5. 将 JSON 写入容器 <code>/tmp/module07-coverage.json</code>；
6. 读取 totals 计算 line、branch、combined；
7. 任一指标低于 95.00% 或 pytest 非零时非零退出；
8. 不吞 pytest 参数、不重跑测试、不修改源码、不联网。

安全合同测试必须包含权威设计 §18.2 两套失败优先级的相邻碰撞矩阵；不能只分别覆盖单个错误。

公式：

<pre><code>line = 100 * covered_lines / num_statements
branch = 100 * covered_branches / num_branches
combined = 100 * (covered_lines + covered_branches)
           / (num_statements + num_branches)</code></pre>

<code>num_statements=0</code> 失败；<code>num_branches=0</code> 时 branch=100；combined 分母为 0 失败。阈值用未舍入分子/分母判断，报告时才显示两位小数。

## 19. 阶段 07-6：模块 Gate 与独立审查准备

### 19.1 目标

在不做 closeout 的情况下完成 Module 07 离线资格和独立代码审查输入。

### 19.2 文件白名单

原则上无生产写入。只允许在明确修复授权下修改：

<pre><code>app/hotel_selection/__init__.py
app/hotel_selection/artifacts.py
app/hotel_selection/errors.py
app/hotel_selection/models.py
app/hotel_selection/protocols.py
app/hotel_selection/query_policy.py
app/hotel_selection/service.py
tests/unit/hotel_selection/test_artifacts.py
tests/unit/hotel_selection/test_errors.py
tests/unit/hotel_selection/test_models.py
tests/unit/hotel_selection/test_query_policy.py
tests/unit/hotel_selection/test_qualification.py
tests/unit/hotel_selection/test_service_discovery.py
tests/unit/hotel_selection/test_service_selection.py
tests/unit/hotel_selection/test_serialization.py
tests/contract/hotel_selection/test_artifact_registration.py
tests/contract/hotel_selection/test_import_boundaries.py
tests/contract/hotel_selection/test_public_api.py
tests/contract/hotel_selection/test_replay_budget_contract.py
tests/contract/hotel_selection/test_selection_contract.py
tests/contract/hotel_selection/test_upstream_integration.py
tests/contract/hotel_selection/test_security_contract.py
tests/support/hotel_selection_fakes.py
tests/support/run_module07_coverage.py</code></pre>

禁止修改 README、状态页、Dockerfile、上游模块、依赖或配置。

### 19.3 07-5 + 07-6 统一 Gate

从当前源码构建 test target 后，在一次新容器内：

- <code>--rm</code>；
- <code>--network none</code>；
- <code>--read-only</code>；
- <code>--tmpfs /tmp:rw,nosuid,nodev,size=256m</code>；
- <code>--security-opt no-new-privileges:true</code>；
- 不传 <code>.env</code> 或 Secret。

固定执行：

1. 只执行一次 <code>python tests/support/run_module07_coverage.py</code>；该 harness
   同时承担 Module 07 全 unit/contract 和三指标 coverage；
2. 只执行一次互补完整回归：
   <code>python -m pytest -q -p no:cacheprovider tests/unit tests/contract --ignore=tests/unit/hotel_selection --ignore=tests/contract/hotel_selection</code>；
   它覆盖 Module 01/04/06 冻结上游回归及其余全部离线测试；
3. 上述两项收集集合的并集必须精确等于项目完整 unit/contract，不遗漏、不重复 Module 07；
4. <code>python -m compileall -q app tests</code>；
5. 两个 pytest 集合的 collect 数量都非零；
6. skip/xfail/rerun 为 0；
7. diff、whitespace、pycache、白名单和容器残留检查。

每项原则上只执行一次。失败后只重跑失败项、直接受影响项和冻结 Gate。

### 19.4 07-5 + 07-6 联合授权合同

前置条件：

- 分支、设计 hash、staged 和漂移规则同 §15.5；
- 07-3+07-4 状态精确为
  <code>MODULE07_PHASES_03_04_COMPLETE_AWAITING_05_06_AUTHORIZATION</code>；
- §17.4 测试通过，Artifact/预算/选择报告完整；
- 独立审查能力可用。

联合文件白名单精确为 §18.2 与 §19.2 两个代码块的去重并集，正式提示词必须逐行展开。
允许操作：白名单内小修、coverage harness、安全测试、§20 镜像构建和 §19.3 Gate；
禁止 closeout 文件、stage、commit 或任何外部网络。

固定命令只有：

<pre><code>python tests/support/run_module07_coverage.py
python -m pytest -q -p no:cacheprovider tests/unit tests/contract --ignore=tests/unit/hotel_selection --ignore=tests/contract/hotel_selection
python -m compileall -q app tests</code></pre>

三项各执行一次；前两项测试集合并集完整、交集为空。PASS 后必须进入独立代码审查，
唯一阶段状态：
<code>MODULE07_IMPLEMENTATION_GATES_PASSED_AWAITING_INDEPENDENT_REVIEW</code>。
审查 P0/P1/P2 清零后才是 <code>AWAITING_CLOSEOUT_AUTHORIZATION</code>。
STOP 和报告分别使用 §23、§24。

## 20. Docker 构建与安全容器

未来提示词必须按当时批准的 Docker 工作流给出完整命令，并使用临时、任务专属 Docker config；不得复用 HOME 变量。

最低安全要求：

未来提示词必须把镜像名和测试命令展开为实际值；等价 PowerShell 骨架如下：

<pre><code>$module07DockerConfig = Join-Path ([System.IO.Path]::GetTempPath()) "codex-module07-docker"
$module07TestImage = "china-travel-agent:module07-test"
docker --config $module07DockerConfig buildx build --load --progress plain --tag $module07TestImage --target test --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim .
docker --config $module07DockerConfig run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m --security-opt no-new-privileges:true $module07TestImage python -m pytest -q -p no:cacheprovider tests/unit/hotel_selection tests/contract/hotel_selection</code></pre>

实现阶段不得：

- 注入 <code>DEEPSEEK_API_KEY</code> 或 <code>BAIDU_MAPS_API_KEY</code>；
- 读取 <code>.env</code>；
- 启动 MCP、DNS、PostgreSQL 或 live；
- 使用已有容器的偶然状态；
- 把宿主机测试冒充容器 Gate。

## 21. 独立代码审查

Module 07 Gate 通过后必须由与实施者不同的只读 Reviewer 检查：

- 逐条对照两份已批准 Module 07 文档；
- 公共 DTO、Artifact、budget key 和状态机；
- 直接依赖与 import boundary；
- UID/category/事实可信；
- Artifact replay 与 indeterminate；
- selected/declined/stale/conflict；
- cancellation；
- 安全字段和 repr；
- coverage 计算；
- 文件白名单；
- 无 LLM、MCP、Graph、live 和 Secret。

格式：

<pre><code>文件：
章节或行号：
等级：P0 / P1 / P2 / P3
问题：
后果：
最小修复建议：</code></pre>

P0/P1/P2 未清零不得进入 closeout。Reviewer 不修改文件。

## 22. 修复规则

- 小范围且明确的问题可在当前阶段白名单内直接修复；
- 修复后只重跑失败项、直接受影响项和冻结 Gate；
- 需要改变已确认设计、公共 API、Artifact 版本、预算、依赖或仓库范围时立即停止；
- 不得为了测试通过弱化断言、增加 skip/xfail/rerun、捕获过宽异常或降低 coverage；
- 子 Reviewer 不得直接改代码；
- 修复完成后需由原 Reviewer 复核。

## 23. PASS 与 STOP

### 23.1 阶段 PASS

只有以下同时满足才可报告阶段完成：

- 当前授权的两个相邻阶段全部目标完成；
- 文件变化精确位于白名单；
- 定向测试和阶段 Gate 通过；
- 无 skip/xfail/rerun；
- staged 为空；
- 无 commit、push、PR、deploy；
- 对应设计条目逐项给出证据。

### 23.2 STOP

出现任一项立即停止：

- baseline/hash/worktree 漂移；
- 上游合同冲突；
- 需要修改 Module 01/04/06；
- 需要新增 LLM/ReAct、Place Details、Geocoding、MCP 或依赖；
- 需要读网络、Secret、<code>.env</code> 或数据库；
- 需要扩大文件白名单；
- Artifact replay 需要重复调用；
- 无法达到 95% 三指标；
- 独立审查 P0/P1/P2 未清零；
- staged 非空或出现异常文件；
- 未取得下一类授权。

固定状态根据实际选择：

<pre><code>BLOCKED_BASELINE_DRIFT
BLOCKED_UPSTREAM_CONTRACT_CONFLICT
BLOCKED_SCOPE_EXPANSION_REQUIRED
BLOCKED_REPLAY_SAFETY
BLOCKED_TEST_GATE
BLOCKED_REVIEW_FINDINGS
AWAITING_NEXT_STAGE_AUTHORIZATION
AWAITING_CLOSEOUT_AUTHORIZATION
AWAITING_COMMIT_AUTHORIZATION</code></pre>

不得在 closeout 前宣称 <code>MODULE07_COMPLETE</code>。

## 24. 实施阶段报告模板

每次阶段报告至少包含：

<pre><code>任务：
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
- 设计文档：
- 共享执行规范：
- 上游合同：
- 基线漂移：

修改：
- 生产文件：
- 测试文件：
- 白名单外文件：
- 依赖：
- migration：

设计逐条对照：
- 当前阶段目标：
- 公共合同：
- Artifact：
- 预算/replay：
- UID/category：
- 人工选择：
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
- 未决问题：
- STOP 条件：

外部操作：
- commit：
- merge：
- push：
- PR：
- deploy：

下一步：</code></pre>

migration 固定为 <code>NOT_RUN — Module 07 无数据库迁移</code>。未授权操作必须明确写 <code>NOT_RUN / NOT_AUTHORIZED</code>。

## 25. MODULE-CLOSEOUT

MODULE-CLOSEOUT 是实施和独立代码审查通过后的独立阶段，必须取得新的明确授权。

### 25.1 closeout 文件白名单

<pre><code>README.md
docs/IMPLEMENTATION_STATUS.md
Dockerfile</code></pre>

如真实影响要求修改其他文件，停止并申请授权。不得顺手改生产代码或测试；发现代码问题返回对应修复阶段。

### 25.2 closeout 内容

1. README 同步 Module 01～07 的真实状态；
2. IMPLEMENTATION_STATUS 同步 Module 07 的实施、测试、审查和 Git 事实；
3. 检查 Dockerfile：
   - <code>COPY app ./app</code> 已覆盖新包；
   - runtime 默认 import 自检必须从模块 01～06更新为诚实覆盖 01～07；
   - test target 继续复制 tests；
4. 不修改依赖，因为 Module 07 无新增依赖；
5. 不新增 live、Secret、Provider Discovery 或数据库配置。

### 25.3 closeout 后必须重新构建与回归

同步完成后必须从当前源码重新构建 runtime/test 镜像，并在安全容器中重新执行：

1. runtime 默认 CMD；
2. Module 07 coverage harness 一次，覆盖其全 unit/contract 及 line/branch/combined；
3. 排除两个 Module 07 测试目录的互补完整 unit/contract 一次，其中包含
   Module 01/04/06 冻结上游回归；
4. 两个测试集合的并集完整、交集为空；
5. compileall；
6. diff、whitespace、pycache、白名单；
7. staged；
8. 容器残留；
9. closeout 独立只读审查。

closeout 前通过的镜像和测试不能替代同步后的重建结果。

### 25.4 closeout 报告

必须至少包含：

<pre><code>分支：
开始 HEAD：
结束 HEAD：
修改文件：
migration：
依赖：
runtime image：
test image：
runtime 默认 CMD：
Module 07 Gate：
上游回归：
完整 unit/contract：
line：
branch：
combined：
skip/xfail/rerun：
异常文件：
staged：
commit：
merge：
push：
PR：
deploy：
最终状态：</code></pre>

只有 closeout、重建回归和独立审查都通过，且 P0/P1/P2 为 0，才可报告：

<pre><code>MODULE07_READY_FOR_COMMIT_AUTHORIZATION</code></pre>

该状态仍不表示已经 commit 或 merge。

## 26. commit、merge、push、PR 与 deploy

- 实施模型、修复模型和 closeout 模型一律不得 stage 或 commit；
- 只有独立审查通过并取得新的 commit 授权后，才可执行明确提交；
- commit 授权不等于 merge 授权；
- merge 后必须按新的 main 状态执行要求的同步/验证；
- 未经明确允许，永远禁止 push、PR 和 deploy；
- 禁止 reset、clean、stash、强制 checkout 或删除未知文件；
- 最终报告必须逐项写明 commit、merge、push、PR、deploy 的实际状态。

## 27. 未来专用酒店 Agent

当前各阶段不得实现 Agent。未来升级必须先有独立上位设计变更，至少覆盖：

- 主设计允许酒店 ReAct；
- 路线图将 Module 02 加入 Module 07 直接依赖；
- Module 02 新增专用 <code>HOTEL_SELECTION</code> role/profile；
- Module 04 新增模型迭代预算；
- 新的安全 Action/Observation 和排名 Artifact；
- replay、停止、重复 Action、模型失败和质量测试；
- 继续复用本模块的地图执行安全门、候选资格、UID 去重和人工选择。

未来升级不得修改 1.0 Artifact 含义，也不得让 Agent 直接绕过 <code>hotel.place_call</code> 调用 Module 06。

## 28. 最终自检

任何阶段输出前逐项确认：

- 引用了两份已批准 Module 07 文档和共享执行规范；
- 没有“其余沿用”；
- 阶段目标、前置、分支、白名单、允许操作、测试矩阵、STOP 和报告齐全；
- 一次授权原则上覆盖相邻两个阶段；
- 逐条对照设计而非只看测试；
- 生产 import 只使用 01/04/06 包根；
- 无 LLM、ReAct、MCP、Graph、details/geocode、live、Secret；
- 三次预算、Artifact 顺序、UID/category 和人工门可机器验证；
- line、branch、combined 分别至少 95%；
- 无 skip/xfail/rerun；
- staged 为空；
- 未越权 commit、merge、push、PR 或 deploy；
- 最终状态与事实一致。
