# Module 13：完整 Demo 主图、跨能力路由、执行预算和终态映射

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次同步M09统一逻辑slots、17次总账、新版来源/合同及专用recorder装配。交付判定、需求/酒店人工门槛及安全恢复不变，诊断故障不降低业务标签。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

文档类型：设计真源。对应执行规范：[13_demo_workflow_vibe_coding.md](13_demo_workflow_vibe_coding.md)。
本文件定义待实施合同，不能据此宣称 M13 已实现或网页已交付。设计审查证据见 §16；实现状态只写 IMPLEMENTATION_STATUS.md。
module_id=13；depends_on=[01,03,04,05,06,07,08,09,10,11,12]。01 是命令 DTO/领域服务的直接类型依赖；不直接装配 02 Runtime，其所有权在 M14。

## 1. 范围、权威及非目标

权威顺序执行 00_agent_master_design.md §1 和 SHARED_IMPLEMENTATION_RULES.md §1–4。本文的公共包固定为 app.demo_workflow。唯一主图使用 M11 私有编译与 PostgreSQL Saver，消费 M05 embedded child、M07 酒店、M08 研究、M09 规划及 M12 格式化。

M13 新增的是跨能力接线、酒店人工暂停、补充研究路由、跨调用 ID/预算、结果发布、全局状态和应用消费门面。M07 的酒店筛选、M08/M09 的受控循环、M09 校验、M10 数据库、M11 lease/journal 与 M12 中文渲染全部复用。真实链 harness 只提供调用证据，不能作为正式主图拷贝：其中自动选第一家酒店、固定天数和进程内预算不符合本合同。

不实现 HTTP、页面、认证、队列、公开部署、自动重排、费用判定、天气获取、地图生成、供应商重试层或私有模型上下文恢复。M14 实现网页和应用生命周期，M15 执行真实浏览器验收。

## 2. 已确认决定与继承合同

| 决定 | 冻结内容 | 落点 |
|---|---|---|
| D-13-01 | 安全 checkpoint/人工暂停可恢复；未知外部结果或必需私有模型上下文丢失失败关闭，不重启整阶段 | §9 |
| D-13-02 | 天气不纳入本期交付判定；demo_ready 显示“完整达标”；不增加天气确认，保留 M12 提示 | §11 |
| D-13-03 | 研究 Completed 或合法 Incomplete 且 eligible_count≥1 自动进入规划；研究缺口保留，不据此单独降级 | §8 |
| D-13-04 | 非当前候选 UID 可重选，保留候选，不新搜酒店 | §7 |
| D-13-05 | 明确拒绝全部酒店：保存 declined 决策后 cancelled/cancelled_by_user | §7、§11 |
| D-13-06 | 最终规划 Incomplete 为业务失败，不输出部分正式攻略；修改要求须新 run | §8、§11 |
| D-13-07 | 仅主行程营业未知影响整体标签；备选营业未知只作局部提示 | §11 |
| D-13-08 | 不存在 UID 的已提交选择最多3次；前2次重问，第3次输入失败；UI 未提交错误不计，重放不双计 | §7 |
| D-13-09 | 继承 M11 Demo 验收范围；被本模块触发的延期路径必须补验证；M13 coverage三项各≥70% | §14、执行规范 §7 |
| D-13-10 | 完整 RunSummary 无法恢复时明确不可用，不伪造零值或部分整次统计，不阻塞合格结果 | §12、M04 §15.1 |
| D-13-11 | 显式取消意图持久化，安全收束后才显示已取消；必要 M11 扩展先完成 | M11 §22、§13 |
| D-13-12 | 网页替代完整 CLI，M13主图/M14应用API及网页/M15浏览器验收 | §1、§13 |
| D-13-13 | 浏览器刷新、关闭、断线不取消后端；重连同 run，不另建或重复执行 | §13 |
| D-13-14 | 应用安全托管原始暂停凭证，安全交接后的等待可跨进程恢复；不重生token、不绕过检查 | M11 §22.5 |
| D-13-15 | 历史任务列表复用已有数据库，通过新增公开查询接口访问；不默认另建应用任务索引 | M11 §22.4 |
| D-13-16 | 单个后端同时一个执行中 run，无队列；等待输入不占执行名额；Busy不消费token和预算 | §13 |
| D-13-17 | 本次设计包括主设计、路线图、M04/M11设计与执行规范及M13两份文件；前端完整设计后续处理 | 执行规范 §1 |

继承：预算金额延期见 12_budget_amount_deferral.md；可选备选见 M09 D-09-69；地图身份不等于酒店库存/房型/价格；Docker 是唯一未来正式验收环境。D-13-02/07 是主设计已同步的明确范围例外，不可通过运行后“接受失败”把其他 degraded 升为 demo_ready。全部17项已确认，不把审查通过等同于用户最终批准文档或实施授权。

## 3. 消费合同与行为证据

以下测试是应读取与回归的入口，本次设计不运行；不是已有 M13 验收。

| 模块/权威条款 | 消费公共符号/边界 | 现有行为证据 | M13 补足 |
|---|---|---|---|
| 01；05 §6、8 | RequirementDomainService、RequirementTurnInput、RequirementConfirmation；不复制完整性规则 | tests/unit/requirement_subgraph/test_runtime.py | 命令与fresh当前需求state连续 |
| 03 §9–12、14–16 | ExtractionInvocationSink/Event；由M14装配的领域服务借用真实提取器 | app/requirement_extraction/__init__.py；05运行测试 | 安全计数聚合，非新的提取服务 |
| 04 §6–15、17 | TravelSessionState v2、OperationId、Registry、预算、事件 | tests/contract/workflow_contracts/test_public_api.py；tests/unit/workflow_contracts/test_summary.py | 独立注册M13 artifact/terminal；摘要例外 |
| 05 §6–15 | create_requirement_subgraph_embedded_driver、RequirementResume | tests/contract/requirement_subgraph/test_public_api.py；tests/unit/requirement_subgraph/test_runtime.py | 唯一名 requirements；默认NoOp signal sink；M11管理scope |
| 06 §18、20–23 | typed BaiduMapAdapter 由工厂借用；共享并发3 | tests/contract/baidu_map/test_public_api.py | 无供应商直调、无关闭借用会话 |
| 07 §12、16–18 | HotelCandidateSelectionPort.discover_candidates/record_selection | tests/contract/hotel_selection/test_public_api.py | 人工interrupt，非法UID计数，decline终态 |
| 08 §17–20 | CandidateResearchPort.research_initial/research_supplemental | tests/contract/candidate_research/test_public_api.py | 合法Incomplete路由、补研OperationPlan |
| 09 §6、15–18、D-09-69 | ItineraryPlanningPort.plan_initial/plan_resume/close_run | tests/unit/itinerary_planning/test_service_resume.py；tests/contract/itinerary_planning/test_public_api.py | 补研闭环、保留私有continuation，仅Completed输出 |
| 10 §9–12、18–20 | ArtifactRepository 公共端口，Registry由装配方提供 | tests/unit/artifact_persistence/test_runtime.py；tests/contract/artifact_persistence/test_public_api.py | 非第二套持久层 |
| 11 §5、10–13、17、22 | graph capabilities、codec、budget provider、Runtime | tests/contract/durable_execution/test_public_api.py；tests/integration/durable_execution/test_bootstrap_and_restart.py | 新取消/列表协议先行、实际原生暂停恢复Gate |
| 12 §3–7、9–14 | ResultFormattingService.prepare/build_index/load；结果写入helper | tests/unit/result_formatting/test_service.py；tests/contract/result_formatting/test_upstream_integration.py | 受M11保护的day/index发布，终态门槛 |

