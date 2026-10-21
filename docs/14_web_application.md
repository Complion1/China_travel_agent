# Module 14：网页应用与公开受限 Demo 设计

> 公开设计资料：个人路径、实例标识和具体运维环境已改为通用示例。历史验收链接统一指向[公开验证说明](VALIDATION.md)；技术合同与限制保留。

> 网页新任务的输入范围及历史兼容采用本文“有效输入与历史兼容”；用户于 2026-10-13 批准清单并授权在现有 UI 分支实施、本机同步和一次独立子 agent 审查。

> 文档属性：M14 权威设计；用户于2026-10-05批准本文及配套执行规范，并授权按W00–W10实施整个模块。规范业务正文冻结；当前实施进行中，每个通过阶段须经两位子Agent核查。提交、合并、push、PR、真实API及部署尚未授权。
> 配套执行规范：[14_web_application_vibe_coding.md](14_web_application_vibe_coding.md)。需求澄清、独立审查和现场记录并入这两份文件，不另建 M14 报告文件。
> 2026-10-05 澄清交互增量：用户批准“只展示本轮问题、选项优先、特殊情况填写其他”，并授权更新本文、配套规范及网页问题投影。D14-18、§8.1/§11.1/AC14-20 是本次新增规范；原 W00–W10 及其 hash/资格记录仍是历史快照，不能代替本次资格。原上游合同、提交链路、数据库、预算及发布授权保持。
> 规范顺序：[主设计 §1–4、§10–12](00_agent_master_design.md) → [路线图 §2、§9](IMPLEMENTATION_ROADMAP.md) → 本文 → 执行规范；共享规则见 [SHARED_IMPLEMENTATION_RULES.md](SHARED_IMPLEMENTATION_RULES.md)。实现、提交和验证事实以 [实施状态](VALIDATION.md) 为准。

## 1. 目标、授权与边界

面试官从简历中的一个 GitHub 链接进入 README，点击首屏“快速体验”，无需安装、注册或提供密钥即可试用真实规划；额度不足时仍能查看独立的完整脱敏样例。README 同时保留本地复现入口。网页不是占位演示：真实任务必须通过 M13 主图和 M12 完整结果门槛。

实施先交付本机可用的完整网页入口：在维护者电脑用Docker运行Web与独立Demo PostgreSQL，从浏览器访问http://127.0.0.1:8080，完成需求输入、澄清、确认、酒店选择、攻略、历史和统计。先通过本机测试与相应验收，再按另行授权部署到腾讯云；云配置是后续复用准备，不是本机使用的前提。两端使用同一应用代码和锁定runtime镜像，具体顺序见§12.2。

本轮 M14-DESIGN01 只写本文、配套执行规范，并同步执行规范 §2 的既有文档白名单。最新授权取消了第三份独立报告文件。本轮不修改代码、依赖或迁移，不读取真实 .env 或私有真实调用正文，不运行 Python、Docker、数据库、真实 API；不 stage、commit、merge、push、PR 或部署。文档审查通过后仍需用户批准设计及授权相应实施阶段。

路线图唯一直接依赖保持 M14 depends_on=[13]。实际装配消费 M01/M02/M03/M04/M06/M07/M08/M09/M10/M11/M12 公共合同时补充读取其相关章节，不把这些消费关系擅自改写为新的依赖索引。

M13 是业务路由、预算计划、终态映射和完整主图的唯一所有者；M14 拥有应用服务、运行资源、匿名归属、应用元数据、HTTP 和页面。M14 不复制业务校验，不调用供应商绕过 M02/M06，不把任务索引、事件或凭证放入 TravelSessionState；不更改研究/规划预算、旧结果版本或恢复资格。账号、跨设备找回、交易、自动重排、公开原始模型诊断、整库加密、自动删除和自动扩容均不在本期。

## 2. 已确认决定

下表是产品与部署选择的真源；实现细节由后续章节冻结。改变这些选择必须先取得用户同意。

| ID | 决定 | 约束位置 |
| --- | --- | --- |
| D14-01 | 简历只放 GitHub；README 首屏提供体验、完整样例和复现入口；不制作短录屏 | §11、§12 |
| D14-02 | 公开真实受限体验，免注册、无邀请码、访客不提供供应商 Key | §5、§6 |
| D14-03 | 匿名 Cookie 有效 30 天；同浏览器历史、继续、取消和符合条件时显式恢复 | §5、§7 |
| D14-04 | 全站 200 新任务/自然日、每访客 20/自然日、每 IP 5/滚动小时；均可配置 | §6 |
| D14-05 | 单个后端同时执行一个 run，无业务队列；等待用户输入不占名额 | §7 |
| D14-06 | FastAPI 同时提供 HTML/API，原生 HTML/CSS/JS，复用 PostgreSQL，单机 Docker Compose | §3、§4、§12 |
| D14-07 | 腾讯云上海轻量：2 核、2GB、40GB SSD、3Mbps、200GB/月；内地公网 IP 普通 HTTP、不解析域名 | §5、§12 |
| D14-08 | 首页、任务交互与完成攻略、我的任务、独立完整样例；统计默认折叠 | §11 |
| D14-09 | 用现有 PostgreSQL 保存真正生成的最终完整 RunSummary，并提供读取接口 | §9 |
| D14-10 | 正常保存摘要后释放完整事件；写库失败也释放事件，只暂留小摘要做有限重试；攻略不因此降级 | §9 |
| D14-11 | 暂停凭证用 AES-256-GCM；32 字节密钥显式生成一次、独立文件只读挂载、备份并跨重启复用 | §5 |
| D14-12 | Web 1GiB、PG 512MiB；PG shared_buffers=128MB、work_mem=2MB、max_connections=20 | §12 |
| D14-13 | 独立新云库：Artifact 200000 件/4GiB，单份仍 1MiB；交互诊断仍 8MiB/条、1GiB/库 | §12 |
| D14-14 | 数据盘剩余低于 5GiB 暂停接纳新任务；响应压缩，历史每页 20 条 | §8、§12 |
| D14-15 | 不迁移、不删除本地开发库；已有容量行不得静默改大；实际部署另行授权 | §12、执行规范 §6 |
| D14-16 | 浏览器断线不取消、服务器重启不自动恢复；完整历史缺失不得伪造整次统计 | §7、§9 |
| D14-17 | 先实现本机可用网页入口并测试；真实供应商网页验收也先在本机完成，再另行授权部署云端 | §4.2、§12.2、执行W06–W10 |
| D14-18 | 澄清采用当前问题的选择表单；兴趣多选、节奏单选、日期时间和数量控件、房型与限制选项；无默认代选，特殊情况保留其他输入 | §8.1、§11.1、AC14-20、执行§15 |

## 3. 上游公共合同与生产装配

### 3.1 必须使用的入口

| 模块 | 入口及使用规则 | 权威位置 |
| --- | --- | --- |
| M01/M03 | RequirementDomainService(extractor=…, evidence_sink=…, clock=…)；DeepSeekRequirementExtractor(runtime=…, invocation_sink=run_bound_extraction_sink)，关联端口见下文 | [M01 §6、§8](01_requirement_and_dialogue.md)、[M03](03_requirement_extraction_adapter.md) |
| M02 | DeepSeekRuntime(provider=…, sleeper=…)、公开 Profile/Settings；所有模型调用沿既有 runtime | [M02 §5–7、§12、§19](02_llm_runtime_and_model_profiles.md) |
| M04 | 严格 DTO、Registry、ArtifactRepository、RunSummary、时钟和 UUID Protocol；不改 DTO | [M04 §4、§11–15、§17](04_workflow_state_and_artifacts.md) |
| M06 | create_baidu_map_runtime 是异步上下文管理器；借用其 adapter，组合根独占关闭 | [M06](06_baidu_map_adapter_and_mcp_gateway.md) |
| M07 | HotelCandidateSelectionService(adapter, repository, budget_manager, utc_clock) | [M07](07_hotel_candidate_selection.md) |
| M08/M09 | CandidateResearchService / ItineraryPlanningService，以 runtime、map_adapter、artifact_repository、budget_manager 关键字装配 | [M08](08_candidate_research_loop.md)、[M09](09_itinerary_planning_loop.md) |
| M10 | create_artifact_persistence_runtime；repository、interaction_recorder、close；容量只读端口按 §3.2 新增 | [M10 §7–14、§23–24](10_artifact_persistence.md) |
| M11 | start、resume、recover、get_run、list_runs、get_state、request_cancel、settle_cancel、get_terminal_budget_snapshots、close；创建时注入凭证 sink 和取消工厂 | [M11 §22](11_durable_execution_and_recovery.md) |
| M12 | ResultFormattingService(artifact_repository=…)；load(ResultReadRequest(schema_version="2.0", state=…, index_ref=…)) | [M12](12_result_formatting.md) |
| M13 | DemoWorkflowDependencies、DemoDiagnostics、create_demo_workflow_definition；definition 的 contract/codec/plan/graph/cancellation_state_factory/release_run/aclose | [M13 §4–13](13_demo_workflow.md) |

组合根保留自己创建的 diagnostics；DemoWorkflowDefinition 没有 diagnostics 属性。M13 graph_factory 返回未编译主图，由 M11 编译，M14 不自行创建第二张图。节点工厂必须使用 M11 注入的 bound ArtifactRepository 和预算 manager，不把全局 M10 repository 用作节点写入入口。M12 读取结果可以借用 M10 公开只读仓储。

当前 M02 包根没有导出实际生产 Adapter。组合根仅允许从既有 app.llm_runtime.provider 导入 LangChainDeepSeekAdapter(settings)，这是已有生产实现的明确装配入口；不得读取它的私有属性、复制实现、借用 tests fake 或扩大到其他子模块。其余跨模块生产入口使用包根公开符号；若该入口在实施基线消失，STOP，不自行另写供应商客户端或改 M02 导出。

M01 Clock 的方法是 today()->date；M14 以注入的 UTC 时钟转换 Asia/Shanghai 形成日期，不误用 now() 代替该 Protocol。EvidenceSink.accept 是同步方法。M14 的 FingerprintEvidenceSink 对已验证的每份字段证据计算 UTF-8 原文 SHA256，返回 EvidenceReceipt(field_path, evidence_ref, evidence_digest)：ref 固定为 evidence-fingerprint:{turn_id}:{field_path}:{digest_hex}，digest 为 sha256:{digest_hex}。它不保存原文，ref 是证据指纹标识而非可下载地址；不承诺原文找回，不在同步方法里访问异步数据库。M01 仍拥有证据验证和合并；该策略符合 M01 §6.1 的“本模块不定义证据持久化方式”。

生产 M03 不直接接 diagnostics。M14 在 service.py 定义公开 RunBoundExtractionInvocationSink(*,diagnostics:DemoDiagnostics)，实现同步 accept(event:ExtractionInvocationEvent)->None，以及 bind(*,run_id:UUID,trace_id:UUID,kind:Literal["initial","resume","recover"],expected_turn_id:UUID|None,allow_internal_turn:bool)->AbstractContextManager[None]。组合根只创建一次，传给 M03 和 WebApplicationDependencies.extraction_sink；与 dependencies.diagnostics 必须是同一实例，不能用全局可变映射猜关联。

每次已验证的 service 执行调用建立独有 binding 对象并进入 bind，同时外层 diagnostics.operation 包围整个 M11 调用。run/trace/kind 来自服务端已核验上下文；新 start/clarify 的 expected_turn_id 是服务端构造的真实 turn_id，allow_internal_turn=False。confirm(revise) 的新 turn_id 由 M01 内生生成，及 recover 无法由公共入口预取的持久命令轮次，使用 expected_turn_id=None/allow_internal_turn=True；第一个严格真实 M03 事件固定该 binding 的 turn_id，后续 extract/repair 必须同 ID。确认接受/酒店等没有抽取的调用使用 None/False，出现抽取事件即拒绝。不得从 HTTP 填内生 turn_id 或为它更改 M01/M05。

accept 先 strict fresh 复验事件和可信 binding：必须 active、属于当前 service 执行槽的 run/trace、已知/已绑定 turn_id 精确匹配；无 binding、过期继承 binding、字段协议违规均拒绝。之后只临时嵌套公开 diagnostics.operation(run_id/trace_id/kind来自binding,turn_id=event.turn_id)，同步调用 diagnostics.accept(event)，finally恢复；不在该嵌套范围内调用图、finish、_resume_accepted 或补计数。bind 的 finally 将对象 active=False 并恢复 ContextVar，使后来子任务不能把旧事件写入结束的 run；允许 M11 合法 graph 子 Task 继承该 active binding，不要求 current_task 等于 service 父 Task。M02 现有 interaction scope 支持嵌套，专用记录上下文与外层 resumed 计数仍由原实现负责。

