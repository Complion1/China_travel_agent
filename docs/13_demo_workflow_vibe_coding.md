# Module 13：Demo 主图 Vibe Coding 精确执行规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 本次同步M09统一逻辑slots、17次总账、新版来源/合同及专用recorder装配。交付判定、需求/酒店人工门槛及安全恢复不变，诊断故障不降低业务标签。 用户已批准本次设计并授权实施，核心代码及离线门禁已落实；最终验收与真实 API 状态见 [本次报告](VALIDATION.md)。

权威设计：[13_demo_workflow.md](13_demo_workflow.md)。本文只定义实施步骤、文件边界、验证和报告，不另定义业务语义。设计文件通过审查后仍需用户批准；本文件不自动授权代码、commit、push、PR或部署。

## 1. 任务类型、权限和基线

M13-DESIGN01 的文档写作范围仅：
- docs/00_agent_master_design.md；
- docs/IMPLEMENTATION_ROADMAP.md；
- docs/04_workflow_state_and_artifacts.md；
- docs/04_workflow_state_and_artifacts_vibe_coding.md；
- docs/11_durable_execution_and_recovery.md；
- docs/11_durable_execution_and_recovery_vibe_coding.md；
- docs/13_demo_workflow.md；
- docs/13_demo_workflow_vibe_coding.md。

四份前任务dirty（README.md、docs/IMPLEMENTATION_STATUS.md、docs/12_live_pipeline_acceptance.md、docs/12_live_pipeline_fixes.md）在本设计任务只能读取，记录前后SHA-256，不编辑/暂存/提交/撤销。设计分支feature/module-13-demo-workflow-design；动态HEAD、日期、实际测试数字写当次报告，不写入长期合同。

设计阶段只做引用、章节、类型/签名、diff、白名单、决策和一致性检查；Python、Docker、tests、coverage、迁移、供应商API均NOT_RUN。文档审查按§10真实启动两位独立只读Agent。不得把设计资格、用户批准、实现和模块最终完成混为一谈。

未来实施分支feature/module-13-demo-workflow，从已批准且已完成M11 §22扩展的明确基线创建。当次提示词必须核验HEAD/subject、staged、dirty；已有同名分支不能强制接管。无filesystem AGENT.md/AGENTS.md，不能作为缺失引用；会话提供指令仍有效。

## 2. 必读路由与先行门槛

| 用途 | 精确真源 |
|---|---|
| 产品与职责 | 00_agent_master_design.md §1、2、4–7、9–10；IMPLEMENTATION_ROADMAP.md §2–4、9 |
| 工作区/收尾 | SHARED_IMPLEMENTATION_RULES.md §1–4、7；本规范§1、8、9 |
| 本模块合同 | 13_demo_workflow.md §2–14，特别§4公共DTO、§6编号预算、§7拓扑、§9恢复、§10发布、§11终态 |
| 依赖消费 | 权威设计§3的逐依赖条款、公共导出、真实行为测试；必须沿公共入口核对真实签名 |
| 必须先实施 | 11_durable_execution_and_recovery.md §22；其执行规范§8 DE-W00..W05 |
| 摘要例外 | 04_workflow_state_and_artifacts.md §15.1；其执行规范§20 |
| 功能范围 | 09_itinerary_planning_loop.md D-09-69、§15；12_budget_amount_deferral.md延期语义；12_result_formatting.md §3–7、9–14 |
| 既有证据边界 | 11_t07_demo_acceptance.md“结论与边界”；IMPLEMENTATION_STATUS.md；12_live_pipeline_fixes.md；均不替代设计 |

直接依赖均从公共包根导入，禁止 app.* 私有文件导入。tests可使用本模块与既有tests/support的fake，但不可通过生产私有状态证明关键行为。M13完整集成使用真实M01/M04/M05/M07/M08/M09/M10/M11/M12、真实PostgreSQL；模型、地图为受控typed fake，只隔离外部能力。不能用预制最终Completed DTO冒充完整主链。

先行门槛：M11新迁移、取消、查询、受保护凭证交接端口、native resume真实路径、§22.8节点只读Artifact视图和§22.9终态预算读取均通过DE-W相应Gate，未触发历史债务明确记录。M04无需生产DTO改动；不得把尚未实施的扩展当既有符号。设计批准后若仍需改变关键接口或M05/M07–10/M12，STOP提交精确证据。

## 3. 固定文件树和阶段白名单

以下均为M13拟新增目标，实施预检先核验是否已存在；不是本设计任务的允许写入文件。不得无批准扩大目录或顺手改上游。

| 分组 | 精确文件 |
|---|---|
| P0 门面/模型 | app/demo_workflow/__init__.py；app/demo_workflow/models.py；app/demo_workflow/errors.py；app/demo_workflow/artifacts.py |
| P1 codec/计划 | app/demo_workflow/commands.py；app/demo_workflow/plans.py |
| P2 图/节点 | app/demo_workflow/graph.py；app/demo_workflow/nodes.py；app/demo_workflow/supplement.py |
| P3 输出/终态/诊断 | app/demo_workflow/publication.py；app/demo_workflow/terminal.py；app/demo_workflow/diagnostics.py |
| U0 | tests/unit/demo_workflow/test_models.py；tests/unit/demo_workflow/test_commands.py；tests/unit/demo_workflow/test_plans.py |
| U1 | tests/unit/demo_workflow/test_hotel_routing.py；tests/unit/demo_workflow/test_supplement.py；tests/unit/demo_workflow/test_terminal.py |
| U2 | tests/unit/demo_workflow/test_publication.py；tests/unit/demo_workflow/test_diagnostics.py |
| C | tests/contract/demo_workflow/test_public_api.py；tests/contract/demo_workflow/test_import_boundaries.py；tests/contract/demo_workflow/test_upstream_contracts.py；tests/contract/demo_workflow/test_application_boundary.py |
| I | tests/integration/demo_workflow/conftest.py；tests/integration/demo_workflow/test_full_graph.py；tests/integration/demo_workflow/test_recovery.py；tests/integration/demo_workflow/test_cancellation.py；tests/integration/demo_workflow/test_result_publication.py |
| S | tests/support/demo_workflow_fakes.py；tests/support/demo_workflow_worker.py；tests/support/run_module13_coverage.py；scripts/verify_module13.ps1 |