先修上游合同再消费。本次未授权修改任何生产代码；实施发现需要 M05/M07–10/M12 合同变化时停止并报告，不能借接线之名改它们的行为。

## 4. 公共门面与类型

### 4.1 导出与依赖注入

包根仅导出以下名称，不导出图节点、SQL、checkpointer、内部factory/cache或状态可变容器：

    DemoStartCommand, DemoRequirementCommand, DemoHotelCommand, DemoResumeCommand
    DemoWorkflowDependencies, DemoWorkflowDefinition
    DemoCommandCodec, DemoBudgetPlanProvider
    DemoControlArtifact, DEMO_ARTIFACT_REGISTRATIONS
    DEMO_TERMINAL_REGISTRATIONS
    DemoDiagnostics, DemoSummaryView
    DemoWorkflowConfigurationError, DemoWorkflowUsageError
    create_demo_workflow_definition, make_cancelled_state

公共异常为有限零参数异常；repr/str 不含用户原文、凭证或第三方异常。所有公共DTO严格拒绝extra与隐式类型转换；frozen；嵌套 fresh JSON round-trip；所有持久字段 schema_version 必填。

DemoWorkflowDependencies 是 frozen dataclass，字段：
- domain_service:RequirementDomainService（借用）。
- hotel_factory:Callable[[DurableExecutionNodeCapabilities],HotelCandidateSelectionPort]。
- research_factory:Callable[[DurableExecutionNodeCapabilities],CandidateResearchPort]。
- planning_factory:Callable[[DurableExecutionNodeCapabilities],ItineraryPlanningPort]。
- diagnostics:DemoDiagnostics。
- durable_limits:DurableExecutionLimits；artifact_limits:ArtifactRepositoryLimits（来自M04）。两者strict/fresh复制，必须与M14实际传给M11/M10 factory的值全字段相同。
以上工厂由 M14 注入；M13 在对应节点上下文内首次按 run 调用，返回对象只供该 run/该 node 使用。不得在 graph factory 中提前访问需绑定 run 的 budget manager。research 同一节点/实例承担 initial与supplemental；planning 同一节点/实例承担 initial与resume，以保留私有 ModelOperation。借用的 maps/runtime/repository 由 M14 装配工厂共享；不得把前一节点的capabilities传给后一节点。M12 service 由 M13 用 各publish_day_N/publish_index节点的read_only_artifact_repository构造（其构造不拥有生命周期）。

    create_demo_workflow_definition(*, dependencies:DemoWorkflowDependencies)
        -> DemoWorkflowDefinition

返回 frozen 定义对象，字段：
contract:DurableExecutionContract；
command_codec:DemoCommandCodec；
budget_plan_provider:DemoBudgetPlanProvider；
graph_factory:Callable[[DurableExecutionGraphCapabilities],StateGraph]；
cancellation_state_factory:Callable[[TravelSessionState],TravelSessionState]；
release_run:Callable[[UUID],Awaitable[None]]；aclose:Callable[[],Awaitable[None]]；durable_limits:DurableExecutionLimits；artifact_limits:ArtifactRepositoryLimits。

M14将定义中的contract/codec/provider/graph/cancel factory、M10公共仓储、M11settings、definition.durable_limits及受保护pause sink交给M11 factory；创建M10时使用definition.artifact_limits。M14必须拒绝不一致装配，不能各自采用默认limits。M13自身只通过显式值做纯结构预检，不读取私有Runtime字段。graph_factory 每 Runtime 构造一次未编译 root；不使用 InMemorySaver，不自己 compile根图。M11可能重复私有compile是其既有延期，M13不能再创建第二套执行引擎。

### 4.2 命令

DemoStartCommand：schema_version="1.0"；kind="start"；turn:RequirementTurnInput。run_id/trace_id由Runtime入口提供，不嵌入重复制订字段；turn.current必须是M01合法初始state，禁止用start冒充恢复。

DemoRequirementCommand：schema_version="1.0"；kind="requirement"；resume:RequirementResume；map_requested:bool（必填）。仅最终accept确认命令的该字段决定本次地图展示请求；其他需求命令必须为False。现有M01没有地图字段，M13不得虚构它或用模型猜测。M14在最终确认时以明确展示选项提交该值，酒店之后不可改；本期请求时明确提示暂不支持。澄清turn.current必须与最后安全state一致；confirmation复用M01 accept/revise DTO。M14读取公共state构造，不从浏览器接受完整可信state。

DemoHotelCommand：schema_version="1.0"；kind="hotel"；candidate_set_ref:ArtifactRef；action:HotelSelectionCommand。浏览器序号由M14映射当前候选UID，不能把序号当UID。稳定请求身份是(run_id,pause_generation,canonical command digest)，不生成新的应用业务OperationId。

DemoResumeCommand 是 kind 判别 union。取消是M11控制入口，不能编码为resume以绕过waiting校验。

### 4.3 Codec

codec_id=demo_workflow.command；codec_version=1.0。type_id固定demo_workflow.start、demo_workflow.requirement、demo_workflow.hotel；type_version=1.0。canonical JSON沿M04规范（UTF-8、sort_keys、紧凑分隔、无BOM/NaN/重复键）；digest=sha256:；bytes≤M11固化limits.max_payload_bytes。嵌套完整业务命令是待消费输入，不写日志；M11按既有pending生命周期清理正文。

DemoCommandCodec构造时显式接收max_payload_bytes:int，值取definition.durable_limits.max_payload_bytes，1..1048576；M11仍独立复核硬上限，不信任codec自报。

descriptor.codec_digest计算输入是规范JSON对象：
{codec_id,codec_version,type_ids:[按上述顺序],state_schema:"2.0",native_pause:"__root__:hotel",requirement_pause:"requirements",command_schema_revision:"m13_commands_1"}。
contract_digest用M11既有算法计算，不写死假hash。改变字段、语义、节点或ID公式必须新contract_id及codec revision，不原地解释旧数据。

实现M11 Codec全部8个方法：
- encode/decode_initial只允许DemoStartCommand，同ID与fresh校验；initial_state由合法turn.current构造requirements/in_progress、无terminal和refs。
- requirement_turn只返回fresh start.turn。
- encode/decode_resume：node_name=requirements只接受DemoRequirementCommand；node_name=__root__只接受DemoHotelCommand；其余拒绝。generation必须正整数。纯codec只验证形状/分支，活动候选Ref和当前需求state在节点中验证。
- requirement_resume只处理requirements分支，返回fresh RequirementResume；M11负责用原durable pause重建driver。不得把应用token塞入M05业务DTO。
- native resume由M11 current_command提供，酒店节点只消费经过上述codec验证的命令；不依赖LangGraph resume返回值承载完整业务payload。

## 5. 小状态与 M13 控制 Artifact

TravelSessionState保持v2，不新增字段，不在state保存候选正文、图私有模型对象、预算副本或token。所有引用同run，按M04 phase/step稳定排序，无重复operation。

新增 artifact_type=demo.control，schema_version=2.0，内容类型DemoControlArtifact。遵守M04 ArtifactSchemaRegistry精确模型配置：strict/frozen/extra=forbid，无默认值、alias、computed/serializer；每个字段required，可空也必填。字段：

