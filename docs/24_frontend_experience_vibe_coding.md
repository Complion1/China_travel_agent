# Module 24：前端体验与运行过程展示执行规范

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 设计真源：[24_frontend_experience.md](24_frontend_experience.md)，下文简称 D24。公共实施规则简称 S，指 [SHARED_IMPLEMENTATION_RULES.md](SHARED_IMPLEMENTATION_RULES.md)。状态和实际证据只写 [IMPLEMENTATION_STATUS.md](VALIDATION.md)，不回填本文。

## 1. 执行原则与当前授权

按 D24 §1～10 实施，重构和删减重复代码优先。用户已授权从本地 main 新建功能分支，在独立工作区编写文档、两位文档 agent 审查两轮、随后实施、两位新的代码 agent 审查。该授权无需在每阶段重复询问；已确认事项不得重复澄清。

分支使用 feature/m24-frontend-experience。起始 HEAD、实际工作区路径、用户附件、dirty/staged、资源身份等现场事实由当次提示词及实施状态记录。原 M09 工作区仅允许核对 Git 状态和文件摘要，不修改、不暂存、不 stash。

所有实施、修复与收尾均不得 git add、stage、commit、merge main、push、PR 或 deploy。不得 reset --hard、checkout --、clean、自动 stash、删除或覆盖用户变更。不调用真实模型或地图，不读取 .env 或真实暂停 Key，不修改原 18080/8080、本机业务库、腾讯云或其他项目资源。保留 process_simulation-platform-dev 的隔离边界。

## 2. 引用审计与必读

### 2.1 已存在的引用

设计和约束：docs/00_agent_master_design.md §3～4、§9～11；docs/IMPLEMENTATION_ROADMAP.md §2、§5～8；S §1～9；D24 全文；本文；docs/14_web_application.md §3、§7～11 和有效输入/来源展示增量；docs/14_web_application_vibe_coding.md 的 UI、LS 和有效输入相关契约。

直接依赖权威合同与验收：docs/01_requirement_and_dialogue.md §5.2、§6.3～6.6、§10～11、§16；docs/04_workflow_state_and_artifacts.md §6、§8～10、§12、§19、§23；docs/07_hotel_candidate_selection.md §8～9、§17、§19、§23、§25；docs/08_candidate_research_loop.md §18、§21～22、§25、§28、附录 A.8 及 Q08 增量；docs/09_itinerary_planning_loop.md §17、§19～21、附录 B.4～B.5 及必要资格判断增量；docs/10_artifact_persistence.md §6～7、§9～13、§19；docs/11_durable_execution_and_recovery.md §5.6～5.7、§7、§11.1、§12.6、§18、§22.4、§22.8；docs/12_result_formatting.md §4～6、§8、§10.3、§13；docs/13_demo_workflow.md §4～5、§10～14 和活动阶段观测增量。所有引用均消费本地 main 已提交版本；不引入其他工作区未提交的分钟排程。

公共数据与装配：app/workflow_contracts/artifacts/repository.py、app/workflow_contracts/artifacts/models.py；app/hotel_selection/{__init__,artifacts}.py；app/candidate_research/{__init__,artifacts}.py；app/itinerary_planning/{__init__,artifacts}.py；app/result_formatting/{models,artifacts}.py；app/demo_workflow/{diagnostics,nodes,publication}.py；app/web_application/{__init__,models,service,assembly,api,presentation,store,migrate}.py。

网页与验证：app/web_application/static/{index.html,task.html,history.html,samples.html,app.js,site.css}；app/web_application/samples/{manifest.json,demo_trip.md}；tests/unit/web_application/{fakes,test_assembly,test_models_settings,test_service_control}.py；tests/contract/web_application/{test_upstream_composition,test_http_boundaries,test_public_contract}.py；tests/browser/web_application/{conftest,test_demo_pages}.py；tests/integration/web_application/{conftest,test_workflow_api,test_migrations}.py；tests/support/{run_module14_coverage,module14_browser_worker}.py；scripts/verify_module14.ps1；Dockerfile、pyproject.toml、requirements.lock、README.md。