### 3.2 两项已授权的上游增量

这两项是未来实施工作，本轮仅同步设计。除此之外，上游生产代码只读。

- M10 包根新增严格不可变 ArtifactCapacitySnapshot，字段 schema_version="1.0"、max_artifact_bytes、max_artifacts、max_total_content_bytes、artifact_count、total_content_bytes；整数非负、限制为正，均不超过2**53-1；实际值不能超过保存的限制，总限额不小于单份上限。新增 ArtifactPersistenceRuntime.get_capacity_snapshot()->ArtifactCapacitySnapshot 异步方法，仅固定 SELECT 读取既有 capacity_state 单例行，校验其完整性、限制一致性、event-loop owner 和关闭状态；错误沿既有安全 typed error。无缓存、UPDATE、DELETE、迁移或新增 M04 Repository 方法。
- M13 DemoDiagnostics 新增同步 release_run(*,run_id:UUID)->None：释放该 run 的完整事件、累计器、摘要引用、interaction scope 和运行级辅助引用；未知 run/重复释放幂等；活动 operation 上下文未退出时拒绝。调用方必须已经确认执行任务停止，且 run 终态、blocked 或从未成功建立的启动已安全放弃。等待输入时不得调用。它不触发 finish、不生成或补写摘要、不关闭借用 recorder。调用前后实际摘要值不变；M14 不读取 diagnostics 私有映射。

M13 definition.release_run 仍负责业务服务上下文清理，不等于 diagnostics.release_run。两者分别调用，不能以其中一个替代另一个。

## 4. 应用服务、配置与所有权

### 4.1 包根与生命周期

未来 app.web_application 包根只导出 WebApplicationSettings、WebApplicationService、WebApplicationDependencies、WebMetadataStore、WebVisitorContext、RunBoundExtractionInvocationSink、本文 §8 的请求/响应 DTO、WebApplicationError 及有限子类、create_web_application；SQL、Key、Cookie 原文和任务字典不作为全局导出或HTTP正文公开。模块导入不得读取 .env、连接资源或启动服务。

WebApplicationDependencies 是 frozen dataclass，字段 artifacts:ArtifactPersistenceRuntime、durable:DurableExecutionRuntime、definition:DemoWorkflowDefinition、diagnostics:DemoDiagnostics、extraction_sink:RunBoundExtractionInvocationSink、result_service:ResultFormattingService、store:WebMetadataStore、clock:UtcClock、disk_probe:Callable[[],int]。extraction_sink借用同一diagnostics并绑定M03；disk_probe 返回 PG 数据所在宿主文件系统的可用字节，单位校验和失败映射由 M14 负责。WebApplicationService(settings=…, dependencies=…) 借用这些资源，拥有执行名额、后台执行任务、取消维护任务和摘要写入任务。所有调用限定创建时的事件循环；跨 loop/关闭后调用抛安全 UsageError。

create_web_application(*,settings:WebApplicationSettings,service_factory:Callable[[],AbstractAsyncContextManager[WebApplicationService]]) 返回 FastAPI。lifespan 进入工厂一次并退出一次；HTTP handler 只使用其 service。生产工厂由 assembly.py 装配真实资源，测试工厂只能位于 tests，可注入 typed 地图/模型替身，但必须运行真正 M13/M11/M12；没有生产 TEST_MODE 开关或默认测试假服务。

生产 __main__ 只在显式启动时加载环境，调用 Uvicorn 单 worker，禁止 reload。Web 自有 PostgreSQL 池 min_size=1/max_size=2；不访问 M10/M11 私有连接池。另持有一条专用连接，通过固定 session advisory lock 独占本应用实例；第二实例不能进入 ready。专用连接失效后立即停止新执行接纳并安全停止当前应用执行，不凭本地锁继续，既有不确定任务按 M11 恢复分类处理。连接最大预算：M10 4 + M11 coordination 4 + checkpoint 1 + M14 2 + 独占连接 1 = 12；PG 20 个连接保留管理余量。

### 4.2 设置

严格设置拒绝未知字段、布尔冒充整数、无效 origin、负数、相对 Key 路径；repr 不含 Secret。生产环境名固定：WEB_PUBLIC_ORIGIN、WEB_PORT（默认 8080）、WEB_COOKIE_NAME（默认 cta_visitor）、WEB_VISITOR_TTL_SECONDS（固定2592000，其他值拒绝）、WEB_DAILY_GLOBAL_LIMIT（200）、WEB_DAILY_VISITOR_LIMIT（20）、WEB_HOURLY_IP_LIMIT（5）、WEB_MIN_FREE_DISK_BYTES（5368709120）、WEB_PAUSE_KEY_FILE、WEB_DATA_VOLUME_PATH、WEB_ARTIFACT_MAX_COUNT（200000）、WEB_ARTIFACT_MAX_TOTAL_BYTES（4294967296）。后两个只装配本机或云端独立新Demo库的显式profile，不改变既有日常库容量；单份Artifact仍1048576。时区固定Asia/Shanghai，执行槽固定1，历史page_size固定20，不提供加worker/业务队列设置。

WEB_PUBLIC_ORIGIN必须由维护者明确设置：本机默认示例为http://127.0.0.1:8080，也允许显式选择http://localhost[:端口]；云端为实际http://公网IP[:端口]。单实例只接受配置的精确同源地址和由它派生的Host，不因支持本机而同时通配localhost、127.0.0.1或其他Host；不得从请求Host自动推断。浏览器必须使用该配置地址，切换主机名不承诺共享匿名Cookie。未配置origin/Key/数据库必需项不得ready。数据库和供应商设置由其所属模块解析，两端都不能用弱默认密码或假Key使生产装配ready。真实.env、Key、DSN不复制到镜像或Git；.env.example如实施阶段获准创建，仅给变量名、本机origin示例和Secret明确占位值。

## 5. 匿名身份、归属、CSRF 与暂停凭证

### 5.1 身份和归属

GET /api/session 是唯一创建匿名身份的 bootstrap 入口：生成 32 字节密码学随机 Cookie，数据库只存 SHA256 指纹、issued_at 和 expires_at；有效期固定从签发起 30 天，不滑动续期。Cookie 属性 HttpOnly、SameSite=Lax、Path=/、Max-Age=2592000；不设置 Domain。公网 HTTP 不能设置 Secure 或 __Host- 前缀。Cookie 过期、数据库身份过期或未知 Cookie 时签发新身份；拒绝把客户端提供的任意 visitor_id 当身份。

身份只代表持有 Cookie 的浏览器，不代表实名用户。同浏览器可读取自己的全部仍保留任务；换设备、换浏览器、隐身、清 Cookie 或过期无跨设备找回入口。身份过期不自动删除 PG 中的任务和摘要。公网 HTTP 缺少链路保密，HttpOnly 只限制脚本读取 Cookie，不能描述为 HTTPS 的安全替代；用户已接受该部署边界，整套 HTTPS/域名方案不在本期。

先验证有效身份，再查询 M14 run_owners 归属，最后调用 M11/M10/M12。所有 run 读取、SSE、下载、摘要、取消、resume、recover 均遵守此顺序。不存在或不属于当前身份均返回同一种 404，不暴露存在性、trace、暂停代数、候选或攻略。任务 UUID/URL、request_id、历史 cursor 都不是授权。公开样例不关联访客真实 run。

### 5.2 浏览器写请求

session 返回专用 CSRF token，由服务端对 Cookie 指纹和固定 csrf-v1 域做 HMAC-SHA256，Key 从暂停 Key 按固定不同域派生；只在响应体中给浏览器，不作为 Cookie。所有 POST 要求 Content-Type=application/json、Origin 精确等于 WEB_PUBLIC_ORIGIN 和 X-CSRF-Token constant-time 匹配。缺失 Origin 也拒绝；仅当前同源网页使用这些接口。禁止通配 CORS、URL 中传凭证、跨域 Cookie 接口、GET 执行业务操作。Host 白名单由固定 origin 派生，默认不信任 X-Forwarded-*；同源校验必须用配置值，不能用收到的 Host 值。

客户端 IP 从 ASGI socket client 取得并规范化 IPv4/IPv6，缺失或非法 IP 不接纳新任务。直接 IP 模式忽略伪造的 X-Forwarded-For；不启用 Uvicorn proxy headers。IP 指纹为固定规范文本的 SHA256，仅作配额索引，不宣称低熵 IP 哈希不可被枚举。共享网络的面试官共享 5/小时上限；页面说明限制并给样例入口。

### 5.3 加密交接

M14 实现 M11 公开 DurablePauseCredentialSink 的异步方法 async store(*,run_id:UUID,pause_generation:int,contract_digest:ContentHash,pause_token:str)->None。先加密，再在独立短事务保存 run_id/generation/contract_digest、cipher_version="aesgcm-v1"、随机 12 字节 nonce、ciphertext（含认证 tag）和 saved_at。暂停正文不入其他表、普通日志或 HTTP DTO。AESGCM 使用 32 字节 Key；每次加密使用新 nonce，同代数重放先解密已有值并精确比较，一致返回、不覆盖；不一致失败关闭。

AAD 为 UTF-8 固定格式 cta-pause-v1|{run_uuid小写规范串}|{generation十进制}|{contract_digest规范串}。读取时验证所属 run、最新有效代数和相同 contract，再认证解密。代数、run、AAD、Key、nonce/ciphertext 任一不一致均失败关闭；不重置 M11 digest，不合成新 token，不自动恢复。

Key 由显式 python -m app.web_application.keygen --output 指令一次性独占创建，已有文件拒绝覆盖；只输出“已创建”，不输出 Key。文件包含 base64 编码的 32 字节随机 Key 和固定版本；容器只读挂载，宿主限制权限并留维护者独立备份。启动只加载，不生成、不轮换。缺失/坏格式 Key 不启动；配置成另一个合法 Key 时旧凭证不能解密，任务明确“无法继续，请创建新任务或查看已完成结果”，不影响已存摘要的合法读取。密钥轮换、全库加密、保留和删除归 M19，不提前建 KMS。

M11 是等待输入状态、凭证 digest 和 lineage 的唯一裁判。凭证 sink 保存失败时不得显示可继续；孤立密文没有有效 M11 pause 不能使用。HTTP 回复和前端均不接收 pause_token，浏览器仅提交本轮业务选择和 pause_generation。

## 6. 应用元数据、配额与幂等

### 6.1 同库独立 schema

复用既有 PostgreSQL 实例和同一数据库，新增 web_application schema、自有 alembic_version 和一次显式前进迁移 m14_0001_web_application。只新增以下六表；不改变 M10/M11 表、head、容量行或已有迁移，不向它们建立删除级联或写入其 SQL。表使用显式 CHECK、主键和所列索引；时间均为 UTC TIMESTAMPTZ；仅 requests/admissions 的交接记录允许 UPDATE，其余 append-only，无 DELETE/TTL。

| 表 | 必需列与键 | 所有权/用途 |
| --- | --- | --- |
| visitors | visitor_hash CHAR(64) PK、issued_at、expires_at；expires_at=issued_at+30天 | Cookie 指纹和有效期；无原 Cookie |
| run_owners | run_id UUID PK、visitor_hash、bound_at；索引(visitor_hash,bound_at DESC,run_id DESC) | 仅归属和稳定分页锚点，不复制业务状态、phase、结果或预算 |
| requests | (visitor_hash,request_id UUID) PK、run_id、kind、body_fingerprint CHAR(64)、pause_generation nullable、status、reason nullable、created_at | HTTP 幂等和控制交接；status 限 claimed/dispatched/rejected/uncertain，不是工作流状态 |
| admissions | run_id UUID PK、visitor_hash、ip_hash CHAR(64)、reserved_at、day_key DATE、status；索引(day_key,status)、(visitor_hash,day_key,status)、(ip_hash,reserved_at,status) | status 限 reserved/counted/released；持久配额，不靠进程计数 |
| pause_credentials | (run_id,pause_generation) PK、contract_digest、cipher_version、nonce BYTEA、ciphertext BYTEA、saved_at | §5.3；不会返回浏览器 |
| run_summaries | run_id UUID PK、trace_id、terminal_code、availability、content_hash nullable、content_bytes nullable、size_bytes nullable、saved_at | 真正 RunSummary 或明确 process_history_unavailable 收据，详见 §9 |