| 字段 | 类型/范围 |
|---|---|
| schema_version | Literal["2.0"] |
| sequence | int 0..31 |
| previous_ref | ArtifactRef或None |
| hotel_invalid_count | int 0..3 |
| map_requested | bool；来自最终确认命令，首条control保存，后继保持不变 |
| supplement_count | int 0..5 |
| handled_gap_fingerprints | tuple[ContentHash,...]，≤5，唯一，按发生顺序 |
| hotel_candidate_ref、hotel_selection_ref、candidate_set_ref、planning_ref | 各 ArtifactRef或None |
| last_outcome | hotel_ready/hotel_invalid/hotel_selected/hotel_declined/research_ready/supplement_required/planning_ready/failed |
| terminal_code | TerminalCode或None，仅failed或declined可有，必须属于§11 |
| supplemental_plan_call | int 1..5或None |

Ref必须实际可读，精确type/version/phase分别为hotel.candidate_set/1.0、hotel.selection/1.0、candidate_research.candidate_set/1.0和itinerary_planning.itinerary/6.0；previous_ref为demo.control/2.0；previous_ref必须指向sequence-1，同run，计数只能按§7–8合法增量，不能反向减少。当前控制记录由state中的demo.control最大sequence取得；同sequence多Ref/断链/计数超限均失败关闭。控制记录是路由收据，不复制研究/规划DTO或事实。补研计划按§6公式从调用编号和前驱预算证据重建，M09收到的必须等于已经执行的M08计划。

每次hotel/research/planning节点成功完成其公开调用并确定路由后写一个control记录，再将它及上游引用加入fresh state。上游失败无Ref时也可写安全失败收据。操作logical_step=control_{sequence:02d}，phase与写节点一致；每个节点为non_recoverable，不谎称该无permission写入已被journal独立保护。写失败不继续图；中断到checkpoint之前的不确定窗口仍D-13-01失败关闭。最多：酒店发现1、酒店提交3、研究initial+5、规划initial+5，总16条，31是校验硬界，正常路径不得循环到它。

## 6. OperationId、预算与容量

### 6.1 统一生成

所有OperationId只由run_id、固定phase、下表logical_step组成，不能依赖时间、Python hash、进程随机数、用户文字或瞬时调用次数。数值均十进制无前导零（control除外）；同调用重放重建相同值，不重新分配。

| 能力 | logical_step公式 |
|---|---|
| M05 | 完整继承其requirement_pause_{nonce}/clarification_round_{nonce}，M13不替代 |
| 酒店 | hotel_search_{i} i=0..2；hotel_candidates；hotel_selection_{a} a=0..2（提交前invalid_count） |
| 研究调用c | research_{c}_action_{i}、research_{c}_judgment_{i}、research_{c}_state_{i}、research_{c}_place_{i}_{j}；research_{c}_terminal |
| 规划调用c | planning_{c}_action_{i}、planning_{c}_state_{i}、planning_{c}_hub_{j}、planning_{c}_matrix_{j}、planning_{c}_direction_{j}、planning_{c}_terminal |
| M13控制 | control_{sequence:02d} |
| M12发布 | M13分配result_day_{day_no}（day_no=1..days）和result_index；phase=output_formatting；传入M12 build_result_artifact_write_request验证 |

研究c=0初始、1..5补充；初始i=0..5，补充i从0连续到remaining_iterations-1（M08调用内slot合同）；每i最多8个place IDs。补充Place IDs供给总数不超过剩余48预算，按i/j先后截取。研究terminal始终独立且保留，零剩余迭代也可形成合法终止，但不能借此新调模型。

规划c=0初始、1..5补研恢复；i从已消费逻辑调用数连续到16；hub提供2个；matrix数量=剩余逻辑slots×sightseeing_days；direction数量=当前direction remaining；所有字段ID在该plan内全局唯一，跨调用使用c隔离。只保留terminal_successor，取消独立terminal_action。补研请求的operation_plan及前驱planning/candidate refs必须从已确认control/上游Artifact重建，不拿“新生成近似计划”冒充历史调用；阶段和四项计数按M09设计§12.2/20.1继承。

### 6.2 预算Provider与激活

DemoBudgetPlanProvider严格实现M11的plan_for_start/plan_for_phase，纯函数、无I/O。
- start只含M05 REQUIREMENTS_CLARIFICATION_ROUND_KEY=6；不猜days。
- hotel_selection：M07 HOTEL_PLACE_CALL_BUDGET_KEY=3。
- candidate_research：M08 ITERATION=6、PLACE_CALL=48。
- itinerary_planning：M09 planning.model_invocation=17、DIRECTION=8×confirmed.sightseeing_days+2；2/5/5/5分类计数由M09保存。
- output_formatting：新增phase key demo.result_write=days+1，每个day/index写各消耗1，仅用于M11 journal permission，不是模型地图额度。
其余phase不启用、不生成空计划；contract.budgeted_phases依M04声明顺序列出上述5项。每phase在已持久fresh state下首次ensure_phase_budget，再调用能力；重复激活由M11绑定的首次checkpoint重算比较，不能用新candidate refs改变既有额度。

提取每轮最多extract1+repair1；M08 semantic≤12、M09 semantic≤17与provider内部最多两次技术尝试沿M02。M13的planning.model_invocation是同一组M09逻辑调用的持久总账，不再加旧业务迭代账；模型预算、地图并发3、研究6/48、规划17/Direction跨补充共享。预算0仍不可整阶段重启。酒店非法UID不消耗新地图额度，不增加补充轮数。诊断表写入不消费模型/地图/业务journal预算。

注册合并M07、M08、M09、M12、M13 Artifact注册与M04 base终态注册；重复type/version或code拒绝。M10/M11 limits由§4.1显式提供。M13 preflight在definition创建时验证plan/command静态编码上限和days=1/60的ID分配、node rules、journal/recursion配置；实际供应商文本大小、累计数据库剩余容量不能纯结构预知，仍由M10/M11每次操作严格拒绝超限。M13不承诺任意合法最大文本组合都能放入最小用户配置；必须把最大结构测试及超payload/容量失败测试分开，不截断内容。按最多17次逻辑调用保守估计OperationPlan分配不等于实际消耗，不为未用ID创建预算记录。实际journal上限沿M11，不提高4096 operations/16384 rows。容量不足是有限配置/容量错误，不截断证据或删除旧run。

## 7. 图拓扑与酒店人工门槛

### 7.1 唯一拓扑与恢复分类

唯一静态边START→requirements及requirements→after_requirements；M05子图内部沿既有Command路由。下表其他节点返回Command(update=fresh_state,goto=...)，不能再加静态出边造成双调度。只串行调度；能力内部许可的地图并发不等于graph多节点并发。