不建空的未来app/application或frontend stub。M13两个文档及六份上游修订文件在实施时全部冻结，任何改动必须转设计变更。阶段导出冻结：DW-T01仅导出4类命令/union、DemoControlArtifact、两类注册常量及2个公共异常；DW-T02增加DemoCommandCodec、DemoBudgetPlanProvider；DW-T03/04增加DemoWorkflowDependencies、DemoWorkflowDefinition、DemoDiagnostics、DemoSummaryView、make_cancelled_state；DW-T05/06才增加create_demo_workflow_definition并通过完整__all__/签名Gate。不存在的后阶段符号不导出、不做NotImplemented或假factory。test_public_api按当次阶段精确集合验证，最终必须改为完整固定集合，不永久保留条件豁免。

生产文件职责按同名设计段：commands→§4.2–3，plans→§6，graph/nodes→§7，supplement→§8，publication→§10，terminal→§11，diagnostics→§12。依赖注入定义位于models.py，factory位于graph.py。

## 4. 阶段计划与配对原则

顺序：DW-T00 → DW-T01+T02 → DW-T03+T04 → DW-T05+T06 → DW-T07 → MODULE-CLOSEOUT。
原则上每次只授权一对相邻阶段；完成两者后统一运行该对相应测试。不提前实施后续阶段；迁移先行的DE-W01单独Gate，不能为凑阶段跳过。每阶段均继承§1权限、§7测试环境、§9报告；前置不满足则停止，当前阶段允许的小范围明确缺陷可就地修复并重验，设计变化/扩大范围/大改须用户先批准。

### DW-T00：只读就绪核验

目标：确认设计已获用户批准、M11扩展已具备可消费证据、引用/文件树/基线/冻结哈希有效。前置：当次任务明确授权只读预检。分支：记录实际分支与HEAD，未授权实施不得自动创建实现分支。
白名单空。允许核验本规范§2各合同、公共入口、相关测试、Dockerfile/compose/锁文件、预期dirty。验证矩阵：M11方法/DTO/schema/原生恢复行为证据、M04摘要例外、M12 helper实际签名、缺失引用与允许新建目标区分。
PASS：没有未确认关键接口、不能消费的上游符号或不明dirty。STOP：必要接口/迁移未实现、只有历史测试证明新路径、或无法保留现有修改。报告§9及“合同—公共符号—测试—缺口”矩阵。

### DW-T01：严格模型、Artifact和公共合同

前置T00通过、实现分支已按§1安全建立。白名单P0、U0中的test_models.py、C中的test_public_api.py和test_import_boundaries.py、S中的demo_workflow_fakes.py。
允许按权威§4–5/11建立strict DTO、finite error、demo.control注册、terminal注册及本阶段精确导出（§3）；不可提供虚假可用factory或提前编译图。
测试矩阵AC-01：所有命令分支、extra/type/subclass拒绝、fresh copy、canonical JSON、ArtifactRegistry注册、control链/计数/Refs，所有terminal code/字段组合。测试选择完整公共协议和失败边界，不逐getter编写镜像测试。
PASS与T02联合Gate后确认；STOP：新增state字段、修改上游DTO、Any吞错误、终态与Registry不匹配。报告§9。

### DW-T02：Codec、OperationPlan和预算

前置T01合同代码就绪。白名单P1、P0、U0、C中的test_public_api.py/test_upstream_contracts.py、S中的demo_workflow_fakes.py。
允许按§4.3/6实现8个codec方法、descriptor/contract digest、全部plan公式、阶段预算provider与容量计算。Codecs不调用数据库或供应商。上游预算常量直接复用，不再定义同名数值副本。
联合T01/T02测试：AC-01/06，显式limits/装配不一致拒绝、低容量静态预检与运行时容量拒绝边界，初始/requirements/__root__分支，拒绝旧schema/节点/generation；days=1/60、初始与剩余计划0/1/6、M09全局slot index、M08局部slot index、实际supplement plan round-trip、所有OperationId跨调用唯一、phase激活冻结。静态检查§7.4。
PASS：公共contract和公式全部可复现，未知版本失败关闭；STOP：时间/随机数参与业务OperationId、预算重置或固定3天/harness沿用。报告§9。

### DW-T03：主图、需求与酒店