SQL类型固定：UUID列用UUID、时间用TIMESTAMPTZ、generation/size用BIGINT、正文/nonce用BYTEA、受限枚举用TEXT+CHECK、day_key用DATE。所有指纹为64位小写hex，content_hash为sha256:前缀加64位hex；其余非nullable列NOT NULL。run_owners.visitor_hash外键引用visitors，并建立UNIQUE(run_id,visitor_hash)；requests/admissions的(run_id,visitor_hash)复合外键引用该归属对；pause_credentials/run_summaries的run_id外键引用run_owners。全部NO ACTION，不跨schema，不级联删除。requests增加部分唯一索引(run_id,pause_generation)，条件kind IN (clarify,confirm,hotel)且status IN (claimed,dispatched,uncertain)。generation仅三种暂停控制必需且>=0，其余必须NULL；nonce长度12，ciphertext至少16字节，summary的body/size/hash非空联合约束沿§9。admissions.day_key必须与reserved_at转换上海日期一致；visitors.expires_at固定比issued_at晚2592000秒。

requests.kind 固定 start/clarify/confirm/hotel/cancel/recover；reason 仅 §8 有限 code，不存异常或正文；claimed/dispatched的reason必须NULL，rejected/uncertain必须有安全reason。请求正文指纹为对严格规范 JSON 做 HMAC-SHA256，使用 Key 的 request-v1 独立派生域；不存用户正文或可离线枚举的低熵正文哈希。Web 派生 Key 使用 HKDF-SHA256，salt 固定 cta-web-v1，info 分别 csrf-v1/request-v1；与 AES Key 不混用。run_owners 不持久化 M11 lifecycle；首页历史每项仍通过 get_run 取得当前状态，不能把应用记录当第二个任务真源。

### 6.2 新任务接纳算法

1. 身份/CSRF/body 校验后先读同身份 request_id：指纹/类型不同返回 conflict；已交接返回原 run；uncertain/claimed 只核对原 run，不生成新的 ID，也不重发 start。
2. 新请求尝试取得本进程唯一执行名额；获取必须是无等待的原子 try_acquire，Busy 返回 409，不插表、不扣额、不排队。查询、样例和取消意图不要求名额。
3. 使用 M10 公共容量快照及实际数据盘 probe；低于 5GiB、容量已满、检查失败或依赖不健康返回 503。检查不承诺当前 run 必然写得下；最终容量裁判仍是 M10 事务，不扩大/截断/删除。
4. M14 短事务取得固定全局 admission advisory xact lock，先复验幂等，再按注入时钟计算上海自然日及滚动区间 (now-1小时,now]。reserved 和 counted 都占预留名额，以避免并发穿透；仅 counted 是已经成功接纳的新任务计数。检查 200/20/5 后原子插入 owner、claimed request、reserved admission。任一步失败整体回滚，释放名额。
5. 服务器生成固定 run_id/trace_id/turn_id；保存不含用户正文的请求元数据后，把内存中的严格命令交给唯一服务器后台 task。调用 diagnostics.start_run 一次，用 diagnostics.operation(kind="initial") 包围 durable.start。请求断开不取消该 task。
6. start 会先建立 M11 run 再执行图。应用通过公共 get_run 确认 run 已存在后将 admission 置 counted、request 置 dispatched，再返回 202。HTTP 首次确认等待最多 2 秒；超时或提交结果不确定返回 503 admission_unconfirmed 和原 request_id，不给“重新创建”指示。后台继续处理原 run。
7. 重复 POST 同 request_id、后台任务完成回调、启动时元数据核对只做 get_run 和应用元数据提交：存在则确认 counted；仅在执行已停止且确定 get_run=RunNotFound、没有未知数据库提交时释放 reservation 并标 rejected。依赖读取失败或未知 commit 保留 reserved/uncertain；不重发 start，也不猜测退款。启动核对不调用 recover/resume，不恢复业务；按每批最多 20 条读取未核对项。

M14 和 M11 不共享跨库事务；上述确认协议是必须测试的边界，不可伪称“一个原子事务完成 start”。保留的 reservation 显示接纳待核对，维护者可重试核对，不能悄悄过期放行。request 查询只读；核对写入由同 ID POST、后台回调或启动元数据核对承担。

失败/取消但已经 counted 的任务仍计额。澄清、酒店选择、resume/recover、刷新、结果与摘要读取都不重新扣新任务额度。全站自然日按上海时间零点切换；IP 是滚动小时，没有整点清零。429 返回适用的最晚解除时间作为 Retry-After，并给样例入口；日限、访客限、IP 限可同时触发，不泄露其他身份数据。服务器重启、浏览器多标签和并发请求不能清零额度。删 Cookie 可获得新访客身份，因此 IP/全站限额仍必须独立执行；本期不宣称防住分布式滥用。

### 6.3 控制命令幂等

所有 POST 使用 schema_version="1.0" 和客户端随机 request_id，指纹包含 route、run_id、pause_generation 及严格 body。同 ID 同 body 只返回既有交接记录；同 ID 不同 body 409，不能覆盖。一个 run 同一 pause_generation 的 clarify/confirm/hotel 在应用短事务中只允许一个未拒绝的声明；竞争命令 409 stale_pause 或 request_conflict。只有明确未交接的合法失败才可允许另一 ID 重试；unknown 不放开。应用交接记录不能替代 M11 的代数/凭证验证。

控制操作的 202 仅说明服务器持有该任务，不承诺 M11 已完成业务转换；request 查询和 run 视图反映真实后续结果。所有可执行控制都先无等待取得执行名额；request_cancel 仅持久意图，按 §7.3 特例处理。HTTP 超时不自动发送新的命令，不缓存任意数量的后台业务命令。

## 7. 执行、暂停、取消与显式恢复

### 7.1 单执行名额

名额覆盖 start/resume/recover 和真正执行 settle_cancel 的时间。它是应用调度限制，不把 M11 active/stored 上限改成 1。waiting/terminal/blocked 且实际执行 task 停止后释放；不能看到候选 terminal state 就提前释放，也不能持锁等待用户输入。Busy 请求立即结束，无队列、无自动排队重试。任务强引用和 done callback 均由 service 持有；后台异常必须消耗并映射，不能出现遗失 task exception。

暂停时保留必要的业务/诊断上下文；不按终态释放完整事件。新进程只 attach 已存在的 run，不调用 start_run 伪造 monotonic 起点。浏览器刷新、SSE 中断、页面关闭不取消执行；服务重启只重新装配，不自动续跑或重建取消维护任务。

### 7.2 命令构造

新任务由服务端构造 RequirementTurnInput(schema_version="1.0",turn_id=服务端UUID,user_message=输入,session_date=上海today,timezone="Asia/Shanghai",intent=INITIAL,current=全新RequirementDomainState())，再包装 DemoStartCommand。浏览器不提供 state、turn、intent、run_id 或 trace_id。

澄清：先从 M11 的当前安全暂停状态获取 RequirementDomainState，构造 intent=SUPPLEMENT 的新 turn，包装 RequirementClarificationResume 和 DemoRequirementCommand(map_requested=False)。确认：构造 RequirementConfirmation(decision,revision_text) 和 RequirementConfirmationResume；只有 accept 可以 map_requested=True。revise 的文本必填且最多 8000 字，accept 的 revision_text 必须 None；确认期间修改不另外 start。原用户确认摘要由 M01 outcome 提供，M14 不重新推导需求完整性。

酒店：浏览器仅给 ordinal 或 decline；服务端从当前 state/公开 M07 candidate Artifact 严格获取 candidate_set_ref 和相应候选 UID，构造 HotelSelectCommand/HotelDeclineCommand 与 DemoHotelCommand。代数、候选集合、可选资格和人工选择仍由 M07/M13 校验；不接收浏览器构造的 ArtifactRef 或任意 UID。凭证认证后调用 durable.resume；scope kind="resume"，不预先计 resumed。

### 7.3 取消

POST cancel 先验证归属并调用 request_cancel；没有执行名额也允许持久取消意图。requested 显示“正在取消”，不能当 cancelled。每 run 只有一个服务器维护 task，断线不终止；等待当前执行停止后尝试取得名额并调用 settle_cancel。Busy/requested 退避 1/2/4/5 秒，后续最多每 5 秒一次，sleep 时不占名额；不自建业务命令队列，不重新调用模型/地图。

收到 cancelled/already_terminal 时读取真实终态和最终预算，走 §9；blocked 只显示安全阻断，不伪称取消成功。关闭时停止维护，不启动无限任务；重启后用户再次取消或显式恢复才触发相应核对/维护。取消意图不能撤销；非所属任务不会创建维护任务。

### 7.4 恢复和不确定状态

recover 按 M11 get_run 的可恢复分类显示，按钮文字为“尝试安全恢复”；需要名额和 request_id。服务端在 diagnostics.operation(kind="recover") 中调用 durable.recover，不自行重放旧模型操作。合法已持久化人工暂停可跨进程恢复，再读取对应密文继续；M08/M09 私有上下文丢失、未知外部结果、失租、版本不兼容按 M11 失败关闭，不能宣称任意节点续跑。

暂停凭证不可用、契约不兼容或 blocked 时没有“继续规划”按钮；已完成结果可按既有资格读取。M06 broken 时停止接纳新的执行，安全关闭旧 M11/definition 和供应商资源后重新装配新实例；不原地重连旧会话、不重发未知旧操作。原任务只能按 M11 显式恢复规则处理，完整观测起点丢失后不生成整次摘要。

## 8. HTTP 合同与安全投影

### 8.1 严格 DTO

所有请求额外字段拒绝；JSON 最大 64KiB（流式累积超过即 413，不先无限读入内存）。字符串按字符限制，不能把 8000 字限制换成 8000 UTF-8 字节。UUID 必须规范 UUID 字符串，经 Pydantic 严格 JSON 校验；服务边界再 fresh round-trip，拒绝 model_construct/嵌套 mutation 注入。错误输出不含底层 ValidationError、用户原文或 Secret。

| DTO | 固定字段（共用 schema_version="1.0"） |
| --- | --- |
| WebStartRequest | request_id:UUID；user_message:str，1..8000 字 |
| WebClarifyRequest | request_id；pause_generation:int>=0；user_message:str，1..8000 字 |
| WebConfirmRequest | request_id；pause_generation；decision:"accept"/"revise"；revision_text:str/None；map_requested:bool；联合约束见 §7.2 |
| WebHotelRequest | request_id；pause_generation；decision:"select"/"decline"；ordinal:int>=1/None；select 必填 ordinal，decline 必为 None |
| WebControlRequest | request_id；仅 cancel/recover 使用 |
| WebVisitorContext | visitor_hash:str；expires_at:UTC datetime；由 session 校验创建，不能从 HTTP body 构造 |
| WebSessionView | csrf_token:str（URL-safe，1..128）；expires_at:UTC datetime；visitor_ttl_seconds:Literal[2592000]；不返回Cookie原文/visitor_hash |
| WebAdmissionView | can_start:bool；global_remaining/visitor_remaining/ip_remaining:int>=0；reason:Literal[busy,quota_exceeded,capacity_unavailable,service_closing]/None；retry_after_seconds:int>=1/None |
| WebCommandReceipt | request_id；run_id:UUID/None；status:"confirming"/"dispatched"/"rejected"/"uncertain"；reason:有限 code/None |
| WebFact | label:str<=80；value:str<=8000，纯文本 |
| WebHotelChoice | ordinal:int>=1；name:str<=512；address:str<=1024/None；facts:tuple[WebFact,...] |
| WebClarificationOption | value:str，1..64，仅小写字母/数字/下划线；label:str，1..80，纯文本 |
| WebClarificationQuestion | field:RequirementFieldPath（禁止budget字段）；label:str，1..80；kind:"single"/"multiple"/"date"/"datetime"/"number"/"ages"/"text"；options:tuple[WebClarificationOption,...]（最多6）；minimum/maximum:int或None；item_count:int 1..100或None；联合约束见§11.1 |
| WebInteractionCard | kind:"clarification"/"confirmation"/"hotel"；title:str<=128；paragraphs:tuple[str,...]；facts:tuple[WebFact,...]；choices:tuple[WebHotelChoice,...]，非酒店时为空；questions:tuple[WebClarificationQuestion,...]（默认空、最多40、不重复field），只在clarification非空 |
| WebRunView | run:DurableRunView；card:WebInteractionCard/None；actions:tuple[Literal[clarify,confirm,hotel,cancel,recover],...]；result_available:bool；summary_status:§9 状态 |
| WebHistoryPage | items:tuple[WebRunView,...]（最多20；历史投影 card=None）；next_cursor:str/None |
| WebResultView | result_schema_version="2.0"；markdown:str；days:tuple[WebResultDay,...]；delivery_status 和 terminal_code 来自实际 M11 state |
| WebResultDay | ordinal:int>=1；markdown:str，按 M12 原日块；不合成新攻略正文 |
| WebSummaryView | status:§9；summary:RunSummary/None；reason:有限 code/None；与状态联合校验 |
| WebSampleView | slug:str（1..64，仅小写字母/数字/短横线）；title:str<=128；generated_date:date；source_kind:"synthetic"/"validated_result"；realtime:Literal[False]；markdown:str（UTF-8不超过1MiB） |
| WebSamplesView | items:tuple[WebSampleView,...]，1..5份，slug不重复 |
| WebApiError | code:有限 code；message:固定中文；request_id:UUID/None；retry_after_seconds:int>=1/None |