| node | phase | recovery_class | 唯一职责/下一节点 |
|---|---|---|---|
| requirements | requirements | non_recoverable | 注册M05 embedded driver.graph；结束→after_requirements |
| requirements/process_initial_turn | requirements | non_recoverable | M05原节点 |
| requirements/await_clarification | requirements | non_recoverable | M05原节点 |
| requirements/await_confirmation | requirements | non_recoverable | M05原节点 |
| after_requirements | requirements | replay_safe | confirmed→prepare_hotel；domain failed/budget exceeded→terminal |
| prepare_hotel | hotel_selection | replay_safe | 更新phase→hotel_discover |
| hotel_discover | hotel_selection | non_recoverable | 激活预算、discover、写control；ready→hotel_wait；否则terminal |
| hotel_wait | hotel_selection | replay_safe | 单一原生interrupt；resume→hotel_select |
| hotel_select | hotel_selection | non_recoverable | record_selection、control；重选→hotel_wait，selected→prepare_research，declined/失败→terminal |
| prepare_research | candidate_research | replay_safe | 更新phase→research |
| research | candidate_research | non_recoverable | initial或supplemental；可规划→prepare_planning；否则terminal |
| prepare_planning | itinerary_planning | replay_safe | 更新phase→planning |
| planning | itinerary_planning | non_recoverable | initial或resume；需补研→prepare_research；Completed→prepare_output；否则terminal |
| prepare_output | output_formatting | replay_safe | 更新phase→publish_day_1 |
| publish_day_1 .. publish_day_60 | output_formatting | journal_guarded | 每节点至多一个day OperationId；成功N<days到N+1，否则publish_index；仅UNSAFE_TEXT按§10→END，其余失败上抛 |
| publish_index | output_formatting | journal_guarded | 至多一个index OperationId；完成→terminal；仅UNSAFE_TEXT按§10→END，其余失败上抛 |
| terminal | 保留当前phase；rule为output_formatting | replay_safe | §11映射与Registry验证→END |

rule.phase是节点登记类别；terminal只改状态维度，不为失败任务激活output预算。各prepare节点先形成checkpoint，外调节点再ensure。hotel_discover返回awaiting_user state，hotel_wait在interrupt之前无写入、无预算消费；非法重选也先由hotel_select写control和awaiting_user state，再进入hotel_wait。hotel_wait resume后写in_progress、goto hotel_select。interrupt payload只含固定(kind="hotel",schema_version="1.0")字符串字典，候选与错误提示经state refs加载，不增加M05 strict serializer的任意DTO白名单。

M11当前native pending node_name固定__root__，codec不得按hotel_wait猜名字；真实Command(resume=True)接线必须先通过M11 §22.7 Gate。使用caps.register_requirement_subgraph(node_name="requirements",driver=...)；limits明确取caps.requirement_subgraph_limits；signal_sink省略保留NoOp。start/resume operation scope和resolve_result全由M11 bridge管理，M13不能再次手动resolve。

### 7.2 酒店规则

仅M07选出的3..5家候选展示给用户；不自动选第一家，不排名补齐，不绕过人工确认。当前候选set_ref来自control，与请求严格相等。错误优先级沿M07：陈旧Ref、非法候选状态、已决策重放/冲突优先于UID判定。

只有 HotelSelectionRejected 的 candidate_uid_not_found（HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND）进入非法UID分支：invalid_count+1，前2次保留候选重问，第三次hotel_input_exhausted。其他拒绝映射输入/合同失败，不借此反复交互。UI未提交的数字错误不计。M11已接受命令重放仍用同a及control序列，不能再加1。恢复后从control读次数，不初始化为0。

selected保存hotel.selection Ref后进入研究；declined先保存M07拒绝Artifact，再按§11取消。此业务decline与M11外部取消入口不同：它是正常图的已确认终态checkpoint，不使用§22取消维护终态。新候选、新需求或重新选酒店均新run，无原地改已决策artifact。

## 8. 研究、规划与补充闭环

初始研究只调用M08 research_initial。Completed或合法Incomplete且counts.unique_eligible_count≥1允许进入M09；零合格候选→research_no_candidates，Failed→按技术错误映射。保留全部上游gaps及ref，不将Incomplete伪装Completed。M09独立验证完整性，M13不复制行程质量校验器。

planning初始调用plan_initial；后续只能plan_resume(ResumeRequest v2)，继续同一私有ModelOperation。每次CandidateSupplementRequested：
1. 重新读取planning_ref和candidate_set_ref，验证同run/上下文及M09 §15指纹、typed evidence合法。
2. fingerprint在control未处理过且supplement_count<5才继续；重复、空或无合法证据→planning_incomplete，不循环。
3. gaps严格执行09_itinerary_planning_loop.md §15 的PlanningGap→M08 typed gap映射（含全部目标字段、evidence1..8）；不能直接把terminal ResearchGapSummary当SupplementalGap，不能凭空造evidence。
4. 记录fingerprint和补研调用c，再走research节点。M08剩余额度不重置，使用新c的plan。
5. 补研结果Completed/合法Incomplete且eligible≥1并有正确后继Ref→M09 plan_resume；Failed/zero/无合法Ref停止。无新增UID但旧UID证据更新可继续，不能仅按新增数量拒绝。
6. M09 resume必须同时携带本次新规划plan及刚实际执行的supplemental_operation_plan；按M09 §15.1验证terminal Ref和原请求digest，不改变前驱上下文。
7. Completed才进M12；最终Incomplete保留gaps/reasons、failed/planning_incomplete，无部分正式攻略。若public outcome.itinerary_ref=None，M09仍已保存无草案terminal Artifact：使用本次实际operation_plan.terminal_successor_operation_id经公共repository.lookup/get(ItineraryPlanningArtifact)找回；验证same-run/type/version、record_type=incomplete、request digest、directories及stop_reasons/gaps与刚收到的outcome一致，将此诊断Ref存入control.planning_ref及state refs。Ref不存在/冲突按持久完整性失败关闭；不能把它当Completed或当可展示的部分攻略。技术Failed保留有限技术终态，不把它转Incomplete或自动重试。

先验证重放证据，再决定是否需要尚存私有上下文。持久结果可精确读取；新的supplement/resume若所需私有上下文已丢失，即使有Artifact也不能重建上下文或启动新operation来假装恢复。

M08/M09的close_run只在整个run最终结束、取消已安全确认或阻断后清理本进程能力上下文；补研暂停期间不close。清理不改终态、不关闭借用M02/M06/M10。M14在确认对应执行task停止且Runtime已返回终态或blocked后调用definition.release_run(run_id)；它只取本进程已创建的M08/M09实例依次await close_run，并释放hotel实例引用，未知run及重复调用幂等，不使用已失效的node capabilities，不创建新service或发I/O业务调用。缓存清理失败以有限DemoWorkflowUsageError报告且不改持久终态，保留可重试清理的实例。停机先await M11.close并确认执行已停止，再await definition.aclose逐个释放剩余run；之后M14关闭底层资源。尚有活跃执行时不能调用清理，aclose不能把未停止的任务伪称已清理。

## 9. 恢复合同与不确定窗口

contract_id=demo_workflow.web.v2，node_rules精确等于§7.1列表并按Unicode排序；codec descriptor及budgeted_phases参与M11 digest。没有“通配所有节点为replay_safe”。本次OperationPlan/规划持久语义变化不兼容旧测试运行，不转换旧契约。

| 中断点 | 允许行为 |
|---|---|
| 已安全交接的M05或酒店waiting | M14取原凭证，M11校验后resume；预算/非法次数保留 |
| 已接受未dispatch命令 | 仅M11依据完整accepted证据恢复；取消意图优先禁止继续 |
| replay_safe节点 | 仅无外部副作用的确定性重算 |
| M07/M08/M09调用途中、写control但未checkpoint | 默认non_recoverable；缺少完整证据失败关闭 |
| 节点完成checkpoint之后、下一调用不需私有上下文 | 允许M11按合同安全恢复 |
| 研究/规划后仍需丢失的私有continuation | 失败关闭，不从artifact造私有context |
| publish_day_N/publish_index | 按§10与M11 journal/Artifact对账，绝不重复未知外调 |
| 终态checkpoint已有，runs尚未close | M11 terminal repair，不重生成攻略 |
| 取消意图已持久、后端重启 | 仅M11 settle_cancel安全维护，不恢复业务 |
| 暂停checkpoint与凭证/pending交接间故障 | 完整证据不足则阻断，不生成新token |