前置T01/T02 Gate通过。白名单P2的graph.py/nodes.py、P0、P3的terminal.py/diagnostics.py、U1的test_hotel_routing.py/test_terminal.py、U2的test_diagnostics.py、C、S的demo_workflow_fakes.py、I的conftest.py/test_full_graph.py。
允许按§7实现节点及driver接线、phase准备节点、caps工厂接线、酒店人工暂停及control写入；同时实现§11有限终态映射/make_cancelled_state与§12诊断类，使前期decline/业务失败有真实实现。此阶段与T04仅在tests/support私有阶段Graph中接入已经完成的真实节点和有限下游fake，使用测试自己的contract；测试图不作为生产factory导出，不生成正式demo_ready。生产完整factory在T05所有节点就绪后才实现并导出，禁止假factory或空发布节点。T03/T04的阶段集成证据不得替代T05/T06正式主图Gate。
测试矩阵AC-02/03：真实M05/11、clarification/confirmation/revise、一次resolve、hotel wait原生resume、非法UID计数、陈旧Ref/冲突优先级、decline存储后终态、同操作重放、无新搜索与预算。
PASS与T04联合Gate；STOP：自动酒店选择、多个根saver、静态边与Command重复出边、私有M11调用、修改M05 signal sink。报告§9。

### DW-T04：初始研究、规划与补充路由

前置T03代码就绪。白名单P2、P1的plans.py、P0、P3的terminal.py/diagnostics.py、U1、C中的test_upstream_contracts.py、I的test_full_graph.py、S的demo_workflow_fakes.py。
允许按§8实现全部outcome分支及typed gap映射、control/plan关联；复用同run研究与规划实例，不重建私有ModelOperation。
联合T03/T04测试AC-02–06/08：合法研究Incomplete继续、eligible0/Failed停、planning Incomplete停且无草案时查回真实terminal_successor诊断Ref、补研映射与证据1/8边界、fingerprint重复、补研上限5、旧UID更新可继续、resume携带实际M08plan、不重置预算；验证各分支调用次数而非仅最后状态。
PASS：完整业务路由闭合，未进入发布前不能宣称正式攻略；STOP：补研自动确认用户、虚构evidence、重新start planning来代替continuation、把M09Incomplete送M12。报告§9。

### DW-T05：持久结果发布、终态和诊断

前置T03/T04 Gate通过。白名单P3、P2、P0、U2、U1的test_terminal.py、C、I的test_result_publication.py/test_full_graph.py、S的demo_workflow_fakes.py。
允许按§10–12完成逐day/index的只读证据消费与permission→put、FormattingFailureCode分类及仅UNSAFE_TEXT安全直达END、全部成功/失败终态集成及最终摘要；完善前期已实现终态/诊断，冻结完整生产factory、全部导出、release_run/aclose公共清理接线；禁止HTTP或新验证器。
测试矩阵AC-07/08/12：真实Registry四种提取/修复成功失败组合；M12读取上游/跨day不提升journal，写入仍需permission；枚举全部11种FormattingFailureCode，仅UNSAFE_TEXT在consume/put前正常closed且无正式攻略，其余按§10上抛；真实M12将只读视图失租包装为INTERNAL_ERROR时，无新permission/Artifact/普通终态；发布写序、day写失败/index失败/commit unknown、精确复用、消耗replayed但ArtifactAbsent拒绝；天气/主营业/备选/地图请求组合；M03与安全事件映射、NoOp/known delivery failure、事件协议错误/容量、完整summary终态同一与重启不可用；同进程取消后通过M11终态快照接口形成准确summary，不扩大历史缺失例外。
PASS与T06联合Gate；STOP：无真实permission、缺index仍正式展示、用局部counter/0补完整summary、把实际摘要写进state。报告§9。

### DW-T06：真实PG恢复、取消及应用边界

前置T05代码就绪及DE-W全部必要Gate通过。白名单P0–P3、U0–U2、C、I、S全部文件；只为本阶段所测已设计行为修复，不扩范围。
允许补真实子进程恢复、取消、查询接口消费、测试编排与coverage工具。scripts/verify_module13.ps1只管理本次唯一前缀隔离PG资源；不得复用用户开发库或全局prune。
联合T05/T06测试AC-07–13，逐项执行§6矩阵，必须真实PostgreSQL和新进程；fake仅在模型、地图和M14受保护端口边界。M14执行名额/浏览器detach是契约模拟，报告明确仍待M14网页实际验收，不能算M15完成。
PASS：正常主链、主动取消、人工暂停、新进程读完整结果、私有上下文丢失、结果不确定fail-closed均符合合同；STOP：通过fake存储隐藏事务问题、只在同进程换对象冒充跨进程、取消关闭全runtime、缺口以历史延期跳过。报告§9。

### DW-T07：独立代码审查与模块Gate（独立）

前置T05/T06通过。默认只读；确认的小修限定§3白名单，修改后重跑受影响Gate及最终coverage。逐条审查权威设计§2–14及本规范阶段，不仅查看pytest结果。
矩阵：全部M13 unit/contract/integration；受影响上游回归；AC-01..14追踪；source=app.demo_workflow三项coverage各≥70%；无测试弱化；冻结文档hash；准确公共API/依赖方向；M11延期触发清单。
PASS：P0/P1/P2清零且独立复审通过、模块定向Gate和coverage通过。此时只可报告实现待收尾，不报告最终COMPLETE。
STOP：任何确认设计偏离、未测关键路径、coverage不足、资料不全。报告§9。

## 5. 测试文件和 AC 映射