WebSessionView只给JSON部分，Set-Cookie由bootstrap单独设置。WebAdmissionView使用本次socket IP，remaining减去reserved+counted；它是瞬时可接纳性预检，不保证随后start成功。can_start=True必须三项remaining>0、reason/retry=None；False必须有reason，quota_exceeded才带三项限额中最晚解除的retry秒数，其他reason的retry=None；查询依赖失败仍503，不能填0冒充可用快照。优先原因固定service_closing→依赖失败503→capacity_unavailable→busy→quota_exceeded→可接纳；POST实际事务仍重新判断。

request公开投影固定：claimed→confirming/reason=None；dispatched→dispatched/None；rejected→rejected/原安全reason；uncertain→uncertain/原安全reason。GET均200只读，不把claimed改库为dispatched；confirming表示原接纳等待核对，不承诺当前有执行任务。same-ID POST承担§6.2核对；首次2秒确认超时仍503 WebApiError(code=admission_unconfirmed,request_id=原ID)，浏览器保留ID并查询，不能用新ID自动重试。拒绝的启动没有M11 run时run_id=None；其余已确认归属的原run_id可以显示，不由浏览器据此获得更多权限。

投影只读上游严格 DTO：不把完整 state/outcome、PauseEnvelope、诊断原始记录、模型输出、工具正文、预算 ledger 或 Ref 直接 model_dump 给浏览器。确认事实来自 M01 summary；酒店 facts 来源是当前公开候选事实，不新增星级/实时价格承诺。Markdown 可以包含 M12 允许的来源文字，但不得被解释为执行权限或把内部 Ref 变成可任意读取的 API。完成攻略必须由 M12.load 成功确认完整，degraded 按原状态清楚展示；失败不提供半成品“正式攻略”。

### 8.2 路由

HTML 路由为 /、/tasks/{run_id}、/history、/samples；没有通用文件浏览或任意 Artifact API。

| 路由 | service 方法/行为 | 成功/主要失败 |
| --- | --- | --- |
| GET /api/session | bootstrap_session(cookie)，返回WebSessionView及独立Set-Cookie | 200；依赖失败503 |
| GET /api/admission | get_admission(owner,client_ip)，返回WebAdmissionView；socket IP不进入响应 | 200；依赖失败503 |
| POST /api/runs | start(owner,request,client_ip) | 确认接纳202；Busy409；quota429；容量/未知503 |
| GET /api/requests/{request_id} | get_request(owner,request_id)，只读交接记录 | 200；无归属404 |
| GET /api/runs | list_runs(owner,cursor=None)，每页20 | 200；非法cursor422 |
| GET /api/runs/{run_id} | get_run(owner,run_id) | 200；不存在/无归属404 |
| GET /api/runs/{run_id}/events | read-only SSE 快照，调用 get_run | 200；无归属404 |
| POST …/clarify、…/confirm、…/hotel | resume(owner,run_id,严格对应请求)，服务端构造命令 | 202；Busy/过期暂停409；凭证失败409 |
| POST …/cancel | cancel(owner,run_id,WebControlRequest) | requested202；真实已终态200 |
| POST …/recover | recover(owner,run_id,WebControlRequest) | 202；不适用/Busy409 |
| GET …/result | read_result(owner,run_id)，只读 M12.load | 200；未完整409；损坏/依赖503 |
| GET …/summary | read_summary(owner,run_id)，只读 M14 已保存摘要 | 200（包括不可用状态）；依赖失败503 |
| GET /api/samples | get_samples()->WebSamplesView，固定公开清单和正文，不访问真实 run | 200 |
| GET /health/live、/health/ready | 固定状态；ready 核验资源可用性，不迁移/执行 | 200或503；无版本、DSN或内部详情 |

service 公共方法均 async 且关键参数 keyword-only；owner 由 API 层 bootstrap 校验后传入，service 仍二次校验有效期/归属。bootstrap_session(cookie:str|None)->tuple[WebSessionView,str|None]内部委托store/security，第二项仅服务器Set-Cookie使用的新随机值或None，禁止序列化/日志；get_admission(owner,client_ip:str)->WebAdmissionView；get_request/start/resume/cancel/recover返回WebCommandReceipt；get_run返回WebRunView；list_runs返回WebHistoryPage；read_result返回WebResultView；read_summary返回WebSummaryView；get_samples返回WebSamplesView；close()->None。start参数owner/request/client_ip，其他控制owner/run_id/request；get_request为owner/request_id；run读取为owner/run_id；list_runs为owner/cursor=None。上述方法不暴露可变 task/SQL/Key，bootstrap是明确身份签发例外，不能绕过业务归属验证。

错误 code 固定：invalid_request、body_too_large、origin_rejected、csrf_rejected、not_found、busy、quota_exceeded、capacity_unavailable、dependency_unavailable、request_conflict、stale_pause、pause_unavailable、admission_unconfirmed、recovery_unavailable、result_not_ready、summary_not_saved、process_history_unavailable、service_closing。上游不可解释的程序/协议错误仍失败关闭并安全记录，不能套成 quota 或“成功但降级”。HTTP 401 只用于缺失/到期身份；404 把未知和越权合并；403 为 Origin/CSRF；其余按表映射。

### 8.3 读取、分页、推送

历史从当前身份的 run_owners 稳定索引分页，顺序 bound_at DESC/run_id DESC，cursor 包含两者、身份指纹和 HMAC，URL-safe 编码，最大 512 字；跨身份/篡改/无效签名拒绝。每项调用公共 get_run 得到当前状态；查不到的未确认 owner 绑定不冒充任务，不计入正常任务页，接纳待核对项由 requests 入口展示。批量读取并发最多 2，不裸查 M11 私表或返回全站 list_runs 后由浏览器过滤。

SSE 推送的是 M11 get_run 的安全快照（card=None），不是 M13 完整事件流；M13 事件不能被当作实时百分比。每 2 秒最多读一次，15 秒心跳；仅改变时发送 event: snapshot；终态发送后关闭。每连接缓冲最多一个最新快照，不存无限历史；最多 32 个全局 SSE，当前身份同 run 仅一个，替换旧连接只关闭推送连接。断线不触发 cancel/recover；重连重新读取当前状态，不凭 Last-Event-ID 重放业务。Cookie 每轮读取重新校验，到期关闭流。SSE 不压缩；其他 HTML/JSON/Markdown 响应启用 gzip，压缩不能绕过归属与 no-store。

任务详情只在get_run确认awaiting_user时经公开get_state投影交互卡，遇Busy/CancellationPending/新状态变化重读视图并返回无卡；result读取只在closed的合法交付终态使用get_state/M12.load，不能对executing/recovery_required直接取state或触发recover。M11.get_state既有公共行为可能对已验证的候选终态做内部安全元数据收束；此处“读取”不宣称所有上游SQL绝对只读，但绝不调用图/模型/地图、应用控制、finish或扣配额。SSE/list仅get_run不触发该路径；GET requests仅读自有表，GET summary先以M11公共get_run确认真实活动/终态，摘要正文仅读自有run_summaries表，不调用get_state、finish、recover或执行业务。M14不为此修改M11或建立第二份state真源。

访客 API/任务页 Cache-Control:no-store；公共静态文件/脱敏样例可明确缓存。原文、Markdown、来源和候选一律通过 textContent 或安全原生 DOM 构造，不用 innerHTML 注入；URL 仅允许 http/https，外链 rel=noopener noreferrer。CSP 默认同源脚本/样式、禁止 object/frame、无第三方 CDN/分析追踪；不能把私密数据写入 access log、URL、localStorage 或公开样例。

## 9. 最终摘要持久化与诊断内存释放

### 9.1 保存的对象

保存的是 M04 RunSummary 的完整九字段：schema_version、run_id、trace_id、terminal_code、duration_ms、cache_hits、event_delivery_failures、counters、budget_snapshots。字段和值必须来自 M13.finish 的真实输出，不增加 token/费用/原文/事件列表，不以 M02 InteractionRecord 的 run_summary 替代它。M02 专用记录器和维护者诊断导出沿既有 M10 §23，不能公开给访客；供应商未给 usage 的地方不补零、不估算价格。

run_summaries.availability 固定 available/process_history_unavailable：available 时 body 为严格 RunSummary 的规范 UTF-8 JSON、SHA256、size 全部非空且相符，单条最多 1MiB；unavailable 时三列都 None，仅保存明确不可用收据。严格校验字段/列身份、最终 terminal code、预算和 hash，未通过则 typed 失败，不返回部分统计。同 run 完全相同重放成功，任何不同摘要或可用性冲突拒绝覆盖；无 UPDATE/DELETE。不能后续把局部统计升级成“完整摘要”。

### 9.2 终态顺序

1. 必须是 M11 已确认的 closed 终态，或 cancelled/already_terminal 的已验证终态；执行 task 已停止。先取得 get_state 和 get_terminal_budget_snapshots，确认 run/trace/terminal 一致。候选图终态尚未经 M11 收束不能进入该流程。
2. 先只读查询并验证已有 run_summaries 收据；若与本次真实终态一致，直接复用，不再finish或另写不可用收据。否则 await diagnostics.finish(state=…,budget_snapshots=…)；协议违规不吞掉。完整输出复制成严格小对象并规范编码；history 不全则接受明确 None+process_history_unavailable，不能补造 start/event/duration。已有摘要冲突/损坏不是“缺行”，必须失败关闭。
3. 已知完整摘要先尝试在 M14 同库表保存；成功回执或对不确定 commit 的同 ID/hash 读回验证成功后，才释放完整事件内存。无完整摘要时尝试写不可用收据，不构造 RunSummary。
4. 数据库不可用/超时/容量导致诊断保存失败不改变合格攻略。将小摘要交给有限重试任务后立即 diagnostics.release_run，业务上下文通过 definition.release_run 单独清理；不让完整 events、闭包和 scope 随历史任务持续增长。
5. 重试包含首次总共最多 3 次，每次数据库等待最多 2 秒，退避 0.25/1 秒，总周期不超过 8 秒；最多 8 份待保存小摘要。超过上限不排队，释放并报告未保存。最终成功删除小摘要引用；最终失败同样删除，明确 summary_not_saved，不重跑业务、不写本地原始事件兜底。

用每 run 唯一 finalization task 防止多个终态回调重复 finish/保存/释放；handler 断线不取消它。终态/预算查询及finish必须在M11仍open时完成，或在关闭前已取得完整严格小摘要后仅继续保存；M11.close立即禁止这些查询，不能关后再读。关闭按§10有界预收束，超时仍清理完整事件和小摘要，状态为未保存，不能无限阻止退出。操作中的 CancelledError 传播；必要清理由 finally 执行，不能把业务取消吞成正常成功。对于 blocked 或确定未创建的 run，在执行停止后直接清理 diagnostic/business context；必须靠M11.close停止且没有预先取得合法终态的任务不能finish或补造摘要。waiting在服务活跃期保留，退出仅清内存，不把它改成终态或用户取消。

### 9.3 读取与重启

WebSummaryView.status 固定 not_finalized、pending_save、saved、not_saved、process_history_unavailable。saved 时 summary 必须非空且 reason=None；其他状态 summary=None；not_saved 的 reason=summary_not_saved；history 不可用的 reason=process_history_unavailable，其余 reason=None。内存中正在有限保存显示 pending_save；正式读取返回的完整摘要仅来自验证后的 PG 行，不直接返回内存缓存。

读取时先核验归属并以M11公共get_run取得真实活动/终态；尚未终态显示not_finalized，不从应用表推断lifecycle。已终态再读自有run_summaries：真实曾生成并保存的完整摘要可以跨重启读回，不需要重建事件；M13仍不自己查询数据库。已有不可用收据时显示process_history_unavailable；没有行但本进程正在有限保存时显示pending_save；没有行且没有正在保存任务时明确not_saved。上游状态或数据库查询失败返回安全typed错误/503，不能当成“无摘要”。不采用原先提议的正常摘要“100 个/24 小时”缓存；历史摘要存PG，只有失败重试短期保留小对象。