上述花括号是展开路径清单。执行前核验引用与符号存在；缺少真正必读文件时 STOP，不以近似文件替代。根目录及项目目录的 AGENT.md/AGENTS.md 已确认不存在，不得引用或再要求搜索；用户在会话中提供的全局指令仍然有效。

### 2.2 允许创建的目标

以下文件在设计阶段不存在，属于允许新增目标，不是缺失引用：

- app/frontend_experience/__init__.py、models.py、projection.py、store.py、recorder.py、migrate.py、alembic.ini。
- app/frontend_experience/migrations/env.py、script.py.mako、versions/m24_0001_public_progress.py。
- app/web_application/static/ui.js、guide.js、process.js。
- THIRD_PARTY_NOTICES.md，记录实际复用的开源代码、来源提交和完整许可。
- tests/unit/frontend_experience/__init__.py、test_models.py、test_projection.py、test_recorder.py。
- tests/contract/frontend_experience/__init__.py、test_public_progress.py。
- tests/integration/frontend_experience/__init__.py、conftest.py、test_progress_store.py。
- tests/browser/web_application/test_frontend_experience.py。
- tests/support/frontend_experience_worker.py、scripts/verify_module24.ps1。

## 3. 文件白名单

### 3.1 文档阶段

只允许新增本模块两份文档，并在 docs/IMPLEMENTATION_ROADMAP.md §5、§8 增加 M24 职责、明确依赖和测试分类。文档审查期间可修订这三份草案。README.md 和 IMPLEMENTATION_STATUS.md 仅记录必要现场，不把未实施能力写成完成。

### 3.2 F1、F2：过程资料与 API

允许新增 §2.2 的 app/frontend_experience 全部文件和对应 unit/contract/integration 文件；允许修改 app/web_application/{__init__,models,service,assembly,api}.py。

受影响既有测试仅限 tests/unit/web_application/{fakes,test_assembly,test_models_settings,test_service_control}.py、tests/contract/web_application/{test_upstream_composition,test_http_boundaries,test_public_contract}.py。原测试目的和所有原安全断言保留；新增协议测试放入本模块测试目录。

禁止改 M01～M13 生产代码、原有迁移、原始样例、Prompt、预算、依赖与锁文件。新 schema 单独迁移，不修改 M14 migrate.py 的 HEAD、结构指纹和已有迁移行为。

### 3.3 F3、F4：全站与运行详情

允许修改六份原静态资源，新增 ui.js、guide.js、process.js；api.py 仅增加固定资源名。允许新增/修改 tests/browser/web_application/test_frontend_experience.py，必要时精确更新 test_demo_pages.py 的布局选择器，保留原交互及安全验证语义。

允许新增 THIRD_PARTY_NOTICES.md 并在实际复制/改编的脚本中保留来源注释，按 D24 §3.1 的许可边界复用经过审计的通用代码。

### 3.4 F5、F6：攻略、历史和最终验证

沿 §3.3 静态范围；允许在 models.py、service.py 增加 D24 §6.2 的阅读摘要与 trip 兼容投影。允许修改上述本模块测试和既有 test_models_settings.py、test_public_contract.py、test_service_control.py，以覆盖明确新增字段。

允许新增 tests/support/frontend_experience_worker.py、scripts/verify_module24.ps1，用于隔离环境、实际生产装配观察及测试编排；允许修改 tests/unit/web_application/fakes.py 增加明确的可选 M24 接线，默认旧测试行为保持。已有脚本、测试辅助和 Dockerfile 只读。

### 3.5 MODULE-CLOSEOUT