contract不匹配、hash/lineage损坏、超recursion、缺失artifact、budget conflict、租约失效全部保留M11 typed failure。M13不能在数据库失效时伪造已持久failed；应用展示执行阻断与最后确认状态，区别于已提交业务failed终态。raw CancelledError不统一包装。

## 10. 结果发布与读取

发布节点仅消费M09 Completed的itinerary_ref，构造fresh ResultFormattingRequest。M12 prepare为确定性读取/投影，不调用模型；全部60个day节点静态注册于graph/contract，只有1..days会被调度；每个day节点持久一个day后返回freshstate形成独立checkpoint。不得在一个journal_guarded节点写多个OperationId，M11既有恢复只接受单节点一项未完成证据。M12 prepare/build_index必须使用M11 §22.8的read_only_artifact_repository读取上游和已存day；当前节点自身结果重放用原artifact_repository.get验证其journal，lookup/consume/put继续使用原node-bound写能力。M12 prepare可在各day节点确定性重算（或仅用非权威内存缓存），不把prepared大DTO放state。days按照M12固定日期顺序逐一处理：
1. 按§6.1生成OperationId，传给build_result_artifact_write_request生成精确写请求；helper不负责分配ID。
2. 节点仓储lookup。ArtifactFound必须用M12及M04合同重新验证hash、run、type、content；复用并计cache hit，不consume第二次。
3. Absent时consume demo.result_write amount=1、operation_id与写请求完全相同；只有applied可开始put。
4. consume返回replayed但ArtifactAbsent时抛OperationResultIndeterminateError；不把permission重放当许可再做。
5. node-bound repository.put受M11 journal保护，取得day Ref；全部days持久化后由publish_index调用M12 build_index重新核验day_refs，再同顺序保存index；它不再写任何day。
6. 将所有day/index refs加入fresh state并checkpoint，后继terminal才可写demo_ready/degraded。

M12 prepare/build_index会把部分仓储和M11异常包装为ResultFormattingFailed；有限DTO本身不证明可安全形成业务终态。按公开ResultFormattingFailureCode固定分类：仅UNSAFE_TEXT在本节点任何consume/put之前可直接构造严格failed/workflow_output_failed state（category=validation、recoverable=False、action=None）并Command(goto=END)，保留已确认refs；先验证M13 TerminalCodeRegistry，由M11正常终态checkpoint/close收束，不再读取control或追加失败Artifact，不经过terminal节点，不占额外OperationId。ARTIFACT_DEPENDENCY_ERROR抛公共DurableExecutionDependencyError；RESULT_TOO_LARGE抛公共ArtifactTooLargeError；INPUT_NOT_READY、ITINERARY_NOT_COMPLETED、SOURCE_MISMATCH、ARTIFACT_NOT_FOUND、ARTIFACT_INTEGRITY_ERROR、ARTIFACT_CONTRACT_ERROR、UNSUPPORTED_RESULT_VERSION、INTERNAL_ERROR均抛公共DurableExecutionStateError。两类M11异常从app.durable_execution包根导入，ArtifactTooLargeError从app.workflow_contracts包根导入，均使用有限零参数构造。INTERNAL_ERROR可能封装只读视图失租，必须保守停止。所有非白名单分支禁止业务兜底终态、后继节点和补偿Artifact；未被M12包装的异常原样传播。permission之后的写失败、失租、数据库/完整性不确定一律交M11安全规则处理，不能被§11通用终态映射捕获改写。M12输出失败、day/index写失败、验证不一致时停止发布；不得以部分days或缺index产生正式攻略。已写days保留，不删除、不“回滚”不可变Artifact。journal_guarded只成立于上述逐OperationId protocol，不能将整个M12 service黑箱调用当成许可。

独立结果读取由M14通过M12 load(ResultReadRequest)使用M10公开仓储，校验终态state与index；仅demo_ready/degraded允许作为正式攻略展示。failed的诊断可展示gaps/reasons，不展示半成攻略。刷新读结果不重新执行图、不重新格式化、不新增预算。

## 11. 交付状态与有限终态Registry

### 11.1 判定顺序

先满足M11持久/安全要求；其次依据业务outcome；Completed行程且完整结果index可读后才判交付。M13只复核上下游Ref和发布完整性，不重做M09行程规则。

成功时读取M12 index.core.unknown_business_visit_count和core.map_status；地图是否被请求从最新control.map_requested取得：
- 主行程营业未知数>0，或用户明确请求地图但本期未提供→degraded。
- 其余适用门槛通过→demo_ready，显示“完整达标”。
- 天气、未知费用、未启用的价格/预约/临时政策、研究曾Incomplete、备选缺省、备选营业未知均不单独降级。M12原有局部提示保留。
不得以第三方供应商标注或用户事后接受代替上述判定。

### 11.2 固定注册

除workflow_completed外均outcome REQUIRED、recoverable=False、user_action_codes=()、outcome.user_action_code=None；新run建议是M14文案，不是允许原run重试。trace与state一致。

| terminal_code | delivery_status | category | 来源 |
|---|---|---|---|
| workflow_completed | demo_ready | 无outcome | M04 base，完整适用门槛通过 |
| cancelled_by_user | cancelled | input | M04 base，酒店declined或安全控制取消 |
| workflow_degraded | degraded | validation | 主行程营业未知/所请求地图不可用 |
| requirement_failed | failed | input | M05领域失败 |
| hotel_input_exhausted | failed | input | 第3次已提交非法UID |
| hotel_selection_rejected | failed | input | 其他M07选择拒绝 |
| hotel_candidates_insufficient | failed | validation | 搜索完成仍不足 |
| research_no_candidates | failed | validation | 研究合法终止但eligible为0 |
| planning_incomplete | failed | validation | M09最终Incomplete/补研不能合法继续 |
| workflow_provider_failed | failed | provider | M03/M08/M09有限模型错误 |
| workflow_map_failed | failed | tool | M07/M08/M09有限地图错误 |
| workflow_budget_exhausted | failed | budget | 上游明确预算耗尽且未形成Completed |
| workflow_configuration_failed | failed | configuration | 可安全持久化的有限配置失败 |
| workflow_contract_failed | failed | internal | schema/端口/上下文/不可变冲突 |
| workflow_output_failed | failed | validation | M12 UNSAFE_TEXT，且本节点未consume/put |

typed outcome的retryable/possible_duplicate不自动触发M13重试；业务终态从上表选择，诊断仍保留上游Artifact。M08/M09 ControlFailure除明确预算失败外映射contract_failed；如果是持久不确定/失租，优先退出至M11 fail-closed，不强写该终态。M03 extraction技术失败与领域未完整分开，按M05最后outcome类型及公开有限原因映射。未知异常只在确定未破坏持久合同时映射contract_failed，否则原安全故障上抛；不吞BaseException、CancelledError或GraphInterrupt。

terminal Registry由M13合并base注册，禁止改M04 base。failed不要求output_formatting phase；保留最后业务phase。after_requirements直接失败可由requirement_outcome确定原因，无须制造control Artifact。M12仅§10白名单失败直接形成终态，其余失败从最后control读取。所有terminal写前严格验证freshstate和registry。