UF13-RECORD-001 的 M02 专用结束记录取消窗口继续按既有延期条件跟踪；上述完整业务摘要保存不能宣称解决了它，也不能声称专用原始诊断永远完整。

## 10. 启动、关闭和故障处理

启动顺序：严格读取显式配置/Key并创建自有clocks/evidence sink → M14专用metadata pool/独占连接核验自有结构/head并取得实例锁 → 创建M10公开runtime，由它核验所属结构/head及新云capacity profile → M02 provider/runtime → AsyncExitStack进入M06 runtime → 创建并持有diagnostics及借用它的RunBoundExtractionInvocationSink → M03/M01 → M13 definition → 创建M11公开runtime，由它核验所属结构/head并注入definition、凭证sink和取消工厂 → M12 → service → API ready。所有所属公开runtime要求的head/结构检查通过后才ready；M14不裸查上游业务表来提前核验。任一步失败逆序释放已创建资源，不自动迁移或重建库。diag表不可用与业务库损坏的不同启动行为沿M10§23.4，不自行宽容业务head错误。

关闭顺序：停止接纳/取消维护 → 最多5秒等待已有执行自然结束（不自动request_cancel或伪造终态）→ M11仍open时给已停止且合法终态的finalizer最多8秒总预收束窗口，取得真实state/最终预算并完成finish、保存或移交严格小摘要 → M11.close按其5秒合同停止剩余执行 → 仅处理已冻结小摘要的剩余有界保存和内存清理 → definition.aclose → M06异步上下文退出 → M02.aclose → M10.close → M14 metadata pool/独占连接关闭。关后不得get_state/get_terminal_budget_snapshots/finish；关闭中断而未预取合法终态的run仅在确认停止后清上下文，不生成摘要、也不说已取消。小摘要保存仍受原8秒生命周期，不重新起一轮8秒重试。

关闭前预收束窗口到期即停止新的终态采集，取消并等待未完成的采集task退出；若无法确认task已停止，不继续宣称安全关闭。完整事件只在相应执行/operation已退出后释放；已经冻结小摘要可跨M11.close写PG，因为只依赖M14存储。清理异常不遮蔽原异常；专用锁直到已停止所有执行才释放。未确认task停止时不关闭借用供应商对象；M11未知结果留待显式安全处理，实际总关闭时间在AC14-18验收，不伪称任何run都能在关闭期得到完整统计。

| 故障 | 行为 |
| --- | --- |
| 配置/Key/head/capacity 行不匹配 | 不 ready，不自动修复 |
| 单槽忙/额度满/磁盘低 | 新操作明确 Busy/限额/容量提示；样例和合法既有读取可用 |
| 业务 Artifact/预算/checkpoint 写失败 | 沿 M10/M11 fail-closed，不改成仅统计警告 |
| 最终摘要写失败 | 合格攻略仍可读，释放事件、有限重试、显示统计未保存 |
| 凭证缺失/错误 Key/坏 tag | 禁止继续，不生成替代 token；已完成结果与摘要按归属读 |
| 模型截断/研究失败/不完整攻略 | 保留真实失败，不强制转 completed；页面给重新创建与样例入口 |
| 浏览器断线/刷新 | 执行继续，只重新读取，不重复扣额/恢复 |
| 服务重启/独占锁失效 | 不自动重放外部操作；M11 重新分类，用户显式触发 |
| 不属于当前浏览器 | 统一404，无外部调用，无状态泄露 |

## 11. 页面与面试体验

首页给一份可编辑旅行需求示例、开始规划、完整样例和我的任务入口；输入日期需由访客确认，不把过期示例说成实时事实。无供应商 Key 表单。点击提交前显示真实体验会消耗公共额度；Busy/429/503 均保留已填需求和清楚中文说明，样例入口始终可见。

任务页显示实际 phase/活动状态和阶段名称，不给虚构百分比。澄清卡显示领域问题；确认卡展示原领域摘要，允许接受或文本修改；酒店卡逐项显示已验证事实，必须人工选择或全部放弃。只展示当前合法动作；提交中禁用重复按钮，确认未知时保留原 request_id 查询。取消提示“正在取消”，真实收束后才写“已取消”。可恢复状态显示有限说明及显式按钮，不在刷新时自动执行。

完成攻略展示 M12 已验证 Markdown 总览与按日卡片，degraded 原因和费用未核验清楚可见。默认折叠统计，展开读保存状态、总耗时、安全计数、预算快照和观测失败；未知写未知，不给推算费用。安全原生 DOM 先实现完整可读内容；不为解析 Markdown 引入不受控 HTML。可复制/下载同一 M12 Markdown 快照，仍需归属和完整结果资格。

我的任务每页 20 条，只有当前匿名身份；显示真实活动/终态、创建/完成时间（来自 M11）和进入任务按钮。换设备/到期找回限制在此页明确说明，不提供全站历史搜索。

公开样例使用独立固定 manifest 和脱敏 Markdown，包含标题、生成日期、source_kind（synthetic/validated_result）、是否实时=false。离线合成内容必须标为模拟；经 M15 真实验收并获脱敏导出授权后可替换为真实合格结果。不能读取或发布现有 m13-live-evidence；不能把 fixtures 标成真实运行。样例不显示 Key、run/trace、私有 Ref、用户原话或原始模型诊断。

前端无需构建工具或外部 CDN。普通 HTTP 下不能依赖只限安全上下文的 crypto.randomUUID；用 crypto.getRandomValues 生成 UUIDv4 request_id。不可用时显示浏览器不支持安全提交，不用 Math.random 降级。request_id 在内存中保持到确定回执，不把凭证/私人正文写 localStorage；页面重载以服务器任务/请求状态为准。

### 11.1 澄清选择表单（D14-18）

网页投影读取 fresh 后的 M01 NeedClarification.requirement、metadata 和 issues，按 M01 §6.5/6.6 的必要字段存在性、明确未选择的兴趣/节奏以及当前问题 field_path 生成 questions；不解析 message 关键词，不调用领域私有方法，不校验或修改业务 state。已完整且无当前问题的字段不再询问；自愿的交通/酒店/作息偏好不因为空而询问。本期不追问预算金额。原 message 继续以安全纯文本显示，无法以有限控件表达的问题仍可通过“其他补充说明”回答，不能把问题丢掉或伪造已解决。

| 问题 | 控件与选项 |
| --- | --- |
| 兴趣偏好 | multiple：人文、自然、美食、打卡、由 Agent 决定；Agent 决定与其余四项互斥 |
| 行程节奏 | single：轻松、标准、紧凑、由 Agent 决定 |
| 正式游玩日期、抵离日期时间 | date/datetime 原生选择器，时间精度到分钟；不默认今天或猜测时间，不擅自推导并提交天数 |
| 成人/儿童/老人/房间/游玩天数 | number控件范围：成人与房间至少1，儿童老人至少0；人数房间最多100，游玩1..60；无默认值。成人沿主设计§3.1及M01现行领域门槛，游玩上限沿M13；控件范围不代替领域校验，人工文本入口仍可用 |
| 儿童年龄 | ages：每位0..17，按已知正儿童人数或本轮显式选定正人数显示恰好对应数量；选0时隐藏并不提交遗留年龄，未知人数不能当0 |
| 房型要求 | single：大床房、双床房、家庭房、无特殊要求、其他；选择其他必须填写具体说明；“无特殊要求”沿M01原规范化规则 |
| 行动限制、饮食禁忌 | single：无、有，需要说明；选择有必须填写具体说明，不能默认无 |
| 城市、具体机场/车站、无法枚举的问题 | text：保留必要的具体名称/说明输入，不提供未经验证的地点事实或全国固定POI表 |

问题 DTO 只描述控件，不含完整 requirement、证据、暂停凭证或诊断。当前冲突的已保存字段值以只读WebFact显示，不把该值预填为本轮回答；偏好Agent决定字段的问题映射到对应兴趣/节奏控件，单独pending_confirmation值留待原确认摘要，不重复问。single/multiple 至少2个不同 value 的选项，其他 kind 的 options 必须为空；只有 number/ages 必须有 minimum/maximum，两者必须是非布尔整数且0<=minimum<=maximum<=100；只有 ages 必须有 item_count，其余为None。所有输入从未作答开始，表单不得预选、自动提交或因刷新推断同意。

提交把用户实际选择组成有限中文 user_message，继续使用原 WebClarifyRequest 及既有 request_id/pause_generation/CSRF/归属检查，经 M03证据校验和 M01合并再生成确认摘要。前端只验证必答、控件格式、其他说明和8000字上限；业务冲突仍由领域服务判定，不声称前端解决了冲突，不改变原SUPPLEMENT/correction合同。其他说明可作为人工文本回答入口；若使用它回答本轮全部问题，明确选择“用文字回答本轮问题”后才启用该方式，避免在未选择时发送空值或默认值。同一run/代数/问题快照的10秒轮询保留选择、年龄与其他说明草稿；run、代数、问题或合法动作变化时清理旧控件。表单草稿仅在页面内存，不写localStorage/sessionStorage，不污染原请求幂等机制。

这属于未发布网页投影的兼容增量：新 DTO 从包根导出，WebInteractionCard 的 questions 默认空；原字段及全部写请求 schema_version="1.0" 保持，旧文本响应仍有明确人工回答入口，不增加路由、表、依赖或可信状态写接口。

## 12. 本机入口、2 核 2GB 云配置与交付准备

### 12.1 选定 profile

| 项目 | 固定值/用途 |
| --- | --- |
| 服务 | 一个 Web 容器/一个 PG16 容器；单 Uvicorn worker，无 Redis、Celery、独立前端开发服务器 |
| 内存 | Web mem_limit=1GiB、PG mem_limit=512MiB；约512MiB留给系统/Docker，实际测量为准 |
| PG | shared_buffers=128MB、work_mem=2MB、max_connections=20、max_parallel_workers_per_gather=0；保留既有数据安全默认值，不关闭fsync |
| Artifact | 新云库首次初始化 max_artifacts=200000、max_total_bytes=4GiB、max_artifact_bytes=1MiB |
| 交互诊断 | 8MiB单条/1GiB正文总量；云 profile fallback_dir=None，避免无限本地 JSONL |
| 数据盘 | 新专属命名卷，不挂本地开发库；Web 以只读同卷挂载点做 statvfs，不读取PG正文文件 |
| 磁盘门槛 | free_bytes < 5GiB 时拒绝新任务；读取/取消意图不挡；不自动删除、清理或扩容 |
| 流量 | 普通响应 gzip，SSE除外；样例/静态资源可缓存，私有数据 no-store；不提供原始诊断下载 |
| 外部访问 | WEB_PUBLIC_ORIGIN 明确公网IP与端口；PG不映射公网端口；只开放实际 Web 端口 |
| 日志 | Docker json-file max-size=10m/max-file=3；不记录正文、Cookie、Key、DSN或token |

PG 的 Artifact/诊断正文配额不包括 checkpoint、WAL、索引、镜像和系统日志，4GiB+1GiB 不是整个数据库磁盘上限。单容器内存限制不是整个服务器的峰值保证；2GB 无新增业务队列、等待输入不占槽，也不意味着 200 个新任务一天一定跑完。容量不足时保留真实安全错误，不减少 M13 预算、删除必要候选或改成虚假成功。

M10 既有默认 2048/64MiB 仍作为通用默认保留；此新云 profile 只显式传入更大的合法 limits。新库首次写 capacity 行后冻结，已有行不同即启动失败；扩大旧云库也需要独立迁移/容量变更授权。M11 库容量/每 run 操作预算沿既有合同，不因硬件大小擅改；M13 每 run 4096 managed/16384 journal、业务预算和1..60天范围仍有效。

### 12.2 实施后的验收与部署边界

交付顺序固定如下，不以准备cloud文件代表部署已经开始：