只允许同步 README.md、docs/IMPLEMENTATION_STATUS.md，以及精确修复已审查白名单中的 P0/P1/P2。Dockerfile 只读核验；若真实需要改构建或依赖，先给出最小范围并请求批准。D24、本文、路线图及其他权威规范冻结，不得为配合代码修改。

## 4. 固定生产符号及实现顺序

### 4.1 app/frontend_experience

models.py 定义 D24 §4.2 的 PublicFact、PublicTrip、PublicRecordBody、PublicRecord、PublicProgressPage；公共包根导出供 M14 使用的严格类型和服务。额外状态与存储输入必须显式类型化，不能以任意 dict/Any 穿过公共边界。

projection.py 定义 project_artifact(*, request, ref, registrations, secrets) 以及 project_requirement(*, state, confirmed, secrets)。只选择 D24 §4.1 白名单内容；未知 artifact_type 返回 None。注册来自生产公开注册描述符，严格使用其模型，不复制上游版本注册表。公共数据来源与 status 按 D24 §4.1 的表逐项对齐。

store.py 定义 PublicProgressStore(*, pool, clock)，公开 append、read、trip、mark_limited、settle。使用借用的同库 pool，不关闭它。异常用固定安全代码，不附 SQL、原始字段或异常正文。实现 D24 §4.3 的验证、幂等、容量、收束及固定锁顺序，查询只访问 frontend_experience。

recorder.py 定义 PublicProgressRecorder 和 ObservedArtifactRepository。recorder.operation 建立有失效标记的上下文；publish 仅发布当前 run/trace，普通观察异常在边界收束，CancelledError 必须传播。包装仓储的原业务错误原样传播；只有原 put 成功之后才进行可选投影及保存。不得在 get/lookup 或 HTTP 读取路径写过程表。

跨包签名固定如下，类型来自公开包根：WebApplicationDependencies.progress 为 PublicProgressRecorder | None；PublicProgressRecorder(*, store: PublicProgressStore, registrations: tuple[ArtifactSchemaRegistration, ...], secrets: tuple[str, ...])；只读 store 属性返回 PublicProgressStore。operation(*, run_id: UUID, trace_id: UUID, valid: Callable[[], bool] | None = None) 为同步上下文管理器；valid 每次发布前验证当前执行槽或后处理停止状态。publish(*, run_id: UUID, trace_id: UUID, event_key: str, body: PublicRecordBody) 为 async，返回 PublicRecord | None。record_requirement(*, state: TravelSessionState, confirmed: bool, event_key: str) 为 async，内部调用固定投影并使用当前有效作用域。settle(*, run_id: UUID, trace_id: UUID) 为 async，需匹配有效作用域。store.append 使用相同 run/trace/event_key/body 参数与返回类型；store.read(*, run_id: UUID, trace_id: UUID, after: int = 0) 返回 PublicProgressPage；store.trip(*, run_id: UUID, trace_id: UUID) 返回 PublicTrip | None；store.mark_limited 和 store.settle 使用 run_id/trace_id，返回 None。固定安全异常可在 models.py 定义并从包根导出，不新增未列目标文件。

ObservedArtifactRepository(*, repository: ArtifactRepository, recorder: PublicProgressRecorder) 实现原 Protocol 的三个签名及返回类型。投影种类、字段与 status 唯一目录为 D24 §4.2 表，不临时扩大集合。稳定需求键采用 requirement:{pause_generation}:{delivery_status}:{terminal_code}，值取已复验公开运行视图；不使用时间或随机标识。

migrate.py 提供显式 python -m app.frontend_experience.migrate 及 --check，Alembic 配置指向本模块迁移。首次迁移先核对 M14 head 存在，持有独立事务锁，在同一事务建立 M24 schema/head/表和约束；验证该 schema 的完整结构，不自动改已有结构。应用启动使用只读 verify_schema。

### 4.2 M14 接线