## 12. 安全事件与摘要

DemoDiagnostics构造签名：

    DemoDiagnostics(*, sink:SafeWorkflowEventSink,
        clock:UtcClock, monotonic_clock:MonotonicClock,
        uuid_provider:UUIDFactory)

实现M03 ExtractionInvocationSink.accept(event)并提供：
start_run(*,run_id:UUID,trace_id:UUID)->None；
finish(*,state:TravelSessionState,budget_snapshots:tuple[ExecutionBudgetSnapshot,...])->DemoSummaryView。
另提供 operation(*,run_id:UUID,trace_id:UUID,turn_id:UUID|None,kind:Literal["initial","resume","recover"])->AbstractContextManager[None]，供M14包围一次Runtime调用并设置M03事件上下文；仅成功接受的resume在节点入口确认后计workflow.resumed。内部emit不是公共业务入口。start_run仅首次新run使用；重启attach/recover不补起点。每run最多16384事件，sequence单调，event identity按M04固定，投递前Registry验证。容量超限或协议错误失败关闭，不丢弃后装作完整。

DemoSummaryView字段schema_version="1.0"、summary:RunSummary|None、unavailable_reason:Literal["process_history_unavailable"]|None；二者恰有其一非空。完整摘要只在存在整次start起点、所有本地事件及最终预算快照时生成一次。M14收到M11确认的终态或cancelled/already_terminal回执后，经M11 get_terminal_budget_snapshots取得最终全量快照，再调用finish；成功、decline、业务失败和同进程控制取消统一走此路径。图terminal节点只形成候选state，不能提前发workflow.terminal或缓存最终summary，因为取消与close尚未裁决。尚未释放诊断的同进程终态可复用完整摘要；新进程M13输出None+原因。M14计划将真正已生成的完整摘要存同一PG自有表，之后仅读回严格校验的已存值；不要求M13查询数据库或重建历史。正常保存后释放事件，已知保存失败也释放事件、只留有限小摘要重试，详见 [M14 §9](14_web_application.md)。不能存用户原文或用journal模拟观测历史。

M14终态诊断释放增量（尚未实施）：DemoDiagnostics新增同步release_run(*,run_id:UUID)->None，清除该run事件/计数/摘要引用/interaction scope等运行级引用；未知run及重复调用幂等，活动operation未退出拒绝。调用方必须先确认执行已停止且已终态/blocked/确定未创建的启动安全放弃；waiting不得释放。它不finish、不补专用诊断结束记录、不生成摘要、不关闭借用资源。definition.release_run只清业务上下文，不能替代此端口；M14持有原diagnostics，definition没有diagnostics属性。公共释放后不得伪造同进程完整历史，后续summary读取由M14持久层承担。执行归 [M14执行规范 W01+W02/C02、W04+W05/C13](14_web_application_vibe_coding.md)，既有UF13-RECORD-001取消窗口仍延期。

M14事件关联与关闭顺序补充：M03通过M14自己的RunBoundExtractionInvocationSink关联真实领域turn_id，公开operation可嵌套，内层仅accept；不改本模块accept/事件Registry/resumed计数，具体绑定规则见[M14§3.1](14_web_application.md)。M11关闭前必须取得真实终态/最终预算并await finish或冻结完整小摘要，关闭后只做小摘要存储/上下文清理，不能再查已关闭runtime；未得合法终态不造摘要。该次序见[M14§9–10](14_web_application.md)，不是本模块新数据库功能。

事件使用M04 STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS；新增3个事件extraction.extract、extraction.repair、extraction.failed，每个registration.counter_names严格为与event_type相同的单元素tuple，operation_id/duration_ms/terminal_code全部FORBIDDEN；每个事件只有对应delta=1。按attempt_kind先发一个extract或repair事件；outcome_kind=runtime_failure再发一个failed事件，使用不同event_id和下一sequence。正常extract、正常repair、extract失败、repair失败必须分别通过真实M04 Registry；不得填0或填未发生计数，不修改Registry精确集合语义，不留metadata或error对象。counter只使用固定的workflow.started、workflow.resumed、workflow.replayed、workflow.terminal、artifact.cache_hit、extraction.extract、extraction.repair、extraction.failed；值从对应实际事件计，0也须真实可知。M03同步accept用运行调用上下文关联run/trace，无上下文/错run拒绝，不读取私有全局可变映射。M05 durable signal sink必须仍NoOp，不能改成自定义sink；M13仅在公共start/resume/terminal边界计M04标准工作流事件；不自造workflow.paused标准事件，不声称这些是M05内部节点事件。M08/M09预算摘要只用于其合同允许的诊断，不与观察到的模型事件重复相加。

已知EventDeliveryFailed计event_delivery_failures并保留本地counter，不自动重试；NoOp不使计数归零。cache_hits只统计实际OperationId Artifact复用，不能把本地字典命中、HTTP缓存或M09不同定义的tool缓存直接相加。任何无法完整观测的历史不得拼装完整RunSummary；M04 §15.1例外仅为历史缺失，非静默吞事件协议错误。

## 13. M14/M15 接口边界

M14持有唯一应用执行任务和单执行 admission锁；显式取消的每run唯一维护任务按M11 §22.3创建/唤醒/退避/停止，浏览器断线不终止，重启后不自动重建。维护等待不占执行名额，不构成业务命令队列：
- start/resume/recover/settle_cancel（需要维护租约时）先取得执行名额；其他run执行中返回Busy，不建立队列、不接受新pause command、不consume预算。请求取消意图与get_run/list_runs/read-result不受执行名额阻挡。
- waiting或terminal释放名额。M11 active/stored表示库容量，不当作网页执行名额。
- HTTP/browser生命周期与后台task分离；断线、刷新、关闭不传播CancelledError。再次打开只查询同run；有明确操作才start/resume/recover。
- start连接丢失后先get_run；AlreadyExists不是成功重放，不能自动另造run。resume响应丢失后先按generation查询；accepted/执行中/新generation不重发旧token。只有证据表明未接受才精确重试原命令。
- 只经M11公共查询与恢复，不读SQL或Graph内部。安全展示拒绝、busy、recovery_required、cancelling、blocked，不能把数据库open标为正在运行，也不能把blocked伪装已完成业务failed。
- M14凭证store实现必须保护原token，具体加密算法、密钥来源、存储格式和HTTP防重复提交设计在M14冻结；在其Gate前不得宣称跨进程网页恢复完成。
- M14 owns M02/M06/M10/M11生命周期；关闭M11后清理业务service run上下文，再关闭M06/M02和M10。M06 broken runtime不原地重连，按M06 §20重建资源后才接纳新任务；不能重发不确定旧操作。
- 不承诺多进程应用全局单执行名额；本期一个后端实例。扩大部署并发需后续设计。

M15用真实浏览器、PostgreSQL、DeepSeek、百度地图跑完整主链和安全恢复/失败矩阵；M13 fake integration不能冒充该发布Gate。CLI完整交互不作为前置。M06独立capability probe资格保留，与天气是否计入单次交付不同。

## 14. 验收 AC 与停止条件