1. M14先在本机交付可启动应用。compose.yaml新增显式m14-local运行profile，使用runtime镜像的python -m app.web_application和真实生产装配；Web宿主端口默认只映射127.0.0.1:8080:8080，容器内监听服务端口，PG不映射宿主端口。本机Demo使用独立新PG卷及显式生成、只读挂载的Key；M10/M11/M14迁移由维护者显式执行，不连接或修改日常开发库。README在实施后给初始化、启动、停止和浏览器入口步骤。本机运行profile不借用测试服务工厂或测试替身，不新增生产TEST_MODE。
2. M14在隔离测试资源完成自动化、真实PostgreSQL集成、实际浏览器交互、重启、摘要和资源测试。离线自动化允许模型/地图typed替身，但通过真正M13/M11/M12；测试卷与本机Demo卷分离，测试清理不能碰本机Demo数据。M15真实供应商网页主链在另获API额度与验收授权后也先通过本机实际生产入口验证，记录真实成功与失败，不能用离线替身宣布真实业务通过。
3. 本机上述相应测试/验收通过后，才按新的明确部署授权将同一应用代码和已验证锁定runtime镜像部署到腾讯云，使用compose.cloud.yaml及独立新云PG卷，仅替换origin、Secret位置和环境配置，不复制本机数据库或另写云专用业务实现。云端随后完成2GB实机资源与网页冒烟/发布Gate，合格后再填写README真实体验链接。本机测试通过不能直接宣称云端性能或公网体验已通过。

需在隔离环境测量一条完整离线真实主图、多个暂停任务、顺序多 run、摘要失败/释放及 SSE 的峰值 RSS、容器 OOM、PG连接数、磁盘增量、响应大小和关闭时间。M14 可用替身证明接口和资源纪律；2GB 实机及真实供应商的杭州网页主链仍归 M15 授权验收。本轮没有任何“已压测足够”的证据，不能用历史 M13 成功替代。

未来 README 体验地址只在取得实际地址及部署授权、通过相应发布 Gate 后填写。在此之前明确待部署，不编造可访问 IP/URL。腾讯云试用一个月不是永久免费；本期不自动续费、购买资源或部署。Docker CE 镜像及 Compose 能否满足文档配置应在未来只读预检核实；版本升级和真实服务器操作需相应阶段授权。

实施阶段准备单独 cloud Compose 覆盖文件：迁移仅通过显式一次性 migrate 服务/指令；Web 启动校验 head，不以 depends_on 自动触发正式迁移。新云卷命名带项目前缀，运行 profile、Key、备份位置和清理对象在实施报告列明。Windows本机开发只运行Docker内部Python；云端同样使用锁定镜像运行，不要求维护者安装宿主Python。