| 文件（相对tests） | 对应AC/关键证据 |
|---|---|
| unit/demo_workflow/test_models.py | AC-01，DTO/Artifact/Registry |
| unit/demo_workflow/test_commands.py | AC-01/02，codec与M05输入连续性 |
| unit/demo_workflow/test_plans.py | AC-06，全ID/预算/容量、日期边界 |
| unit/demo_workflow/test_hotel_routing.py | AC-03，错误优先级/计数与预算 |
| unit/demo_workflow/test_supplement.py | AC-04/05，全typed gap映射及plan关联 |
| unit/demo_workflow/test_terminal.py | AC-08，五状态与事实门槛 |
| unit/demo_workflow/test_publication.py | AC-07，permission/重放及index门 |
| unit/demo_workflow/test_diagnostics.py | AC-12，真实计数/不可用与安全错误 |
| contract/demo_workflow/test_public_api.py | AC-01，精确导出/签名 |
| contract/demo_workflow/test_import_boundaries.py | AC-01，无上游私有/应用/数据库/供应商依赖 |
| contract/demo_workflow/test_upstream_contracts.py | AC-02/05/06，真实公共DTO/端口与fresh验证 |
| contract/demo_workflow/test_application_boundary.py | AC-10/11/13，M14将消费的入口、同run及generation约束 |
| integration/demo_workflow/test_full_graph.py | AC-02–08，真实能力服务+typed fake外部能力 |
| integration/demo_workflow/test_recovery.py | AC-09/13，真正新进程、真实Saver与原生interrupt |
| integration/demo_workflow/test_cancellation.py | AC-10/11，M11新取消/query接线 |
| integration/demo_workflow/test_result_publication.py | AC-07/13，DB故障窗口与只读加载 |

## 6. 恢复与并发的必测矩阵

每行必须同时断言状态/Refs、预算、供应商调用次数及数据库证据，不能只断言异常类。

| 场景 | 核心断言 |
|---|---|
| requirements等待跨进程 | 原token/当前state完整校验；无新extract直到合法resume |
| 酒店等待跨进程（已激活酒店预算） | 同候选集、非法次数保留；无重复需求/搜索；native Command恢复 |
| invalid UID响应丢失与重复提交 | 同generation不重复计数；第三次结束 |
| accepted未dispatch | 精确命令可恢复，取消已先提交则禁止dispatch |
| M08/M09私有context丢失 | 不重建ModelOperation、不新外调、报告fail-closed |
| published day但index未有 | 不显示正式攻略；按journal证据处理，不删day |
| terminal checkpoint后close前崩溃 | 只repair close，不重生成结果 |
| cancel与complete/resume/permission | 两种事务顺序可控复现，不用sleep；胜者与receipt一致 |
| 一次cancel先requested | 测试应用控制层模拟唯一维护，执行退出/lease到期后无需再次点击收束；断线不停止，名额Busy退避且不占名额；重复取消合并，未知结果blocked、有限故障退出；用注入时钟/屏障验证退避，不用真实sleep |
| cancel后重启 | 意图仍在，查询cancelling，settle只做安全维护 |
| cancel外部结果不确定/lease丢失 | 未确认前不得cancelled，不增加调用/预算或生成新token |
| 列表执行中/等待/完成/blocked/旧contract | activity不是open/closed简单映射；分页正确、不泄漏token |
| 刷新与断线契约模拟 | 保留同run后台任务；重连只query；不能当作真实网页E2E |
| 单执行名额契约模拟 | waiting释放；另一执行中请求Busy且未消费token/预算；query/cancel可用 |
| 重启读取已完成攻略 | M12 load经真实M10读取，零新模型/地图调用；summary明确不可用 |
| 无草案规划Incomplete | 通过实际terminal_successor ID恢复诊断Ref；新进程可读gaps/reasons，零新模型地图调用 |
| 终态后清理 | success/decline/failed/控制取消/blocked分别调用release_run；幂等、不访问失效caps、不关闭借用资源；停机aclose只在执行停止后 |
| 本进程控制取消摘要 | M11终态快照全量、同run/终态/trace匹配，不能先发正常完成summary后覆盖取消 |
| 已保存凭证的响应丢失 | 原凭证可用，不重生；未完成交接窗口则fail-closed |

独立核对M11 UF-11-003/004的实际触发路径；必要上游阻断缺陷精确报告，不能无授权扩上游修复文件。无需因M13使用恢复而把未触发全部历史债务强制清零，也不可拿Demo例外为本矩阵缺测开脱。

## 7. Docker、命令与质量门槛

### 7.1 编排契约

scripts/verify_module13.ps1新增参数Set（Mandatory，UnitContract/Integration/Module/Closeout）、ProjectPrefix（Mandatory，本次唯一安全前缀）。它必须拒绝空/已有别项目prefix、记录创建资源ID，只清理自己创建的资源。所有Python/pytest/coverage/compileall运行在当前源码构建的新Docker容器，--rm、read-only、/tmp tmpfs、非root、无live凭据。M13纯unit/contract使用--network none；integration以及包含M11数据库contract的全项目unit/contract回归使用本次内部PG网络，无外网。不得对需要PG的既有contract强行断网后skip。PG16、数据库身份保护和环境变量沿M11执行规范§4.3，不使用compose默认postgres_data。

build runtime/test各自新tag，与当次HEAD及dirty证据关联；不得沿用旧镜像PASS。先按M10/M11批准migration runner完成空隔离库准备，再启动Runtime；测试不自动修schema。故障或环境失败如实BLOCKED，不伪造PASS。宿主只运行PowerShell和Docker编排，不运行Python。

### 7.2 容器内精确命令

以下为未来验收命令，本次设计NOT_RUN。公共参数均保留，collected0/非零/collection error为FAIL，failed/skipped/xfail/rerun必须分别报告且均为0。

    python -m pytest -q -p no:cacheprovider --import-mode=importlib tests/unit/demo_workflow tests/contract/demo_workflow
    python -m pytest -q -p no:cacheprovider --import-mode=importlib tests/integration/demo_workflow
    python tests/support/run_module13_coverage.py
    python -m compileall -q app tests
    python -m pytest -q -p no:cacheprovider --import-mode=importlib tests/unit tests/contract