| AC | 必须证明 |
|---|---|
| AC-01 | 公共DTO strict/fresh/JSON、精确导出、无私有import；codec错误分支/generation/未知版本 |
| AC-02 | 真实M05 child与M11装配，需求连续性、clarify/confirm/revise，单次resolve，无双出边 |
| AC-03 | 酒店人工选择/decline；非法UID1/2/3、陈旧Ref、精确重放、预算不增加 |
| AC-04 | 研究Incomplete eligible≥1继续；0/Failed停止；最终规划Incomplete无正式结果 |
| AC-05 | 补研typed映射、fingerprint一次、无新增UID可继续、实际M08plan关联、额度不重置 |
| AC-06 | 全局OperationId唯一稳定，days边界1/60，phase激活重放与容量/recursion界 |
| AC-07 | days/index顺序、跨节点只读证据与本节点写隔离、11种FormattingFailureCode逐项分类，含真实M12包装失租的INTERNAL_ERROR拒绝普通终态、每写permission、replay/commit unknown、缺index拒绝正式攻略 |
| AC-08 | demo_ready/degraded/failed/cancelled/awaiting_user矩阵；天气与备选排除、主营业未知降级 |
| AC-09 | 无草案Incomplete的terminal诊断Ref重启可读；安全暂停跨进程；原生酒店已激活phase恢复；私有context丢失与未知调用失败关闭 |
| AC-10 | 一次取消从requested经维护自动安全收束，含断线/名额Busy/lease到期/未知结果blocked；M11持久取消/竞争/重启及凭证交接故障、正常/取消/blocked/停机服务清理；不能关闭整个runtime取消一个run |
| AC-11 | 真实数据库列表/查询与Busy、分页、旧契约；不新增应用索引 |
| AC-12 | 真实Registry的四类提取组合、同进程取消终态预算快照、完整摘要与不可用、NoOp/投递失败、安全事件/identity/容量，终态一致 |
| AC-13 | 新进程加载完成攻略不新调用；M14接线contract测试证明UI不直接访问Graph/DB |
| AC-14 | M13 line/branch/combined分别≥70%，当前源码Docker、全模块离线回归、独立审查、MODULE-CLOSEOUT |

STOP：上游必要扩展未通过；必须改已确认决定；需要新依赖或扩大阶段白名单；无法满足实际恢复路径只能skip/xfail；需要放宽strict/预算/事实门槛；需要重构M05/M07–10/M12公共合同；出现未知dirty或意外并行graph节点。只修当前白名单内明确小问题，大改先报告用户。纯设计阶段不运行Python/Docker/tests/live、不修改代码、不stage/commit/merge/push/PR/deploy。

## 15. 文档变更、下游开发空间

本次8文档修订同步主设计的网页入口、天气/备选/摘要边界，M04摘要包装例外、M11控制/查询协议；没有把历史报告状态改成PASS。未来网页框架、HTTP schema、认证、公开部署、M14加密实现、M15真链额度均不在此冻结；它们必须消费本文业务与M11恢复合同，不能重写路由或减弱安全。

应用单执行与M11库级多run能力并存；现有数据库持久化不等于已经具有网页历史列表和取消接口。不得重复实现Artifact存储、研究/规划、Formatter或第二套validation。

## 16. 设计审查记录

此节仅记录本次设计交付的审查证据，不是生产状态。
首轮独立审查：architecture_review（A）P1=2/P2=5，execution_review（B）P1=2/P2=4，均CHANGES_REQUIRED，P0/P3均0。
主模型修复：合并10项全部采纳并修订，详情见执行规范§10.1；未改变17项业务决定。
第二轮独立复核：A报告新增P2=1（取消维护未闭合）；B报告R02仍有P2=1（M12包装故障分类不明确）；P0/P1/P3均0。主模型已按执行规范§10.2修订。
第三轮独立复核：architecture_review与execution_review分别PASS；R01–R11及R02续均RESOLVED，各自未解决P0/P1/P2/P3均0。
主模型总审：PASS。17/17已确认决定逐条对应；上游扩展、恢复安全、发布错误分类、阶段白名单和验收矩阵一致；只做文档/源码只读核验，未把历史测试结果当本次验证。四份前任务dirty哈希不变，无生产代码或范围外修改。
最终设计状态：DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL。送审后仅回填本节和执行规范§10审查记录，合同正文未变；冻结版本证据见执行规范§10.3。用户最终批准仍待取得，M13实施和MODULE-CLOSEOUT未执行，不标记模块COMPLETE。


## 本次诊断装配与静态容量更新（2026-09-30）

M13注入M10 Runtime公共interaction_recorder，经M02 interaction_run_context绑定run/trace，再由M03/M08/M09各自关联逻辑调用。模型与typed工具反馈按M02§19全量记录；root finish时追加run_summary，diagnostics完整性独立于delivery_status，写入失败不把本来demo_ready降级。普通RunSummary/Event/Graph state不增加正文或诊断消息。

planning.model_invocation limit=17及invocation slots由§6和M09§14.1统一供给；新demo_workflow.web.v2的contract digest在实施时重算。days=60的保守managed-operation准入由原612同步623（原预算组成增加11次模型调用），M11 4096 operations/16384 rows上限不提高；矩阵计划容量不等于实际预算消费。新版结果只消费M12/2.0；TravelSessionState不变；demo.control虽未增字段，其planning_ref版本约束改变，因此升2.0并拒绝旧control。

新的recording导出不提供跨进程模型恢复。M11 non_recoverable、需求/酒店安全暂停、候选补充M08共享余额和完整提交判定保持。验收以M09§25.3/原执行规范§29为本次维护入口。

M13公共依赖装配显式增加interaction_recorder:InteractionRecorder或None；正式Demo优先注入M10 Runtime公共recorder，None只标diagnostics不完整而不阻断业务。artifacts.py按每个Ref类型分别校验1.0/2.0，不统一强制1.0；publication.py构造M12请求/读取时使用ResultFormatting版本2.0，不能改上游业务Gate。控制Artifact2.0注册、previous_ref/state读取与web.v2合同一起同步。


## M09共享格式储备装配增量（2026-10-06，用户已批准）