production_service 创建 M24 store/recorder，验证新 schema 后将 ObservedArtifactRepository 传给 M11；register 描述符复用原生产注册集合。仍由原持有者关闭 M10、M11 及 pool。

WebApplicationDependencies 兼容新增可空 progress，默认 None 仅用于无 M24 的旧替身测试；正式 production_service 必须提供。service 的 _start_job/_control_job 在已验证执行槽内建立 recorder.operation。_after_execution 按 D24 §4.1 先核验停止与运行视图，合法读取后复验身份、暂停代数和状态，再建立短 operation，仅保存结构化需求；终态按 D24 §4.3 保存 settled。Busy 或竞争时不发布，终态读回失败尽力标记 limited，不能放宽旧 binding 的失效判断。不改变原摘要与释放顺序。无 M24 的旧替身读取返回 unavailable，不能冒充正式生产采集已接线。

tests/unit/web_application/fakes.py 的 offline_factory 实际调用生产装配，既有集成与浏览器也复用它。该测试专用工厂在已核验前缀和测试身份的隔离库内显式执行 M24 迁移，再进入原生产装配；不跳过 verify_schema，不修改原 M14 迁移或 worker。手写 WebApplicationDependencies 的旧纯替身可保留 progress=None。测试准备迁移与生产启动只读检查必须分开。

WebApplicationService.read_progress(*, owner, run_id, after=0) 先 _owned_view 再读 M24。HTTP 新路由、参数校验、严格响应和安全头依 D24 §4.4；禁止遍历 M10/M11 私表或读取 M02 诊断。

WebRunView.trip 及 WebResultDay.reading 按 D24 §6.2 增加兼容字段；不改变原版本与必填参数。新字段默认 None，旧 JSON 仍能读取。PublicTrip 仅取完整已保存需求，城市或时间不明则显示通用标题。

### 4.3 静态代码

ui.js、guide.js、process.js、app.js 的边界按 D24 §8。先迁移既有安全渲染及流程函数，再添加交互，防止同时保留两套实现。所有页面采用固定 defer 顺序，不能依靠未声明变量、字符串 onclick 或 innerHTML。

process.js 管理已选节点、增量序号、连接状态和详情展开；app.js 负责唯一请求/任务协调。guide.js 的组织入口同时供结果和样例使用。重复快照和没有新增事实的响应不重绘已有内容。

## 5. 两阶段实施批次

### 5.1 F1＋F2：公开事实与只读入口

目标：D24 AC24-04～08。前置：文档双审两轮完成、主协调者总审通过，§2 引用和 §3.2 白名单核验；Git 基于已提交 main，staged 空。

F1 实现严格投影、作用域、独立迁移和有界持久化。F2 实现正式装配、合法执行采集和归属 API。两阶段完成后统一执行 §6.1、§6.2；未通过不得进入静态交互阶段。存储迁移在独立测试库先验证，不对现有服务迁移。

### 5.2 F3＋F4：统一工作区与节点详情

目标：D24 AC24-02～03、09～12、17～18。前置：F1/F2 通过，冻结文档摘要未变，staged 空。文件限 §3.3。

F3 统一框架、视觉变量、首页和人工交互。F4 加入六节点点选、详情分页、无记录/失败状态与状态主次切换。两阶段完成后统一执行 §6.3 的相关状态；维护旧请求幂等、取消、表单保持和 SSE 语义。

### 5.3 F5＋F6：攻略、历史和验收

目标：D24 AC24-13～18。前置：F3/F4 通过；文件限 §3.4。F5 实现结构化日摘要、共用攻略层次和紧凑历史；F6 验证整合后的实际 API、分页、刷新、手机版、导出和可见性策略。统一执行 §6.1～6.3 的最终矩阵，不重复已经闭合且未受影响的上游全模块 Gate。

F6 后启动两位全新的只读代码 agent；不得复用文档审查者。两位分别检查结构/数据/安全和页面/执行/测试，逐条核对 D24 AC24-01～20、本文阶段与实际 diff。主协调者修复精确问题、运行受影响验证，原两位代码审查者复核后才能进入 §7。