定向阶段运行§5映射到该阶段的精确文件，配对阶段取并集；不能以全量collection代替。M11扩展Gate运行其完整目录，M13后续改动只重跑受影响M11矩阵，最终模块Gate必须覆盖所有M13实际恢复路径。

### 7.3 Coverage

run_module13_coverage.py使用coverage.py Python API和pytest，同一版本源码收集source=app.demo_workflow、branch=True的完整unit/contract/integration。line=covered_statements/num_statements；branch=covered_branches/num_branches；combined=(covered_statements+covered_branches)/(num_statements+num_branches)。无分支项按coverage工具约定报告分母，不能伪造100掩盖没有执行目标。三项分别用未舍入比值判≥70%，报告分子/分母及展示百分比；禁止omit低覆盖生产文件或合并不同版本。测试子进程以coverage官方机制采集需要的M13代码；不能用mock替代关键跨进程证据。

首次未达标只补有价值的缺失行为；禁止大量实现镜像测试、降低门槛、skip/xfail/rerun到偶然绿灯。M12诊断性coverage例外不适用于M13。

### 7.4 静态检查

每对阶段与最终Gate：git diff --check；trailing whitespace；精确文件白名单（含untracked）；staged为空；冻结设计SHA-256；禁止私有import/Any/生产fake/未授权网络；pycache在容器环境受PYTHONDONTWRITEBYTECODE控制；compileall产物只能落临时容器可写目录，不能写宿主源码；残留容器/网络/volume仅核对本次prefix。执行compileall时将编译目标复制到/tmp后运行或提供独立缓存目录，不以只读失败跳过编译。

## 8. MODULE-CLOSEOUT：同步和最终资格（独立）

前置：DW-T07通过且P0/P1/P2清零；明确新的收尾授权；冻结设计hash不变。
分支：保持feature/module-13-demo-workflow；动态HEAD、已审查dirty和预期新增同步文件由当次提示词核验。
精确白名单：README.md、docs/IMPLEMENTATION_STATUS.md、Dockerfile。其他配置只读核验，实际受影响但需修改时先请求白名单扩展；不得修改八份冻结设计或历史M12报告。
允许与顺序：
1. 同步真实生产包、公共入口、文档索引、已实现/未实现范围、下一模块、测试命令与结果；旧任务dirty必须先核对归属，不能默默覆盖。
2. Dockerfile默认CMD现为模块01–12 import自检，M13新增生产包必需纳入app.demo_workflow，更新成功文字；仍不把默认CMD伪装网页已实现。
3. 同步后重建runtime/test，运行默认CMD，完整M13测试与三项coverage、compileall、截至当前仓库的全量unit/contract、M13真实PG integration；所有容器新建并移除。即使纯说明变化也必须重验，旧镜像证据不可复用。
4. 核验README/状态无过期模块范围，compose/.env.example/运行脚本是否受影响；不需要修改时报告实际理由。
5. 独立审查同步diff和设计逐条一致性；报告§9。冻结文件修改、未授权配置变更或测试故障STOP。

完成后无提交授权仍保持未暂存READY_FOR_INDEPENDENT_REVIEW。只有独立审查通过并获得明确Git授权，才由独立审查者提交/按授权合并；实施模型不得自行stage/commit。未获push/PR/部署授权一律不执行。最终模块COMPLETE须符合SHARED_IMPLEMENTATION_RULES.md §7及用户最终资格流程，不把“主图可供M14消费”写成“网页Demo已完成”。

## 9. 每阶段报告与提示词生成

实施提示词必须放在一个代码框；保留阶段目标、前置条件、分支/HEAD、预期dirty、必读、精确白名单、允许/禁止操作、测试矩阵、PASS/STOP及报告栏目。已经成文部分只写本文精确章节/阶段/AC引用，不粘贴或改写大段合同；只有当次动态现场及新增授权展开。不能过度精简为“按文档实现”。

每次报告至少包含：
- 任务/阶段；最终状态；实现状态；模块收尾状态。
- 开始/结束分支、HEAD、subject；开始/结束dirty；staged；冻结设计前后hash。
- 指定与实际读取引用、缺失引用、允许新建目标；AGENT文件既有核验结论。
- 创建/修改文件、白名单外与异常文件；迁移/依赖变化。
- 逐条主设计/模块设计/执行规范/路线图/公共合同对照，AC证据与差异；不能只有测试数字。
- Docker新构建tag/ID、Python/关键依赖、默认CMD、隔离、自动移除与本次资源残留。
- 定向unit/contract/integration、完整模块、全量离线、compileall；passed/failed/skipped/xfail/rerun；NOT_RUN及原因。
- coverage line/branch/combined原始分子分母；环境阻断不写PASS。
- README/状态/Dockerfile/compose/.env检查、是否同步及理由；MODULE-CLOSEOUT。
- 独立审查P0/P1/P2/P3、修复及重验、未解决事项。
- commit及hash、merge、push、PR、deploy逐项真实状态；未执行明确写未执行/未授权。
- 其他项目是否触碰；剩余问题；提交资格；模块最终完成资格。