技术依据：[PG16资源设置](https://www.postgresql.org/docs/16/runtime-config-resource.html)、[连接配置](https://www.postgresql.org/docs/16/runtime-config-connection.html)、[Docker内存限制](https://docs.docker.com/engine/containers/resource_constraints/)、[FastAPI单/多进程](https://fastapi.tiangolo.com/deployment/server-workers/)、[lifespan](https://fastapi.tiangolo.com/advanced/events/)、[AESGCM](https://cryptography.io/en/49.0.0/hazmat/primitives/aead/)、[Cookie属性](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)、[getRandomValues](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues)。上述具体容量与参数是本项目选择，资料不构成2GB承载保证。

## 13. 验收矩阵

实施/审查必须逐条对照本表及引用的上游合同；测试通过不能替代所有权、版本、释放顺序、失败语义和资源证据。

| AC | 必须证实 | 核心测试层 |
| --- | --- | --- |
| AC14-01 | 两份文档、上游增量及范围一致，公共导入无资源副作用 | contract/独立审查 |
| AC14-02 | 真实生产组合根入口/资源归属；M06异步ctx、M01时钟/证据、M13不重复图 | unit/contract |
| AC14-03 | Cookie固定30天、身份有效期、所有读取/写/SSE归属、统一404 | unit/HTTP/PG |
| AC14-04 | Origin/CSRF/Host/IP/body/XSS/Secret canary，无原token和原始诊断公开 | contract/browser |
| AC14-05 | 200/20/5持久配额、上海跨日/滚动小时、并发原子预留、失败取消计额 | PG integration |
| AC14-06 | start与metadata非共享事务；重复/未知commit/重启核对不重发、不虚报退款 | PG/新解释器 |
| AC14-07 | 唯一执行槽无队列，waiting释放；跨请求后台存活、独占实例失锁关闭 | unit/PG/browser |
| AC14-08 | 正确clarify/confirm/hotel命令，真实M01/M03 revise及recover事件关联、已知turn精确核对/内部turn绑定/无或失效binding拒绝，旧代数/假Ref拒绝 | contract/完整主图 |
| AC14-09 | AES同代数重放、随机nonce/AAD/错误Key/tamper/重启；无替代token | PG/新解释器 |
| AC14-10 | cancel意图不挡，唯一维护/退避/收束；安全显式recover，不恢复私有模型上下文 | M11回归/集成 |
| AC14-11 | M12完整load才显示结果，degraded正确、failed无正式攻略，旧版本拒绝不复制门槛 | 主图/HTTP |
| AC14-12 | 完整summary规范/hash/幂等/读回；事件保存后释放，失败也释放且有界；M11关闭前采集/finish，关后只保存已冻结摘要 | unit/PG/资源 |
| AC14-13 | 重启可读已保存summary，历史缺失无伪造；取消/decline/失败同样终态收束 | 新解释器/主图 |
| AC14-14 | 每页20且不泄漏全站任务，cursor绑定身份，SSE有界/重连只读/no-store | PG/browser |
| AC14-15 | 本机浏览器入口及四页面/样例完整、默认折叠统计、普通HTTP request_id、离线替身明确标记 | browser |
| AC14-16 | 新云profile初始化/旧行拒绝/磁盘<5GiB拒新/公共只读容量，无删除迁移本地库 | PG/容量 |
| AC14-17 | 显式迁移新schema、幂等重跑/失败回滚/新进程结构head，旧业务表不变 | 独立迁移Gate |
| AC14-18 | 本机runtime入口/回环端口/独立卷/显式初始化可复现；单worker/两容器内存/连接/关闭/日志配置，离线Docker全Gate、三coverage均>=70% | Docker/coverage |
| AC14-19 | 先本机真实杭州浏览器/供应商验收，后授权云部署及2GB实机/公网重新资格、README真实体验链接 | M15：本轮NOT_RUN |
| AC14-20 | 当前问题准确投影/严格DTO/选项无代选；Agent多选互斥、日期数量、其他必填、儿童条件年龄、草稿稳定；选项实际提交真M01/M03并达到原确认/酒店/完整结果门槛，错误不绕过原校验 | unit/contract/真实PG主图/Chromium/双审 |

P0/P1 和高概率影响主演示的 P2 必须修复；低概率/边缘 P2 与 P3 登记 [统一延期清单](VALIDATION.md)，不得以延期掩盖迁移、归属、幂等、内存泄漏或真实主链高概率故障。发生已确认决定冲突、上游合同缺口、超白名单或生产动作要求时 STOP 并请求唯一必要决定。

## 14. 澄清与独立审查记录（非规范性）

### 14.1 澄清收束

本轮已收束 D14-01..16。用户选择了腾讯云2核2GB并要求按硬件选最合适的配置；摘要由“是否现有持久化”进一步明确为同库保存最终真实 RunSummary，再释放事件，且批准保存失败的有限小摘要例外与凭证AES方案。最后写作授权只创建两份M14文件，原第三份中的澄清/审查内容并入本文与配套规范；既有相关文档按获准白名单同步。

没有取得真实服务器登录/部署、GitHub发布、API额度或代码实施授权；没有把历史真实测试当作M14/M15资格。未来这些动作必须由相应阶段用户授权。短录屏、旧正常摘要缓存、海外服务器、域名/HTTPS及其他平台重建均不在本次确认范围。

### 14.2 审查状态

初稿写成后安排两个新的 GPT-6.1-sol / max 子 Agent，仅只读：A审架构及上下游一致性；B审弱模型可执行性。两位都先逐条审查，再由主模型修订，之后同两位第二轮复核，最后主模型总审。此处回填实际结果，不预写PASS。发现条目必须给文件/章节或行号、级别、问题、后果和最小建议；延期进入统一清单。

两位首轮均BLOCK；去重后5项P1、3项P3。主模型在授权文档范围最小修订，同两位第二轮确认首轮项全部关闭；第二轮仍分别发现A2-01（P2）和B2-01（P1），当轮结论均为BLOCK。主模型修订这两处及对应测试矩阵后，同两位定向补充复核均PASS，主模型总审PASS。累计6项P1、1项P2、3项P3全部文档层关闭；没有改变D14-01..16、修改代码或扩大本轮文件白名单，未拒绝或延期本轮意见。

| 记录ID | 来源 | 级别 | 问题与最小修订位置 | 当前处理 |
| --- | --- | --- | --- | --- |
| DR14-01 | 首轮A-R1/B-01 | P1 | M11.close后不可读终态/预算；§9.2/§10、执行§7/C13/C17改为关前取证/finish、关后仅保存冻结摘要 | 第二轮复核关闭 |
| DR14-02 | 首轮A-R2 | P1 | 确认revise内生turn_id与直接sink不匹配；§3.1/§4.1、W05/C10用显式可失效RunBound sink、真实M01/M03 Gate | 第二轮复核关闭 |
| DR14-03 | 首轮B-02 | P1 | M10精确__all__旧测试不在未来白名单；执行§3.3/W01、M10执行§24只追加导出预期 | 第二轮复核关闭 |
| DR14-04 | 首轮B-03 | P1 | 早期验证入口晚创建/跨未来阶段测试；W02建unit入口，W03加PG，W05扩Integration，子项明确分阶段 | 第二轮复核关闭 |
| DR14-05 | 首轮B-04 | P1 | session/admission/samples和claimed投影未闭合；§8.1–8.2严格DTO/方法类型、socket IP、confirming只读回执 | 第二轮复核关闭 |
| DR14-06 | 首轮A-N1 | P3 | 正式DurablePauseCredentialSink/async store、await finish名称与调用写明 | 第二轮复核关闭 |
| DR14-07 | 首轮A-N2 | P3 | README框架确认时态与首屏不一致；同步为已选、未实施 | 第二轮复核关闭 |
| DR14-08 | 首轮B-05 | P3 | 主设计不存在§13；两M14必读引用改§10–12 | 第二轮复核关闭 |
| DR14-09 | 第二轮A2-01 | P2 | 自有表不足以区分缺摘要时活动/终态；§8.3/§9.3以公共get_run判断真实状态、正文只读自有摘要；执行C13同步 | 两位定向补充复核关闭 |
| DR14-10 | 第二轮B2-01 | P1 | diagnostics/sink必须先于M03构造；§10闭合启动依赖，各公开runtime核验自身head、失败逆序清理；执行C17同步 | 两位定向补充复核关闭 |

主审另明确了M11.get_state既有内部安全元数据收束行为，SSE/list只用get_run、交互卡/结果读取限制在合法状态；不伪称所有上游SQL绝对只读，不改变M11合同。此前主总审逐条核对16项决定、19条AC、阶段/矩阵及10份同步文档，无未处理阻断或新增延期。此前定稿状态：DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL；最终批准尚未取得，代码/迁移/云操作未执行。后续本机优先澄清及当前资格见§14.3，现场检查和各轮实际结果见配套执行规范§14。

### 14.3 用户追加的本机优先顺序

上述PASS是追加澄清前的定稿记录。用户随后明确先实现本机可使用的前端入口、测试后再云部署；本次补入D14-17，明确本机origin、独立m14-local运行profile、真实生产装配入口以及本机测试/本机真实验收/授权云部署的先后。D14-01..16和业务合同保持，未新增文件或代码授权。原两位对本次新正文独立定向复核均PASS，无新增阻断/延期，旧关闭项未回归；主总审核对17项决定、19条AC及相关阶段/矩阵通过。当前状态DESIGN_REVIEW_PASSED_AWAITING_USER_APPROVAL，最终正文hash/结果见配套执行规范§14.3；不沿用旧正文hash冒充本次修订资格。本机入口尚未实施，服务、代码测试、迁移、真实API及部署仍未执行。

### 14.4 批准后的实施与逐阶段核查（2026-10-05）

用户已明确批准两份文档并授权按W00–W10实施整个M14、每个通过阶段由原两位子Agent核查、完成后等待提交。顶部为批准后的开工授权状态，§14.1–14.3保留当时历史事实；当前实施资格以本节和IMPLEMENTATION_STATUS为准。批准规范正文SHA256（首“## 14. ”前，CRLF转LF/UTF-8）为W14 C1044580CEBDB290FC1A18F581C1F27F426563C69AC42DC4A9C6FA0886C6E2E6、E14 8BE34F71BB0DF858BEF7275BB04CC6A7CF3D68CCD8C57393BA6CF7FC7A8FABDD，实施期间保持。

W00、W01、W02、W03、W04、W05、W06、W07、W08、W09现均获原架构A和执行B各自正式PASS。相邻批按合同统一验证，迁移W03独立；各阶段发现的小范围合同一致问题已在精确白名单修复并复核。W08最终证据来自<EVIDENCE_DIR>的M14通过分项、w08up-20261005e完整上游补验、w08browser-20261005e和w08capacity-20261005e独立资格；d整批FAIL保留，上游补验仅TARGETED_UPSTREAM_GATE_ONLY，均不冒充W10 Closeout。两位逐AC14-01..18及C01..17核对源码、事务、凭证、终态释放和资源证据，无未结P0/P1/高概率Demo P2、无新增延期；AC14-19/M15仍NOT_RUN。

M14 279定向/2594全量unit-contract、53真PG及三个新Docker读取通过，读取外部调用0。覆盖全部16份生产Python/166实际官方subprocess片段，line2002/2232=89.6953%、branch490/652=75.1534%、combined2492/2884=86.4078%；无omit/缺源/skip/xfail/rerun。受影响M10原coverage及真实PG restart对、M11全253功能、M13原122 coverage、M12原94诊断/4PG/三日真restart一致通过，旧M11严格coverage债和M13专用记录窗口边界保持。Browser五项及非终态SSE SIGTERM退出0、EOF0.651479s、资源均关闭；Capacity五完整/两暂停、weakref释放、peak307007488 bytes/connections10/OOM0、root close0.002132s通过。详细计数/命令/早期失败和证据见IMPLEMENTATION_STATUS，F仅记录真实延期边界。

当前进入W10最终同步和新构建完整关闭Gate，README使用实际生产CLI、本机独立profile、显式Key/三迁移/启动/停止。分支feature/module-14-web-application，开工/当前HEAD均3a7564fa1ea451c356b2d998094e9aa0b4de57fc，staged空，78获准文件（57新建/21跟踪修改），私有旧目录保留且未读正文。真实API/云/公开链接尚未执行；stage/commit/merge/push/PR/deploy均未授权、未执行。W10完成并双审通过后才报告MODULE14_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE。

### 14.5 整模块实施完成与最终关闭资格（2026-10-05）

当前状态 `MODULE14_IMPLEMENTATION_READY_FOR_USER_ACCEPTANCE`，W00–W10 全部由原 A 架构与 B 执行审查者分别逐阶段正式 PASS，等待用户验收与提交授权。顶部和 §14.1–14.4 为批准开工及各时点快照，不改写当时进行中或失败；本节与 [实施状态最终报告](VALIDATION.md) 是最新事实。规范正文 hash 保持 §14.4 批准值，没有为实现更改设计或扩大范围。

同步后唯一完整关闭命令 `./scripts/verify_module14.ps1 -Set Closeout -ProjectPrefix cta-m14-w10-20261005b -EvidenceDir <EVIDENCE_DIR>` 退出 0；新 runtime/test/browser 镜像、65 步骤全部 exit0，qualification status/cleanup PASS。279 定向、2594 全仓 unit/contract、53 真 PG、三个新 Docker 重读、M10 原 coverage/真 PG restart、M11 全253功能、M13 原122 coverage、M12 原94诊断/4 PG/三完整日块 restart manifests、独立 Capacity 与5 Chromium 均通过。M14 覆盖全16生产 Python/166官方 subprocess片段/七 required入口，line2002/2232=89.6953405018%、branch489/652=75%、combined2491/2884=86.3730929265%，无omit/缺源/skip/xfail/rerun；原 M10/M13 三项70% Gate通过，M12仍仅诊断，M11原coverage债保持。

独立资源测量为5完整终态、2暂停均真实取消、终态事件weakref释放，peak307208192 bytes、最多10应用连接、OOM0、root close0.001917s；模型42/地图125计数仅typed替身，真实外调0。非终态SSE SIGTERM后0.574163s EOF，server exit0/OOMfalse、service/provider/maps已关闭、SSE0。五精确归属资源cleanup各exit0，主独立inventory剩余0；亲看本批历史/公开样例PNG完整。新schema实际revision为 `m14_0001_web_application`，只在本次独立测试PG显式迁移，旧业务结构/数据与日常库边界保持。

两位 W10 正式核查逐AC14-01..18/C01..17对源码和原始收据，确认实际生产CLI、README本机Key/三迁移/启动停止、回环端口、Demo与测试独立卷、云文件仅准备；文案及收据引用P3均在白名单内修正，无未结阻断或新延期，不改变最终构建输入。a批为最后README同步而有意中断，integration137/原Gate1/无qualification仍保留，不能套为PASS。所有计数、镜像ID、逐AC、阶段表、78文件逐项和早期失败见上述实施状态；F原风险与UF13-RECORD-001专用记录窗口保持。

最终分支 `feature/module-14-web-application`，前/后HEAD均 `3a7564fa1ea451c356b2d998094e9aa0b4de57fc`，78文件（57新建/21修改）、异常源文件0、staged空、diff/hash检查PASS，私有旧目录保留且未读正文。stage/commit/merge/push/PR/deploy均NOT_RUN；M15真实API、本机实际供应商业务、腾讯云2GB实机、公网资格及README真实体验链接均NOT_RUN。当前本机应用与离线资格完成，尚未提交、合并或发布。

### 14.6 获批选择表单维护与本机交付（2026-10-05）

用户本机体验后明确批准D14-18、§8.1/§11.1/AC14-20增量及配套执行§15。U01+U02已在14份既有文件白名单内完成：澄清使用准确选项、日期/人数专用控件、条件年龄和其他说明，保留人工回答、原message/只读冲突及既有M03/M01提交链。未新增路由、上游意图、预算、依赖或schema；原W00–W10收据仍保留为历史。

新完整资格为`cta-m14-clarify-20261005b`，正式Closeout命令退出0、65步骤全exit0，305模块/受影响与2620全仓unit/contract、53真PG、三个新Docker重读、7 Chromium、原上游Gate、容量/安全关闭及五精确资源清理均通过。全16生产Python coverage line2078/2308=90.0347%、branch532/696=76.4368%、combined2610/3004=86.8842%。原A架构和B执行审查者各自逐U-C01–06、AC14-01..18/20正式U01 PASS、U02 PASS、Closeout PASS；无未结阻断或新增延期。M11历史coverage债/UF13边界保留，AC14-19/M15未运行。具体命令、镜像ID、覆盖率/原始收据和逐条矩阵见[最新实施报告](VALIDATION.md)。

本机在资格通过、确认无执行任务后仅安全更新Web镜像，`http://127.0.0.1:8080`就绪/首页200且HTTP JS/CSS哈希等于新源码。首次进程环境空值导致启动失败及随后修复的收据均保留；原PG容器/卷、Key、一个暂停任务保持，没有替用户提交或恢复任务。本机更新不构成M15真实规划或云部署资格；原页面Ctrl+F5可载入新选择表单。

当前`MODULE14_CLARIFICATION_MAINTENANCE_READY_FOR_USER_ACCEPTANCE`。分支仍`feature/module-14-web-application`，前/后HEAD同`3a7564fa1ea451c356b2d998094e9aa0b4de57fc`；本轮14份既有文件/新增0/异常0，56项用户index及指纹保持。三段规范hash见配套§15.5；无新增schema，仅隔离测试验证既有迁移，本机/云库迁移未运行。stage/commit/merge/push/PR/云部署均未执行，等待用户验收与提交授权。

### 14.7 真实API测试与修复授权（2026-10-06，进行中）

用户在选择表单完成后明确要求真实API测试三次直到成功，并追加发现问题就修复。现沿§12.2/AC14-19/20及配套§16现场提示词，采用独立真实生产测试项目/新PG卷/新Key与18080回环入口，保留原8080 Demo及用户暂停任务；三场景顺序执行，不以旧M13或离线替身数字宣称通过。失败先定位实现问题、保存真实记录，再按批准合同修复及必要离线新资格后复测；不改冻结设计、预算、配额或公开发布范围。进度、实际调用、修复与核查仅由[实施状态最新记录](VALIDATION.md)回填，未完成的项目不预写PASS。

杭州a3已在否定约束修复版本生成demo_ready的2日完整攻略、保存摘要并通过复制下载；原A/B同时确认正常终态残留断流提示为实际P2。当前按配套§16.3两文件最小修复，旧源码浏览器红Gate明确复现且资源清理完成，新完整源码资格尚待完成。保留a1失败、a2本机中断和a3修前业务成功，最终源码三个真实完整场景重新核验，不覆盖历史或套用旧资格；当前不宣称全M15或云端合格。

修后terminal-20261006a新完整Closeout现exit0/65步骤全0，305模块、2639全回归、53真PG、10Chromium、三新解释器、原上游Gate/三70%coverage及容量/SSE关闭/五精确清理通过；正式原A/B收据核查进行中。后续配套§16.4仅维护自己的旧中断任务并更新独立18080 Web，仍以实际安全收据和最终三个真实场景完成度判定，原8080和暂停任务保留。

原A/B现均正式R-F03 PASS/R-F04 PASS，逐W14受影响AC、E14 §16.3矩阵与S §7.3亲读实际原始收据；A-LIVE-SSE-01/B-LIVE-UI-01的源码及正式离线维护阻断关闭，无未结阻断。独立原始9条Docker查询保留exit0/stdout空数组、剩余0于terminal-cleanup-inventory.json；薄汇总序列化警告不替代完整leaf证据。453输入、56项用户index、HEAD与三规范hash保持。接续E14 §16.4仅执行自己的独立任务维护/18080 Web更换；当前最终源码真实成功仍0/3，L-R01/L-R02实际验收及真实现场三场景不预写PASS，迁移/stage/commit/merge/push/PR/云部署均NOT_RUN。

L-R01/L-R02实际已完成：先显式关闭033控制器、停止精确独立Web，再使用qualified runtime的一次性容器执行公开M11维护；a2从open/blocked、cancel_requested=false收束为lifecycle=blocked、cancel_requested=true，settle_status=blocked。五拒绝业务入口计数全部0、供应商客户端0、无Graph或恢复、未直接执行SQL改状态；取消意图和blocked由公开M11正常持久化，finally关闭真实M11/M13/M10、exit0；实际没有a2 summary，旧2份available摘要保留，不伪造cancelled或新摘要。后核有效lease/inflight/未决request/reserved/open均0，一次性容器已自动清理。

独立18080 Web已更换最终qualified runtime 12912f5d…e54486，新Web ID5139002d54d73e3c0bc1225c6aae4f76acfb787b9b0dd2fcdd0b1e8656621989；原PG精确ID、同卷/只读Key绑定、Key长度/UTC修改ticks保持，Key正文未读。默认生产CMD、ready/home/JS/site.css均200，HTTP静态hash同已合格源码，OOMfalse/restart0。新controller正常载入自己的会话，ready-recovery-03 PASS；副作用库存0、不重放。维护准备的unsupported --no-build及外部脚本import path错误均在业务入口前失败，原失败日志保留，最终以未请求build的run --pull never加runpy执行；HTTP app.css误探及DateTime字符串对比误报只修正观察器，实际源码/Key均未变、业务POST0。安全收据为cancel-a2-public-maintenance-receipt.json、terminal-live-update-ready.json、terminal-live-replacement-audit.json及各准备失败收据。原8080/用户任务未操作，迁移与Git写/云部署均NOT_RUN；原A/B实际两阶段核查PENDING，新最终真实场景仍0/3。

原A/B已正式L-R01/L-R02 PASS，独立18080的最终qualified runtime已安全更换；当前杭州a4/上海a1两完整场景demo_ready、summary saved、确认与最终输出一致、复制下载PASS，成功2/3。按§16统一进行原A/B L02/L03核查；苏州及重启读取仍PENDING，详细事实与限制见I最新收据。

现场第三苏州a1真实失败，安全归因为规划工具参数严格合同拒绝tool_arguments_invalid，失败摘要已保存；具体参数路径未知，不将其猜测为网络或预算问题，不放宽schema。成功仍2/3。依据E14 §16使用同输入新建suzhou-a2，保留失败且不恢复/重放闭合run；最新安全收据与逐条双审以I为准。该非规范事实不改变权威设计。

真实现场已取得杭州2日demo_ready、上海2日demo_ready、苏州3日degraded三份完整攻略；苏州主行程营业未知按M13原规则标注，不改设计或伪装全信息成功。三份摘要已保存、复制下载一致；独立Web真实重启后输出/hash/日数/质量和summary保持，模型/地图预算与记录零增长。原失败保留，原A/B最终实际L04核查PENDING；完整事实、Git/迁移/资源边界见I最新现场记录。

最终原A/B均正式L04 PASS，无未结阻断；三份完整真实结果及统一重启读取完成，质量保持2demo_ready+1degraded。主营业未知降级与原合同一致，旧失败保留。资格/源码/index保持，当前本轮COMPLETE、待提交；commit/push/PR/云部署均NOT_RUN。最新证据与完整边界见I最终状态。


## M09参数修复展示维护（2026-10-06，用户已批准）

仅对workflow_provider_failed使用覆盖模型调用/输出校验失败的中性中文文案；不再统一声称供应商暂不可用。M13聚合code、API DTO、公开运行统计、归属/配额和既有冻结设计保持。M02/M09恢复语义按[M09 §26](09_itinerary_planning_loop.md#26-通用参数修正与共享储备2026-10-06用户已批准)。实施与完整网页回归入口见[E09 §30](09_itinerary_planning_loop_vibe_coding.md#30-通用参数修复实施入口2026-10-06用户已批准)。本次仍不授权实际部署。

## 枢纽澄清与安全输入提示增量（2026-10-07，用户已批准）

§11.1 新增 arrival/departure.hub_type 的 single 控件，选项仅 airport/机场、railway_station/火车站或高铁站，无预选和默认。枢纽名称问题标签包含已记录目的城市，例如“离开合肥的机场/车站”；目的城市缺项时保持普通标签。字段为 null 或有当前 M01 Issue/INVALID/CONFLICT 都展示，不以非空跳过有问题的值。仍以有限中文 user_message 经真 M03/M01 提交，前端不更改业务 state。

WebRunView/1.0 增加可选 input_issues，默认空；元素 WebInputIssue/1.0 只含 M01 RequirementFieldPath、上述五种有限 code 和固定中文 message，最多六项且路径唯一。不返回用户原文/完整需求/供应商信息。仅所属任务 details=true、closed/terminal/failed 且 terminal_code 为 requirement_failed 或历史 workflow_contract_failed 时，经公共 M11.get_state 读取最终状态；核对 run/trace/code/交付一致，仅对 ConfirmedTravelRequirement 调用 M01 公开纯检查。其他终态/历史列表/SSE 不读 state、不投影问题，不猜其他失败原因。读取无法验证时保持既有失败语义，不补写摘要/控制收据、不自动恢复。

网页输入失败显示需求不完整及逐字段固定提示，引导核对后新建任务；不把已结束任务伪装成可继续补充。历史 generic contract 任务仅在读取后实际发现端点问题时展示，原终态 code/摘要原样保留。日期/配额/归属/CSRF/幂等/预算与生产服务均保持既有合同，无迁移/日常数据库写入或本地部署授权。
### 前端首版批准增量：流程画布与活动阶段投影（2026-10-10）

用户已批准六节点画布和攻略阅读首版，并针对真实执行期间 M11.phase=None 的缺口明确批准 M13→M14 最小安全投影。§8.1 WebRunView 兼容新增 `progress_phase:WorkflowPhase|None=None`：仅 open/executing、未请求取消、run.phase=None 时允许非空；其值来自本进程合法执行槽匹配 run/trace 的 DemoDiagnostics.get_active_phase。run 原样保留 M11 视图，不把观测伪装为已持久 phase。暂停、终态、阻断、不兼容、恢复、取消或观测缺失时字段为 None；历史和 SSE 也沿同一合法投影，不建立第二份业务状态。

§8.3 的安全快照由既有 M11 视图加这一个阶段枚举组成；仍不是事件流，GET/SSE/刷新不运行图、模型、地图、恢复或扣额。作用域和释放沿 M13“活动阶段观测”增量；最外层执行结束即释放，重启不重建标记。M11.get_state 的既有限制保持。兼容字段的默认值为空，写入 DTO、版本、路由、归属及安全门槛保持。

§11 任务页使用真实 run.phase，执行时仅以合法 progress_phase 作为活动观测。需求、酒店、研究、规划、整理、交付六节点同一画布三分区，居中等宽；删除“确认并补充”流程节点但保留实际人工操作。执行中的已观测节点才有动画；等待/断读/恢复/停止与减少动画偏好停动画。阶段未知时明确告知，保留最近已确认观测并区分当前未知，不清成假进度或猜后继。研究回退按最新观测退回研究。仅完整 M12 结果读取成功标记交付。很快经过的阶段可能在下一快照直接跨过，不保证逐帧显示每个节点。

攻略仅对完整 M12 快照作安全原生 DOM 排版，包含总览、每日导航、时间线、原乘车顺序、返程、费用、来源及所有提醒；恢复转义文字但不执行 HTML或不安全URL，不新增事实。原文默认折叠，复制和下载原 Markdown 字节保持；不匹配既有 M12 拼接时展示全部正文，不猜切而丢段落。AC14-04/11/14/15/20 继续适用；新增活动观测的真实业务离线 HTTP/SSE/浏览器资格必须与界面 fixture 分开。


## 2026-10-11 批准修订：研究合同修复

状态：用户已批准并授权实施。R14：通过M13安全终态code投影中文原因，区分用户需补充、必要供给不足、动作纠错耗尽、模型输出不合合同、内部状态失败和真实供应商不可用。统计保存后释放保持，已知消耗不清零、未知标记不完整；不展示需求原文/内部异常/reasoning/凭据。补充兴趣允许单选，空/冲突走既有选项交互。仅调整必要安全映射，不改页面布局、数据库、写接口或更新运行服务。

共同依据与验收见 [M08 R08](08_candidate_research_loop.md)及[执行规范 R1～R6](08_candidate_research_loop_vibe_coding.md)，覆盖冲突旧条款。

## 2026-10-12 必要资格判断接线

Q14：只投影M13的必要资格终态code和固定中文说明，区分判断未完成、证据不足、不适用、恢复耗尽和预算耗尽；不将这些状态显示为供应商暂不可用。保留归属、CSRF、统计先保存后释放及原UI；不公开候选工作单、模型原文、reasoning或内部异常。测试含浏览器安全文案、真实主链失败分类和消费统计，不更新既有18080/8080服务。

实施和验收共同引用 [M08 Q08-01～09](08_candidate_research_loop.md)、[Q1～Q6执行契约](08_candidate_research_loop_vibe_coding.md)及本模块执行规范。用户已批准实施及隔离真实验证，完成等待提交。

## 2026-10-13 页面风格与攻略阅读优化

授权依据：用户明确要求切换到现有 UI 分支并开始优化 UI 和结果显示；指定六节点流程画布的风格作为全站基准，并要求降低攻略来源信息对阅读的干扰。本次复用 `feature/web-ui-progress-results`，先快进同步本地 main 的最新代码。

### 展示规则

1. 首页、任务页、历史页和样例页使用同一套蓝灰色、字体层级、细边框、圆角、按钮和状态标签。六节点画布的布局、连线、状态含义及动画条件沿用 §11 和首版批准增量。
2. 需求补充、需求确认和酒店选择保持原输入字段和提交语义。表单采用统一的分组与留白；主要提交操作突出，修改使用次要按钮，取消和放弃明确区分。酒店只展示当前公开候选事实。
3. 完整攻略仍只在 M12.load 成功后展示。总览、每日导航、时间线、乘车顺序、返程和具体未核验提醒保留。来源清单能够被明确识别时，移入默认关闭的“数据来源”区域；正文中匹配该清单的来源编号及地点身份核验标签默认收起，展开来源时一并显示。
4. 来源处理只改变网页呈现。原 Markdown、结果 Artifact、来源记录、复制与下载字节均不改变。不能识别清单、出现多个来源章节或日块拼接不匹配时保留全部内容。代码片段、链接和未匹配的来源编号不得被隐藏；仍以原生 DOM 渲染，不执行 HTML 或不安全 URL。
5. 样例与真实攻略共用来源展示逻辑，明确保留离线合成、生成日期和非实时标签。历史页仅使用现有接口字段显示状态与时间，不推测城市或旅行内容。空列表提供清楚的开始入口。
6. 手机布局、键盘操作、焦点指示与减少动画偏好必须保持可用。颜色不能成为状态与选中信息的唯一表达方式。

### 本次验收范围

按配套执行规范 UI05、UI06 实施后统一验证：四页公共风格与手机无溢出；原需求补充、确认、酒店选择和取消流程；来源默认折叠、展开与保守回退；营业及费用提醒；安全 Markdown；原文复制和下载；流程回退、阶段未知与中断语义。后端合同、Prompt、调用预算、数据库结构和云端服务保持当前实现。

### 本机网页同步

用户随后要求“同步到网页我要看效果”，授权将已验证的 UI 更新到现有本机预览 `http://127.0.0.1:18080`。使用相同后端及原服务配置，保留数据库卷、暂停密钥和任务记录。旧暂停或不确定记录不触发恢复、取消或重放；换版前确认没有正在执行的任务与待接纳的新请求，换版后核对持久化计数及网页资源。云端服务不在本次同步范围内。

### 用户指定攻略作为完整样例

用户明确指定本机任务的结果替换当前完整样例，授权读取并公开该份攻略。仅从指定已结束任务只读导出，通过现有检查点核对与 M12.load 验证后，保存为独立静态 Markdown；不使用访客 Cookie 或向公开接口开放真实任务读取。发布正文不得包含任务标识、内部 Ref、密钥、用户原话或模型诊断。原任务及持久化数据不变。

该份结果为合肥三日攻略，生成日期为 2026-10-13，交付状态为 degraded。source_kind 使用 validated_result 表示保存结果已通过加载校验，页面文字显示“真实任务结果”，并保留部分信息未核验的说明；不将其称为完全成功或新的 M15 供应商验收。原行程、路线、来源及具体提醒保持，仅补充固定快照说明，realtime=false。首页采用适用于固定攻略的样例说明，不再限定为模拟内容；独立合成夹具仍须标为模拟。

## 有效输入与历史兼容

网页输入只收集能够被当前业务流程使用的信息。新任务继续收集目的城市、游玩日期及天数、到达和离开目的地的机场或车站及时间、兴趣、节奏、必去和避开地点、酒店检索偏好，并保留人工酒店选择。人数、儿童年龄、房间数量、房型、行动限制及饮食禁忌停止抽取、追问和确认；总预算金额保持延期。首页示例及提示不再承诺使用这些信息。删除地图链接复选框，新确认请求显式提交 map_requested=false，地图事实查询和路线规划保持。

交通偏好、每日作息、活动时刻和时长保持原有文本收集及展示路径，规划提示词、默认时间窗、活动时长校验和交通策略不改。保留首页自然语言、补充和修订文字入口；新提取提示词不得把停用字段绕存为自由备注，其他尚未支持的备注不宣传为已生效条件。

新网页任务的 RequirementDomainState.schema_version 为 1.1，确认产物使用同一版本；没有版本增量的旧状态仍为 1.0。TravelRequirement 的字段、默认值和规范序列化保持，使旧确认摘要及源绑定摘要不变。领域服务、网页投影和提取器按状态版本选择范围；旧任务使用原规则和原提示词，不迁移存量任务。ExtractionInput 增加有默认值的 collection_version，由领域服务显式传递；旧调用的序列化提示上下文保持。原提取和修复资源保留，新版本独立资源及固定摘要，不增加模型调用次数、重试或执行预算。

新结果使用 zh-guide-v2-active-inputs 模板；旧结果保持 zh-guide-v1 的原文及摘要。结果人数和房间数量允许空值，但旧模板仍要求完整旧字段。新模板不显示停用字段，不编造人数、房间或“无限制”；住宿日期、酒店、行程、路线、费用未核验提醒、营业提醒和来源完整性保持。日块及索引的模板版本、摘要和重新读取必须一致，旧结果仍按其原任务版本重建核验。后端字段、校验函数、旧手工模板、数据库字段和历史产物全部保留，不运行生产数据迁移或清理。

验收逐条检查：仅有效输入即可确认；停用字段不进入新任务证据与摘要；四项保留字段不变；缺日期或枢纽继续暂停；旧确认摘要、命令和检查点规范字节保持；新结果可以保存后重新读取；旧结果原文不变；网页无地图链接选项；本机同步保留原数据库、暂停密钥、Cookie 归属和端口，不触发真实规划调用。

### 合肥三日游首页预设

首页提供一份已填写有效必填信息的合肥三日游示例：2026-10-23 至 2026-10-25，23 日 09:00 到达合肥南站，25 日 20:00 从同一车站离开，两端均明确为火车站。兴趣为人文、美食、打卡，节奏标准，酒店检索偏好为交通方便。日期及车站属于可编辑示例，不是用户已确定的真实行程。

这份预设用于避免因缺少必填字段而追加澄清；不绕过需求确认、日期核对、酒店选择或模型输出校验，不增加新的业务能力或修改历史任务。