## 6. 测试矩阵与 Docker

### 6.1 单元与合同

| 编号 | 路径/对象 | 必测行为 |
|---|---|---|
| U24-01 | tests/unit/frontend_experience/test_models.py | 严格数字/时间/边界、字节上限、trip 完整性和兼容缺省 |
| U24-02 | test_projection.py | 每类实际 Artifact 的白名单字段、拟查/事实/提交区分、脱敏、未知类型、畸形内容和不泄露原文 |
| U24-03 | test_recorder.py | 匹配/无/过期/继承/跨 run 作用域、独立后处理作用域、委托返回与错误、观察失败不改业务、取消传播、不在读取时写入 |
| C24-01 | tests/contract/frontend_experience/test_public_progress.py | owner 认证、跨访客404、参数与分页、安全头、GET 无业务写入和旧 DTO 兼容 |
| U14-REG | 受影响 M14 unit/contract | 原身份、幂等、卡片、控制、SSE、安全 DOM 和公开签名仍通过 |

### 6.2 真实 PostgreSQL

I24-01～08 置于 tests/integration/frontend_experience/test_progress_store.py：M24 首次及幂等迁移；旧三 schema 结构指纹保持；追加/顺序/分页；同键复用与不同内容冲突；per-run/global 容量；读取坏 hash/身份拒绝；采集失败/limited 与无记录历史；新进程读回及运行过程中不使用 get_state。覆盖 settled 单向收束、暂停后续写及终态后拒绝追加。

隔离测试库要求具名 owner 身份，数据库名包含本次 prefix，使用临时密码，绝不解析开发者 .env。测试显式初始化 M10/M11/M14/M24，记录目标与 revision。至少一个用例实际把观察包装接入生产 M11 主图并使用替身模型/地图生成记录，不能只用直接 INSERT 代替接线证明。

### 6.3 浏览器

B24-01～12 置于 tests/browser/web_application/test_frontend_experience.py：四页布局；首页示例/清空和日期；需求表单不重绘；酒店点击与键盘；节点详情；after 分页去重及选中保持；记录未保存/失败；终态攻略优先与过程重开；来源及提醒；原文复制/下载；390/1440 与焦点；后台暂停和重新读取。必须制造业务 terminal 先到、最后记录稍后到，验证只有 settled 且分页读尽才停止；未收束 12 次后的提示和手动再读也应验证。

原 test_demo_pages.py 的完整矩阵继续执行。独立路由替身可验证难以稳定制造的网络/分页状态，但必须与 §6.2 真实装配和原生产主链浏览器证据分开标注。截图至少包含首页、运行详情、酒店和完整攻略的桌面及手机版；人工查看，不能只保存不检查。

### 6.4 构建与覆盖率

scripts/verify_module24.ps1 明确记录每个命令、退出码和资源身份；允许调用既有 verify_module14.ps1 的适用入口，不修改它或把旧证据视为新结果。Docker 路径与 context 在现场核验，后台启动 Docker Desktop 可作为本机测试准备，不改变项目业务服务。

新 runtime/test/browser-test 从当前源码构建；一次性测试容器 --rm、--read-only、/tmp tmpfs、cap-drop ALL、no-new-privileges。纯离线用 --network none；PG/浏览器只加入具名 --internal 网络，无真实凭据。资源标签用 cta-m24- 前缀；若调用既有 M14 验证脚本，其 prefix 必须是独立 cta-m14-m24- 开头且单独归属本次，不能借用现有资源。

M24 包 pytest-cov 或 coverage 的 source 固定 app.frontend_experience，line、branch、combined 三项各 >=70%，原始分子/分母和未覆盖分支写收据。不得 omit 生产文件来抬升数值；JS 结果单列浏览器行为。M14 受影响测试完整通过；全库 unit/contract 使用新 test 镜像，必要的原 PostgreSQL依赖按既有隔离脚本准备。