设计任务最终报告另区分：17项决定确认、八文档写作、两Agent首轮、主模型修复、两Agent复核、主模型总审、用户文档批准、M13实施与MODULE-CLOSEOUT。设计阶段所有生产测试/迁移/Docker/coverage为NOT_RUN；不复制历史PASS。

## 10. 本设计任务的独立审查流程及记录

完整八份草案完成且主模型基础一致性检查后，同一冻结版本同时交给两个不同子Agent。A主审逻辑/架构/上下游，B主审弱模型可执行性/阶段/验收；两者均须交叉检查前后模块，逐条对照权威§2决定表。只读，不改文件、不运行测试、不执行Git写操作或API，不参加需求决策。

两者完成前主模型不改送审文档。发现按“文件/章节或行号/等级/问题/后果/最小修复”报告，不能预设PASS。主模型合并并逐项核验，记录采纳/拒绝/待确认；改变用户决定或超八文档范围先问用户。修复后同一版本交回两者复核首轮问题和全交付；仍有P0/P1/P2就继续修复/复核，不因两轮已满通过。最后主模型总审，无未解决P0/P1/P2且两位真实复核通过才具备设计交付条件。用户最终批准仍独立于审查。

审查记录只在本节和权威设计§16保存，不另建报告文件。

| 轮次 | 审查者 | 结论 | 问题与处理 |
|---|---|---|---|
| 首轮 | architecture_review | CHANGES_REQUIRED | P1=2、P2=5；见下表 |
| 首轮 | execution_review | CHANGES_REQUIRED | P1=2、P2=4；独立交叉核验，见下表 |
| 修复 | 主模型 | 已修订，待复核 | 10项去重问题全部采纳，无业务决定变化 |
| 第二轮 | architecture_review | CHANGES_REQUIRED | 首轮项已闭合；新增A2-01，P2=1 |
| 第二轮 | execution_review | CHANGES_REQUIRED | R02分类仍未闭合，P2=1；其余已解决 |
| 再修订 | 主模型 | 已修订，待再次复核 | §10.2两项，无业务决定变化 |
| 第三轮 | architecture_review | PASS | R01–R11/R02续已解决；P0/P1/P2/P3均0 |
| 第三轮 | execution_review | PASS | 独立复核全部交付；P0/P1/P2/P3均0 |
| 总审 | 主模型 | PASS | 决策、源码接口、阶段、引用与白名单一致，待用户最终批准 |
### 10.1 首轮意见逐项处置

首轮审查者为 /root/architecture_review 和 /root/execution_review。两份完整报告均为CHANGES_REQUIRED，P0/P3均0；合并去重后10项，不把重复报告重复计数。以下“已修订”只是主模型处置，不等于复核通过。

| 编号/来源 | 等级 | 核验与处置 | 修订落点 |
|---|---|---|---|
| R01 A-01/B-02 | P1 | 采纳；真实Registry要求精确counter集合，改为3个单counter事件，不改M04 | M13 §12；AC-12、DW-T05 |
| R02 A-02 | P1 | 采纳；FormattingFailed在permission前直接构造严格终态并END，无失败Artifact | M13 §7.1/10/11；AC-07/08 |
| R03 A-03 | P2 | 采纳；无草案Incomplete也按实际terminal_successor ID查回诊断Ref | M13 §8；AC-09、§6测试矩阵 |
| R04 A-04 | P2 | 采纳；Definition新增release_run/aclose，定义执行停止后的清理所有权 | M13 §4.1/8；DW-T05/06 |
| R05 A-05 | P2 | 采纳；M11新增只读终态预算接口；不扩大D-13-10摘要例外 | M11 §22.9及DE-W02；M13 §12 |
| R06 A-06/B-06 | P2 | 采纳；删Web未编号旧句，保留框架/公开部署边界 | 路线图§5 |
| R07 A-07/B-05 | P2 | 采纳；test_runtime.py、test_lifecycle.py明确拟新建 | M11执行规范DE-W02/03 |
| R08 B-01 | P1 | 采纳；新增node-bound只读Artifact视图，不放宽原写journal | M11 §22.8、DE-W02；M13 §4.1/10 |
| R09 B-03 | P2 | 采纳；冻结逐阶段导出；提前开放terminal/diagnostics，完整factory至T05，不写stub | 本规范§3、DW-T01/03/04/05 |
| R10 B-04 | P2 | 采纳；显式传M04/M11 limits，冻结装配一致性；区分静态预检和运行时容量拒绝 | M13 §4.1/4.3/6.2；DW-T02 |

另外主模型检查修正：M11查询保持只读，损坏只在视图标阻断；显式取消的本地poison不得阻止纯取消维护；未配置取消factory在写意图前拒绝；全项目M11 contract所需PG网络与普通断网测试区分。主设计同时同步已经批准的预算金额延期：本期不采集或要求金额，仅历史非空金额只读兼容。以上均保持已确认决定及八文档范围。
### 10.2 第二轮意见逐项处置

| 编号/来源 | 等级 | 核验与处置 | 修订落点 |
|---|---|---|---|
| R11 A2-01 | P2 | 采纳；一次显式取消持久后由M14唯一维护任务继续纯settle，规定唤醒/退避/名额/断线/退出/重启边界 | M11 §22.3、DE-W03；M13 §13、AC-10、执行规范§6 |
| R02续 / B第二轮 | P2 | 采纳；M12真实实现会包装安全错误，冻结11种code分类，仅UNSAFE_TEXT可普通终态，其余保守抛有限公共异常；禁止通用catch兜底 | M13 §7/10/11、AC-07；DW-T05 |