按[M09 §26](09_itinerary_planning_loop.md#26-通用参数修正与共享储备2026-10-06用户已批准)，主图planning模型预算由17升级为19，新OperationPlan19槽；ordinary四分类2/5/5/5和format≤2由M09持有，主图补研不重置。地图direction/hub/matrix容量不增加。当前规划只接受itinerary6.0来源，终态聚合code保持原语义；真实外部资格本轮NOT_RUN。执行见[E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)。

## 枢纽输入失败映射增量（2026-10-07，用户已批准）

§11 中 M08/M09 control failure 的有限 input_invalid 映射为已有 requirement_failed（input），不映射 internal 的 workflow_contract_failed。其他业务、持久安全和预算错误的优先级不变；不新增自动恢复/重试，不更改 run 合同、Artifact/命令/预算版本或数据库。M09 严格输入检查保持。错误具体字段由 M14 在已验证的输入失败终态读取既有确认需求，并调用 M01 公开纯检查投影，不修改原状态/摘要。
### 前端首版批准增量：活动阶段观测（2026-10-10）

用户已批准 M13→M14 最小安全阶段投影，用于六节点流程图的实际执行高亮。M11 的 executing 视图有意不读取 state，phase=None；不得为显示进度改变该行为。DemoDiagnostics 新增同步公共 `get_active_phase(*, run_id:UUID, trace_id:UUID)->WorkflowPhase|None`，仅返回当前 operation 内主图实际进入节点的阶段枚举，不返回事件、正文、状态、节点名或预算。输入类型严格；无活动观测、身份不符或新进程均返回 None。

主图在 M11 已验证的节点入口记录 state 的 run/trace/phase；requirements 的既有 accepted 入口同样记录。只在匹配且仍活动的 diagnostics.operation 上下文内观测，未建立上下文时不补造进度。每个活动 run 最多一个 `(trace_id, phase)` 标记，同 run 嵌套 operation 不清除外层观测；最外层 finally 无论成功、异常或取消均释放。继承到已结束 operation 的子任务不能继续写入。该标记不进入 TravelSessionState、checkpoint、Artifact 或 RunSummary，不增加事件与计数，不改变业务路由、节点恢复规则、合同 digest、预算或终态资格。观测表示当前执行最近实际进入的阶段，并不证明该阶段已完成；很快经过的阶段可以被轮询跳过。

M14 只在已验证归属、M11 activity=executing、未请求取消、且本进程当前执行槽的 run/trace 匹配时读取该公共方法。停止、暂停、失败、取消及重启时不能沿用活动标记；未知阶段如实显示未知。验收必须经过真实 M11/M13 主图和隔离 PG 的执行入口，不能用人为注入 executing+phase 的页面 fixture 代替。
## 2026-10-11 批准增量：用途供给补研接线

消费 M08 用途判断与 M09 §28 的唯一准入判定；M13 只路由，不复制资格规则。M09 在入口发现已知必要供给缺口，返回已有 awaiting_candidate_supplement 合同，经 prepare_research→research→prepare_planning 接线。补研不能重置阶段、问题目标、Directions缓存或已用额度；无余额按 M09 具体 incomplete 映射既有 planning_incomplete。可选备选不足不得触发补研。

新 graph contract_id=`demo_workflow.web.v6`，旧运行合同不自动恢复/迁移。Control 和 Reader 接缝只增加 candidate_set/2.0、itinerary/6.0 的读取，保留旧终态只读与原 public terminal、安全、人工交互及已合并UI。纯读取不执行图或新外调。

入口候选状态证明引用candidate_set/2.0；count/must目标保持既有身份，meal_count/2.0精确转换为M08 meal_count_shortfall/2.0，使用新supplemental request/2.0。候选状态Ref不能塞入Place observation目录；M08是目标事实与证据归属的唯一核验者。

新M09没有可交付草案的incomplete也必须通过当前operation的terminal/6.0 Artifact安全收束为既有planning_incomplete；核对Ref身份、请求摘要、缺口、预算来源与上游Ref，不按旧3.0接缝读取新执行，不将旧非终态升级续跑。


## 2026-10-11 批准修订：研究合同修复

状态：用户已批准并授权实施。R13：新执行contract_id=demo_workflow.web.v5，旧活动执行不能静默续跑。消费M08新版本，保留精确前驱及typed gap转换。input_invalid、context_contract_error、action_correction_exhausted、judgment_patch_invalid、state_invariant_failed和真实replay_mismatch必须区分；只有真实供应商错误映射供应商不可用。用户缺失在原需求澄清阶段处理，不在酒店后补造输入。失败保留实际累计消耗和最后一致Ref；无候选但零地图策略拒绝不能声称实际无搜索结果。

共同依据与验收见 [M08 R08](08_candidate_research_loop.md)及[执行规范 R1～R6](08_candidate_research_loop_vibe_coding.md)，覆盖冲突旧条款。

R13错误投影落实：M02本地configuration_error/capability_unavailable/schema_unsupported/input_too_large映射workflow_contract_failed，quota_exhausted映射workflow_budget_exhausted；结构化输出、工具参数、未知工具、截断及非法响应映射workflow_model_output_invalid。其余真实供应商错误沿供应商失败分类；不得将未出站的本地门禁称为供应商暂不可用。按完整LLMErrorCode枚举逐项验收。

## 2026-10-12 必要资格判断接线

Q13：新执行contract_id=demo_workflow.web.v6，消费M08 candidate_set/4.0，旧活动执行不升级续跑。研究无可用候选时按M08安全分类区分地图无候选、判断未完成、证据不足、全部不适用及预算耗尽；必要判断恢复耗尽单独投影。M13只读取分类并路由，不复制候选资格，不新增调用、重试或预算。精确前驱补研、最终消费统计及历史读取保留；终态Artifact安全code目录同步且无SQL迁移。

实施和验收共同引用 [M08 Q08-01～09](08_candidate_research_loop.md)、[Q1～Q6执行契约](08_candidate_research_loop_vibe_coding.md)及本模块执行规范。用户已批准实施及隔离真实验证，完成等待提交。

## 2026-10-13 分钟规划读取合同

依据[M09设计§29](09_itinerary_planning_loop.md)与[执行规范T5＋T6](09_itinerary_planning_loop_vibe_coding.md)，新规划消费itinerary/6.0、model_action/9.0、路线快照3.0与分钟时间快照2.0，预算和已有职责保持。新父链、来源目录、类型/version/hash及实际字段必须一致；当前来源须满足十分钟交通预留及分钟时刻规则，禁止混用不同版本记录。结果层沿用北京时间和原始地图耗时/按10分钟向上取整的交通占用的分别展示，工作流不增加预算或排程工具。历史测试行程兼容不纳入本次验收，无数据库表迁移。

## 每日规划接线合同

依据[D09 §30 UD-05～06](09_itinerary_planning_loop.md)接入itinerary8.0及同版当前请求、结果和预算。新执行contract_id为demo_workflow.web.v8；旧6/7非终态不自动恢复到新执行合同，不重写历史checkpoint、操作标识或预算。历史终态来源仅按各自结果读取合同只读。

阶段预算2／5／5／5、普通17＋格式2、Directions8×天数＋2、已有limited原因均保持。完整有效Completed才能进入M12 v4；缺日、长空日及其他Incomplete沿原planning_incomplete终态，没有伪完整结果。质量问题数大于零仍计入degraded，营业未知、地图请求与其他既有降级条件保持。补研后继续使用原操作计划、账本和已验证来源，保留已接受每日安排；来源变化必须重新核验，不能使用失效基线兜底。无新增工具、模型、数据库结构或公开接口。

## 研究纠错接线合同

本次执行依据为[M08设计§31](08_candidate_research_loop.md#31-研究纠错计数与已有成果交付c08)及[M08执行规范§30](08_candidate_research_loop_vibe_coding.md)。当前新执行合同为demo_workflow.web.v9，研究OperationPlan2为每轮原始及最多两次修正预分配Action/Judgment attempt IDs，每轮最终主操作唯一，跨调用编号由call隔离。初次和补研共用研究6、地图48、模型12及纠错2；纠错不能重新领取普通预算，未用ID不记消费。

研究当前产物使用candidate_set5、Action3、Judgment4、invocation_attempt1。候选来源的精确版本/链/预算由M08公共核验函数拥有；M13只读取已持久化结果并核对公共摘要。可恢复纠错停止返回Incomplete；实际存在合格用途候选才进M09，零供给保持原安全分类及明确预算原因。技术Failed仍终止，不把存储/状态/来源/外调不确定错误降为普通缺口。

规划和结果仍使用当前itinerary8/M12 v4及原准入。新旧研究Ref显式分派，历史候选1/2/3/4及历史结果只读，web.v8及更早活动checkpoint不自动升级，不重写旧操作或终态。新增Artifact通过既有M04注册，无新数据库表迁移；公开网页的现有归属隔离、配额和读取幂等保持。