compileall 在容器 /tmp 的源码副本执行，避免向仓库写 pycache。git diff --check、已修改文本尾部空白、固定资源引用、白名单和 staged 均检查。新增目标也需要检查，不能只看 git diff 的已跟踪文件。

## 7. MODULE-CLOSEOUT

前置：F1～F6 通过、两位新的代码审查者已复核，无 P0/P1/P2。文件限 §3.5。

同步 README 的 M24 范围、必要显式迁移命令、当前实际预览方式和限制；同步 IMPLEMENTATION_STATUS 的分支、HEAD、阶段、文件、迁移、精确命令/结果、coverage、镜像、审查、未运行项与异常。不能将尚未更新的 18080、云端或 GitHub 写成当前新版本。

逐项核对 Dockerfile 的 COPY app/scripts、COPY tests、静态与迁移资源和默认 CMD。现有 COPY 覆盖新增包时保留 Dockerfile，并以新镜像的文件/摘要验证为证，不只写“无需修改”。

记录 D24、本文、路线图和上位规范在文档冻结时与收尾后的 SHA-256，必须一致。收尾不改变设计。README/状态同步后重建 runtime/test，重新运行默认 CMD、当前 M24 完整测试及覆盖率、受影响 M14、完整 unit/contract、容器 compileall 和静态检查。

最后由两位代码审查者核对实际收尾差异与新证据，主协调者总审。正常默认 CMD 缺配置时应安全退出2；不能用另写 python -c 替代。清理仅本次精确身份的测试容器/网络/卷，保留证据与需要供查看的隔离预览资源并明确列出。

## 8. PASS、STOP 与提示词格式

PASS：本批次对应 D24 条目、§6 适用测试和逐条代码检查均有可核验证据，非零失败为0，无 skip/xfail/rerun 掩盖失败，未改变冻结文件或其他工作区。

STOP：超出 §3、设计冲突、缺少必读引用、观察侵入业务写入语义、泄漏原文/凭据、修改原资源、Docker 环境无法验收、零测试/失败/coverage 不足或用户授权不足。先保留现场与最小修复证据；不能带失败进入下一批。小范围明确问题可在本批白名单内修复。

每次生成执行/修复/审查提示词必须完整放入单一代码框，之后生成的提示词继续遵守。已成文目标、步骤、白名单、测试、PASS、STOP 和报告只写“路径＋精确章节/阶段/AC”引用，不复制、摘要或改写。保留任务编号、当前阶段、目标、前置、分支/HEAD、预期 dirty、引用、白名单、允许/禁止操作、测试、停止和报告栏目；动态现场与本次新增授权可以具体说明。原则上每次授权 §5 的相邻两个阶段，完成后统一测试；§7 单独执行。

## 9. 报告格式

最终报告至少包含：任务/阶段与授权；开始/结束分支和 HEAD/subject；原 M09 工作区保持证据；新增/修改文件和白名单外异常；读取引用与缺失目标；冻结文档前后摘要；逐 AC 设计对照；迁移目标/revision与旧结构保持；定向、模块、PG、浏览器、coverage、全量、compileall、Docker 精确命令与结果；line/branch/combined；镜像与默认CMD；截图检查；两轮文档审查和新代码双审问题闭合；运行/其他项目资源；未运行真实API；MODULE-CLOSEOUT状态；staged、commit、merge、push、PR、deploy；剩余问题。

实施交付状态写 READY_FOR_INDEPENDENT_REVIEW；收尾和最终双审通过后可写 READY_FOR_USER_REVIEW。提交资格固定为 NOT AUTHORIZED；等待用户新的明确命令。不得因实现与验证通过就宣称已经合并或发布，最终完成资格按附件第二十一节及 S §7 的真实条件单独说明。