两项修订均在八文档范围内，不修改M12代码或合同，不增加业务重试、自动业务恢复或队列。两位审查者第三轮均已实际复核并确认RESOLVED，见§10.3。

### 10.3 最终复核与主模型总审

第三轮两位审查者分别独立给出PASS，未解决P0/P1/P2/P3均0；首轮10项去重问题和第二轮新增R11全部解决，R02经第二轮进一步分类后闭合。未拒绝审查意见，未改变17项业务决定，无待用户裁决的设计冲突。审查及本次文档检查不是生产测试；Python、Docker、迁移、coverage、真实API均NOT_RUN。

第三轮实际送审文件SHA256：
- docs/13_demo_workflow.md：495426F6BD0A2865F3FDEE3A7656B7AA244D8CD12B31527454C50C4C5FD926FB。
- docs/13_demo_workflow_vibe_coding.md：F032ED962341544263F48C6C5518BDF9643469D877DF43E3B34A2C47CE70AEEE。
- docs/11_durable_execution_and_recovery.md：9A74B3BD82BD8FC5596F33E3EC9EB16A7D2BECA9B20DD76F9F74610DC19A5312。

审查完成后只回填两份M13文档的审查记录；设计正文（§16之前）UTF-8 SHA256为35A7A1D9047D5E9838F8121204B1A3D5B8AC90A8BF26E564AF819CCC1923C9B8，执行正文（§10之前）为788B4B245BD920DA249C23714841F615E9853B05D6C9F65E165A591395DD31B1，均与送审版本一致；六份上游修订文件不变。

主模型总审核对17/17决定、各模块公开接口及真实错误包装、节点恢复分类、取消维护、摘要与预算、阶段导出/白名单/测试矩阵、链接及diff。四份前任务文件的前后哈希一致；无异常文件、无代码/依赖/迁移变更、staged为空，未commit/merge/push/PR/deploy。

最终状态：DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL。文档审查已完成，用户最终批准尚未取得；不授权实施，不标记M13或MODULE-CLOSEOUT完成。之后生成实施提示词仍按§9和用户会话指令。


## 11. 本次阶段预算与完整记录维护入口（2026-09-30）

原设计§6及末节定义slots/17总账/623静态容量/web.v2/recorder，原M09执行规范§29/W02+W03为该次维护代码阶段白名单及矩阵；原DW-T01～08不是该次复建要求。设计审查采用用户后续演示优先策略：P0/P1与高概率演示P2阻断，其余记录统一延期清单。原§10的审查次数/八文档边界属于M13-DESIGN01历史，该次维护按其范围独立记录 [审查报告](VALIDATION.md)。

## 12. M14 最终摘要保存与诊断释放衔接（待实施）

合同为 [M13设计 §12](13_demo_workflow.md)、[M04 §15.1](04_workflow_state_and_artifacts.md)、[M14 §3.2、§9–10](14_web_application.md)。未来代码唯一入口 [M14执行规范 W01+W02、W04+W05、C02/C12/C13](14_web_application_vibe_coding.md)：M13只可改app/demo_workflow/diagnostics.py，新增tests/unit/demo_workflow/test_diagnostics_release.py；不改graph、budget、terminal、commands、definition DTO或finish缓存算法。

逐条核对活动scope拒绝/已停止未知与重复释放/事件和interaction scope引用释放/摘要值不变/释放后不补完整观测；M14负责真实保存回执或失败小摘要移交后调用，definition.release_run仍单独清业务。持久摘要读回不等于M13历史恢复；UF13-RECORD-001专用结束追加取消窗口不因此标修复。当前只同步文档，没有代码或迁移执行，不重跑原DW阶段或声称M14/M15通过。

M14装配中的RunBoundExtractionInvocationSink仅调用已有公开operation/accept，真实revise和recover事件绑定及关闭前取证/finish在[M14执行规范W05/C10/C13/C17](14_web_application_vibe_coding.md)验收；不扩大本模块代码白名单或更改accept校验。M11.close之后不得调用get_state/get_terminal_budget_snapshots或补finish，避免把已关闭runtime当作仍可读取。


## M09参数修复装配执行入口（2026-10-06）

设计为M13本次共享储备装配增量及M09 §26；阶段、白名单、允许操作、测试、STOP和报告按[E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)。仅其已列出的plans.py、nodes.py、artifacts.py、graph.py、commands.py预算、版本及新运行合同/codec隔离接缝，不触碰日常数据库或旧Demo容器。

## 枢纽输入失败映射执行入口（2026-10-07）

仅实现 D13“枢纽输入失败映射增量”；统一执行 E01“枢纽澄清维护执行入口”HF01+HF02/HF03 的前置、白名单、允许操作、矩阵、STOP及报告。有限 input_invalid 采用已有 requirement_failed；安全/持久/预算优先级保持，真实 M01/M03 主图与 M09 input guard 对照验收。新研究/模型调用、预算/合同版本变化、旧任务重写和生产部署均禁止。
### 前端活动阶段观测维护规范（2026-10-10，已获批准）

引用主设计“前端首版批准增量：活动阶段观测”、M14 §8.3/§11 的批准增量及共享规则 §3/§5/§7—9。UI03+UI04 相邻实施后统一验证：UI03 在 diagnostics.py 与 graph.py 落实有界观测及最外层 finally 释放；UI04 经 M14 合法投影同步实际页面，不能读取执行中的 state 或把诊断事件用作百分比。

M13 精确代码白名单为 app/demo_workflow/diagnostics.py、app/demo_workflow/graph.py；测试为 tests/unit/demo_workflow/test_diagnostics.py。不改 NodeRecoveryRule、持久合同 digest、模型/地图逻辑、预算、state、schema 或依赖；公共包根已有 DemoDiagnostics，无新导出类型。针对未绑定/活动/嵌套/继承后失效/异步子任务/错 run或trace/非法类型/异常和取消验证；finish 计数与摘要必须不变。没有活动作用域的直接 M11 调用保持原业务行为，阶段未知。

前置条件为用户最小扩展授权、当前 feature/web-ui-progress-results 分支与 main 基线 aa83d5ecbc925411b1d12b1da77db92906010348。允许上述代码、两份 M13 文档及 M14 配套白名单内修复、Docker 离线测试与专属测试库既有迁移；禁止真实供应商、既有本机/云换版、stage/commit/merge/push/PR/部署。合同边界需要进一步改变、白名单不足或验收失败即停止相关后续动作。双代码审查按条款核查，不只认绿灯；最终报告按共享规则列分支、前后 HEAD、文件、迁移、测试、异常、所有 Git/部署动作及状态。
## 2026-10-11 用途补研接线执行增量

仅按 [M13同日设计增量](13_demo_workflow.md)、[M08 A1/A2](08_candidate_research_loop_vibe_coding.md)与[M09 B1/B2](09_itinerary_planning_loop_vibe_coding.md)实施必要接线、contract_id与版本读者，不复制用途规则或增额度。分支、白名单、允许操作、统一测试、STOP及报告以两份本次执行契约为准。真实隔离PG验证用途缺口→补研→规划、预算不重领、旧合同不兼容且不自动恢复、终态/M12/已合并UI不变；两个子agent按设计逐条审查。


## 2026-10-11 批准执行入口：研究合同修复

执行本设计同日R条款及 [M08 R1～R6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约拥有阶段目标、前置/分支、联合文件白名单、允许操作、测试矩阵、停止条件和最终报告。R1/R2、R3/R4、R5/R6每组结束两个子agent逐条对照文档审查。未经新的提交授权保持未暂存；禁止push/PR/部署。

## 2026-10-12 必要资格判断执行入口

执行本设计“必要资格判断接线”和 [M08 Q08合同](08_candidate_research_loop.md)、[Q1～Q6精确执行契约](08_candidate_research_loop_vibe_coding.md)。该契约明确分支/HEAD、既有变更、每组目标/前置、生产及测试白名单、允许操作、统一测试、停止条件和报告字段。每两阶段统一测试后两个子agent逐条对照文档；全部离线通过后隔离三城各一次真实API，记时间及token。不得提交、推送、PR、部署或更新既有服务。

## 2026-10-13 分钟规划读取合同

依据[M09设计§29](09_itinerary_planning_loop.md)与[执行规范T5＋T6](09_itinerary_planning_loop_vibe_coding.md)，新规划消费itinerary/6.0、model_action/9.0、路线快照3.0与分钟时间快照2.0，预算和已有职责保持。新父链、来源目录、类型/version/hash及实际字段必须一致；当前来源须满足十分钟交通预留及分钟时刻规则，禁止混用不同版本记录。结果层沿用北京时间和原始地图耗时/按10分钟向上取整的交通占用的分别展示，工作流不增加预算或排程工具。历史测试行程兼容不纳入本次验收，无数据库表迁移。

## 每日规划接线执行合同

引用[D13“每日规划接线合同”](13_demo_workflow.md)、[D09 §30](09_itinerary_planning_loop.md)和[E09 §32](09_itinerary_planning_loop_vibe_coding.md)。分支／HEAD、前置、精确白名单、UD3～UD6允许操作、测试矩阵、STOP及完整报告统一归E09；不复建其他模块，不独立授权真实调用或Git／部署。

同步当前请求／结果8.0、来源读者、artifact refs及web.v8执行合同；保持方向操作标识数量与旧预算，不因缓存或容量反馈重领额度。验证Completed→M12 v4、质量degraded、长空日Incomplete终态、补研／恢复不重置安排和预算、旧非终态拒绝自动升级、旧终态只读。新旧混配或持久化／账本故障不得以普通质量降级掩盖。两名新代码审查通过后只执行E09授权的一次新真实任务。

## 研究纠错接线执行合同

本次目标、前置、分支/HEAD、精确白名单、阶段C1～C6、测试矩阵、STOP和报告格式统一引用[M08设计§31](08_candidate_research_loop.md#31-研究纠错计数与已有成果交付c08)、[M08执行规范§30](08_candidate_research_loop_vibe_coding.md#30-c1c6-研究纠错与成果交付执行规范)及[D13研究纠错接线合同](13_demo_workflow.md)。不重复开展M09功能或UI变更。

C1/C4将research_plan生成OperationPlan2及每轮两组各3个唯一attempt IDs，保持6/48并由M08共享12/2；执行合同升web.v9。M13读当前candidate_set5时核对候选主记录和来源目录，Incomplete仍经candidate_supply_status判定，有合格供给进入规划，无供给准确终止；不能把所有Incomplete当成可用。旧Ref只按原版本读取，旧活动执行禁止自动升级。

C3/C4矩阵必须通过真实M08控制器和M13节点/图证明最后一轮错误修正、修正耗尽后交付、零供给、技术错误及initial/supplemental不重领额度；PG重新打开和重复读取不能产生模型/地图调用。C5新代码双审后，仅C6允许本轮唯一真实任务；不沿任何历史三城资格重新调用。版本和预算摘要变化之外的主流程行为保持。
